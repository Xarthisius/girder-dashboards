"""What a micrograph file says about itself: its pixel scale and its info panel.

Two things a user should not have to type in by hand, because the instrument
already recorded them:

* **The scale.** SEM software writes the pixel size into a private TIFF tag, and
  draws a scale bar into the info panel at the bottom of the image. The tag is
  the source of truth where it exists; the drawn bar is a cross-check, and the
  only thing left to measure when a round trip through an image editor has
  stripped the tag — which is exactly what happened to four of the six sample
  micrographs this was developed against.
* **The info panel** itself, the strip of instrument readings across the bottom.
  It is not specimen, so it must not be analysed: its white text and drawn ruler
  are the brightest, most compact "precipitates" in the file, and on a 16-bit
  image they also decide the 0-255 stretch that
  :py:func:`~.analysis.toGrayscale` applies to everything else.

The two live in one module because they are entangled: the drawn bar is *inside*
the panel, so measuring it means finding the panel first.

Nothing here raises. Every function returns ``None`` when it is not confident,
because a wrong answer that silently prefills a form or crops an image is worse
than no answer — the dashboard shows what was found and where it came from, and
the user can always overrule it.

Verified against real micrographs from three instruments (a TESCAN MIRA3 with
its header intact, the same image after Photoshop stripped the header, another
MIRA3 export, and an FEI Apreo), plus two negative controls: an already-cropped
micrograph and an AFM page export whose whole background is white.
"""

import re

#: TESCAN (MIRA/VEGA/CLARA) writes its acquisition header into this private tag
#: as ``key=value`` text, after an embedded JP2 thumbnail.
TESCAN_TAG = 50431
_MAKE_TAG = 271
_MODEL_TAG = 272

#: Keys a genuine TESCAN header always carries. The tag number is not registered,
#: so requiring these to parse out is what tells its header apart from some other
#: vendor's private bytes.
_TESCAN_SIGNATURE = ("PixelSizeX", "PixelSizeY", "Device", "Magnification")

#: An info panel is a small fraction of the image. Well above what the samples
#: show (90/858, 70/1094, 120/1144 — 6% to 11%) and well below the 41% that would
#: be needed to mistake a white page margin for a panel.
MAX_PANEL_FRACTION = 0.25

#: Below this a "panel" is more likely a saturated edge row than a strip of text.
MIN_PANEL_HEIGHT = 8

#: The bottom row of a panel is its background almost everywhere: the real ones
#: measure 0.99-1.00.
MIN_BOTTOM_ROW_UNIFORMITY = 0.6

#: Rows further up carry text, so they are only required to be *mostly*
#: background. The real panels never drop below 0.47; image rows sit at 0.00-0.02,
#: so there is a wide gap to put this in.
MIN_PANEL_ROW_UNIFORMITY = 0.2

#: ...and the first row of specimen above the panel has to be clearly on the other
#: side of that gap. This is what distinguishes a panel — which starts abruptly —
#: from a dark or blown-out band at the bottom of the image itself, which does not.
MAX_CONTENT_ROW_UNIFORMITY = 0.1

#: A scale bar is a long horizontal run of the brightest pixels in the panel.
_BAR_BRIGHTNESS = 0.9
_MIN_BAR_PIXELS = 20

#: Above this, the "brightest" pixels of a panel are its background rather than
#: anything drawn on it. The real panels are at 2-4%.
_MAX_BRIGHT_FRACTION = 0.25

#: A bar may be drawn in two pieces with its label between them (FEI does this).
#: A second run is part of the same bar when it is comparably long and the gap is
#: no wider than the run itself; a stray glyph on the same row is neither.
_BAR_PIECE_RATIO = 0.5
_BAR_GAP_RATIO = 1.0

#: End posts rise this far out of the bar. Above the 1-2 px thickness of the bar
#: itself, below the ~10 px cap height of the panel's text.
_MIN_POST_HEIGHT = 3

#: Bar lengths a human would print next to it, scaled by a power of ten.
_NICE_LENGTHS = (1.0, 2.0, 5.0)


def _rowUniformity(row):
    """Fraction of a row taken up by its single most common value.

    The discriminator this whole module rests on. Specimen has noise, so no
    single grey level owns more than a few percent of a row; a drawn panel is a
    flat fill with text on top, so its background owns most of one.
    """
    import numpy as np

    values, counts = np.unique(row, return_counts=True)
    index = int(counts.argmax())
    return float(counts[index]) / row.size, values[index]


