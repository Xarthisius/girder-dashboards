import importlib.util
import pathlib

import pytest

from girder_dashboards import registry
from girder_dashboards.builtin import DATA_OVERVIEW_KEY
from girder_dashboards.precipitate import KEY as PRECIPITATE_KEY

EXTRA_KEY = "test-extra"

#: The synthetic-micrograph generator lives with the browser harness, whose seed
#: step must stay standard-library only. It is loaded by path rather than copied
#: so that both suites test the same image.
_MICROGRAPH_PY = (
    pathlib.Path(__file__).resolve().parents[2] / "test" / "browser" / "micrograph.py"
)


@pytest.fixture(autouse=True)
def cleanRegistry():
    """Undo registry mutations so tests can't leak dashboards into each other.

    The registry is module-level state that survives the ``db`` fixture's
    teardown, so restoring it explicitly is what keeps the "unavailable
    dashboard" tests from breaking every test that runs after them.
    """
    savedDashboards = dict(registry._dashboards)
    savedListeners = list(registry._listeners)
    yield registry
    registry._dashboards.clear()
    registry._dashboards.update(savedDashboards)
    registry._listeners[:] = savedListeners


@pytest.fixture
def dataOverview(server, db):
    """The document provisioned for the built-in Data Overview dashboard."""
    from girder_dashboards.models.dashboard import Dashboard as DashboardModel

    return DashboardModel().findOne({"key": DATA_OVERVIEW_KEY})


@pytest.fixture
def enabledDataOverview(dataOverview):
    from girder_dashboards.models.dashboard import Dashboard as DashboardModel

    dataOverview["enabled"] = True
    return DashboardModel().save(dataOverview)


@pytest.fixture(scope="session")
def micrographModule():
    spec = importlib.util.spec_from_file_location("_micrograph", _MICROGRAPH_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def micrograph(micrographModule, tmp_path_factory):
    """Path to a synthetic 8-bit TIFF with a known number of precipitates."""
    path = tmp_path_factory.mktemp("micrograph") / "synthetic.tif"
    written, count = micrographModule.write(str(path))
    return pathlib.Path(written), count


@pytest.fixture(scope="session")
def micrographWithPanel(micrographModule, tmp_path_factory):
    """The same micrograph, but as an instrument would have written it.

    Carries a TESCAN header stating the pixel size and an info panel with a scale
    bar drawn in it, so the detection in ``precipitate.scale`` has a file whose
    right answers are known by construction rather than by measurement.
    """
    path = tmp_path_factory.mktemp("micrograph") / "tescan.tif"
    written, count = micrographModule.write(str(path), panel=True)
    return pathlib.Path(written), count


@pytest.fixture
def precipitateDashboard(server, db):
    """The provisioned Precipitate Analysis document, enabled."""
    from girder_dashboards.models.dashboard import Dashboard as DashboardModel

    doc = DashboardModel().findOne({"key": PRECIPITATE_KEY})
    doc["enabled"] = True
    return DashboardModel().save(doc)
