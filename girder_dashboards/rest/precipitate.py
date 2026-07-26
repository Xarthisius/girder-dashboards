"""REST interface for the Precipitate Analysis dashboard.

A *run* is a folder (see :py:mod:`girder_dashboards.precipitate.store`), so this
resource is deliberately thin: it creates run folders, starts the two job steps,
and reports run state. The micrograph is uploaded and the outputs are downloaded
through Girder's own file endpoints — there is no reason to proxy bytes that core
already serves with the right ACL checks, and it means the browser's ``<img>`` can
point straight at the stored preview.
"""

from girder.api import access
from girder.api.describe import Description, autoDescribeRoute
from girder.api.rest import Resource, filtermodel
from girder.constants import AccessType, TokenScope
from girder.exceptions import RestException
from girder.models.file import File
from girder.models.folder import Folder
from girder.models.item import Item

from ..models.dashboard import Dashboard as DashboardModel
from ..precipitate import DEFAULT_SETTINGS, KEY, missingRequirements, store
from ..precipitate import jobs as precipitateJobs
from ..precipitate.presets import PRESETS

#: Extensions the dashboard offers to analyse. Anything Pillow or tifffile can
#: decode would work, but the pipeline was written for micrograph TIFFs and the
#: brightness gates assume that kind of image.
ALLOWED_EXTENSIONS = (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp")


class Precipitate(Resource):
    """Runs of the precipitate detection / inter-particle spacing pipeline."""

    def __init__(self):
        super().__init__()
        self.resourceName = "precipitate"
        self.route("GET", ("capability",), self.getCapability)
        self.route("GET", ("run",), self.listRuns)
        self.route("POST", ("run",), self.createRun)
        self.route("GET", ("run", ":id"), self.getRun)
        self.route("DELETE", ("run", ":id"), self.deleteRun)
        self.route("POST", ("run", ":id", "prepare"), self.prepareRun)
        self.route("POST", ("run", ":id", "analyze"), self.analyzeRun)

    # -- helpers ----------------------------------------------------------

    def _dashboard(self):
        """The dashboard document, if the current user may use it.

        Disabling the dashboard from the plugin config page has to disable the
        dashboard's *functionality*, not merely hide its card, or "enable/disable
        available dashboards" would only be cosmetic.
        """
        user = self.getCurrentUser()
        doc = DashboardModel().findOne({"key": KEY})
        if doc is None:
            raise RestException(
                "The Precipitate Analysis dashboard is not installed.", code=404
            )
        if not doc.get("enabled"):
            raise RestException(
                "The Precipitate Analysis dashboard is disabled on this instance.",
                code=403,
            )
        if not DashboardModel().hasAccess(doc, user=user, level=AccessType.READ):
            raise RestException(
                "You do not have access to the Precipitate Analysis dashboard.",
                code=403,
            )
        return doc

    def _settings(self, dashboard=None):
        """The dashboard's settings over this module's defaults.

        Takes an already-loaded dashboard, because ``_dashboard()`` is a database
        read plus an access check: a route that needs both the gate and the
        settings should not pay for it twice.
        """
        settings = dict(DEFAULT_SETTINGS)
        settings.update((dashboard or self._dashboard()).get("settings") or {})
        return settings

    def _requireRun(self, folder):
        """Refuse a folder that is not one of this dashboard's runs.

        The routes load the folder with ``modelParam``, which is what enforces the
        ACL; this is only the type check on top of it.
        """
        if not store.isRunFolder(folder):
            raise RestException(
                "That folder is not a precipitate analysis run.", code=400
            )
        return folder

    def _serialize(self, folder):
        state = store.runState(folder)
        return {
            "_id": str(folder["_id"]),
            "name": folder["name"],
            "created": folder.get("created"),
            "updated": folder.get("updated"),
            "state": state,
        }

    def _loadInputFile(self, folder, fileId):
        """Load an uploaded micrograph, insisting it lives in this run's folder.

        Without the containment check, a run could be pointed at any file the
        user can read, and its results would be stored next to an image they did
        not come from.
        """
        user = self.getCurrentUser()
        file = File().load(fileId, level=AccessType.READ, user=user, exc=True)
        item = Item().load(file["itemId"], level=AccessType.READ, user=user, exc=True)
        if item["folderId"] != folder["_id"]:
            raise RestException(
                "The image must be uploaded into the run's own folder.", code=400
            )
        if not file["name"].lower().endswith(ALLOWED_EXTENSIONS):
            raise RestException(
                "Unsupported image type: expected one of "
                f"{', '.join(ALLOWED_EXTENSIONS)}.",
                code=400,
            )
        return file

    def _requireDependencies(self):
        missing = missingRequirements()
        if missing:
            raise RestException(
                "This Girder is missing the analysis dependencies "
                f"({', '.join(missing)}). Install them with "
                "'pip install girder-dashboards[precipitate]' in both the Girder "
                "and the Celery worker environment.",
                code=503,
            )

    # -- routes -----------------------------------------------------------

    @access.user(scope=TokenScope.DATA_READ)
    @autoDescribeRoute(
        Description("Report what this instance can do, and the form defaults.")
        .notes(
            "Tells the dashboard whether the analysis dependencies are installed "
            "and whether a Celery worker is available, so it can warn up front "
            "rather than failing a run."
        )
        .errorResponse()
        .errorResponse("The dashboard is disabled or not visible to you.", 403)
    )
    def getCapability(self):
        missing = missingRequirements()
        worker = precipitateJobs.workerAvailable()
        return {
            "settings": self._settings(),
            "dependencies": {"ok": not missing, "missing": missing},
            "worker": worker,
            "compute": "celery" if worker else "local",
            "presets": [
                {"key": key, "label": preset["label"]}
                for key, preset in sorted(PRESETS.items())
            ],
            "workspace": store.WORKSPACE_NAME,
            "allowedExtensions": list(ALLOWED_EXTENSIONS),
        }

    @access.user(scope=TokenScope.DATA_READ)
    @autoDescribeRoute(
        Description("List the current user's analysis runs, newest first.")
        .param(
            "limit",
            "Maximum number of runs to return; 0 for all of them.",
            dataType="integer",
            required=False,
            default=25,
        )
        .errorResponse()
    )
    def listRuns(self, limit):
        self._dashboard()
        runs = store.listRuns(self.getCurrentUser(), limit=int(limit or 0))
        return [self._serialize(folder) for folder in runs]

    @access.user(scope=TokenScope.DATA_WRITE)
    @autoDescribeRoute(
        Description("Create a folder for a new analysis run.")
        .notes(
            "Creates the user's dedicated dashboard folder if this is their first "
            "run. Upload the micrograph into the returned folder with the normal "
            "file endpoints, then call prepare."
        )
        .param("name", "A name for the run. Defaults to a timestamp.", required=False)
        .errorResponse()
    )
    def createRun(self, name):
        self._dashboard()
        self._requireDependencies()
        folder = store.createRun(self.getCurrentUser(), name)
        return self._serialize(folder)

    @access.user(scope=TokenScope.DATA_READ)
    @autoDescribeRoute(
        Description("Get one analysis run.")
        .modelParam(
            "id",
            "The ID of the run folder.",
            model=Folder,
            level=AccessType.READ,
            destName="folder",
        )
        .errorResponse()
        .errorResponse("Read access was denied on the run.", 403)
    )
    def getRun(self, folder):
        self._dashboard()
        self._requireRun(folder)
        return self._serialize(folder)

    # DATA_OWN, not DATA_WRITE: this destroys a folder and its whole subtree, which
    # is what core's own DELETE /folder/{id} reserves for that scope. Anything less
    # would let a write-scoped API key delete data it could not delete through the
    # core endpoint.
    @access.user(scope=TokenScope.DATA_OWN)
    @autoDescribeRoute(
        Description("Delete an analysis run and everything in it.")
        .modelParam(
            "id",
            "The ID of the run folder.",
            model=Folder,
            level=AccessType.ADMIN,
            destName="folder",
        )
        .errorResponse()
        .errorResponse("Admin access was denied on the run.", 403)
    )
    def deleteRun(self, folder):
        self._dashboard()
        self._requireRun(folder)
        Folder().remove(folder)
        return {"message": f"Deleted run '{folder['name']}'."}

    @access.user(scope=TokenScope.DATA_WRITE)
    @filtermodel(model="job", plugin="jobs")
    @autoDescribeRoute(
        Description(
            "Decode an uploaded micrograph and render a preview to pick regions on."
        )
        .notes(
            "Schedules a job. Follow it as usual; when it succeeds the run's "
            "state carries previewFileId and the full-resolution dimensions."
        )
        .modelParam(
            "id",
            "The ID of the run folder.",
            model=Folder,
            level=AccessType.WRITE,
            destName="folder",
        )
        .param("fileId", "The ID of the uploaded image file.")
        .errorResponse()
        .errorResponse("Write access was denied on the run.", 403)
    )
    def prepareRun(self, folder, fileId):
        self._dashboard()
        self._requireDependencies()
        self._requireRun(folder)

        user = self.getCurrentUser()
        file = self._loadInputFile(folder, fileId)
        folder = Folder().setMetadata(
            folder,
            {
                store.STATE_KEY: dict(
                    store.runState(folder),
                    inputFileId=str(file["_id"]),
                    inputName=file["name"],
                )
            },
        )
        return precipitateJobs.schedule(
            precipitateJobs.STEP_PREPARE, user, folder, file
        )

    @access.user(scope=TokenScope.DATA_WRITE)
    @filtermodel(model="job", plugin="jobs")
    @autoDescribeRoute(
        Description("Run the precipitate detection and spacing analysis.")
        .notes(
            "Schedules a job. Follow it as usual; when it succeeds the run's "
            "state carries resultFileId, whose contents are the numbers the "
            "dashboard plots."
        )
        .modelParam(
            "id",
            "The ID of the run folder.",
            model=Folder,
            level=AccessType.WRITE,
            destName="folder",
        )
        .param(
            "scaleBarMicrons",
            "Length of the micrograph's scale bar, in micrometres.",
            dataType="number",
        )
        .param(
            "scaleBarPixels",
            "Length of that same scale bar, in pixels.",
            dataType="number",
        )
        .param(
            "edgeToEdge",
            "Measure edge-to-edge spacing instead of centre-to-centre.",
            dataType="boolean",
            required=False,
            default=False,
        )
        .param(
            "preset",
            "Detection preset: 'fine' for small dim precipitates, 'coarse' for "
            "large bright ones.",
            required=False,
            enum=sorted(PRESETS),
        )
        .jsonParam(
            "regions",
            "JSON array of regions of interest, each {label, x, y, width, "
            "height} in full-resolution pixels. Omit or pass [] to analyse the "
            "whole image.",
            requireArray=True,
            required=False,
        )
        .jsonParam(
            "overrides",
            "JSON object of individual detection parameters to override.",
            requireObject=True,
            required=False,
        )
        .errorResponse()
        .errorResponse("Write access was denied on the run.", 403)
    )
    def analyzeRun(
        self,
        folder,
        scaleBarMicrons,
        scaleBarPixels,
        edgeToEdge,
        preset,
        regions,
        overrides,
    ):
        settings = self._settings(self._dashboard())
        self._requireDependencies()
        self._requireRun(folder)

        user = self.getCurrentUser()
        state = store.runState(folder)
        if not state.get("inputFileId"):
            raise RestException(
                "This run has no image yet; upload one and prepare it first.", code=400
            )
        file = self._loadInputFile(folder, state["inputFileId"])

        if scaleBarMicrons <= 0 or scaleBarPixels <= 0:
            raise RestException(
                "The scale bar length and pixel count must both be positive.", code=400
            )

        regions = regions or []
        maxRegions = int(settings.get("maxRegions") or 0)
        if maxRegions and len(regions) > maxRegions:
            raise RestException(
                f"At most {maxRegions} regions of interest can be analysed in one "
                f"run; {len(regions)} were given.",
                code=400,
            )

        options = {
            "scaleBarMicrons": float(scaleBarMicrons),
            "scaleBarPixels": float(scaleBarPixels),
            "edgeToEdge": bool(edgeToEdge),
            "preset": preset or settings.get("defaultPreset"),
            "regions": regions,
            "overrides": overrides or {},
        }
        return precipitateJobs.schedule(
            precipitateJobs.STEP_ANALYZE, user, folder, file, options
        )
