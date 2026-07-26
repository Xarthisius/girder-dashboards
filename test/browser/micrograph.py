#!/usr/bin/env python3
"""Generate a synthetic micrograph for the tests to upload.

The real SEM micrographs the pipeline was developed against are large and
unpublished, so the tests use a synthetic stand-in: bright round "precipitates"
on a textured background, at a known count and spacing. That also makes it a
better test subject than real data — the expected answer is known.

Written with nothing but the standard library (a bare uncompressed 8-bit TIFF is
a header, one IFD and one strip) so that the browser harness's seed step stays
dependency-free, exactly like the rest of ``seed.py``.

``write(..., panel=True)`` produces a second variant carrying the two things a
real SEM file carries and this one otherwise does not: an instrument info panel
across the bottom, with a scale bar drawn in it, and a TESCAN acquisition header
in private tag 50431 stating the pixel size. It exists so the scale and panel
detection in :py:mod:`girder_dashboards.precipitate.scale` can be tested against
a file whose right answers are known by construction — 1 µm is exactly
``BAR_PIXELS`` px, and the panel is exactly ``PANEL_HEIGHT`` px tall.

    python3 test/browser/micrograph.py [output.tif]
"""

import math
import os
import random
import struct
import sys

WIDTH = 512
HEIGHT = 512

#: Grid of precipitates. Jittered off the lattice so nearest-neighbour spacing has
#: a distribution rather than a single value.
GRID = 8
JITTER = 9
RADIUS = 2.1

BACKGROUND = 38
PEAK = 255
NOISE = 7

#: The info panel of the ``panel=True`` variant: a mid-grey strip carrying a
#: scale bar and some blocks standing in for the instrument's readout text.
PANEL_HEIGHT = 64
PANEL_BACKGROUND = 96
PANEL_INK = 255

#: Side of one "glyph" in the panel's stand-in text. Matched to the specimen's
#: precipitates (``RADIUS`` 2.1 px) so the detector treats them alike, which is
#: what makes leaving the panel in a measurable mistake.
GLYPH = 4

#: The drawn bar, in pixels from end-post *centre* to end-post centre, which is
#: how the instrument renders it and how ``scale.measureScaleBar`` measures it.
#: One micrometre, so PixelSizeX below and the drawn bar describe the same scale
#: and either route to it yields the same answer.
BAR_PIXELS = 128
BAR_LEFT = 180
BAR_POST_WIDTH = 2
BAR_POST_HEIGHT = 10
BAR_TICK_HEIGHT = 5
BAR_TICK_STEP = 16
BAR_THICKNESS = 2

#: Metres per pixel: 1 µm / BAR_PIXELS.
PIXEL_SIZE_M = 1e-6 / BAR_PIXELS

#: Where seed.py puts them, and where verify.cjs looks for them.
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
DEFAULT_PATH = os.path.join(FIXTURES, "micrograph.tif")
PANEL_PATH = os.path.join(FIXTURES, "micrograph-tescan.tif")
STRIPPED_PATH = os.path.join(FIXTURES, "micrograph-stripped.tif")


def _blob(pixels, cx, cy, radius, peak):
    """Add one round Gaussian blob, clipped to the image."""
    reach = int(math.ceil(radius * 2.5))
    for y in range(max(0, int(cy) - reach), min(HEIGHT, int(cy) + reach + 1)):
        for x in range(max(0, int(cx) - reach), min(WIDTH, int(cx) + reach + 1)):
            distSq = (x - cx) ** 2 + (y - cy) ** 2
            value = peak * math.exp(-distSq / (2 * (radius * 0.62) ** 2))
            index = y * WIDTH + x
            pixels[index] = min(255, pixels[index] + int(value))