def detectInfoPanel(gray):
    """Find the instrument info panel across the bottom of ``gray``.

    :param gray: the float image :py:func:`~.analysis.loadImage` returns.
    :returns: ``{'height', 'top', 'background'}`` in full-resolution pixels, or
        ``None`` when there is no panel — including for an image that is simply
        dark at the bottom, which is what the ``MAX_CONTENT_ROW_UNIFORMITY``
        check is there to reject.
    """
    import numpy as np

    height, width = gray.shape
    if height < 4 * MIN_PANEL_HEIGHT:
        return None

    uniformity, background = _rowUniformity(gray[height - 1])
    if uniformity < MIN_BOTTOM_ROW_UNIFORMITY:
        return None

    limit = int(height * MAX_PANEL_FRACTION)
    top = height
    for y in range(height - 1, height - limit - 1, -1):
        if float(np.count_nonzero(gray[y] == background)) / width < (
            MIN_PANEL_ROW_UNIFORMITY
        ):
            break
        top = y

    panelHeight = height - top
    if panelHeight < MIN_PANEL_HEIGHT or panelHeight >= limit or top == 0:
        return None

    # The boundary has to be sharp. A panel is pasted on; a vignette fades.
    above = float(np.count_nonzero(gray[top - 1] == background)) / width
    if above > MAX_CONTENT_ROW_UNIFORMITY:
        return None

    return {
        "height": int(panelHeight),
        "top": int(top),
        "background": round(float(background), 6),
    }


def _brightRuns(row):
    """``[(start, end)]``, inclusive, of each run of True in a boolean row."""
    import numpy as np

    edges = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
    return list(zip(np.where(edges == 1)[0], np.where(edges == -1)[0] - 1))


def _barExtent(bright):
    """The row of ``bright`` holding a scale bar, and the bar's extent on it.

    Returns ``(y, x0, x1)`` or ``None``. The widest bright run in the panel is
    the seed; runs on the same row that are comparably long and close by are
    absorbed, which is how a bar drawn as ``|—— 50 µm ——|`` is measured as one
    bar rather than as its left half.
    """
    best = None
    for y in range(bright.shape[0]):
        runs = _brightRuns(bright[y])
        for x0, x1 in runs:
            if best is None or x1 - x0 > best[1] - best[0]:
                best = (int(x0), int(x1), y, runs)
    if best is None:
        return None

    x0, x1, y, runs = best
    length = x1 - x0 + 1
    if length < _MIN_BAR_PIXELS:
        return None

    for start, end in runs:
        if end - start + 1 < length * _BAR_PIECE_RATIO:
            continue
        gap = start - x1 - 1 if start > x1 else x0 - end - 1
        if 0 <= gap <= length * _BAR_GAP_RATIO:
            x0, x1 = min(x0, int(start)), max(x1, int(end))

    return y, x0, x1


def measureScaleBar(gray, panelTop):
    """Measure the scale bar drawn in the panel, in image pixels.

    :returns: ``{'pixels', 'x', 'y', 'width', 'method'}`` in full-resolution
        image coordinates, or ``None``. ``pixels`` is the bar's length: end-post
        centre to end-post centre where the bar has posts, which is how the
        instrument drew it, and the plain extent where it has none.

    Not the length in *micrometres* — that is printed beside the bar as text, and
    reading it would need OCR. The pixel count is the tedious half anyway.
    """
    import numpy as np

    strip = gray[panelTop:]
    if strip.size == 0 or strip.max() <= strip.min():
        return None

    bright = strip >= strip.max() * _BAR_BRIGHTNESS
    # A featureless panel — all black, or a flat fill with nothing drawn on it —
    # has no brightest *marks*, only a brightest value that most of it shares. Its
    # widest "run" would then be the panel itself, reported as a bar the width of
    # the image. Real bars and text cover a few percent of a panel.
    if np.count_nonzero(bright) > _MAX_BRIGHT_FRACTION * bright.size:
        return None

    extent = _barExtent(bright)
    if extent is None:
        return None
    y, x0, x1 = extent

    # Tick heights, measured as the bright run rising out of the bar in each
    # column. Anchoring to the bar is what keeps the panel's text out of it.
    heights = {}
    for x in range(x0, x1 + 1):
        run = 0
        row = y - 1
        while row >= 0 and bright[row, x]:
            run += 1
            row -= 1
        if run >= _MIN_POST_HEIGHT:
            heights[x] = run

    pixels, method = float(x1 - x0 + 1), "extent"
    if heights:
        tallest = max(heights.values())
        posts = [x for x, run in heights.items() if run >= tallest * 0.9]
        middle = (x0 + x1) / 2.0
        left = [x for x in posts if x < middle]
        right = [x for x in posts if x >= middle]
        if left and right:
            pixels = sum(right) / len(right) - sum(left) / len(left)
            method = "posts"

    if pixels < _MIN_BAR_PIXELS:
        return None
    return {
        "pixels": round(pixels, 2),
        "x": int(x0),
        "y": int(panelTop + y),
        "width": int(x1 - x0 + 1),
        "method": method,
    }


