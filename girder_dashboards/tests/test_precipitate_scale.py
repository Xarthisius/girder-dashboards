"""Reading a micrograph's scale and finding its info panel.

The fixtures are the synthetic micrograph with and without an instrument info
panel (``test/browser/micrograph.py``), so every expected number here is one the
generator put there: the panel is ``PANEL_HEIGHT`` px tall and the drawn bar is
``BAR_PIXELS`` px from end post to end post, which is 1 µm at the ``PixelSizeX``
the header states.

Both routes to the scale are exercised separately, because they fail
independently in the wild: four of the six real micrographs this was developed
against had been through an image editor that dropped the vendor header but left
the panel — and its drawn bar — untouched.
"""

import numpy as np
import pytest

from girder_dashboards.precipitate.analysis import AnalysisError, loadImage
from girder_dashboards.precipitate.scale import (
    _niceBar,
    detectInfoPanel,
    inspectMicrograph,
    measureScaleBar,
    parseTescanHeader,
    readHeaderScale,
)


@pytest.fixture
def plain(micrograph):
    path, _ = micrograph
    return path, loadImage(path)


@pytest.fixture
def withPanel(micrographWithPanel):
    path, _ = micrographWithPanel
    return path, loadImage(path)


# --- the info panel ---------------------------------------------------------


def test_the_info_panel_is_found_in_the_pixels(withPanel, micrographModule):
    _, gray = withPanel
    panel = detectInfoPanel(gray)

    assert panel["height"] == micrographModule.PANEL_HEIGHT
    assert panel["top"] == gray.shape[0] - micrographModule.PANEL_HEIGHT
    assert panel["background"] == pytest.approx(
        micrographModule.PANEL_BACKGROUND / 255.0, abs=1e-5
    )


def test_a_micrograph_without_a_panel_has_none_invented(plain):
    _, gray = plain
    assert detectInfoPanel(gray) is None


def test_a_dark_band_at_the_bottom_is_not_a_panel(plain):
    """The guard that separates a pasted-on panel from the image being dark.

    A panel starts abruptly: the row above it looks nothing like it. A vignette,
    a saturated edge or a shadow fades in, and cropping one off would quietly
    throw away specimen.
    """
    _, gray = plain
    faded = gray.copy()
    height = faded.shape[0]
    for offset in range(60):
        faded[height - 60 + offset] *= 1.0 - offset / 60.0

    assert detectInfoPanel(faded) is None


def test_a_mostly_blank_page_is_not_a_panel():
    """An AFM-style page export: a figure floating in a white background.

    Its bottom margin is uniform for hundreds of rows, which is exactly what a
    panel looks like — the size cap is what tells them apart.
    """
    page = np.ones((1000, 800))
    page[300:600, 200:600] = np.linspace(0.2, 0.7, 400)

    assert detectInfoPanel(page) is None


# --- the drawn scale bar ----------------------------------------------------


def test_the_drawn_scale_bar_is_measured_post_to_post(withPanel, micrographModule):
    _, gray = withPanel
    panel = detectInfoPanel(gray)

    bar = measureScaleBar(gray, panel["top"])

    # Post centre to post centre, which is how the instrument spaced them; the
    # white run itself is wider by half a post at each end.
    assert bar["pixels"] == float(micrographModule.BAR_PIXELS)
    assert bar["method"] == "posts"
    assert bar["x"] == micrographModule.BAR_LEFT
    assert bar["width"] == micrographModule.BAR_PIXELS + micrographModule.BAR_POST_WIDTH
    assert bar["y"] > panel["top"]


def _withPanel(build):
    """A noisy 200-row image with a 100-row panel that ``build`` fills in."""
    image = np.random.default_rng(0).random((200, 300)) * 0.5
    build(image[100:])
    return image


def _flat(panel):
    panel[:] = 0.4


def _darkTextOnWhite(panel):
    # Some vendors print black text on a white strip. The brightest pixels are
    # then the *background*, and its widest run is the full panel width.
    panel[:] = 1.0
    panel[20:28, 30:120] = 0.0


def _uniformlyBlack(panel):
    panel[:] = 0.0


@pytest.mark.parametrize(
    "build", [_flat, _darkTextOnWhite, _uniformlyBlack], ids=lambda f: f.__name__
)
def test_a_featureless_panel_is_not_a_scale_bar(build):
    """A panel with no bright marks on it has a brightest *value*, not marks.

    Thresholding at 90% of the maximum then selects the background, whose widest
    run is the whole panel width — which would be reported as a bar two or three
    times the length of a real one, and set the scale as far out.
    """
    assert measureScaleBar(_withPanel(build), 100) is None


