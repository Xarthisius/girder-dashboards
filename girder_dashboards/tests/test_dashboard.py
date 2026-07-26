import json

import pytest
from girder.constants import AccessType
from girder.models.user import User
from pytest_girder.assertions import assertStatus, assertStatusOk

from girder_dashboards.builtin import DATA_OVERVIEW_KEY
from girder_dashboards.models.dashboard import Dashboard as DashboardModel
from girder_dashboards.precipitate import KEY as PRECIPITATE_KEY
from girder_dashboards.registry import registerDashboard, unregisterDashboard

pytestmark = pytest.mark.plugin("dashboards")


@pytest.fixture
def otherUser(db):
    return User().createUser(
        login="other",
        password="other-password",
        email="other@example.com",
        firstName="Other",
        lastName="User",
    )


def _get(server, user=None, **params):
    resp = server.request(path="/dashboard", method="GET", user=user, params=params)
    assertStatusOk(resp)
    return resp.json


def _byKey(dashboards):
    """Index a listing by key.

    More than one dashboard ships with this plugin, so assertions about a
    particular one must not depend on how many others exist.
    """
    return {dashboard["key"]: dashboard for dashboard in dashboards}


# --- provisioning -----------------------------------------------------------


def test_builtin_dashboard_is_provisioned(server, db):
    doc = DashboardModel().findOne({"key": DATA_OVERVIEW_KEY})

    assert doc is not None
    assert doc["name"] == "Data Overview"
    assert doc["image"].startswith("data:image/svg+xml;base64,")
    assert doc["settings"] == {"collectionLimit": 10}
    # Disabled until an admin opts in, but readable by everyone once enabled.
    assert doc["enabled"] is False
    assert doc["public"] is True


def test_provision_preserves_admin_edits(server, dataOverview):
    model = DashboardModel()
    dataOverview["name"] = "Renamed by admin"
    dataOverview["enabled"] = True
    model.save(dataOverview)

    model.provisionAll()

    doc = model.findOne({"key": DATA_OVERVIEW_KEY})
    assert doc["name"] == "Renamed by admin"
    assert doc["enabled"] is True
    assert model.collection.count_documents({"key": DATA_OVERVIEW_KEY}) == 1


def test_late_registration_is_provisioned(server, db):
    registerDashboard("late-arrival", name="Late Arrival")

    doc = DashboardModel().findOne({"key": "late-arrival"})
    assert doc is not None
    assert doc["enabled"] is False


@pytest.mark.parametrize(
    "key", ["", "Has Caps", "has space", "-leading-dash", "sla/sh"]
)
def test_registry_rejects_bad_keys(key):
    with pytest.raises(ValueError):
        registerDashboard(key, name="Nope")


# --- listing ----------------------------------------------------------------


def test_disabled_dashboards_are_not_listed(server, dataOverview, user):
    assert _get(server) == []
    assert _get(server, user=user) == []


def test_enabled_dashboard_is_listed(server, enabledDataOverview, user):
    for caller in (None, user):
        dashboards = _get(server, user=caller)
        assert len(dashboards) == 1
        assert dashboards[0]["key"] == DATA_OVERVIEW_KEY
        assert dashboards[0]["available"] is True


def test_include_disabled_is_admin_only(server, dataOverview, admin, user):
    resp = server.request(
        path="/dashboard", method="GET", user=user, params={"includeDisabled": True}
    )
    assertStatus(resp, 403)

    assert DATA_OVERVIEW_KEY not in _byKey(_get(server, user=admin))
    assert DATA_OVERVIEW_KEY in _byKey(_get(server, user=admin, includeDisabled=True))


def test_acl_restricts_listing(server, enabledDataOverview, user, otherUser):
    model = DashboardModel()
    model.setPublic(enabledDataOverview, False, save=False)
    model.setUserAccess(enabledDataOverview, user, level=AccessType.READ, save=True)

    assert _get(server) == []
    assert _get(server, user=otherUser) == []
    assert len(_get(server, user=user)) == 1


def test_unavailable_dashboard_is_hidden_from_users(
    server, enabledDataOverview, admin, user
):
    unregisterDashboard(DATA_OVERVIEW_KEY)

    assert _get(server, user=user) == []

    dashboards = _byKey(
        _get(server, user=admin, includeDisabled=True, includeUnavailable=True)
    )
    assert dashboards[DATA_OVERVIEW_KEY]["available"] is False
    # Unregistering one dashboard must not make the others look uninstalled.
    assert dashboards[PRECIPITATE_KEY]["available"] is True


