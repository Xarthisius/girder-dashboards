"""Precipitate detection and inter-particle spacing measurement.

A port of the research code at
https://github.com/Taheri-Mousavi-Laboratory/Image-analysis-precipitate-detection-and-particle-spacing-estimation
(``src/PrecipitateDetection_IPspacing_SingleImage.py`` and its multi-image
sibling) with three deliberate changes:

* **No plotting and no printing.** :py:func:`analyze` returns a JSON-serializable
  dict of numbers; the browser draws the figures. The originals' five matplotlib
  panels map onto the arrays in ``regions[].particles`` — the overlay and
  nearest-neighbour map need ``x``/``y``/``diameterPx``/``nnIndex``, the
  histograms need ``diameterNm``/``spacingNm``, the heat map needs all of them.
* **Regions of interest replace the multi-image script's separate files.** Each
  ROI is cropped out and run through the pipeline on its own, exactly as the
  original treated its three ROI *files*, then all regions are pooled. Detection
  is therefore per-region: top-hat normalisation and the blob threshold see only
  the crop, which is what made the original's per-ROI numbers what they were.
* **No OpenCV.** ``tifffile``/``imageio`` plus numpy cover the two things cv2 was
  used for (decode, grey conversion + min/max stretch), which keeps a heavy
  wheel out of the worker image. The conversion order is kept identical to
  ``cv2.imread(IMREAD_UNCHANGED)`` + ``cvtColor(BGR2GRAY)``: to grey first, then
  stretch to 8 bits only if the input was not already 8-bit.

Everything here is pure: numpy in, plain dicts out, no Girder and no Celery. That
is what lets the same function run in a Celery worker and in Girder's own
local-job thread, and be tested with neither.
"""

import numpy as np
from scipy.spatial import KDTree
from skimage import filters
from skimage.feature import blob_log
from skimage.measure import label, regionprops
from skimage.morphology import disk, white_tophat

from .presets import (  # noqa: F401  (re-exported for callers of this module)
    DEFAULT_PRESET,
    PRESETS,
    AnalysisError,
    presetParams,
)


def toGrayscale(image):
    """Convert a decoded image array to float grey in ``[0, 1]``.

    Mirrors the original ``load_image`` step for step, because the detection
    thresholds are absolute: colour is collapsed with the same luminance weights
    (and the same round-to-integer) that ``cv2.COLOR_BGR2GRAY`` applies, and the
    0-255 stretch only happens for inputs that were not already 8-bit, so 8-bit
    micrographs keep the brightness the ``minPeakBrightness`` gate assumes.

    The rounding matters: dropping it shifts a handful of the thousands of
    Laplacian-of-Gaussian candidates across the threshold and changes the
    particle count by ~1%.
    """
    array = np.asarray(image)
    if array.ndim == 3:
        if array.shape[2] < 3:
            array = array[:, :, 0]
        else:
            # Alpha, if present, is dropped exactly as cvtColor drops it.
            weights = np.array([0.299, 0.587, 0.114])
            gray = array[:, :, :3].astype(np.float64) @ weights
            array = np.round(gray).astype(array.dtype)
    elif array.ndim != 2:
        raise AnalysisError(
            f"Expected a 2D or 3D image, got an array with shape {array.shape}."
        )

    if array.dtype != np.uint8:
        lo, hi = float(np.min(array)), float(np.max(array))
        if hi <= lo:
            array = np.zeros(array.shape, dtype=np.uint8)
        else:
            # cv2.normalize saturate_casts, i.e. rounds, on the way to an
            # integer dtype; truncating instead biases every pixel down by up
            # to one grey level.
            scaled = (array.astype(np.float64) - lo) * (255.0 / (hi - lo))
            array = np.round(scaled).astype(np.uint8)

    return array.astype(np.float64) / 255.0


