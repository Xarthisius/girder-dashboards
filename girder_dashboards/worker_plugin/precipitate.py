"""Celery tasks for the Precipitate Analysis dashboard.

Both tasks receive the micrograph as a :py:class:`GirderFileId`, which
girder_worker turns into a local path by downloading the file, and write their
outputs back through ``task.girder_client``. So the worker needs nothing but HTTP
access to Girder: no shared assetstore, no database connection, no Girder plugin
loading. That is what makes it safe to run this on a worker that is not on the
same host.

The scientific stack is imported inside the task bodies (via
:py:mod:`girder_dashboards.precipitate.runner`) so that a worker whose image
lacks it still starts, and reports the problem against the run that needed it.
"""

from girder_worker.app import app
from girder_worker.utils import girder_job

from ..precipitate import runner
from ..precipitate.store import RestStore


def _progress(task):
    """Adapt the runner's ``(fraction, message)`` to the job manager."""
    manager = getattr(task, "job_manager", None)
    if manager is None:
        return None

    def report(fraction, message):
        manager.updateProgress(
            total=runner.PROGRESS_TOTAL,
            current=runner.progressCount(fraction),
            message=message,
            forceFlush=True,
        )

    return report


def _sink(task, folderId):
    if getattr(task, "girder_client", None) is None:
        raise RuntimeError(
            "This task needs an authenticated Girder client to store its output. "
            "It must be scheduled with girder_api_url and girder_client_token, "
            "which girder_dashboards.precipitate.jobs does."
        )
    return RestStore(task.girder_client, folderId)


def _run(task, folderId, work):
    sink = _sink(task, folderId)
    try:
        return work(sink)
    except Exception as exc:
        # Record the failure on the run itself before letting Celery mark the job
        # ERROR, so the dashboard can explain a failed run after a page reload
        # rather than only while the job document is on screen.
        runner.recordFailure(sink, exc)
        raise


@girder_job(title=runner.STEP_TITLES[runner.STEP_PREPARE], type=runner.JOB_TYPE)
@app.task(bind=True, queue="local")
def prepareRun(task, imagePath, folderId):
    """Decode the micrograph and store a browsable preview for ROI selection."""
    return _run(
        task,
        folderId,
        lambda sink: runner.prepare(imagePath, sink, progress=_progress(task)),
    )


@girder_job(title=runner.STEP_TITLES[runner.STEP_ANALYZE], type=runner.JOB_TYPE)
@app.task(bind=True, queue="local")
def analyzeRun(task, imagePath, folderId, options):
    """Detect precipitates, measure spacing, store ``results.json``."""
    return _run(
        task,
        folderId,
        lambda sink: runner.analyze(imagePath, sink, options, progress=_progress(task)),
    )
