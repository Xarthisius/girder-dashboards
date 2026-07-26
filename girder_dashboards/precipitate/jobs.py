"""Scheduling a run's two steps, and running them when there is no worker.

The dashboard's work is a Celery task on the ``local`` queue — that is the
design. But a Girder started with nothing but ``girder serve`` has no broker and
no worker, and a dashboard that only works in a full deployment cannot be
demonstrated or browser-tested. So :py:func:`schedule` sends the task when a
worker is consuming ``local`` and otherwise creates an equivalent Girder job that
runs the *same* :py:mod:`~.runner` functions in a background thread of the Girder
process.

Both paths produce a normal Girder job document, so the web client polls exactly
one thing either way and never has to know which happened.
"""

import importlib.util
import logging
import os
import pathlib
import shutil
import tempfile
import threading

from girder.constants import TokenScope
from girder.models.file import File
from girder.models.folder import Folder
from girder.models.token import Token
from girder.models.user import User
from girder_jobs.constants import JobStatus
from girder_jobs.models.job import Job

from . import runner, store
from .runner import (  # noqa: F401  (re-exported: this is the scheduling entry point)
    JOB_TYPE,
    PROGRESS_TOTAL,
    STEP_ANALYZE,
    STEP_PREPARE,
)

logger = logging.getLogger(__name__)


def workerAvailable():
    """Whether the Celery path can be used at all.

    Two things have to be true: a worker is consuming the ``local`` queue, and
    ``girder_plugin_worker`` is installed here — girder_worker's publish hook
    imports it to create the job document, so without it ``.delay()`` raises
    instead of scheduling.

    The queue check delegates to Girder core so this agrees with the rest of the
    instance. Core caches its answer for an hour, so a worker started just now is
    not necessarily picked up yet.
    """
    if importlib.util.find_spec("girder_plugin_worker") is None:
        return False

    from girder.tasks import is_local_worker_available

    try:
        return bool(is_local_worker_available())
    except Exception:
        logger.exception("Could not determine local worker availability")
        return False


def _title(step, folder):
    return f"{runner.STEP_TITLES[step]}: {folder['name']}"


def _statusFor(step):
    return store.STATUS_PREPARING if step == STEP_PREPARE else store.STATUS_ANALYZING


def schedule(step, user, folder, file, options=None):
    """Start ``step`` for a run, on a worker if there is one.

    :returns: the Girder job document the client should follow.
    """
    # Re-read before merging. The folder handed in was loaded at the start of the
    # request, and a previous step's job — running in a thread or in a worker — may
    # have written to its state since; merging into the stale copy would silently
    # drop what it wrote.
    folder = Folder().load(folder["_id"], force=True, exc=True)

    state = dict(store.runState(folder), status=_statusFor(step), error=None)
    if step == STEP_ANALYZE:
        # Drop the previous analysis. Without this, a re-run that fails leaves the
        # old resultFileId and summary in place, and the dashboard renders a
        # failure banner above the *previous* run's charts and numbers.
        for key in ("resultFileId", "summary", "request"):
            state.pop(key, None)

    Folder().setMetadata(folder, {store.STATE_KEY: state})

    if workerAvailable():
        return _scheduleCelery(step, user, folder, file, options)
    return _scheduleLocal(step, user, folder, file, options)


def _scheduleCelery(step, user, folder, file, options):
    from girder_worker.utils.transforms.girder_io import GirderFileId

    from ..worker_plugin.precipitate import analyzeRun, prepareRun

    # girder_worker attaches a token of its own, but only with the scope needed
    # to update the job. The task also writes results back into the run folder,
    # so it gets a token that can do exactly that, on behalf of this user.
    token = Token().createToken(
        user=user,
        days=1,
        scope=[TokenScope.DATA_READ, TokenScope.DATA_WRITE],
    )

    task = prepareRun if step == STEP_PREPARE else analyzeRun
    args = [GirderFileId(str(file["_id"])), str(folder["_id"])]
    if step == STEP_ANALYZE:
        args.append(options)

    async_ = task.delay(
        *args,
        girder_user=user,
        girder_job_title=_title(step, folder),
        girder_job_type=JOB_TYPE,
        girder_job_other_fields={"precipitateRun": str(folder["_id"])},
        girder_client_token=str(token["_id"]),
    )
    return async_.job


def _scheduleLocal(step, user, folder, file, options):
    job = Job().createLocalJob(
        title=_title(step, folder),
        type=JOB_TYPE,
        user=user,
        public=False,
        asynchronous=True,
        module="girder_dashboards.precipitate.jobs",
        function="runLocal",
        kwargs={
            "step": step,
            "folderId": str(folder["_id"]),
            "fileId": str(file["_id"]),
            "options": options,
        },
        otherFields={"precipitateRun": str(folder["_id"])},
    )
    # scheduleJob returns nothing; for a local job it synchronously calls
    # runLocal, which starts the thread and comes straight back.
    Job().scheduleJob(job)
    return job


