"""The algorithm, on its own: no Girder, no Celery, no HTTP.

These exercise the port against a synthetic micrograph whose blob count is known.
Bit-level fidelity to the original research scripts is checked separately, by
``test/fidelity/compare_to_original.py``, which needs the research repository and
OpenCV and so cannot run here.
"""

import json

import numpy as np
import pytest

from girder_dashboards.precipitate import preview
from girder_dashboards.precipitate.analysis import (
    analyze,
    isTiff,
    loadImage,
    toGrayscale,
)
from girder_dashboards.precipitate.presets import (
    PRESETS,
    AnalysisError,
    presetParams,
)

SCALE_MICRONS = 1.0
SCALE_PIXELS = 129


# --- parameters -------------------------------------------------------------


def test_presets_cover_both_published_tunings():
    assert set(PRESETS) == {"fine", "coarse"}
    # The 5 hr script deliberately has no equivalent-diameter gate.
    assert PRESETS["coarse"]["minEquivDiameter"] is None
    assert PRESETS["fine"]["minEquivDiameter"] == 1.5


def test_preset_params_applies_overrides():
    params = presetParams("fine", {"threshold": 0.5})

    assert params["preset"] == "fine"
    assert params["threshold"] == 0.5
    assert params["tophatRadius"] == PRESETS["fine"]["tophatRadius"]
    assert "label" not in params


@pytest.mark.parametrize(
    "preset,overrides,message",
    [
        ("nonsense", None, "Unknown detection preset"),
        ("fine", {"nonsense": 1}, "Unknown detection parameter"),
        ("fine", {"threshold": "high"}, "must be a number"),
    ],
)
def test_preset_params_rejects_bad_input(preset, overrides, message):
    with pytest.raises(AnalysisError, match=message):
        presetParams(preset, overrides)


# --- image loading ----------------------------------------------------------


def test_grayscale_uses_cv2_luminance_weights_and_rounds():
    rgb = np.array([[[10, 200, 30]]], dtype=np.uint8)

    gray = toGrayscale(rgb)

    expected = round(0.299 * 10 + 0.587 * 200 + 0.114 * 30) / 255.0
    assert gray.shape == (1, 1)
    assert gray[0, 0] == pytest.approx(expected)


def test_grayscale_drops_alpha():
    rgba = np.array([[[10, 200, 30, 7]]], dtype=np.uint8)

    assert toGrayscale(rgba) == pytest.approx(toGrayscale(rgba[:, :, :3]))


def test_grayscale_stretches_only_non_8bit_input():
    # 8-bit input keeps its absolute brightness, which the detection thresholds
    # depend on...
    eightBit = np.array([[100, 150]], dtype=np.uint8)
    assert toGrayscale(eightBit).tolist() == [[100 / 255.0, 150 / 255.0]]

    # ...while a 16-bit image is min/max stretched over the full range.
    sixteenBit = np.array([[1000, 5000]], dtype=np.uint16)
    assert toGrayscale(sixteenBit).tolist() == [[0.0, 1.0]]


def test_grayscale_handles_a_flat_image():
    assert toGrayscale(np.full((2, 2), 4000, dtype=np.uint16)).tolist() == [
        [0.0, 0.0],
        [0.0, 0.0],
    ]


def test_grayscale_rejects_odd_shapes():
    with pytest.raises(AnalysisError, match="2D or 3D"):
        toGrayscale(np.zeros((2, 2, 2, 2)))


def test_load_image_reports_unreadable_files(tmp_path):
    broken = tmp_path / "broken.tif"
    broken.write_bytes(b"not a tiff")

    with pytest.raises(AnalysisError, match="Could not read"):
        loadImage(broken)


def test_synthetic_micrograph_decodes(micrograph):
    path, _ = micrograph
    gray = loadImage(path)

    assert gray.shape == (512, 512)
    assert gray.dtype == np.float64
    assert 0.0 <= gray.min() < gray.max() <= 1.0


# --- analysis ---------------------------------------------------------------


@pytest.fixture(scope="module")
def wholeImage(request):
    """One whole-image run, shared by the assertions that only read it."""
    path, _ = request.getfixturevalue("micrograph")
    return analyze(path, SCALE_MICRONS, SCALE_PIXELS, preset="fine")


def test_analyze_finds_the_synthetic_precipitates(wholeImage, micrograph):
    _, drawn = micrograph
    pooled = wholeImage["pooled"]

    # Blobs near the edges are legitimately lost to the local-window checks, so
    # this is a band rather than an equality: what matters is that the detector
    # finds most of them and invents none.
    assert 0.75 * drawn <= pooled["nTotal"] <= drawn
    assert pooled["nRegions"] == 1
    assert wholeImage["regions"][0]["label"] == "Whole image"
    assert wholeImage["regions"][0]["bbox"] == {
        "x": 0,
        "y": 0,
        "width": 512,
        "height": 512,
    }


