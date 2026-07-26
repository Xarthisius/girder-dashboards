"""The dependency probe and the packaging metadata have to agree.

``missingRequirements()`` is what makes the dashboard say "this Girder cannot run
the analysis" instead of failing a job with an ImportError. It only works if its
list of modules matches the extra that installs them — so that correspondence is
asserted rather than maintained by hand.
"""

import pathlib
import re

from girder_dashboards.precipitate import REQUIREMENTS, missingRequirements

SETUP_PY = pathlib.Path(__file__).resolve().parents[2] / "setup.py"


def _extraRequirements():
    """The distribution names in extras_require["precipitate"], from setup.py.

    Parsed rather than imported: importing setup.py would run setup().
    """
    source = SETUP_PY.read_text()
    marker = '"precipitate": ['
    block = source[source.index(marker) + len(marker) :]
    block = block[: block.index("]")]
    # 'scikit-image>=0.21' -> 'scikit-image'
    return {
        re.split(r"[<>=!~]", name)[0].strip()
        for name in re.findall(r'"([^"]+)"', block)
    }


def test_probe_covers_every_packaged_dependency():
    assert set(REQUIREMENTS.values()) == _extraRequirements()


def test_probe_finds_the_installed_stack():
    # The suite cannot run at all without these, so anything missing here means
    # the probe is looking for the wrong import name.
    assert missingRequirements() == []
