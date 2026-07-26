"""The Precipitate Analysis dashboard: declaration and capability probe.

Implements, as a dashboard, the pipeline from
*Image-analysis-precipitate-detection-and-particle-spacing-estimation*: upload an
SEM/TEM micrograph, give it a scale, pick regions of interest, and get precipitate
size and inter-particle spacing statistics back as numbers to plot.

The computation itself is a Celery task (:py:mod:`girder_dashboards.worker_plugin`
``.precipitate``); this package holds the declaration, the storage layout
(:py:mod:`.store`), the pure algorithm (:py:mod:`.analysis`), and the scheduling
that makes both execution paths look the same to the client (:py:mod:`.jobs`).
"""

import base64
import importlib.util

KEY = "precipitate-analysis"

#: The scientific stack the *analysis* needs. It is an extra rather than a hard
#: requirement of this plugin (``pip install girder-dashboards[precipitate]``),
#: because a Girder that only wants the other dashboards should not have to carry
#: scikit-image. The capability endpoint reports what is missing so the dashboard
#: can say so plainly instead of failing a run with an ImportError.
#: Import name -> distribution name. Must stay in step with
#: ``extras_require["precipitate"]`` in setup.py: a name missing from here means
#: the capability endpoint reports "ok" and the run then dies on an ImportError,
#: which is exactly what this probe exists to prevent. ``imagecodecs`` is what
#: tifffile delegates LZW and deflate to — i.e. what real micrographs need — and
#: ``imageio`` decodes the non-TIFF formats the REST layer accepts.
REQUIREMENTS = {
    "numpy": "numpy",
    "scipy": "scipy",
    "skimage": "scikit-image",
    "tifffile": "tifffile",
    "imagecodecs": "imagecodecs",
    "imageio": "imageio",
    "PIL": "pillow",
}

DEFAULT_SETTINGS = {
    # The scale bar of the micrographs the pipeline was developed against; every
    # instrument is different, so this is only the pre-filled value in the form.
    "defaultScaleBarMicrons": 1.0,
    "defaultScaleBarPixels": 129,
    "defaultPreset": "fine",
    "defaultEdgeToEdge": False,
    # A guard on how much work one run can ask for, since each region is a full
    # detection pass.
    "maxRegions": 12,
}

_CARD_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180">
  <defs>
    <linearGradient id="pbg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#3a3358"/>
      <stop offset="100%" stop-color="#171528"/>
    </linearGradient>
  </defs>
  <rect width="320" height="180" fill="url(#pbg)"/>
  <g stroke="#8f86c9" stroke-width="1" opacity="0.55">
    <line x1="58" y1="46" x2="104" y2="72"/>
    <line x1="104" y1="72" x2="86" y2="120"/>
    <line x1="104" y1="72" x2="160" y2="58"/>
    <line x1="160" y1="58" x2="214" y2="88"/>
    <line x1="214" y1="88" x2="188" y2="134"/>
    <line x1="214" y1="88" x2="262" y2="60"/>
    <line x1="86" y1="120" x2="140" y2="140"/>
  </g>
  <g fill="#f2e9c8">
    <circle cx="58" cy="46" r="7"/>
    <circle cx="104" cy="72" r="9"/>
    <circle cx="86" cy="120" r="6"/>
    <circle cx="160" cy="58" r="8"/>
    <circle cx="214" cy="88" r="10"/>
    <circle cx="188" cy="134" r="7"/>
    <circle cx="262" cy="60" r="6"/>
    <circle cx="140" cy="140" r="5"/>
  </g>
  <g fill="#ffffff" opacity="0.85">
    <rect x="28" y="158" width="60" height="3" rx="1.5"/>
    <rect x="28" y="152" width="2" height="15" rx="1"/>
    <rect x="86" y="152" width="2" height="15" rx="1"/>
  </g>
  <text x="96" y="167" fill="#ffffff" opacity="0.85"
        font-family="sans-serif" font-size="10">1 µm</text>
</svg>"""


def cardImage():
    encoded = base64.b64encode(_CARD_SVG.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def missingRequirements():
    """Return the pip names of the analysis dependencies that are not installed.

    Uses ``find_spec`` rather than importing: this runs on every capability
    request in the Girder process, which has no other reason to pull scikit-image
    into memory.
    """
    missing = []
    for module, distribution in REQUIREMENTS.items():
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(distribution)
    return missing


def registerPrecipitateDashboard():
    """Declare the dashboard so it appears on the config page and in the gallery."""
    from ..registry import registerDashboard

    return registerDashboard(
        KEY,
        name="Precipitate Analysis",
        description=(
            "Detect precipitates in an SEM/TEM micrograph and measure their "
            "equivalent diameter and nearest-neighbour spacing. Upload a TIFF, "
            "set the scale bar, pick regions of interest, and the numbers come "
            "back as interactive plots and tables."
        ),
        # The authors of the research code this dashboard is a port of.
        authors=["Hasan Al Jame", "Mohadeseh Taheri-Mousavi"],
        image=cardImage(),
        icon="icon-chart-bar",
        settings=dict(DEFAULT_SETTINGS),
    )