def test_analyze_reports_scale_and_units(wholeImage):
    scale = wholeImage["scale"]

    assert scale["umPerPx"] == pytest.approx(SCALE_MICRONS / SCALE_PIXELS)
    assert scale["nmPerPx"] == pytest.approx(scale["umPerPx"] * 1000)

    diameter = wholeImage["pooled"]["diameter"]
    assert diameter["mean"]["nm"] == pytest.approx(
        diameter["mean"]["px"] * scale["nmPerPx"]
    )
    assert diameter["mean"]["um"] == pytest.approx(
        diameter["mean"]["px"] * scale["umPerPx"]
    )


def test_analyze_particle_arrays_line_up(wholeImage):
    particles = wholeImage["regions"][0]["particles"]
    n = wholeImage["regions"][0]["n"]

    for key in (
        "x",
        "y",
        "diameterPx",
        "diameterNm",
        "spacingPx",
        "spacingNm",
        "nnIndex",
    ):
        assert len(particles[key]) == n, key

    # Nearest-neighbour indices have to be usable as indices into these arrays.
    assert all(0 <= index < n for index in particles["nnIndex"])
    # ...and nobody is their own nearest neighbour.
    assert all(index != position for position, index in enumerate(particles["nnIndex"]))


def test_analyze_statistics_are_self_consistent(wholeImage):
    diameters = np.asarray(wholeImage["regions"][0]["particles"]["diameterPx"])
    stats = wholeImage["pooled"]["diameter"]

    assert stats["n"] == len(diameters)
    assert stats["mean"]["px"] == pytest.approx(diameters.mean(), abs=1e-3)
    assert stats["median"]["px"] == pytest.approx(np.median(diameters), abs=1e-3)
    assert stats["min"]["px"] == pytest.approx(diameters.min(), abs=1e-3)
    assert stats["max"]["px"] == pytest.approx(diameters.max(), abs=1e-3)
    assert stats["sem"]["px"] == pytest.approx(
        stats["std"]["px"] / np.sqrt(stats["n"]), rel=1e-6
    )
    assert stats["ci95"]["px"] == pytest.approx(1.96 * stats["sem"]["px"], rel=1e-6)
    assert stats["cv"] == pytest.approx(stats["std"]["px"] / stats["mean"]["px"])


def test_analyze_result_is_json_serializable(wholeImage):
    # allow_nan=False mirrors how the result is stored; a non-finite value would
    # be unreadable by JSON.parse in the browser.
    assert json.loads(json.dumps(wholeImage, allow_nan=False))["version"] == 1


def test_regions_are_analysed_separately_then_pooled(micrograph):
    path, _ = micrograph
    regions = [
        {"label": "ROI 1", "x": 10, "y": 10, "width": 240, "height": 240},
        {"label": "ROI 2", "x": 260, "y": 260, "width": 240, "height": 240},
    ]

    result = analyze(path, SCALE_MICRONS, SCALE_PIXELS, regions=regions, preset="fine")

    assert [region["label"] for region in result["regions"]] == ["ROI 1", "ROI 2"]
    assert result["pooled"]["nRegions"] == 2
    assert result["pooled"]["nTotal"] == sum(r["n"] for r in result["regions"])

    # Coordinates come back in whole-image pixels, so the UI can draw every
    # region's particles on one preview.
    for region, spec in zip(result["regions"], regions):
        for x, y in zip(region["particles"]["x"], region["particles"]["y"]):
            assert spec["x"] <= x <= spec["x"] + spec["width"]
            assert spec["y"] <= y <= spec["y"] + spec["height"]

    # Two disjoint regions must not see each other's particles.
    assert result["regions"][0]["n"] < result["pooled"]["nTotal"]


def test_pooled_statistics_span_every_region(micrograph):
    path, _ = micrograph
    regions = [
        {"x": 0, "y": 0, "width": 256, "height": 512},
        {"x": 256, "y": 0, "width": 256, "height": 512},
    ]

    result = analyze(path, SCALE_MICRONS, SCALE_PIXELS, regions=regions, preset="fine")

    everyDiameter = [
        value
        for region in result["regions"]
        for value in region["particles"]["diameterPx"]
    ]
    assert result["pooled"]["diameter"]["mean"]["px"] == pytest.approx(
        float(np.mean(everyDiameter)), abs=1e-3
    )
    assert result["pooled"]["diameter"]["min"]["px"] == pytest.approx(
        min(everyDiameter), abs=1e-3
    )


