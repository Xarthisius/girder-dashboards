"""Where a precipitate analysis run lives in Girder.

Everything a run consumes and produces is a Girder object under one dedicated
folder in the user's own space::

    <user>/Precipitate Analysis/          <- WORKSPACE_NAME, created on demand
        2026-07-26 10-42-13/              <- one folder per run
            1_0HR_725C.tif                <- the uploaded micrograph
            preview.png                   <- derived, for picking regions
            results.json                  <- every number the dashboard plots

The run folder's ``meta.precipitate`` is the run's state: which files are which,
the scale and regions it was asked for, how the job went, and a small summary so
the run list does not have to download every ``results.json``.

Two writers implement the same small interface, because the analysis runs in two
places. :py:class:`ModelStore` is used in-process (Girder's own thread) and talks
to the models directly. :py:class:`RestStore` is used inside the Celery worker,
which may be a different container with no database or assetstore access, and
talks HTTP through the authenticated ``girder_client`` that girder_worker hands
the task. Keeping the interface this narrow is what lets
:py:mod:`~girder_dashboards.precipitate.runner` be written once.
"""

import datetime
import io
import json

STATE_KEY = "precipitate"

WORKSPACE_NAME = "Precipitate Analysis"
WORKSPACE_DESCRIPTION = (
    "Micrographs and precipitate/inter-particle-spacing results created by the "
    "Precipitate Analysis dashboard."
)

PREVIEW_NAME = "preview.png"
RESULTS_NAME = "results.json"

#: Run lifecycle, mirrored in the web client.
STATUS_NEW = "new"
STATUS_PREPARING = "preparing"
STATUS_READY = "ready"
STATUS_ANALYZING = "analyzing"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"


def _now():
    return datetime.datetime.now(datetime.UTC)


def encodeJson(payload):
    """Serialize a result document to bytes, rejecting NaN/Infinity.

    ``allow_nan=False`` is deliberate: ``JSON.parse`` cannot read ``NaN``, so a
    stray non-finite value has to fail here — where the message names the run —
    rather than in the browser as an unexplained parse error.
    """
    return json.dumps(payload, allow_nan=False, sort_keys=True).encode("utf-8")


class ModelStore:
    """Read and write a run's files and state through the Girder models."""

    def __init__(self, folder, user):
        self.folder = folder
        self.user = user

    @property
    def folderId(self):
        return str(self.folder["_id"])

    def writeBytes(self, name, data, mimeType):
        from girder.models.item import Item
        from girder.models.upload import Upload

        # Re-running a step must replace its output, not stack up copies of it.
        existing = Item().findOne({"folderId": self.folder["_id"], "name": name})
        if existing is not None:
            Item().remove(existing)

        file = Upload().uploadFromFile(
            io.BytesIO(data),
            size=len(data),
            name=name,
            parentType="folder",
            parent=self.folder,
            user=self.user,
            mimeType=mimeType,
        )
        return str(file["_id"])

    def writeJson(self, name, payload):
        return self.writeBytes(name, encodeJson(payload), "application/json")

    def getState(self):
        from girder.models.folder import Folder

        self.folder = Folder().load(self.folder["_id"], force=True)
        return dict((self.folder.get("meta") or {}).get(STATE_KEY) or {})

    def patchState(self, patch):
        from girder.models.folder import Folder

        state = self.getState()
        state.update(patch)
        self.folder = Folder().setMetadata(self.folder, {STATE_KEY: state})
        return state


class RestStore:
    """Read and write a run's files and state over the REST API.

    Used by the Celery task. The ``girder_client`` it is handed already carries a
    ``DATA_READ``/``DATA_WRITE`` token minted for the user who started the run,
    so the worker can only touch what that user could.
    """

    def __init__(self, gc, folderId):
        self.gc = gc
        self._folderId = str(folderId)

    @property
    def folderId(self):
        return self._folderId

    def writeBytes(self, name, data, mimeType):
        for item in self.gc.listItem(self._folderId, name=name):
            self.gc.delete(f"item/{item['_id']}")

        file = self.gc.uploadStreamToFolder(
            self._folderId,
            io.BytesIO(data),
            name,
            len(data),
            mimeType=mimeType,
        )
        return str(file["_id"])

    def writeJson(self, name, payload):
        return self.writeBytes(name, encodeJson(payload), "application/json")

    def getState(self):
        folder = self.gc.getFolder(self._folderId)
        return dict((folder.get("meta") or {}).get(STATE_KEY) or {})

    def patchState(self, patch):
        state = self.getState()
        state.update(patch)
        # PUT /folder/:id/metadata merges top-level keys only, so the whole
        # nested state object goes over the wire every time.
        self.gc.addMetadataToFolder(self._folderId, {STATE_KEY: state})
        return state


def getWorkspace(user, create=True):
    """Return the user's dedicated dashboard folder, creating it on demand.

    Private, because micrographs are usually unpublished data; the user can share
    it from the normal Girder UI if they want to.
    """
    from girder.models.folder import Folder

    existing = Folder().findOne(
        {
            "parentId": user["_id"],
            "parentCollection": "user",
            "name": WORKSPACE_NAME,
        }
    )
    if existing is not None or not create:
        return existing

    return Folder().createFolder(
        user,
        WORKSPACE_NAME,
        description=WORKSPACE_DESCRIPTION,
        parentType="user",
        public=False,
        creator=user,
        reuseExisting=True,
    )


def createRun(user, name=None):
    """Create a folder for one analysis run inside the user's workspace."""
    from girder.models.folder import Folder

    workspace = getWorkspace(user)
    stamp = _now().strftime("%Y-%m-%d %H-%M-%S")
    folder = Folder().createFolder(
        workspace,
        (name or "").strip() or f"Run {stamp}",
        description="Precipitate analysis run.",
        parentType="folder",
        public=False,
        creator=user,
        # Two runs started in the same second, or two runs the user gave the same
        # name, must both survive.
        allowRename=True,
    )
    return Folder().setMetadata(
        folder,
        {
            STATE_KEY: {
                "status": STATUS_NEW,
                "created": stamp,
            }
        },
    )


def isRunFolder(folder):
    return STATE_KEY in (folder.get("meta") or {})


def listRuns(user, limit=0, offset=0):
    """Return the user's run folders, newest first."""
    from girder.models.folder import Folder

    workspace = getWorkspace(user, create=False)
    if workspace is None:
        return []

    cursor = Folder().find(
        {
            "parentId": workspace["_id"],
            "parentCollection": "folder",
            f"meta.{STATE_KEY}": {"$exists": True},
        },
        sort=[("created", -1)],
        limit=limit,
        offset=offset,
    )
    return list(cursor)


def runState(folder):
    return dict((folder.get("meta") or {}).get(STATE_KEY) or {})
