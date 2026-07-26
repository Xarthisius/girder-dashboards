#!/usr/bin/env python3
"""Check the ported pipeline against the original research scripts, number by number.

``girder_dashboards.precipitate.analysis`` is a port of

    https://github.com/Taheri-Mousavi-Laboratory/\
Image-analysis-precipitate-detection-and-particle-spacing-estimation

and the point of the port is that it computes *the same numbers* as the published
scripts. This script proves that: it imports the original modules, runs them and
the port over the same micrographs, and compares every reported statistic.

Unlike the pytest suite, this cannot run in CI — it needs the research repository
(which does not ship its micrographs) and OpenCV (which the port deliberately does
not depend on). Run it by hand when touching the detection or statistics code.

    pip install opencv-python-headless pandas       # reference only
    git clone <the research repo> /tmp/precipitate-original
    python3 test/fidelity/compare_to_original.py /tmp/precipitate-original

Expected output: every statistic 'ok', "ALL MATCH", exit status 0. The two scripts
are compared with the presets that correspond to them: ``fine`` for the 1 hr
single-image script, ``coarse`` for the 5 hr multi-image one.
"""

import argparse
import importlib.util
import pathlib
import sys
import types

# What the originals print, mapped onto where the same number lives in our result.
STATS = [
    ("n", ("n",), 0),
    ("mean px", ("mean", "px"), 1e-9),
    ("std px", ("std", "px"), 1e-9),
    ("median px", ("median", "px"), 1e-9),
    ("min px", ("min", "px"), 1e-9),
    ("max px", ("max", "px"), 1e-9),
    ("mean nm", ("mean", "nm"), 1e-9),
    ("std nm", ("std", "nm"), 1e-9),
    ("median nm", ("median", "nm"), 1e-9),
]

# (script, preset, images, spacing mode) — the settings each script ships with.
CASES = [
    (
        "PrecipitateDetection_IPspacing_SingleImage.py",
        "fine",
        ["1_0HR_725C.tif"],
        False,
    ),
    (
        "PrecipitateDetection_IPspacing_MultiImage.py",
        "coarse",
        ["5_0HR_725C_10.tif", "5_0HR_725C_20.tif", "5_0HR_725C_30.tif"],
        True,
    ),
]


def stubMatplotlib():
    """The originals import pyplot at module scope and plot at the end.

    Only their numeric functions are called here, so pyplot is stubbed rather than
    installed — that keeps this script's dependencies down to the two the
    *comparison* genuinely needs.
    """
    matplotlib = types.ModuleType("matplotlib")
    pyplot = types.ModuleType("matplotlib.pyplot")
    for name in ("figure", "show", "subplots_adjust", "colorbar", "Circle"):
        setattr(pyplot, name, lambda *args, **kwargs: None)
    matplotlib.pyplot = pyplot
    sys.modules.setdefault("matplotlib", matplotlib)
    sys.modules.setdefault("matplotlib.pyplot", pyplot)


def loadOriginal(path):
    spec = importlib.util.spec_from_file_location(f"original_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def reference(module, imagePath, edgeToEdge):
    """Run the original pipeline and return its two stats dicts."""
    gray = module.load_image(str(imagePath))
    tophatNorm, tophatSmooth = module.preprocess(gray)
    candidates = module.detect_blobs(tophatSmooth)
    validated, _, _ = module.validate_precipitates(gray, tophatNorm, candidates)

    umPerPx = module.SCALE_BAR_MICRONS / module.SCALE_BAR_PIXELS
    diameter = module.diameter_stats(validated, umPerPx)
    _, _, spacing = module.spacing_stats(validated, umPerPx, edgeToEdge)
    return len(candidates), diameter, spacing


def dig(stats, keys):
    value = stats
    for key in keys:
        value = value[key]
    return value


def compare(label, reference_, ours):
    """Compare one stats block. The originals flatten what we nest by unit."""
    ok = True
    for name, keys, tolerance in STATS:
        left = reference_["_".join(keys)]
        right = dig(ours, keys)
        good = abs(left - right) <= tolerance * max(1.0, abs(left))
        ok = ok and good
        print(
            f"  {'ok  ' if good else 'DIFF'} {label} {name:<11}"
            f" original={left:<16.8f} ours={right:<16.8f}"
        )
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repo",
        type=pathlib.Path,
        help="checkout of the research repository (needs src/ and data/)",
    )
    args = parser.parse_args()

    source = args.repo / "src"
    data = args.repo / "data"
    if not source.is_dir() or not data.is_dir():
        raise SystemExit(f"{args.repo} does not look like the research repo")

    stubMatplotlib()
    try:
        import cv2  # noqa: F401  (the originals need it; we only check against it)
    except ImportError:
        raise SystemExit(
            "This comparison needs OpenCV as the reference decoder: "
            "pip install opencv-python-headless"
        ) from None

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from girder_dashboards.precipitate.analysis import analyze

    allOk = True
    for script, preset, images, edgeToEdge in CASES:
        module = loadOriginal(source / script)
        print(
            f"\n=== {script}  (preset '{preset}', "
            f"{'edge-to-edge' if edgeToEdge else 'centre-to-centre'}) ==="
        )

        for image in images:
            path = data / image
            if not path.exists():
                print(f"  SKIP {image}: not present (the repo does not ship its data)")
                continue

            candidates, refDiameter, refSpacing = reference(module, path, edgeToEdge)
            ours = analyze(
                path,
                module.SCALE_BAR_MICRONS,
                module.SCALE_BAR_PIXELS,
                edgeToEdge=edgeToEdge,
                preset=preset,
            )["regions"][0]

            print(f"\n  {image}")
            same = candidates == ours["candidates"]
            allOk = allOk and same
            print(
                f"  {'ok  ' if same else 'DIFF'} blob candidates"
                f"   original={candidates:<16} ours={ours['candidates']}"
            )
            allOk = compare("d", refDiameter, ours["diameter"]) and allOk
            allOk = compare("s", refSpacing, ours["spacing"]) and allOk

    print("\n" + ("ALL MATCH" if allOk else "MISMATCHES FOUND"))
    return 0 if allOk else 1


if __name__ == "__main__":
    sys.exit(main())
