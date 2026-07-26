"""The REST resource, the storage layout, and how a run gets scheduled."""

import io
import json
import pathlib
import tempfile
import time

import pytest
from girder.models.folder import Folder
from girder.models.item import Item
from girder.models.upload import Upload
from girder.models.user import User
from girder_jobs.constants import JobStatus
from girder_jobs.models.job import Job
from pytest_girder.assertions import assertStatus, assertStatusOk

from girder_dashboards.precipitate import jobs as precipitateJobs
from girder_dashboards.precipitate import runner, store

pytestmark = pytest.mark.plugin("dashboards")


@pytest.fixture
def otherUser(db):
    return User().createUser(
        login="stranger",
        password="stranger-password",
        email="stranger@example.com",
        firstName="Some",
        lastName="Stranger",
    )


def _upload(folder, user, path, name="micrograph.tif"):
    data = path.read_bytes()
    return Upload().uploadFromFile(
        io.BytesIO(data),
        size=len(data),
        name=name,
        parentType="folder",
        parent=folder,
        user=user,
        mimeType="image/tiff",
    )


def _createRun(server, user, name="run"):
    resp = server.request(
        path="/precipitate/run", method="POST", user=user, params={"name": name}
    )
    assertStatusOk(resp)
    return resp.json


@pytest.fixture
def run(server, precipitateDashboard, user, fsAssetstore, micrograph):
    """A run folder with the micrograph already uploaded into it."""
    created = _createRun(server, user)
    folder = Folder().load(created["_id"], force=True)
    path, _ = micrograph
    file = _upload(folder, user, path)
    return {"run": created, "folder": folder, "file": file, "user": user}


# --- capability -------------------------------------------------------------


def test_capability_describes_the_instance(server, precipitateDashboard, user):
    resp = server.request(path="/precipitate/capability", method="GET", user=user)
    assertStatusOk(resp)

    body = resp.json
    assert body["workspace"] == store.WORKSPACE_NAME
    assert sorted(preset["key"] for preset in body["presets"]) == ["coarse", "fine"]
    assert body["dependencies"]["ok"] is True
    assert body["compute"] in ("celery", "local")
    assert body["compute"] == ("celery" if body["worker"] else "local")
    assert body["settings"]["defaultScaleBarPixels"] == 129


def test_capability_needs_a_login(server, precipitateDashboard):
    assertStatus(server.request(path="/precipitate/capability", method="GET"), 401)


def test_endpoints_refuse_a_disabled_dashboard(server, db, user):
    # Disabling a dashboard has to disable its functionality, not just hide its
    # card, or the config page would be cosmetic.
    resp = server.request(path="/precipitate/capability", method="GET", user=user)
    assertStatus(resp, 403)
    assert "disabled" in resp.json["message"]


def test_endpoints_respect_the_dashboard_acl(server, precipitateDashboard, user):
    from girder_dashboards.models.dashboard import Dashboard as DashboardModel

    DashboardModel().setPublic(precipitateDashboard, False, save=True)

    resp = server.request(path="/precipitate/capability", method="GET", user=user)
    assertStatus(resp, 403)
    assert "do not have access" in resp.json["message"]


# --- runs and where they live ----------------------------------------------


def test_creating_a_run_makes_a_folder_in_the_users_own_space(
    server, precipitateDashboard, user
):
    created = _createRun(server, user, name="first run")

    folder = Folder().load(created["_id"], force=True)
    workspace = Folder().load(folder["parentId"], force=True)

    assert folder["name"] == "first run"
    assert folder["parentCollection"] == "folder"
    assert workspace["name"] == store.WORKSPACE_NAME
    assert workspace["parentCollection"] == "user"
    assert workspace["parentId"] == user["_id"]
    # Micrographs are usually unpublished, so nothing here is public by default.
    assert workspace["public"] is False
    assert created["state"]["status"] == store.STATUS_NEW


def test_runs_with_the_same_name_both_survive(server, precipitateDashboard, user):
    first = _createRun(server, user, name="same")
    second = _createRun(server, user, name="same")

    assert first["_id"] != second["_id"]
    assert {first["name"], second["name"]} == {"same", "same (1)"}


def test_runs_are_listed_newest_first(server, precipitateDashboard, user):
    _createRun(server, user, name="older")
    _createRun(server, user, name="newer")

    resp = server.request(path="/precipitate/run", method="GET", user=user)
    assertStatusOk(resp)
    assert [item["name"] for item in resp.json] == ["newer", "older"]


