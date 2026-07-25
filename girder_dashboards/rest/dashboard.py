from girder.api import access
from girder.api.describe import Description, autoDescribeRoute
from girder.api.rest import Resource
from girder.constants import AccessType, SortDir, TokenScope
from girder.exceptions import RestException

from ..models.dashboard import Dashboard as DashboardModel


class Dashboard(Resource):
    """REST interface to the dashboards available in this instance."""

    def __init__(self):
        super().__init__()
        self.resourceName = "dashboard"
        self.route("GET", (), self.listDashboards)
        self.route("GET", (":id",), self.getDashboard)
        self.route("PUT", (":id",), self.updateDashboard)
        self.route("PUT", (":id", "reset"), self.resetDashboard)
        self.route("DELETE", (":id",), self.deleteDashboard)
        self.route("GET", (":id", "access"), self.getDashboardAccess)
        self.route("PUT", (":id", "access"), self.updateDashboardAccess)

    def _serialize(self, doc, user):
        """Filter ``doc`` for ``user`` and annotate whether it's implemented.

        ``available`` is False when the document's key has no registered
        implementation any more, e.g. because the plugin that shipped the
        dashboard was uninstalled. Such dashboards are hidden from the gallery
        but still shown on the config page so an admin can prune them.
        """
        model = DashboardModel()
        result = model.filter(doc, user)
        result["available"] = model.isAvailable(doc)
        return result

    @access.public(scope=TokenScope.DATA_READ)
    @autoDescribeRoute(
        Description("List the dashboards visible to the current user.")
        .param(
            "includeDisabled",
            "Also return dashboards that have been disabled. Requires site "
            "admin privileges.",
            dataType="boolean",
            required=False,
            default=False,
        )
        .param(
            "includeUnavailable",
            "Also return dashboards whose implementation is no longer "
            "installed. Requires site admin privileges.",
            dataType="boolean",
            required=False,
            default=False,
        )
        .pagingParams(defaultSort="name", defaultSortDir=SortDir.ASCENDING)
        .errorResponse()
    )
    def listDashboards(self, includeDisabled, includeUnavailable, limit, offset, sort):
        user = self.getCurrentUser()
        if (includeDisabled or includeUnavailable) and not (user and user["admin"]):
            raise RestException(
                "Only site administrators may list disabled or unavailable dashboards.",
                code=403,
            )

        cursor = DashboardModel().listForUser(
            user=user,
            level=AccessType.READ,
            includeDisabled=includeDisabled,
            includeUnavailable=includeUnavailable,
            offset=offset,
            limit=limit,
            sort=sort,
        )
        return [self._serialize(doc, user) for doc in cursor]

    @access.public(scope=TokenScope.DATA_READ)
    @autoDescribeRoute(
        Description("Get a single dashboard by ID.")
        .modelParam(
            "id",
            "The ID of the dashboard.",
            model=DashboardModel,
            level=AccessType.READ,
        )
        .errorResponse("ID was invalid.")
        .errorResponse("Read access was denied on the dashboard.", 403)
    )
    def getDashboard(self, dashboard):
        return self._serialize(dashboard, self.getCurrentUser())

    @access.user(scope=TokenScope.DATA_WRITE)
    @autoDescribeRoute(
        Description("Update a dashboard's card metadata, settings or state.")
        .notes(
            "Only the fields that are passed are changed. Requires ADMIN access "
            "on the dashboard, which site administrators always have."
        )
        .modelParam(
            "id",
            "The ID of the dashboard.",
            model=DashboardModel,
            level=AccessType.ADMIN,
        )
        .param("name", "The name shown on the card.", required=False)
        .param("description", "The description shown on the card.", required=False)
        .param(
            "image",
            "URL or data URI of the card image. Pass an empty string to fall "
            "back to the icon.",
            required=False,
        )
        .param(
            "icon",
            "Fontello icon class used when there is no image.",
            required=False,
        )
        .param(
            "enabled",
            "Whether the dashboard is offered to users.",
            dataType="boolean",
            required=False,
        )
        .jsonParam(
            "settings",
            "JSON object of dashboard-specific settings.",
            requireObject=True,
            required=False,
        )
        .errorResponse("ID was invalid.")
        .errorResponse("Admin access was denied on the dashboard.", 403)
    )
    def updateDashboard(
        self, dashboard, name, description, image, icon, enabled, settings
    ):
        if name is not None:
            dashboard["name"] = name
        if description is not None:
            dashboard["description"] = description
        if image is not None:
            dashboard["image"] = image
        if icon is not None:
            dashboard["icon"] = icon
        if enabled is not None:
            dashboard["enabled"] = enabled
        if settings is not None:
            dashboard["settings"] = settings

        dashboard = DashboardModel().save(dashboard)
        return self._serialize(dashboard, self.getCurrentUser())

    @access.user(scope=TokenScope.DATA_WRITE)
    @autoDescribeRoute(
        Description("Reset a dashboard's metadata and settings to their defaults.")
        .notes(
            "Restores the values declared by the plugin that registered the "
            "dashboard. The enabled flag and the access control list are left "
            "untouched."
        )
        .modelParam(
            "id",
            "The ID of the dashboard.",
            model=DashboardModel,
            level=AccessType.ADMIN,
        )
        .errorResponse("ID was invalid.")
        .errorResponse("Admin access was denied on the dashboard.", 403)
    )
    def resetDashboard(self, dashboard):
        dashboard = DashboardModel().resetToDefaults(dashboard)
        return self._serialize(dashboard, self.getCurrentUser())

    @access.admin(scope=TokenScope.DATA_OWN)
    @autoDescribeRoute(
        Description("Delete a dashboard that is no longer installed.")
        .notes(
            "Only dashboards whose implementation is gone can be deleted; "
            "registered dashboards are re-created at startup, so disable them "
            "instead."
        )
        .modelParam(
            "id",
            "The ID of the dashboard.",
            model=DashboardModel,
            level=AccessType.ADMIN,
        )
        .errorResponse("ID was invalid.")
        .errorResponse("The dashboard is still installed.", 400)
        .errorResponse("You are not a site administrator.", 403)
    )
    def deleteDashboard(self, dashboard):
        model = DashboardModel()
        if model.isAvailable(dashboard):
            raise RestException(
                f"Dashboard '{dashboard['key']}' is still installed; disable it "
                "instead of deleting it.",
                code=400,
            )
        model.remove(dashboard)
        return {"message": f"Deleted dashboard '{dashboard['key']}'."}

    @access.user(scope=TokenScope.DATA_OWN)
    @autoDescribeRoute(
        Description("Get the access control list for a dashboard.")
        .modelParam(
            "id",
            "The ID of the dashboard.",
            model=DashboardModel,
            level=AccessType.ADMIN,
        )
        .errorResponse("ID was invalid.")
        .errorResponse("Admin access was denied on the dashboard.", 403)
    )
    def getDashboardAccess(self, dashboard):
        return DashboardModel().getFullAccessList(dashboard)

    @access.user(scope=TokenScope.DATA_OWN)
    @autoDescribeRoute(
        Description("Set the access control list for a dashboard.")
        .modelParam(
            "id",
            "The ID of the dashboard.",
            model=DashboardModel,
            level=AccessType.ADMIN,
        )
        .jsonParam(
            "access", "The JSON-encoded access control list.", requireObject=True
        )
        .jsonParam(
            "publicFlags",
            "JSON list of public access flags.",
            requireArray=True,
            required=False,
        )
        .param(
            "public",
            "Whether the dashboard should be visible to all users.",
            dataType="boolean",
            required=False,
        )
        .errorResponse("ID was invalid.")
        .errorResponse("Admin access was denied on the dashboard.", 403)
    )
    def updateDashboardAccess(self, dashboard, access, publicFlags, public):
        model = DashboardModel()
        user = self.getCurrentUser()
        if public is not None:
            model.setPublic(dashboard, public, save=False)
        if publicFlags is not None:
            model.setPublicFlags(dashboard, publicFlags, user=user, save=False)
        dashboard = model.setAccessList(dashboard, access, save=True, user=user)
        return self._serialize(dashboard, user)