#: TIFF, BigTIFF, both byte orders. The 4-byte signature is exact, which is why
#: this is worth doing instead of trusting a file name.
_TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def isTiff(path):
    """Whether ``path`` is a TIFF, by signature rather than by extension."""
    try:
        with open(path, "rb") as handle:
            return handle.read(4) in _TIFF_MAGIC
    except OSError:
        return False


def loadImage(path):
    """Decode an image file to float grey in ``[0, 1]``.

    TIFF goes through ``tifffile``, which handles the LZW-compressed and 16-bit
    variants SEM software emits; anything else falls back to ``imageio``.

    The choice is made from the file's **signature, not its name**. The name is not
    dependable here: girder_worker's ``GirderFileId`` transform downloads to a
    temporary path named after the file's ObjectId, with no extension at all, so
    dispatching on the extension sent every Celery-path micrograph down the
    non-TIFF branch — quietly decoding with a different library than the
    in-process path used, for a pipeline whose thresholds are absolute.
    """
    if isTiff(path):
        import tifffile

        try:
            raw = tifffile.imread(str(path))
        except Exception as exc:
            raise AnalysisError(f"Could not read the TIFF image: {exc}") from exc
    else:
        import imageio.v3 as iio

        try:
            raw = iio.imread(str(path))
        except Exception as exc:
            raise AnalysisError(f"Could not read the image: {exc}") from exc

    # A 3D array is either channels-last (H, W, C) or a page stack (P, H, W). The
    # last axis is the one that decides: a 3-page greyscale stack has shape
    # (3, H, W), and guessing from the *first* axis would collapse three pages into
    # three columns of nonsense.
    if raw.ndim == 3 and raw.shape[-1] not in (1, 2, 3, 4):
        # A multi-page TIFF: analyse the first page, as the originals did.
        raw = raw[0]
    return toGrayscale(raw)


def _preprocess(gray, tophatRadius, smoothSigma):
    tophat = white_tophat(gray, disk(tophatRadius))
    peak = tophat.max()
    tophatNorm = tophat / peak if peak > 0 else tophat.copy()
    return tophatNorm, filters.gaussian(tophatNorm, sigma=smoothSigma)


def _detectBlobs(tophatSmooth, params):
    blobs = blob_log(
        tophatSmooth,
        min_sigma=params["minSigma"],
        max_sigma=params["maxSigma"],
        num_sigma=int(params["numSigma"]),
        threshold=params["threshold"],
        overlap=params["overlap"],
    )
    if len(blobs) == 0:
        return np.empty((0, 4))

    radius = np.sqrt(2) * blobs[:, 2]
    diameter = 2 * radius
    keep = (diameter >= params["minDiameterPx"]) & (diameter <= params["maxDiameterPx"])
    # Columns: y, x, radius, diameter.
    return np.column_stack(
        [blobs[keep, 0], blobs[keep, 1], radius[keep], diameter[keep]]
    )


