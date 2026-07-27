from pathlib import Path

from girder.plugin import GirderPlugin, registerPluginStaticContent
from girder.utility.model_importer import ModelImporter

from .builtin import registerBuiltinDashboards
from .models.dashboard import Dashboard as DashboardModel
from .registry import (
    DashboardDefinition,
    addRegistrationListener,
    getDashboard,
    listDashboards,
    registerDashboard,
    unregisterDashboard,
)
from .rest.dashboard import Dashboard as DashboardResource

__all__ = [
    "DashboardDefinition",
    "DashboardsPlugin",
    "getDashboard",
    "listDashboards",
    "registerDashboard",
    "unregisterDashboard",
]


class DashboardsPlugin(GirderPlugin):
    DISPLAY_NAME = "Dashboards"

    def load(self, info):
        ModelImporter.registerModel("dashboard", DashboardModel, plugin="dashboards")

        registerBuiltinDashboards()

        # Plugins that depend on this one register their dashboards from their
        # own load(), which runs after ours. Listening keeps those provisioned
        # immediately instead of only after the next restart, while
        # provisionAll() covers anything registered before we loaded.
        model = DashboardModel()
        addRegistrationListener(model.provision)
        model.provisionAll()

        info["apiRoot"].dashboard = DashboardResource()

        registerPluginStaticContent(
            plugin="dashboards",
            css=["/style.css"],
            js=["/girder-plugin-dashboards.umd.cjs"],
            staticDir=Path(__file__).parent / "web_client" / "dist",
            tree=info["serverRoot"],
        )