def test_a_bar_on_a_light_panel_is_still_measured():
    """The guard above must not cost the ordinary case a measurement.

    A light panel is fine; what is not fine is a light panel with nothing on it.
    """

    def build(panel):
        panel[:] = 0.55
        panel[40:42, 50:181] = 1.0  # a 131 px bar...
        panel[32:42, 50:52] = 1.0  # ...with end posts 128 px apart
        panel[32:42, 178:180] = 1.0

    bar = measureScaleBar(_withPanel(build), 100)

    assert bar["pixels"] == 128.0
    assert bar["method"] == "posts"


def test_no_bar_is_reported_where_there_is_none(plain):
    _, gray = plain
    # Pointed at the bottom of a micrograph with no panel: the brightest runs
    # there are precipitates, which are nowhere near long enough to be a bar.
    assert measureScaleBar(gray, gray.shape[0] - 60) is None


# --- the vendor header ------------------------------------------------------


def test_the_tescan_header_is_read_from_the_private_tag(withPanel, micrographModule):
    path, _ = withPanel
    header = readHeaderScale(path)

    assert header["source"] == "tescan-header"
    assert header["umPerPx"] == pytest.approx(micrographModule.PIXEL_SIZE_M * 1e6)
    assert header["panelHeight"] == micrographModule.PANEL_HEIGHT
    assert "MI4131573" in header["label"]


def test_a_file_with_no_vendor_header_reports_nothing(plain):
    path, _ = plain
    assert readHeaderScale(path) is None


def test_the_header_text_is_read_past_the_embedded_thumbnail():
    # The real tag holds a JP2 thumbnail first. Parsing from byte zero lets its
    # binary parse as key=value lines, which is why the EOI marker is sought.
    blob = b"\x00\xff\x4f\x51=nonsense\r\n\xff\xd9" + b"PixelSizeX=1.5e-09\r\nHV=3e3"
    header = parseTescanHeader(blob)

    assert header == {"PixelSizeX": "1.5e-09", "HV": "3e3"}


def test_arbitrary_bytes_are_not_a_header():
    assert parseTescanHeader(b"\x01\x02\x03\x04") == {}
    assert parseTescanHeader(None) == {}


def test_a_private_tag_that_is_not_tescans_is_ignored(tmp_path):
    """Tag 50431 is unregistered, so another vendor may well be using it.

    Two of the header's own keys have to parse out before it is believed.
    """
    import tifffile

    path = tmp_path / "other-vendor.tif"
    tifffile.imwrite(
        path,
        np.zeros((64, 64), dtype=np.uint8),
        extratags=[(50431, 7, 24, b"PixelSizeX=1.0e-09\r\n", True)],
    )

    assert readHeaderScale(path) is None


# --- the two together -------------------------------------------------------


def test_a_header_micrograph_yields_a_complete_scale(withPanel, micrographModule):
    path, gray = withPanel
    found = inspectMicrograph(path, gray)

    scale = found["scale"]
    assert scale["complete"] is True
    assert scale["source"] == "tescan-header"
    # The suggested bar is the one drawn on the image, at the header's exact
    # scale — so the user can read "1 µm" off the panel and see it in the form.
    assert scale["barMicrons"] == 1.0
    assert scale["barPixels"] == pytest.approx(micrographModule.BAR_PIXELS)
    assert scale["barMicrons"] / scale["barPixels"] == pytest.approx(scale["umPerPx"])
    assert "1.000 µm" in scale["detail"]

    # The header states the panel height, and the pixels agree with it.
    assert found["panel"]["source"] == "tescan-header"
    assert found["panel"]["height"] == micrographModule.PANEL_HEIGHT
    assert found["panel"]["agrees"] is True