def _validate(gray, tophatNorm, candidates, params):
    """Keep only candidates that are bright, compact and round enough.

    Straight transliteration of the originals' ``validate_precipitates``. Every
    place the two scripts differ — the annulus inner edge, the minimum blob and
    local-window radii, whether a too-small window is clamped or rejected, what a
    black annulus means, and whether the equivalent-diameter gate applies at all
    — is a parameter rather than a branch on the preset name.
    """
    accepted = []
    height, width = gray.shape
    annulusInner = params["annulusInner"]

    for cy_f, cx_f, radiusPx, _ in candidates:
        cx, cy = int(round(cx_f)), int(round(cy_f))
        rBlob = max(params["minBlobRadius"], int(round(radiusPx)))
        r = int(round(radiusPx * params["localRadiusFactor"]))
        if params["clampLocalRadius"]:
            r = max(params["minLocalRadius"], r)
        elif r < params["minLocalRadius"]:
            continue

        y1, y2 = max(0, cy - r), min(height, cy + r + 1)
        x1, x2 = max(0, cx - r), min(width, cx + r + 1)

        patch = tophatNorm[y1:y2, x1:x2]
        if patch.size == 0:
            continue

        yy, xx = np.indices(patch.shape)
        cyLoc, cxLoc = cy - y1, cx - x1
        distSq = (xx - cxLoc) ** 2 + (yy - cyLoc) ** 2

        coreVals = patch[distSq <= rBlob**2]
        annulusVals = patch[(distSq > (rBlob * annulusInner) ** 2) & (distSq <= r**2)]
        if len(coreVals) == 0 or len(annulusVals) == 0:
            continue

        peak = coreVals.max()
        annulusMean = annulusVals.mean()
        if annulusMean < 1e-6:
            if not params["acceptOnDarkAnnulus"]:
                continue
            contrastRatio = np.inf
        else:
            contrastRatio = coreVals.mean() / annulusMean

        if (
            peak < params["minPeakBrightness"]
            or contrastRatio < params["minContrastRatio"]
        ):
            continue

        roiMask = distSq <= r**2
        vals = patch[roiMask]
        lo, hi = vals.min(), vals.max()
        if hi <= lo:
            continue

        binary = (patch >= lo + params["thresholdFraction"] * (hi - lo)) & roiMask
        labeled = label(binary, connectivity=2)
        props = regionprops(labeled)
        if not props or len(props) > params["maxComponentCount"]:
            continue

        # Prefer the component the blob actually sits on; fall back to the
        # nearest one when the centre landed just outside it.
        centerLabel = labeled[cyLoc, cxLoc]
        if centerLabel == 0:
            dists = [
                np.hypot(p.centroid[1] - cxLoc, p.centroid[0] - cyLoc) for p in props
            ]
            selected = props[int(np.argmin(dists))]
        else:
            selected = next((p for p in props if p.label == centerLabel), None)
        if selected is None:
            continue

        area, perimeter = selected.area, selected.perimeter
        major, minor = selected.axis_major_length, selected.axis_minor_length
        equivDiameter = selected.equivalent_diameter_area
        if minor == 0 or perimeter == 0:
            continue

        aspectRatio = major / minor
        circularity = 4 * np.pi * area / (perimeter**2)
        fillFraction = area / roiMask.sum()

        if not (
            params["minAreaLocal"] <= area <= params["maxAreaLocal"]
            and aspectRatio <= params["maxAspectRatio"]
            and circularity >= params["minCircularity"]
            and selected.solidity >= params["minSolidity"]
            and selected.eccentricity <= params["maxEccentricity"]
            and fillFraction >= params["minFillFraction"]
        ):
            continue
        # Independently, because the coarse preset sets both bounds to None and a
        # caller may override only one of them: a chained comparison against a
        # None bound is a TypeError mid-run, not a rejected particle.
        if (
            params["minEquivDiameter"] is not None
            and equivDiameter < params["minEquivDiameter"]
        ):
            continue
        if (
            params["maxEquivDiameter"] is not None
            and equivDiameter > params["maxEquivDiameter"]
        ):
            continue

        localCy, localCx = selected.centroid
        accepted.append(
            (
                x1 + localCx,
                y1 + localCy,
                equivDiameter,
                float(peak),
                float(min(contrastRatio, 1e9)),
                float(circularity),
            )
        )

    if not accepted:
        return np.empty((0, 6))
    return np.array(accepted, dtype=np.float64)


def _describe(values, umPerPx):
    """Descriptive statistics in px, µm and nm, plus CV, SEM and 95% CI.

    A superset of what the two scripts printed: the single-image one stopped at
    min/max, the multi-image one added CV/SEM/CI for its pooled block only.
    Computing all of them everywhere costs nothing and keeps one shape of stats
    object in the UI.
    """
    values = np.asarray(values, dtype=np.float64)
    n = int(values.size)
    if n == 0:
        return {"n": 0}

    mean = float(np.mean(values))
    # ddof=1 needs at least two samples; the originals would have raised here.
    std = float(np.std(values, ddof=1)) if n > 1 else 0.0
    nmPerPx = umPerPx * 1000.0

    def scaled(px):
        return {"px": px, "um": px * umPerPx, "nm": px * nmPerPx}

    return {
        "n": n,
        "mean": scaled(mean),
        "std": scaled(std),
        "median": scaled(float(np.median(values))),
        "min": scaled(float(np.min(values))),
        "max": scaled(float(np.max(values))),
        "sem": scaled(std / np.sqrt(n)),
        "ci95": scaled(1.96 * std / np.sqrt(n)),
        "cv": (std / mean) if mean else None,
    }


