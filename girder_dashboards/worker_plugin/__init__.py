"""girder_worker plugin: makes this package's Celery tasks discoverable.

Registered under the ``girder_worker_plugins`` entry point, which
``girder_worker.entrypoint`` loads at worker startup to build ``CELERY_INCLUDE``.

Deliberately thin. A failure while loading an entry point in this group takes the
*whole worker* down, not just this plugin's tasks, so nothing here imports numpy,
scikit-image, or Girder models — the task module keeps its heavy imports inside
the task bodies for the same reason.
"""

from girder_worker import GirderWorkerPluginABC


class DashboardsWorkerPlugin(GirderWorkerPluginABC):
    DISPLAY_NAME = "Dashboards"

    def __init__(self, app, *args, **kwargs):
        self.app = app

    def task_imports(self):
        return ["girder_dashboards.worker_plugin.precipitate"]