def parseTescanHeader(blob):
    """Parse the bytes of tag 50431 into a dict of strings, ``{}`` if it is not one.

    The text follows an embedded JP2 thumbnail, so everything up to its EOI
    marker is skipped: without that, random binary bytes can parse as key=value
    lines.
    """
    if not blob:
        return {}

    end = blob.rfind(b"\xff\xd9")
    text = blob[end + 2 if end != -1 else 0 :].decode("latin-1")

    header = {}
    for line in re.split(r"[\x00-\x08\x0b\x0c\x0e-\x1f\r\n]+", text):
        match = re.fullmatch(r"\s*([A-Za-z0-9_.\[\]]+)=(.*?)\s*", line)
        if match:
            header[match.group(1)] = match.group(2)
    return header


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def readHeaderScale(path):
    """Read the pixel size out of a TIFF's vendor header.

    :returns: ``{'umPerPx', 'source', 'label', 'detail', 'panelHeight'}`` or
        ``None``. ``panelHeight`` is the info panel's height where the same header
        states it, which is more authoritative than measuring it.

    Only the two vendors there were files to verify against are read. The
    standard ``XResolution``/``ResolutionUnit`` pair deliberately is not: on all
    four real micrographs here it holds a screen or print DPI left behind by the
    export (96, 146, 314 dpi), which has nothing to do with the specimen and
    would be a confidently wrong answer.
    """
    from .analysis import isTiff

    if not isTiff(path):
        return None

    import tifffile

    try:
        with tifffile.TiffFile(str(path)) as handle:
            page = handle.pages[0]
            tags = page.tags
            blob = tags[TESCAN_TAG].value if TESCAN_TAG in tags else None
            make = str(tags[_MAKE_TAG].value) if _MAKE_TAG in tags else ""
            model = str(tags[_MODEL_TAG].value) if _MODEL_TAG in tags else ""
            fei = handle.fei_metadata
    except Exception:
        return None

    tescan = parseTescanHeader(blob)
    if sum(key in tescan for key in _TESCAN_SIGNATURE) >= 2:
        metresPerPx = _float(tescan.get("PixelSizeX"))
        if metresPerPx and metresPerPx > 0:
            # The Model tag is the fuller of the two ("MIRA3 LMH MI4131573" against
            # the header's "MIRA3 LMH"), so it wins where the export kept it.
            device = model or tescan.get("Device") or "TESCAN"
            return {
                "umPerPx": metresPerPx * 1e6,
                "source": "tescan-header",
                "label": f"{device} header",
                "detail": f"PixelSizeX = {metresPerPx * 1e9:.4f} nm/px.",
                "panelHeight": _int(tescan.get("ImageStripSize")),
            }

    if fei:
        metresPerPx = _float((fei.get("Scan") or {}).get("PixelWidth"))
        if metresPerPx and metresPerPx > 0:
            system = (fei.get("System") or {}).get("SystemType") or "FEI"
            # ResolutionY is the *specimen* height; the file is taller by exactly
            # the databar the software drew underneath it.
            resolutionY = _int((fei.get("Image") or {}).get("ResolutionY"))
            panelHeight = None
            if resolutionY and 0 < resolutionY < page.imagelength:
                panelHeight = int(page.imagelength - resolutionY)
            return {
                "umPerPx": metresPerPx * 1e6,
                "source": "fei-header",
                "label": f"{system} header",
                "detail": f"Scan.PixelWidth = {metresPerPx * 1e9:.4f} nm/px.",
                "panelHeight": panelHeight,
            }

    if make or model:
        # Branded, but the header is gone — a Photoshop round trip does this. Say
        # nothing here; the drawn bar is still measurable and is what gets used.
        return None
    return None