def render(seed=20260726):
    """Return ``(pixels, centres)``: the 8-bit image and the true blob centres."""
    rng = random.Random(seed)

    # A little low-frequency shading plus per-pixel noise, so the detector has to
    # do its top-hat work rather than finding blobs in a flat field.
    pixels = bytearray(WIDTH * HEIGHT)
    for y in range(HEIGHT):
        shade = BACKGROUND + int(10 * math.sin(y / 90.0))
        for x in range(WIDTH):
            pixels[y * WIDTH + x] = max(
                0,
                min(
                    255,
                    shade + int(6 * math.sin(x / 70.0)) + rng.randint(-NOISE, NOISE),
                ),
            )

    centres = []
    step = WIDTH // GRID
    for row in range(GRID):
        for column in range(GRID):
            cx = step * (column + 0.5) + rng.uniform(-JITTER, JITTER)
            cy = step * (row + 0.5) + rng.uniform(-JITTER, JITTER)
            _blob(pixels, cx, cy, RADIUS, PEAK - BACKGROUND)
            centres.append((cx, cy))

    return pixels, centres


def _fill(pixels, width, x0, y0, w, h, value):
    for y in range(y0, y0 + h):
        row = y * width
        for x in range(x0, x0 + w):
            pixels[row + x] = value


def renderPanel():
    """Return the info-strip rows: a scale bar and some stand-in readout text.

    Deliberately built the way the real ones are, because that is what the
    detector keys on: a flat background that owns most of every row, marks that
    are the brightest thing in the strip, and a bar whose two end posts stand
    taller than its interior ticks.
    """
    pixels = bytearray([PANEL_BACKGROUND]) * (WIDTH * PANEL_HEIGHT)

    barY = PANEL_HEIGHT // 2
    barRight = BAR_LEFT + BAR_PIXELS + BAR_POST_WIDTH - 1
    _fill(
        pixels, WIDTH, BAR_LEFT, barY, barRight - BAR_LEFT + 1, BAR_THICKNESS, PANEL_INK
    )

    # End posts, at the two ends of the bar; their centres are BAR_PIXELS apart.
    for x0 in (BAR_LEFT, BAR_LEFT + BAR_PIXELS):
        _fill(
            pixels, WIDTH, x0, barY - BAR_POST_HEIGHT, BAR_POST_WIDTH,
            BAR_POST_HEIGHT, PANEL_INK,
        )
    # Interior ticks, shorter, so the post detection can tell them apart.
    for offset in range(BAR_TICK_STEP, BAR_PIXELS, BAR_TICK_STEP):
        _fill(
            pixels, WIDTH, BAR_LEFT + offset, barY - BAR_TICK_HEIGHT, 1,
            BAR_TICK_HEIGHT, PANEL_INK,
        )

    # "Text": rows of small bright marks standing in for the instrument's
    # readout. Deliberately the size and shape of a precipitate, because that is
    # the whole problem with an info panel — on the real micrographs the glyphs
    # are detected as several dozen extra particles, and a fixture whose panel is
    # made of long rectangles the shape gates reject would not reproduce it.
    for row, y0 in enumerate((10, 24, 44)):
        for column in range(18):
            x0 = 12 + column * 26 + (row % 2) * 9
            if x0 + GLYPH > BAR_LEFT - 8 and y0 < PANEL_HEIGHT // 2 + BAR_THICKNESS:
                continue  # leave the bar and its label room
            _fill(pixels, WIDTH, x0, y0, GLYPH, GLYPH, PANEL_INK)

    return pixels


def tescanHeader():
    """Bytes for private tag 50431, in the shape a MIRA3 writes them.

    Including the leading binary: the real header follows an embedded JP2
    thumbnail, and the parser skips to its EOI marker precisely so that binary
    cannot masquerade as key=value lines. A fixture without it would leave that
    branch untested.
    """
    thumbnail = b"\x00\xff\x4f\xff\x51" + bytes(range(48)) + b"\xff\xd9"
    fields = [
        ("Device", "MIRA3 LMH"),
        ("Magnification", "35.005e3"),
        ("PixelSizeX", f"{PIXEL_SIZE_M:.10e}"),
        ("PixelSizeY", f"{PIXEL_SIZE_M:.10e}"),
        ("ImageStripSize", str(PANEL_HEIGHT)),
        ("HV", "3.0000e3"),
    ]
    text = "".join(f"{key}={value}\r\n" for key, value in fields)
    return thumbnail + text.encode("latin-1")