# --- updating ---------------------------------------------------------------


def test_update_requires_admin_access(server, dataOverview, user):
    resp = server.request(
        path="/dashboard/%s" % dataOverview["_id"],
        method="PUT",
        user=user,
        params={"enabled": True},
    )
    assertStatus(resp, 403)


def test_admin_can_update_card_and_settings(server, dataOverview, admin):
    resp = server.request(
        path="/dashboard/%s" % dataOverview["_id"],
        method="PUT",
        user=admin,
        params={
            "name": "Overview",
            "description": "Tweaked",
            "image": "",
            "icon": "icon-chart-pie",
            "enabled": True,
            "settings": json.dumps({"collectionLimit": 3}),
        },
    )
    assertStatusOk(resp)

    assert resp.json["name"] == "Overview"
    assert resp.json["description"] == "Tweaked"
    # An empty image falls back to the icon rather than rendering a broken <img>.
    assert resp.json["image"] is None
    assert resp.json["icon"] == "icon-chart-pie"
    assert resp.json["enabled"] is True
    assert resp.json["settings"] == {"collectionLimit": 3}


def test_update_rejects_non_object_settings(server, dataOverview, admin):
    resp = server.request(
        path="/dashboard/%s" % dataOverview["_id"],
        method="PUT",
        user=admin,
        params={"settings": json.dumps([1, 2, 3])},
    )
    assertStatus(resp, 400)


def test_update_rejects_empty_name(server, dataOverview, admin):
    resp = server.request(
        path="/dashboard/%s" % dataOverview["_id"],
        method="PUT",
        user=admin,
        params={"name": "   "},
    )
    assertStatus(resp, 400)


def test_reset_restores_declared_defaults(server, dataOverview, admin):
    model = DashboardModel()
    dataOverview.update(
        {
            "name": "Mangled",
            "description": "Mangled",
            "image": None,
            "settings": {"collectionLimit": 999},
            "enabled": True,
        }
    )
    model.save(dataOverview)

    resp = server.request(
        path="/dashboard/%s/reset" % dataOverview["_id"], method="PUT", user=admin
    )
    assertStatusOk(resp)

    assert resp.json["name"] == "Data Overview"
    assert resp.json["settings"] == {"collectionLimit": 10}
    assert resp.json["image"].startswith("data:image/svg+xml;base64,")
    # Resetting the card must not silently take the dashboard away from users.
    assert resp.json["enabled"] is True


# --- access control endpoint ------------------------------------------------


def test_admin_can_restrict_access_through_the_api(
    server, enabledDataOverview, admin, user
):
    resp = server.request(
        path="/dashboard/%s/access" % enabledDataOverview["_id"],
        method="PUT",
        user=admin,
        params={
            "access": json.dumps(
                {
                    "users": [{"id": str(user["_id"]), "level": AccessType.READ}],
                    "groups": [],
                }
            ),
            "public": False,
        },
    )
    assertStatusOk(resp)

    assert len(_get(server, user=user)) == 1
    assert _get(server) == []

    resp = server.request(
        path="/dashboard/%s/access" % enabledDataOverview["_id"],
        method="GET",
        user=admin,
    )
    assertStatusOk(resp)
    assert [entry["id"] for entry in resp.json["users"]] == [str(user["_id"])]


# --- deletion ---------------------------------------------------------------


def test_installed_dashboard_cannot_be_deleted(server, dataOverview, admin):
    resp = server.request(
        path="/dashboard/%s" % dataOverview["_id"], method="DELETE", user=admin
    )
    assertStatus(resp, 400)
    assert DashboardModel().findOne({"key": DATA_OVERVIEW_KEY}) is not None


def test_leftover_dashboard_can_be_deleted(server, dataOverview, admin):
    unregisterDashboard(DATA_OVERVIEW_KEY)

    resp = server.request(
        path="/dashboard/%s" % dataOverview["_id"], method="DELETE", user=admin
    )
    assertStatusOk(resp)
    assert DashboardModel().findOne({"key": DATA_OVERVIEW_KEY}) is None


def test_delete_requires_site_admin(server, dataOverview, user):
    unregisterDashboard(DATA_OVERVIEW_KEY)

    resp = server.request(
        path="/dashboard/%s" % dataOverview["_id"], method="DELETE", user=user
    )
    assertStatus(resp, 403)
