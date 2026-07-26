from pathlib import Path

from girder.plugin import GirderPlugin, getPlugin, registerPluginStaticContent
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
from .rest.precipitate import Precipitate as PrecipitateResource

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
        # The Precipitate Analysis dashboard schedules its computation as a job,
        # and its web client shows job progress, so the jobs plugin has to be
        # loaded before ours registers anything.
        getPlugin("jobs").load(info)

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
        info["apiRoot"].precipitate = PrecipitateResource()

        registerPluginStaticContent(
            plugin="dashboards",
            css=["/style.css"],
            js=["/girder-plugin-dashboards.umd.cjs"],
            staticDir=Path(__file__).parent / "web_client" / "dist",
            tree=info["serverRoot"],
        )