def test_one_users_runs_are_invisible_to_another(
    server, precipitateDashboard, user, otherUser
):
    created = _createRun(server, user)

    resp = server.request(
        path=f"/precipitate/run/{created['_id']}", method="GET", user=otherUser
    )
    assertStatus(resp, 403)

    resp = server.request(path="/precipitate/run", method="GET", user=otherUser)
    assertStatusOk(resp)
    assert resp.json == []


def test_a_plain_folder_is_not_a_run(server, precipitateDashboard, user):
    folder = Folder().createFolder(
        user, "not a run", parentType="user", creator=user, public=False
    )

    resp = server.request(
        path=f"/precipitate/run/{folder['_id']}", method="GET", user=user
    )
    assertStatus(resp, 400)
    assert "not a precipitate analysis run" in resp.json["message"]


def test_deleting_a_run_removes_its_folder(server, precipitateDashboard, user):
    created = _createRun(server, user)

    resp = server.request(
        path=f"/precipitate/run/{created['_id']}", method="DELETE", user=user
    )
    assertStatusOk(resp)
    assert Folder().load(created["_id"], force=True) is None


# --- request validation -----------------------------------------------------


def test_prepare_refuses_a_file_from_another_folder(server, run, user, micrograph):
    elsewhere = Folder().createFolder(
        user, "elsewhere", parentType="user", creator=user, public=False
    )
    path, _ = micrograph
    stray = _upload(elsewhere, user, path)

    resp = server.request(
        path=f"/precipitate/run/{run['run']['_id']}/prepare",
        method="POST",
        user=user,
        params={"fileId": str(stray["_id"])},
    )
    assertStatus(resp, 400)
    assert "run's own folder" in resp.json["message"]


def test_prepare_refuses_a_non_image(server, run, user):
    notes = Upload().uploadFromFile(
        io.BytesIO(b"hello"),
        size=5,
        name="notes.txt",
        parentType="folder",
        parent=run["folder"],
        user=user,
        mimeType="text/plain",
    )

    resp = server.request(
        path=f"/precipitate/run/{run['run']['_id']}/prepare",
        method="POST",
        user=user,
        params={"fileId": str(notes["_id"])},
    )
    assertStatus(resp, 400)
    assert "Unsupported image type" in resp.json["message"]


def test_analyze_needs_an_image_first(server, precipitateDashboard, user):
    created = _createRun(server, user)

    resp = server.request(
        path=f"/precipitate/run/{created['_id']}/analyze",
        method="POST",
        user=user,
        params={"scaleBarMicrons": 1.0, "scaleBarPixels": 129},
    )
    assertStatus(resp, 400)
    assert "no image yet" in resp.json["message"]


def test_analyze_rejects_a_nonpositive_scale(server, run, user, monkeypatch):
    monkeypatch.setattr(precipitateJobs, "schedule", _unreachable)
    store.ModelStore(run["folder"], user).patchState(
        {"inputFileId": str(run["file"]["_id"]), "inputName": run["file"]["name"]}
    )

    resp = server.request(
        path=f"/precipitate/run/{run['run']['_id']}/analyze",
        method="POST",
        user=user,
        params={"scaleBarMicrons": 0, "scaleBarPixels": 129},
    )
    assertStatus(resp, 400)
    assert "must both be positive" in resp.json["message"]


def test_analyze_caps_the_number_of_regions(
    server, precipitateDashboard, run, user, monkeypatch
):
    from girder_dashboards.models.dashboard import Dashboard as DashboardModel

    monkeypatch.setattr(precipitateJobs, "schedule", _unreachable)
    precipitateDashboard["settings"] = dict(
        precipitateDashboard["settings"], maxRegions=2
    )
    DashboardModel().save(precipitateDashboard)
    store.ModelStore(run["folder"], user).patchState(
        {"inputFileId": str(run["file"]["_id"]), "inputName": run["file"]["name"]}
    )

    regions = [
        {"label": f"ROI {index}", "x": 0, "y": 0, "width": 60, "height": 60}
        for index in range(3)
    ]
    resp = server.request(
        path=f"/precipitate/run/{run['run']['_id']}/analyze",
        method="POST",
        user=user,
        params={
            "scaleBarMicrons": 1.0,
            "scaleBarPixels": 129,
            "regions": json.dumps(regions),
        },
    )
    assertStatus(resp, 400)
    assert "At most 2 regions" in resp.json["message"]