def _nearestNice(microns):
    """The 1/2/5 × 10ⁿ value closest to ``microns``, and the relative gap to it."""
    import math

    exponent = math.floor(math.log10(microns))
    best = None
    for step in (exponent, exponent + 1):
        for nice in _NICE_LENGTHS:
            candidate = nice * (10.0**step)
            error = abs(candidate - microns) / microns
            if best is None or error < best[1]:
                best = (candidate, error)
    return best


def _niceBar(umPerPx, bar=None):
    """Express a pixel size as a scale bar a person would recognise.

    ``0.0077221 µm/px`` is true but unreadable, and the form asks for a bar
    length and a pixel count. So the length is rounded to 1, 2 or 5 times a power
    of ten and the pixel count is whatever that comes to.

    Where a bar was measured on the image, the length it comes to is the one
    used — so the form ends up describing *the bar the user can see*, at the
    header's exact scale rather than at the instrument's whole-pixel rendering
    of it. Failing that, any length that puts the bar in the 80-800 px range a
    drawn one occupies will do.
    """
    import math

    if bar:
        microns, error = _nearestNice(bar["pixels"] * umPerPx)
        # 3%: comfortably more than the ±1 px the rendering can be out on the
        # shortest bar seen here (1/129), and far less than the 100% gap to the
        # next value up.
        if error <= 0.03:
            return microns, round(microns / umPerPx, 3)

    exponent = math.floor(math.log10(80 * umPerPx))
    for _ in range(6):
        for nice in _NICE_LENGTHS:
            microns = nice * (10.0**exponent)
            pixels = microns / umPerPx
            if 80 <= pixels <= 800:
                return microns, round(pixels, 3)
        exponent += 1
    return 1.0, round(1.0 / umPerPx, 3)


def inspectMicrograph(path, gray):
    """Everything the file and its pixels say about scale and info panel.

    :param path: local path to the image, for its vendor header.
    :param gray: the decoded float image, for its pixels.
    :returns: ``{'panel': ..., 'scale': ...}``, either of which may be ``None``.

    The scale is reported as a bar length and a pixel count because that is what
    the analysis takes, and what the user can check against the bar printed on
    the image. When only the drawn bar could be measured, ``barMicrons`` is
    ``None`` and ``complete`` is ``False``: the pixel count is known and the
    length is not, so the dashboard fills in half the form and says so.
    """
    header = readHeaderScale(path)
    panel = detectInfoPanel(gray)

    if header and header.get("panelHeight"):
        stated = int(header["panelHeight"])
        if 0 < stated < gray.shape[0]:
            measured = panel["height"] if panel else None
            panel = {
                "height": stated,
                "top": int(gray.shape[0] - stated),
                "background": panel["background"] if panel else None,
                "source": header["source"],
                "agrees": measured == stated if measured else None,
            }
    if panel is not None:
        panel.setdefault("source", "pixels")
        panel.setdefault("agrees", None)

    bar = measureScaleBar(gray, panel["top"]) if panel else None

    if header:
        umPerPx = header["umPerPx"]
        microns, pixels = _niceBar(umPerPx, bar)
        detail = header["detail"]
        if bar:
            # The drawn bar is only rendered to the nearest pixel, so it can
            # corroborate the header but never correct it. Quoting what it comes
            # to lets the user check it against the length printed beside it.
            drawn = bar["pixels"] * umPerPx
            detail += (
                f" The bar drawn on the image spans {bar['pixels']:g} px, "
                f"which at that scale is {drawn:.3f} µm."
            )
        return {
            "panel": panel,
            "scale": {
                "umPerPx": umPerPx,
                "nmPerPx": umPerPx * 1000.0,
                "barMicrons": microns,
                "barPixels": pixels,
                "source": header["source"],
                "label": header["label"],
                "detail": detail,
                "bar": bar,
                "complete": True,
            },
        }

    if bar:
        return {
            "panel": panel,
            "scale": {
                "umPerPx": None,
                "nmPerPx": None,
                "barMicrons": None,
                "barPixels": bar["pixels"],
                "source": "scale-bar",
                "label": "the scale bar drawn on the image",
                "detail": (
                    f"A scale bar {bar['pixels']:g} px wide (±1 px) was measured "
                    "in the info panel. Its length is printed beside it on the "
                    "image — type that in and the scale is set."
                ),
                "bar": bar,
                "complete": False,
            },
        }

    return {"panel": panel, "scale": None}