def test_edge_to_edge_spacing_is_shorter_and_never_negative(micrograph):
    path, _ = micrograph
    centre = analyze(path, SCALE_MICRONS, SCALE_PIXELS, edgeToEdge=False, preset="fine")
    edge = analyze(path, SCALE_MICRONS, SCALE_PIXELS, edgeToEdge=True, preset="fine")

    assert centre["spacingMode"] == "centre-to-centre"
    assert edge["spacingMode"] == "edge-to-edge"
    assert edge["regions"][0]["spacing"]["edgeToEdge"] is True

    edgeValues = edge["regions"][0]["particles"]["spacingPx"]
    centreValues = centre["regions"][0]["particles"]["spacingPx"]
    assert min(edgeValues) >= 0.0
    # Same detections, so the two runs are directly comparable particle by particle.
    assert all(e <= c + 1e-9 for e, c in zip(edgeValues, centreValues))
    assert (
        edge["pooled"]["spacing"]["mean"]["px"]
        < centre["pooled"]["spacing"]["mean"]["px"]
    )


@pytest.mark.parametrize(
    "microns,pixels",
    [(0, 129), (1.0, 0), (-1, 129), ("wide", 129)],
)
def test_analyze_rejects_a_bad_scale(micrograph, microns, pixels):
    path, _ = micrograph
    with pytest.raises(AnalysisError, match="scale bar"):
        analyze(path, microns, pixels, preset="fine")


@pytest.mark.parametrize(
    "region",
    [
        {"x": 900, "y": 900, "width": 100, "height": 100},  # entirely outside
        {"x": 0, "y": 0, "width": 4, "height": 400},  # too thin to detect in
        {"x": 0, "y": 0, "width": 100},  # missing height
    ],
)
def test_analyze_rejects_unusable_regions(micrograph, region):
    path, _ = micrograph
    with pytest.raises(AnalysisError):
        analyze(path, SCALE_MICRONS, SCALE_PIXELS, regions=[region], preset="fine")


def test_analyze_says_so_when_it_finds_nothing(micrograph):
    path, _ = micrograph

    # The coarse preset looks for large bright precipitates; this fixture has
    # only small ones, so it should come up empty rather than report noise.
    with pytest.raises(AnalysisError, match="No precipitates were validated"):
        analyze(path, SCALE_MICRONS, SCALE_PIXELS, preset="coarse")


# --- excluding the info panel -----------------------------------------------


def test_excluding_the_panel_keeps_it_out_of_the_results(
    micrographWithPanel, micrographModule
):
    """The panel's text and drawn scale bar are bright, compact and round.

    That is the description of a precipitate, so left in they are detected as
    several dozen of them, and they drag the pooled statistics with them.
    """
    path, _ = micrographWithPanel
    panelHeight = micrographModule.PANEL_HEIGHT

    included = analyze(path, SCALE_MICRONS, SCALE_PIXELS, preset="fine")
    excluded = analyze(
        path, SCALE_MICRONS, SCALE_PIXELS, preset="fine", excludeBottomPx=panelHeight
    )

    assert excluded["pooled"]["nTotal"] < included["pooled"]["nTotal"]
    # Nothing survives below the boundary, which is the actual claim.
    lowest = max(excluded["regions"][0]["particles"]["y"])
    assert lowest < excluded["image"]["contentHeight"]

    assert excluded["image"]["excludeBottomPx"] == panelHeight
    assert excluded["image"]["contentHeight"] == 512
    # The full height is still reported: it is the frame the coordinates above
    # are in, and the frame the browser overlays them on.
    assert excluded["image"]["height"] == 512 + panelHeight


def test_excluding_the_panel_matches_analysing_the_specimen_alone(
    micrograph, micrographWithPanel, micrographModule
):
    """The two fixtures share their specimen pixels exactly, by construction.

    So cropping the panel off has to reproduce the plain micrograph's numbers
    outright — not merely improve on leaving it in.
    """
    plainPath, _ = micrograph
    panelPath, _ = micrographWithPanel

    plain = analyze(plainPath, SCALE_MICRONS, SCALE_PIXELS, preset="fine")
    cropped = analyze(
        panelPath,
        SCALE_MICRONS,
        SCALE_PIXELS,
        preset="fine",
        excludeBottomPx=micrographModule.PANEL_HEIGHT,
    )

    assert cropped["pooled"]["nTotal"] == plain["pooled"]["nTotal"]
    assert cropped["pooled"]["diameter"]["mean"] == plain["pooled"]["diameter"]["mean"]
    assert cropped["pooled"]["spacing"]["mean"] == plain["pooled"]["spacing"]["mean"]