def runLocal(job):
    """Local-job entry point: hand the work to a thread and return.

    Girder's local job handler calls this synchronously from the request thread
    (``asynchronous=True`` on the job document is recorded but not acted on by
    core), and a full analysis takes tens of seconds. Running it inline would
    hold the HTTP request open for the whole run and give the client nothing to
    poll, so the work goes to a daemon thread that owns the job from here on.

    A daemon thread does mean a run in flight is abandoned if Girder is
    restarted; its job document is then left in RUNNING. That is the acceptable
    cost of a fallback — the Celery path is what survives a restart.
    """
    # The thread gets the id, not the document: the request thread is still
    # serializing this same dict on its way out, and the thread mutates status,
    # progress and timestamps on it.
    thread = threading.Thread(
        target=_runLocalBody,
        args=(job["_id"],),
        name=f"precipitate-{job['_id']}",
        daemon=True,
    )
    thread.start()
    return job


def _runLocalBody(jobId):
    job = Job().load(jobId, force=True, includeLog=False)
    kwargs = job.get("kwargs") or {}
    step = kwargs["step"]
    sink = None

    try:
        # RUNNING first, before anything that can fail. A job that is still
        # INACTIVE cannot legally transition to ERROR (jobs' valid_transitions
        # allows it only from QUEUED or RUNNING), so failing earlier than this
        # would make the error report itself raise and leave the job stuck.
        Job().updateJob(
            job,
            status=JobStatus.RUNNING,
            log=f"Running {step} in the Girder process (no Celery worker available).\n",
            progressTotal=PROGRESS_TOTAL,
            progressCurrent=0,
            progressMessage="Starting",
        )

        user = User().load(job["userId"], force=True)
        folder = Folder().load(kwargs["folderId"], force=True, exc=True)
        file = File().load(kwargs["fileId"], force=True, exc=True)
        sink = store.ModelStore(folder, user)

        def progress(fraction, message):
            Job().updateJob(
                job,
                progressTotal=PROGRESS_TOTAL,
                progressCurrent=runner.progressCount(fraction),
                progressMessage=message,
            )

        with _localCopy(file) as path:
            if step == STEP_PREPARE:
                result = runner.prepare(path, sink, progress=progress)
            else:
                result = runner.analyze(
                    path, sink, kwargs["options"], progress=progress
                )

        try:
            Job().updateJob(
                job,
                status=JobStatus.SUCCESS,
                log="Finished.\n",
                progressTotal=PROGRESS_TOTAL,
                progressCurrent=PROGRESS_TOTAL,
                progressMessage="Done",
            )
        except Exception:
            # The work is done and its output is already stored, so a rejected
            # status transition — the user cancelled the job while it ran, most
            # likely — must not be reported as an analysis failure.
            logger.exception("Could not mark job %s as finished", jobId)
        return result
    except Exception as exc:
        logger.exception("Precipitate %s failed for job %s", step, jobId)
        if sink is not None:
            runner.recordFailure(sink, exc)
        try:
            Job().updateJob(job, status=JobStatus.ERROR, log=f"\n{exc}\n")
        except Exception:
            # Nothing above this in a daemon thread; letting it propagate would
            # lose both the original failure and this one.
            logger.exception("Could not mark job %s as failed", jobId)


class _localCopy:
    """Stream a Girder file to a temporary path, and clean up afterwards.

    The analysis needs a real path (scikit-image/tifffile seek), and an
    assetstore is not necessarily a filesystem, so the bytes are copied out
    rather than pointed at.
    """

    def __init__(self, file):
        self.file = file
        self.directory = None

    def __enter__(self):
        self.directory = tempfile.mkdtemp(prefix="precipitate-")
        try:
            # NOT the Girder file name. Girder does not reject path separators in
            # a file name, so joining one onto the temp directory would let
            # 'name=/etc/cron.d/x.tif' or '../../x.tif' escape it and write
            # wherever the Girder process can — os.path.join discards the
            # directory entirely for an absolute name. Only the extension matters
            # here anyway: it is how loadImage picks a decoder.
            suffix = pathlib.PurePosixPath(self.file["name"]).suffix.lower()
            self.path = os.path.join(self.directory, f"micrograph{suffix}")
            with File().open(self.file) as source, open(self.path, "wb") as target:
                shutil.copyfileobj(source, target)
        except Exception:
            # __exit__ never runs if __enter__ raises, so the directory (and the
            # partial copy in it) would be left behind on every failed attempt.
            self._cleanup()
            raise
        return self.path

    def __exit__(self, *exc):
        self._cleanup()
        return False

    def _cleanup(self):
        if self.directory:
            shutil.rmtree(self.directory, ignore_errors=True)
            self.directory = None
