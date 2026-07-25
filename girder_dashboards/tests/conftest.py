import pytest

from girder_dashboards import registry
from girder_dashboards.builtin import DATA_OVERVIEW_KEY

EXTRA_KEY = "test-extra"


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