def _spacing(coords, diameters, edgeToEdge):
    """Nearest-neighbour distances via a KD-tree over centroids.

    ``edgeToEdge`` subtracts both particles' radii, clamped at zero for the
    overlapping-mask case; otherwise the raw centre-to-centre distance is used.
    """
    tree = KDTree(coords)
    dists, idx = tree.query(coords, k=2)
    nnDist, nnIndex = dists[:, 1], idx[:, 1]

    if edgeToEdge:
        radii = diameters / 2.0
        spacing = np.maximum(nnDist - radii - radii[nnIndex], 0.0)
    else:
        spacing = nnDist.copy()
    return spacing, nnIndex


def _normalizeRegions(regions, width, height):
    """Validate ROIs and clamp them to the image, or return the whole image."""
    if not regions:
        return [
            {
                "label": "Whole image",
                "x": 0,
                "y": 0,
                "width": int(width),
                "height": int(height),
            }
        ]

    normalized = []
    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict):
            raise AnalysisError("Each region of interest must be a JSON object.")
        try:
            x = int(round(float(region.get("x", 0))))
            y = int(round(float(region.get("y", 0))))
            w = int(round(float(region["width"])))
            h = int(round(float(region["height"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise AnalysisError(
                "Each region of interest needs numeric x, y, width and height."
            ) from exc

        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(int(width), x + w), min(int(height), y + h)
        if x1 - x0 < 8 or y1 - y0 < 8:
            raise AnalysisError(
                f"Region '{region.get('label') or index}' is outside the image or "
                "smaller than 8×8 pixels."
            )

        normalized.append(
            {
                "label": str(region.get("label") or f"ROI {index}"),
                "x": x0,
                "y": y0,
                "width": x1 - x0,
                "height": y1 - y0,
            }
        )
    return normalized


def analyzeRegion(gray, params, umPerPx, edgeToEdge, offset=(0, 0)):
    """Run the full pipeline over one already-cropped array.

    Particle coordinates come back in *whole image* pixels (``offset`` is added
    back on) so the UI can overlay every region on one preview, while spacing is
    measured strictly within the region — the same containment the original had
    when its regions were separate files.
    """
    tophatNorm, tophatSmooth = _preprocess(
        gray, params["tophatRadius"], params["smoothSigma"]
    )
    candidates = _detectBlobs(tophatSmooth, params)
    validated = _validate(gray, tophatNorm, candidates, params)

    result = {"candidates": int(len(candidates)), "n": int(len(validated))}
    if len(validated) == 0:
        result["particles"] = {
            key: []
            for key in (
                "x",
                "y",
                "diameterPx",
                "diameterNm",
                "spacingPx",
                "spacingNm",
                "nnIndex",
            )
        }
        result["diameter"] = {"n": 0}
        result["spacing"] = {"n": 0}
        return result

    xs, ys, diameters = validated[:, 0], validated[:, 1], validated[:, 2]
    nmPerPx = umPerPx * 1000.0

    if len(validated) > 1:
        spacing, nnIndex = _spacing(np.column_stack([xs, ys]), diameters, edgeToEdge)
    else:
        # One particle has no neighbour: report the diameter but no spacing,
        # rather than failing the whole run.
        spacing, nnIndex = np.array([]), np.array([], dtype=int)

    result["particles"] = {
        "x": [round(float(v + offset[0]), 3) for v in xs],
        "y": [round(float(v + offset[1]), 3) for v in ys],
        "diameterPx": [round(float(v), 4) for v in diameters],
        "diameterNm": [round(float(v * nmPerPx), 4) for v in diameters],
        "spacingPx": [round(float(v), 4) for v in spacing],
        "spacingNm": [round(float(v * nmPerPx), 4) for v in spacing],
        "nnIndex": [int(v) for v in nnIndex],
    }
    result["diameter"] = _describe(diameters, umPerPx)
    result["spacing"] = _describe(spacing, umPerPx)
    result["spacing"]["edgeToEdge"] = bool(edgeToEdge)
    return result


def analyze(
    path,
    scaleBarMicrons,
    scaleBarPixels,
    edgeToEdge=False,
    regions=None,
    preset=None,
    overrides=None,
    progress=None,
):
    """Analyse one micrograph and return a JSON-serializable result document.

    :param path: Local path to the image file.
    :param scaleBarMicrons: Length of the scale bar in micrometres.
    :param scaleBarPixels: Length of that same bar in pixels.
    :param edgeToEdge: Measure edge-to-edge spacing instead of centre-to-centre.
    :param regions: ROI dicts (``label``/``x``/``y``/``width``/``height``). Empty
        or ``None`` analyses the whole image.
    :param preset: Detection preset name, see :py:data:`PRESETS`.
    :param overrides: Individual detection parameters to override.
    :param progress: Optional ``callable(fraction, message)`` for job progress.
    """
    try:
        scaleBarMicrons = float(scaleBarMicrons)
        scaleBarPixels = float(scaleBarPixels)
    except (TypeError, ValueError) as exc:
        raise AnalysisError(
            "The scale bar length and pixel count must be numbers."
        ) from exc
    if scaleBarMicrons <= 0 or scaleBarPixels <= 0:
        raise AnalysisError("The scale bar length and pixel count must be positive.")

    params = presetParams(preset, overrides)
    umPerPx = scaleBarMicrons / scaleBarPixels

    def report(fraction, message):
        if progress:
            progress(fraction, message)

    report(0.05, "Reading image")
    gray = loadImage(path)
    height, width = gray.shape
    regionSpecs = _normalizeRegions(regions, width, height)

    results = []
    for index, spec in enumerate(regionSpecs):
        report(
            0.1 + 0.85 * index / len(regionSpecs),
            f"Detecting precipitates in {spec['label']} "
            f"({index + 1}/{len(regionSpecs)})",
        )
        crop = gray[
            spec["y"] : spec["y"] + spec["height"],
            spec["x"] : spec["x"] + spec["width"],
        ]
        region = analyzeRegion(
            crop, params, umPerPx, edgeToEdge, offset=(spec["x"], spec["y"])
        )
        region["label"] = spec["label"]
        region["bbox"] = {k: spec[k] for k in ("x", "y", "width", "height")}
        results.append(region)

    total = sum(region["n"] for region in results)
    if total == 0:
        raise AnalysisError(
            "No precipitates were validated. Try the other detection preset, "
            "check the scale, or select a region with visible precipitates."
        )

    report(0.97, "Pooling statistics")
    pooledDiameters = np.concatenate(
        [np.asarray(r["particles"]["diameterPx"]) for r in results]
    )
    pooledSpacings = np.concatenate(
        [np.asarray(r["particles"]["spacingPx"]) for r in results]
    )

    return {
        "version": 1,
        "image": {"width": int(width), "height": int(height)},
        "scale": {
            "barMicrons": scaleBarMicrons,
            "barPixels": scaleBarPixels,
            "umPerPx": umPerPx,
            "nmPerPx": umPerPx * 1000.0,
        },
        "spacingMode": "edge-to-edge" if edgeToEdge else "centre-to-centre",
        "params": params,
        "regions": results,
        "pooled": {
            "nRegions": len(results),
            "nTotal": int(total),
            "diameter": _describe(pooledDiameters, umPerPx),
            "spacing": _describe(pooledSpacings, umPerPx),
        },
    }