def test_analyze_refuses_an_exclusion_that_leaves_nothing(
    server, run, user, monkeypatch
):
    """Caught here, not by the job a minute later.

    The run folder already knows how tall the image is, so an impossible
    exclusion is a bad request, not a failed analysis.
    """
    monkeypatch.setattr(precipitateJobs, "schedule", _unreachable)
    store.ModelStore(run["folder"], user).patchState(
        {
            "inputFileId": str(run["file"]["_id"]),
            "inputName": run["file"]["name"],
            "image": {"width": 512, "height": 512},
        }
    )

    resp = server.request(
        path=f"/precipitate/run/{run['run']['_id']}/analyze",
        method="POST",
        user=user,
        params={
            "scaleBarMicrons": 1.0,
            "scaleBarPixels": 129,
            "excludeBottomPx": 512,
        },
    )
    assertStatus(resp, 400)
    assert "nothing to analyse" in resp.json["message"]


def _unreachable(*args, **kwargs):
    raise AssertionError("the request should have been rejected before scheduling")


# --- scheduling -------------------------------------------------------------


class _FakeAsyncResult:
    def __init__(self, job):
        self.job = job


class _FakeTask:
    """Stands in for a Celery task, recording how it was called."""

    def __init__(self):
        self.calls = []

    def delay(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _FakeAsyncResult({"_id": "fake-job", "status": JobStatus.QUEUED})


def test_celery_is_used_when_a_worker_is_available(run, user, monkeypatch):
    from girder_dashboards.worker_plugin import precipitate as tasks

    fake = _FakeTask()
    monkeypatch.setattr(precipitateJobs, "workerAvailable", lambda: True)
    monkeypatch.setattr(tasks, "prepareRun", fake)

    job = precipitateJobs.schedule(
        precipitateJobs.STEP_PREPARE, user, run["folder"], run["file"]
    )

    assert job["_id"] == "fake-job"
    (args, kwargs) = fake.calls[0]
    # The image reaches the worker as a transform it resolves by downloading, so
    # the worker needs no database or assetstore access...
    assert type(args[0]).__name__ == "GirderFileId"
    assert args[1] == str(run["folder"]["_id"])
    # ...and it writes its output back with a token that can do exactly that.
    assert kwargs["girder_client_token"]
    assert kwargs["girder_job_type"] == precipitateJobs.JOB_TYPE
    assert kwargs["girder_job_other_fields"] == {
        "precipitateRun": str(run["folder"]["_id"])
    }
    assert run["folder"]["name"] in kwargs["girder_job_title"]


def test_analyze_options_travel_to_the_worker(run, user, monkeypatch):
    from girder_dashboards.worker_plugin import precipitate as tasks

    fake = _FakeTask()
    monkeypatch.setattr(precipitateJobs, "workerAvailable", lambda: True)
    monkeypatch.setattr(tasks, "analyzeRun", fake)

    options = {
        "scaleBarMicrons": 1.0,
        "scaleBarPixels": 129,
        "edgeToEdge": True,
        "preset": "fine",
        "regions": [],
        "overrides": {},
    }
    precipitateJobs.schedule(
        precipitateJobs.STEP_ANALYZE, user, run["folder"], run["file"], options
    )

    (args, _) = fake.calls[0]
    assert args[2] == options


def test_a_local_job_is_used_when_no_worker_is_available(run, user, monkeypatch):
    monkeypatch.setattr(precipitateJobs, "workerAvailable", lambda: False)
    monkeypatch.setattr(precipitateJobs, "runLocal", lambda job: job)

    job = precipitateJobs.schedule(
        precipitateJobs.STEP_PREPARE, user, run["folder"], run["file"]
    )

    assert job["handler"] == "jobs._local"
    assert job["module"] == "girder_dashboards.precipitate.jobs"
    assert job["function"] == "runLocal"
    assert job["kwargs"]["step"] == precipitateJobs.STEP_PREPARE
    assert job["precipitateRun"] == str(run["folder"]["_id"])


def test_scheduling_marks_the_run_as_busy(run, user, monkeypatch):
    monkeypatch.setattr(precipitateJobs, "workerAvailable", lambda: False)
    monkeypatch.setattr(precipitateJobs, "runLocal", lambda job: job)

    precipitateJobs.schedule(
        precipitateJobs.STEP_ANALYZE, user, run["folder"], run["file"], {}
    )

    state = store.runState(Folder().load(run["folder"]["_id"], force=True))
    assert state["status"] == store.STATUS_ANALYZING
    assert state["error"] is None


def test_worker_is_unavailable_without_the_worker_plugin(monkeypatch):
    monkeypatch.setattr(precipitateJobs.importlib.util, "find_spec", lambda name: None)

    # girder_worker's publish hook imports girder_plugin_worker to create the job
    # document, so without it .delay() would raise rather than schedule.
    assert precipitateJobs.workerAvailable() is False


# --- the steps, run in process ---------------------------------------------


def test_prepare_stores_a_preview_next_to_the_image(run, user, micrograph):
    path, _ = micrograph
    sink = store.ModelStore(run["folder"], user)

    result = runner.prepare(str(path), sink)

    state = store.runState(Folder().load(run["folder"]["_id"], force=True))
    assert state["status"] == store.STATUS_READY
    assert state["previewFileId"] == result["previewFileId"]
    assert state["image"]["width"] == 512
    assert {
        item["name"] for item in Item().find({"folderId": run["folder"]["_id"]})
    } == {
        "micrograph.tif",
        store.PREVIEW_NAME,
    }


def test_prepare_records_what_the_image_says_about_itself(
    run, user, micrographWithPanel, micrographModule
):
    path, _ = micrographWithPanel
    sink = store.ModelStore(run["folder"], user)

    runner.prepare(str(path), sink)

    detected = store.runState(Folder().load(run["folder"]["_id"], force=True))[
        "detected"
    ]
    assert detected["panel"]["height"] == micrographModule.PANEL_HEIGHT
    assert detected["scale"]["complete"] is True
    assert detected["scale"]["barMicrons"] == 1.0
    assert detected["scale"]["barPixels"] == float(micrographModule.BAR_PIXELS)


def test_prepare_survives_an_image_it_cannot_make_sense_of(
    run, user, micrograph, monkeypatch
):
    """A surprise in the metadata costs a prefilled field, not the run.

    Nothing the inspection produces is needed to analyse anything — every value
    is one the user could type in themselves — so it must never be the reason a
    run fails.
    """
    from girder_dashboards.precipitate import scale as scaleModule

    def explode(*args, **kwargs):
        raise ValueError("some vendor's tag is not what it claimed")

    monkeypatch.setattr(scaleModule, "inspectMicrograph", explode)
    path, _ = micrograph

    result = runner.prepare(str(path), store.ModelStore(run["folder"], user))

    assert result["detected"] == {}
    state = store.runState(Folder().load(run["folder"]["_id"], force=True))
    assert state["status"] == store.STATUS_READY
    assert state["previewFileId"]


def test_rerunning_a_step_replaces_its_output(run, user, micrograph):
    path, _ = micrograph
    sink = store.ModelStore(run["folder"], user)

    first = runner.prepare(str(path), sink)
    second = runner.prepare(str(path), sink)

    assert first["previewFileId"] != second["previewFileId"]
    previews = list(
        Item().find({"folderId": run["folder"]["_id"], "name": store.PREVIEW_NAME})
    )
    assert len(previews) == 1


def test_analyze_stores_results_and_a_summary(run, user, micrograph):
    path, _ = micrograph
    sink = store.ModelStore(run["folder"], user)
    sink.patchState(
        {"inputFileId": str(run["file"]["_id"]), "inputName": run["file"]["name"]}
    )
    runner.prepare(str(path), sink)

    regions = [{"label": "ROI 1", "x": 0, "y": 0, "width": 256, "height": 256}]
    runner.analyze(
        str(path),
        sink,
        {
            "scaleBarMicrons": 1.0,
            "scaleBarPixels": 129,
            "edgeToEdge": True,
            "preset": "fine",
            "regions": regions,
        },
    )

    folder = Folder().load(run["folder"]["_id"], force=True)
    state = store.runState(folder)
    assert state["status"] == store.STATUS_COMPLETE
    assert state["summary"]["nRegions"] == 1
    assert state["summary"]["spacingMode"] == "edge-to-edge"
    assert state["summary"]["nParticles"] > 0
    # The request is recorded so re-opening the run restores the form and regions.
    assert state["request"]["edgeToEdge"] is True
    assert state["request"]["excludeBottomPx"] == 0
    assert state["request"]["regions"] == [
        {"label": "ROI 1", "x": 0, "y": 0, "width": 256, "height": 256}
    ]

    stored = _download(state["resultFileId"])
    assert stored["pooled"]["nTotal"] == state["summary"]["nParticles"]
    # results.json has to stand on its own for anyone who downloads it.
    assert stored["source"]["name"] == "micrograph.tif"
    assert stored["source"]["fileId"] == str(run["file"]["_id"])
    assert stored["image"]["width"] == 512


def test_a_failed_step_is_recorded_on_the_run(run, user, tmp_path):
    broken = tmp_path / "broken.tif"
    broken.write_bytes(b"not a tiff at all")
    sink = store.ModelStore(run["folder"], user)

    with pytest.raises(Exception):
        runner.prepare(str(broken), sink)
    runner.recordFailure(sink, "Could not read the TIFF image")

    state = store.runState(Folder().load(run["folder"]["_id"], force=True))
    assert state["status"] == store.STATUS_FAILED
    assert "Could not read" in state["error"]


def _download(fileId):
    from girder.models.file import File

    file = File().load(fileId, force=True)
    with File().open(file) as handle:
        return json.loads(handle.read())


# --- the whole way through, on the local path ------------------------------


def test_a_run_completes_end_to_end_without_a_worker(server, run, user, monkeypatch):
    """prepare then analyze, scheduled as real jobs and executed in-process.

    This is the path a plain ``girder serve`` takes, and the one the browser
    harness drives, so it is worth exercising for real rather than mocking the
    runner out.
    """
    monkeypatch.setattr(precipitateJobs, "workerAvailable", lambda: False)
    runId = run["run"]["_id"]

    resp = server.request(
        path=f"/precipitate/run/{runId}/prepare",
        method="POST",
        user=user,
        params={"fileId": str(run["file"]["_id"])},
    )
    assertStatusOk(resp)
    _waitForJob(resp.json["_id"])

    state = store.runState(Folder().load(runId, force=True))
    assert state["status"] == store.STATUS_READY
    assert state["previewFileId"]

    resp = server.request(
        path=f"/precipitate/run/{runId}/analyze",
        method="POST",
        user=user,
        params={
            "scaleBarMicrons": 1.0,
            "scaleBarPixels": 129,
            "edgeToEdge": "false",
            "preset": "fine",
        },
    )
    assertStatusOk(resp)
    job = _waitForJob(resp.json["_id"])
    assert job["progress"]["current"] == job["progress"]["total"]

    state = store.runState(Folder().load(runId, force=True))
    assert state["status"] == store.STATUS_COMPLETE
    assert state["summary"]["nParticles"] > 0
    assert _download(state["resultFileId"])["spacingMode"] == "centre-to-centre"


def test_a_detected_scale_and_panel_survive_the_whole_round_trip(
    server,
    precipitateDashboard,
    user,
    fsAssetstore,
    micrographWithPanel,
    micrographModule,
    monkeypatch,
):
    """Upload an instrument's own file and let the two steps do the work.

    Everything the dashboard needs in order to fill the scale in and grey out the
    panel travels as run state, and everything it then asks for travels as the
    analyze request — so this is the feature end to end over HTTP, with no
    knowledge of either shared between the two halves except that state.
    """
    monkeypatch.setattr(precipitateJobs, "workerAvailable", lambda: False)
    path, _ = micrographWithPanel
    created = _createRun(server, user, name="tescan run")
    folder = Folder().load(created["_id"], force=True)
    file = _upload(folder, user, path, name="tescan.tif")

    resp = server.request(
        path=f"/precipitate/run/{created['_id']}/prepare",
        method="POST",
        user=user,
        params={"fileId": str(file["_id"])},
    )
    assertStatusOk(resp)
    _waitForJob(resp.json["_id"])

    state = store.runState(Folder().load(created["_id"], force=True))
    detected = state["detected"]
    assert detected["scale"]["barPixels"] == float(micrographModule.BAR_PIXELS)
    assert detected["panel"]["height"] == micrographModule.PANEL_HEIGHT
    # The preview is of the whole file, panel included: it is where the scale bar
    # is printed, so it is the one thing the user needs in order to check the
    # scale that was filled in for them.
    assert state["image"]["height"] == 512 + micrographModule.PANEL_HEIGHT

    resp = server.request(
        path=f"/precipitate/run/{created['_id']}/analyze",
        method="POST",
        user=user,
        params={
            "scaleBarMicrons": detected["scale"]["barMicrons"],
            "scaleBarPixels": detected["scale"]["barPixels"],
            "preset": "fine",
            "excludeBottomPx": detected["panel"]["height"],
        },
    )
    assertStatusOk(resp)
    _waitForJob(resp.json["_id"])

    state = store.runState(Folder().load(created["_id"], force=True))
    assert state["request"]["excludeBottomPx"] == micrographModule.PANEL_HEIGHT

    results = _download(state["resultFileId"])
    assert results["image"]["contentHeight"] == 512
    assert results["scale"]["nmPerPx"] == pytest.approx(
        micrographModule.PIXEL_SIZE_M * 1e9
    )
    # Nothing was detected in the panel, because the panel was never looked at.
    assert max(results["regions"][0]["particles"]["y"]) < 512


def _waitForJob(jobId, timeout=90):
    """Wait for a job the local handler is running in a background thread."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = Job().load(jobId, force=True, includeLog=True)
        if job["status"] in (JobStatus.SUCCESS, JobStatus.ERROR, JobStatus.CANCELED):
            log = job.get("log") or []
            assert job["status"] == JobStatus.SUCCESS, "".join(log)[-2000:]
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {jobId} did not finish within {timeout}s")


# --- defects found in review ------------------------------------------------


def test_a_failed_reanalysis_drops_the_previous_results(run, user, monkeypatch):
    """Otherwise the dashboard shows a failure banner over the old run's charts."""
    monkeypatch.setattr(precipitateJobs, "workerAvailable", lambda: False)
    monkeypatch.setattr(precipitateJobs, "runLocal", lambda job: job)

    sink = store.ModelStore(run["folder"], user)
    sink.patchState(
        {
            "status": store.STATUS_COMPLETE,
            "resultFileId": "6a662dd70a29fd3410c88629",
            "summary": {"nParticles": 42},
            "request": {"preset": "fine"},
        }
    )

    precipitateJobs.schedule(
        precipitateJobs.STEP_ANALYZE, user, run["folder"], run["file"], {}
    )

    state = store.runState(Folder().load(run["folder"]["_id"], force=True))
    assert state["status"] == store.STATUS_ANALYZING
    for key in ("resultFileId", "summary", "request"):
        assert key not in state, key


def test_preparing_again_keeps_the_previous_results(run, user, monkeypatch):
    """A re-prepare is not a new analysis, so it must not discard one."""
    monkeypatch.setattr(precipitateJobs, "workerAvailable", lambda: False)
    monkeypatch.setattr(precipitateJobs, "runLocal", lambda job: job)

    store.ModelStore(run["folder"], user).patchState(
        {"resultFileId": "6a662dd70a29fd3410c88629", "summary": {"nParticles": 42}}
    )

    precipitateJobs.schedule(
        precipitateJobs.STEP_PREPARE, user, run["folder"], run["file"]
    )

    state = store.runState(Folder().load(run["folder"]["_id"], force=True))
    assert state["summary"] == {"nParticles": 42}


def test_the_local_copy_never_writes_where_the_file_name_says(run, user, tmp_path):
    """Girder does not reject path separators in a file name.

    Joining one onto a temp directory let an uploaded file be written anywhere the
    Girder process could reach — `os.path.join(dir, "/tmp/x.tif")` is `/tmp/x.tif`.
    """
    escape = tmp_path / "escaped.tif"
    hostile = dict(run["file"], name=str(escape))

    with precipitateJobs._localCopy(hostile) as path:
        assert not escape.exists()
        assert pathlib.Path(path).parent.name.startswith("precipitate-")
        assert pathlib.Path(path).name == "micrograph.tif"

    # ...and the temp directory is gone afterwards.
    assert not pathlib.Path(path).exists()


def test_the_local_copy_cleans_up_when_it_cannot_read_the_file(run):
    missing = dict(run["file"], assetstoreId=None, _id=None)

    before = set(pathlib.Path(tempfile.gettempdir()).glob("precipitate-*"))
    with pytest.raises(Exception):
        with precipitateJobs._localCopy(missing):
            pass
    assert set(pathlib.Path(tempfile.gettempdir()).glob("precipitate-*")) == before