def _ifd(entries):
    """Serialize the TIFF header, the IFD, and any out-of-line tag data.

    ``entries`` are ``(tag, kind, count, value)`` in ascending tag order, where a
    ``bytes`` value is one too big for the 4-byte field and is written after the
    IFD with its offset left behind — which is the only reason this is not the
    six lines it started as.
    """
    # Header (8) + entry count (2) + entries (12 each) + next-IFD offset (4).
    dataOffset = 8 + 2 + len(entries) * 12 + 4
    payload = bytearray()
    for _, _, _, value in entries:
        if isinstance(value, bytes):
            payload += value + (b"\x00" if len(value) % 2 else b"")

    out = bytearray()
    out += struct.pack("<2sHI", b"II", 42, 8)
    out += struct.pack("<H", len(entries))
    cursor = dataOffset
    for tag, kind, count, value in entries:
        if isinstance(value, bytes):
            field = struct.pack("<I", cursor)
            cursor += len(value) + (len(value) % 2)
        elif tag == 273:  # StripOffsets: the pixels follow everything else.
            field = struct.pack("<I", dataOffset + len(payload))
        elif kind == 3:  # A SHORT value is left-justified in its 4-byte field.
            field = struct.pack("<HH", value, 0)
        else:
            field = struct.pack("<I", value)
        out += struct.pack("<HHI", tag, kind, count) + field
    out += struct.pack("<I", 0)
    out += payload
    return out


def write(path=None, seed=20260726, panel=False, header=True):
    """Write the micrograph as an uncompressed 8-bit greyscale TIFF.

    :param panel: also draw an instrument info panel across the bottom and
        declare a TESCAN header, as a real micrograph out of an SEM has.
    :param header: with ``panel``, whether to write the vendor tags at all.
        ``False`` is the file a round trip through an image editor leaves behind
        — the drawn bar survives, the header does not — which is the state four
        of the six real sample micrographs were in.
    """
    if path is None:
        path = (PANEL_PATH if header else STRIPPED_PATH) if panel else DEFAULT_PATH
    branded = panel and header

    pixels, centres = render(seed)
    height = HEIGHT
    if panel:
        pixels = pixels + renderPanel()
        height += PANEL_HEIGHT

    # tag, type (2=ASCII, 3=SHORT, 4=LONG, 7=UNDEFINED), count, value.
    # Tags must be in ascending order.
    entries = [
        (256, 3, 1, WIDTH),  # ImageWidth
        (257, 3, 1, height),  # ImageLength
        (258, 3, 1, 8),  # BitsPerSample
        (259, 3, 1, 1),  # Compression: none
        (262, 3, 1, 1),  # PhotometricInterpretation: BlackIsZero
    ]
    if branded:
        make = b"TESCAN - http://www.tescan.com/\x00"
        model = b"MIRA3 LMH MI4131573\x00"
        entries += [(271, 2, len(make), make), (272, 2, len(model), model)]
    entries += [
        (273, 4, 1, 0),  # StripOffsets, filled in by _ifd
        (277, 3, 1, 1),  # SamplesPerPixel
        (278, 3, 1, height),  # RowsPerStrip: the whole image
        (279, 4, 1, WIDTH * height),  # StripByteCounts
    ]
    if branded:
        blob = tescanHeader()
        entries.append((50431, 7, len(blob), blob))

    out = _ifd(entries) + pixels

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(out)

    return path, len(centres)


if __name__ == "__main__":
    # With no arguments, both fixtures; with one, just the plain micrograph, so
    # the original one-argument invocation still means what it did.
    if len(sys.argv) > 1:
        variants = [(sys.argv[1], False, True)]
    else:
        variants = [(None, False, True), (None, True, True), (None, True, False)]

    for target, withPanel, withHeader in variants:
        written, count = write(target, panel=withPanel, header=withHeader)
        notes = f" + {PANEL_HEIGHT} px info panel" if withPanel else ""
        notes += "" if withHeader else ", header stripped"
        print(f"wrote {written} ({WIDTH}x{HEIGHT}{notes}, {count} precipitates)")