def test_a_region_reaching_into_the_panel_is_clipped_at_the_boundary(
    micrographWithPanel, micrographModule
):
    path, _ = micrographWithPanel
    height = 512 + micrographModule.PANEL_HEIGHT

    results = analyze(
        path,
        SCALE_MICRONS,
        SCALE_PIXELS,
        preset="fine",
        regions=[{"label": "ROI 1", "x": 0, "y": 300, "width": 512, "height": height}],
        excludeBottomPx=micrographModule.PANEL_HEIGHT,
    )

    assert results["regions"][0]["bbox"] == {
        "x": 0,
        "y": 300,
        "width": 512,
        "height": 212,
    }


def test_a_region_entirely_inside_the_panel_is_refused(
    micrographWithPanel, micrographModule
):
    path, _ = micrographWithPanel
    with pytest.raises(AnalysisError, match="512×512 px area being analysed"):
        analyze(
            path,
            SCALE_MICRONS,
            SCALE_PIXELS,
            preset="fine",
            regions=[{"x": 0, "y": 520, "width": 200, "height": 40}],
            excludeBottomPx=micrographModule.PANEL_HEIGHT,
        )


# --- preview ----------------------------------------------------------------


def test_preview_is_a_png_at_full_resolution(micrograph):
    path, _ = micrograph

    png, info = preview.renderPreview(path)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert info == {
        "width": 512,
        "height": 512,
        "previewWidth": 512,
        "previewHeight": 512,
    }


def test_preview_downscales_but_never_upscales(micrograph):
    path, _ = micrograph

    _, small = preview.renderPreview(path, maxEdge=128)
    assert (small["previewWidth"], small["previewHeight"]) == (128, 128)
    # The full-resolution dimensions are what regions are expressed in, so they
    # must be reported unchanged.
    assert (small["width"], small["height"]) == (512, 512)

    _, big = preview.renderPreview(path, maxEdge=4096)
    assert (big["previewWidth"], big["previewHeight"]) == (512, 512)


# --- defects found in review ------------------------------------------------


def test_tiff_is_recognised_by_signature_not_by_name(micrograph, tmp_path):
    """The Celery path gets a temp file named after an ObjectId, with no suffix.

    Dispatching on the extension sent every worker-path micrograph to the non-TIFF
    decoder, so one image could be decoded by a different library depending on
    where the analysis happened to run.
    """
    path, _ = micrograph
    unnamed = tmp_path / "6a662dd70a29fd3410c88629"
    unnamed.write_bytes(path.read_bytes())

    assert isTiff(unnamed) is True
    assert np.array_equal(loadImage(unnamed), loadImage(path))


def test_not_a_tiff_is_not_claimed_to_be_one(tmp_path):
    lying = tmp_path / "actually-a-png.tif"
    lying.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    assert isTiff(lying) is False


def test_overriding_one_equivalent_diameter_bound_is_allowed(micrograph):
    """The coarse preset leaves both bounds None. Overriding one left a None in a
    chained comparison, which raised TypeError partway through a run."""
    path, _ = micrograph

    # Nothing is found either way with this preset on this fixture; what matters is
    # that the failure is the analysis' own message, not a TypeError.
    with pytest.raises(AnalysisError, match="No precipitates were validated"):
        analyze(
            path,
            SCALE_MICRONS,
            SCALE_PIXELS,
            preset="coarse",
            overrides={"minEquivDiameter": 2.0},
        )


def test_null_is_rejected_for_parameters_used_in_arithmetic():
    with pytest.raises(AnalysisError, match="cannot be null"):
        presetParams("fine", {"tophatRadius": None})

    # ...but the two gates for which None means "no gate" still accept it.
    assert presetParams("fine", {"maxEquivDiameter": None})["maxEquivDiameter"] is None


def test_page_stacks_are_not_mistaken_for_colour_channels(tmp_path):
    """A 3-page greyscale stack is (3, H, W); guessing from the first axis turned
    it into a 3-row image of nonsense."""
    import tifffile

    stack = np.zeros((3, 64, 80), dtype=np.uint8)
    stack[0, 10, 10] = 255
    path = tmp_path / "stack.tif"
    tifffile.imwrite(path, stack)

    assert loadImage(path).shape == (64, 80)
    # Channels-last is still read as colour, not as three pages.
    assert toGrayscale(np.zeros((64, 80, 3), dtype=np.uint8)).shape == (64, 80)