def test_without_a_header_the_bar_gives_pixels_but_not_micrometres(
    tmp_path, micrographWithPanel, micrographModule
):
    """The case an image editor leaves behind, and the honest half-answer to it.

    The bar's length in pixels is measurable; the length printed beside it is
    text, and reading text is not something this does. So the pixel count is
    filled in and the user is asked for the one thing only they can supply —
    rather than a plausible micrometre value being invented to go with it.
    """
    path, _ = micrographWithPanel
    stripped = tmp_path / "no-header.tif"
    # Re-encoding through tifffile keeps the pixels and drops the private tags,
    # which is exactly what the round trip through Photoshop did.
    import tifffile

    tifffile.imwrite(stripped, tifffile.imread(path))

    found = inspectMicrograph(stripped, loadImage(stripped))

    assert found["panel"]["height"] == micrographModule.PANEL_HEIGHT
    assert found["panel"]["source"] == "pixels"
    assert found["scale"]["complete"] is False
    assert found["scale"]["umPerPx"] is None
    assert found["scale"]["barMicrons"] is None
    assert found["scale"]["barPixels"] == float(micrographModule.BAR_PIXELS)
    assert "printed beside it" in found["scale"]["detail"]


def test_a_plain_micrograph_yields_neither(plain):
    path, gray = plain
    assert inspectMicrograph(path, gray) == {"panel": None, "scale": None}


# --- turning a pixel size into a scale bar ----------------------------------


def test_a_pixel_size_becomes_a_bar_a_person_would_recognise():
    # 7.7221 nm/px, the real MIRA3 sample: 1 µm is the only 1/2/5 value that puts
    # the bar in the range a drawn one occupies.
    microns, pixels = _niceBar(0.0077221)
    assert (microns, pixels) == (1.0, 129.498)

    # 134.896 nm/px, the real Apreo sample. A 1 µm bar would be 7 px.
    microns, pixels = _niceBar(0.134896)
    assert microns == 20.0
    assert 80 <= pixels <= 800


def test_a_measured_bar_decides_the_length_where_there_is_one():
    """The form should describe the bar the user can see, not another one.

    The Apreo prints a 50 µm bar; left to the range rule alone the form would
    have offered 20 µm, which is correct but is not what the image says.
    """
    microns, pixels = _niceBar(0.134896, {"pixels": 369.0})

    assert microns == 50.0
    # ...and at the header's exact scale, not the instrument's whole-pixel
    # rendering of it.
    assert pixels == pytest.approx(50.0 / 0.134896, abs=0.001)


def test_a_bar_that_is_no_round_length_is_not_forced_into_one():
    # 2.4x the nearest 1/2/5 value: this is not a mis-rendered 100 µm bar, so
    # the range rule takes over rather than the number being bent to fit.
    microns, _ = _niceBar(0.1, {"pixels": 2400.0})
    assert microns != 240.0


# --- cropping the panel off before anything else ----------------------------


def test_loading_an_image_can_drop_the_panel(withPanel, micrographModule):
    path, full = withPanel
    cropped = loadImage(path, excludeBottomPx=micrographModule.PANEL_HEIGHT)

    assert cropped.shape == (full.shape[0] - micrographModule.PANEL_HEIGHT, 512)
    # The crop comes off the bottom, so the origin — and therefore every region
    # coordinate the user drew on the full image — still means what it did.
    assert np.array_equal(cropped[0], full[0])


def test_the_panel_comes_off_before_the_grey_stretch(tmp_path):
    """The reason the crop is inside ``loadImage`` and not applied afterwards.

    A 16-bit micrograph is stretched to 0-255 by its own extremes. The panel's
    white scale bar and black text *are* those extremes, so leaving them in
    compresses the specimen into the middle of the range and every absolute
    brightness gate in the detector reads something dimmer than the same
    specimen cropped by hand — which is how the published analysis was run.
    """
    import tifffile

    image = np.zeros((128, 64), dtype=np.uint16)
    # Specimen: a narrow band of 16-bit values, nowhere near the extremes.
    image[:96] = np.linspace(20000, 30000, 96, dtype=np.uint16)[:, None]
    image[96:] = 40000  # panel background
    image[100:104, 8:56] = 65535  # its scale bar
    tifffile.imwrite(tmp_path / "16bit.tif", image)

    withPanelIncluded = loadImage(tmp_path / "16bit.tif")[:96]
    withPanelDropped = loadImage(tmp_path / "16bit.tif", excludeBottomPx=32)

    # Same pixels, different numbers: dropped, the specimen uses the full range.
    assert withPanelDropped.min() == 0.0
    assert withPanelDropped.max() == 1.0
    assert withPanelIncluded.max() < 0.25
    assert not np.array_equal(withPanelIncluded, withPanelDropped)


def test_an_impossible_crop_is_refused(withPanel):
    path, gray = withPanel
    with pytest.raises(AnalysisError, match="Cannot exclude"):
        loadImage(path, excludeBottomPx=gray.shape[0])
