"""The dashboards this plugin ships itself.

Kept separate from ``__init__`` so it reads as an example of what a third-party
plugin has to do: call :py:func:`~girder_dashboards.registry.registerDashboard`
from ``load()`` and register a view under the same key in its web client.

Card images are inline SVG data URIs rather than files under ``web_client/dist``
so that a dashboard needs no static-asset plumbing to look presentable. Admins
can point ``image`` at any URL from the config page.
"""

import base64

DATA_OVERVIEW_KEY = "data-overview"

_DATA_OVERVIEW_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#2b4a6f"/>
      <stop offset="100%" stop-color="#16283d"/>
    </linearGradient>
  </defs>
  <rect width="320" height="180" fill="url(#bg)"/>
  <g fill="#ffffff" opacity="0.14">
    <rect x="28" y="104" width="26" height="48" rx="3"/>
    <rect x="70" y="80" width="26" height="72" rx="3"/>
    <rect x="112" y="60" width="26" height="92" rx="3"/>
    <rect x="154" y="90" width="26" height="62" rx="3"/>
  </g>
  <polyline points="41,92 83,68 125,44 167,74 209,52 251,34"
            fill="none" stroke="#7fd1e8" stroke-width="3"
            stroke-linecap="round" stroke-linejoin="round"/>
  <g fill="#7fd1e8">
    <circle cx="41" cy="92" r="4"/><circle cx="83" cy="68" r="4"/>
    <circle cx="125" cy="44" r="4"/><circle cx="167" cy="74" r="4"/>
    <circle cx="209" cy="52" r="4"/><circle cx="251" cy="34" r="4"/>
  </g>
  <rect x="28" y="152" width="252" height="2" rx="1" fill="#ffffff" opacity="0.3"/>
</svg>"""


def _dataUri(svg: str) -> str:
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def registerBuiltinDashboards():
    """Register the dashboards bundled with this plugin."""
    from .precipitate import registerPrecipitateDashboard
    from .registry import registerDashboard

    registerDashboard(
        DATA_OVERVIEW_KEY,
        name="Data Overview",
        description=(
            "At-a-glance counts of the collections, users and groups in this "
            "instance, with a breakdown of the largest collections."
        ),
        authors=["JHU/NCSA Data Team"],
        image=_dataUri(_DATA_OVERVIEW_SVG),
        icon="icon-chart-bar",
        settings={"collectionLimit": 10},
    )

    # Declared in its own package, which owns the whole vertical slice: the
    # algorithm, the Celery task, the storage layout and the REST resource.
    registerPrecipitateDashboard()
