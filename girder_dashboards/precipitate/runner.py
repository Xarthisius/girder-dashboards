"""The two steps of a run, written once for both places they execute.

``prepare`` and ``analyze`` take a local image path and a store, and are called
identically from the Celery task (with a :py:class:`~.store.RestStore`) and from
Girder's in-process fallback (with a :py:class:`~.store.ModelStore`). Nothing in
here knows which one it got, and nothing in here imports Celery.

The heavy scientific imports live inside :py:mod:`~.analysis` and
:py:mod:`~.preview`, which are imported *inside* these functions on purpose: a
Celery worker that loads this module at startup must not fall over because
scikit-image is missing from the image — the failure belongs to the run that
needs it, where the message reaches the user.
"""

import logging

from . import store

logger = logging.getLogger(__name__)

#: The two steps of a run. Shared, because the Girder side schedules them and the
#: worker side runs them, and a job labelled differently by the two paths would
#: break the promise that the client cannot tell which one happened.
STEP_PREPARE = "prepare"
STEP_ANALYZE = "analyze"

JOB_TYPE = "dashboards.precipitate"

STEP_TITLES = {
    STEP_PREPARE: "Preparing micrograph",
    STEP_ANALYZE: "Detecting precipitates",
}

#: Girder job progress is an integer out of a total; the runner speaks fractions.
PROGRESS_TOTAL = 100


def progressCount(fraction):
    """Convert a runner fraction to a Girder progress count."""
    return int(max(0.0, min(1.0, fraction)) * PROGRESS_TOTAL)


def prepare(imagePath, sink, progress=None):
    """Decode the micrograph, store a browsable preview, record what it says.

    Also the one place the file is examined for the two things the user would
    otherwise have to supply by hand — the pixel scale and the info panel across
    the bottom (:py:mod:`.scale`). Both are recorded as *findings*, under
    ``detected``, separately from the ``request`` the user eventually makes: they
    prefill the form and explain themselves, and the user is free to overrule
    them.
    """
    from .analysis import loadImage
    from .preview import renderPreview

    if progress:
        progress(0.1, "Decoding image")
    gray = loadImage(imagePath)
    png, info = renderPreview(imagePath, gray=gray)

    if progress:
        progress(0.6, "Looking for a scale bar and an info panel")
    detected = _inspect(imagePath, gray)

    if progress:
        progress(0.8, "Storing preview")
    fileId = sink.writeBytes(store.PREVIEW_NAME, png, "image/png")

    state = {
        "status": store.STATUS_READY,
        "previewFileId": fileId,
        "image": info,
        "detected": detected,
        "error": None,
    }
    sink.patchState(state)
    if progress:
        progress(1.0, "Preview ready")
    return {"previewFileId": fileId, "image": info, "detected": detected}


def _inspect(imagePath, gray):
    """Scale and info panel, or ``{}`` — never a failed run.

    Nothing here is required to analyse anything: every value it produces is one
    the user can type in themselves. So an unreadable vendor header or a surprise
    in the pixel data costs them a prefilled field, not their run.
    """
    from .scale import inspectMicrograph

    try:
        return inspectMicrograph(imagePath, gray)
    except Exception:
        logger.exception("Could not inspect %s for scale and info panel", imagePath)
        return {}


def analyze(imagePath, sink, options, progress=None):
    """Detect precipitates, measure spacing, store ``results.json``.

    ``options`` is the request as the user made it: ``scaleBarMicrons``,
    ``scaleBarPixels``, ``edgeToEdge``, ``regions``, ``preset``,
    ``excludeBottomPx`` and optional ``overrides``.
    """
    from .analysis import analyze as runAnalysis

    results = runAnalysis(
        imagePath,
        options["scaleBarMicrons"],
        options["scaleBarPixels"],
        edgeToEdge=bool(options.get("edgeToEdge")),
        regions=options.get("regions") or [],
        preset=options.get("preset"),
        overrides=options.get("overrides"),
        excludeBottomPx=options.get("excludeBottomPx") or 0,
        progress=progress,
    )

    # The run folder already knows which image this is; carrying it into the
    # result document too makes results.json self-describing for anyone who
    # downloads it on its own.
    state = sink.getState()
    results["image"].update(
        {
            k: v
            for k, v in (state.get("image") or {}).items()
            if k not in results["image"]
        }
    )
    results["source"] = {
        "fileId": state.get("inputFileId"),
        "name": state.get("inputName"),
        "folderId": sink.folderId,
    }

    if progress:
        progress(0.98, "Storing results")
    fileId = sink.writeJson(store.RESULTS_NAME, results)

    sink.patchState(
        {
            "status": store.STATUS_COMPLETE,
            "resultFileId": fileId,
            "summary": summarize(results),
            "request": {
                "scaleBarMicrons": results["scale"]["barMicrons"],
                "scaleBarPixels": results["scale"]["barPixels"],
                "edgeToEdge": results["spacingMode"] == "edge-to-edge",
                "preset": results["params"]["preset"],
                "excludeBottomPx": results["image"]["excludeBottomPx"],
                "regions": [
                    dict(region["bbox"], label=region["label"])
                    for region in results["regions"]
                ],
            },
            "error": None,
        }
    )
    if progress:
        progress(1.0, "Done")
    return {"resultFileId": fileId, "summary": summarize(results)}


def summarize(results):
    """The few numbers the run list shows, so it need not fetch every result."""
    pooled = results["pooled"]
    diameter = pooled["diameter"]
    spacing = pooled["spacing"]
    return {
        "nRegions": pooled["nRegions"],
        "nParticles": pooled["nTotal"],
        "spacingMode": results["spacingMode"],
        "preset": results["params"]["preset"],
        "diameterMeanNm": diameter.get("mean", {}).get("nm"),
        "diameterStdNm": diameter.get("std", {}).get("nm"),
        "spacingMeanNm": spacing.get("mean", {}).get("nm"),
        "spacingStdNm": spacing.get("std", {}).get("nm"),
    }


def recordFailure(sink, message):
    """Mark a run as failed so the UI can explain itself after a page reload."""
    try:
        sink.patchState({"status": store.STATUS_FAILED, "error": str(message)[:2000]})
    except Exception:
        logger.exception("Could not record the failure on run %s", sink.folderId)
