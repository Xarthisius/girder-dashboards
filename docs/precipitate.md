# The Precipitate Analysis dashboard

Everything specific to the **Precipitate Analysis** dashboard — the algorithm port, its two
execution paths, its storage layout, its REST slice, its tests and its defect history. The
rest of the plugin is in `../CLAUDE.md`; nothing here is needed to work on dashboards in
general.

A port of the research code at
`https://github.com/Taheri-Mousavi-Laboratory/Image-analysis-precipitate-detection-and-particle-spacing-estimation`
(kept locally at `/tmp/Image-analysis-...` while this was written — clone it again if you need to
re-run the fidelity check). Detects precipitates in an SEM/TEM micrograph and measures equivalent
diameter and nearest-neighbour spacing.

## Where the code is

```
girder_dashboards/
  rest/precipitate.py  # Precipitate(Resource) at /api/v1/precipitate
  precipitate/         # the dashboard's whole server slice
    __init__.py        #   declaration, card SVG, dependency probe
    presets.py         #   the two published tunings + request -> params (no heavy imports)
    analysis.py        #   the algorithm: numpy/scipy/skimage in, plain dicts out
    scale.py           #   pixel scale from the vendor header or the drawn bar; info panel
    preview.py         #   micrograph -> downscaled PNG for region selection
    store.py           #   folder layout + the two write sinks (models / girder_client)
    runner.py          #   the two steps, written once for both execution paths
    jobs.py            #   schedule on Celery, or in a Girder thread when there is no worker
  worker_plugin/       # girder_worker_plugins entry point + the two @app.task functions
  web_client/dashboards/
    PrecipitateDashboard.js        # the stepper: upload -> scale -> regions -> job -> results
    precipitate/
      RoiSelector.js               # image + rubber-band regions + detection/NN overlays,
                                   #   the excluded info panel, the measured scale bar
      ResultsView.js               # tiles, tables, and the charts
      charts.js                    # Chart.js histograms + spacing map
      palette.js                   # validated colour/ink tokens
test/fidelity/         # compare_to_original.py — the port vs the research scripts, by hand
```

## What changed relative to the original scripts

Three deliberate departures, all documented in `precipitate/analysis.py`'s docstring:

1. **No plotting, no printing.** `analyze()` returns a JSON-serializable dict. The originals' five
   matplotlib panels are all reproduced in the browser: the two histograms and the spacing heat map
   are Chart.js charts, and the detection overlay and nearest-neighbour map are SVG drawn straight
   over the micrograph by `RoiSelector` (better than a chart — it is the actual image).
2. **ROIs replace the multi-image script's separate files.** Each region is cropped and run through
   the pipeline on its own, then pooled, which is what the original did with its three ROI *files*.
   Detection is therefore per-region: top-hat normalisation and the blob threshold see only the
   crop. Do not "optimise" this into one whole-image detection pass followed by filtering — it
   would change every number.
3. **No OpenCV.** `tifffile`/`imageio` + numpy cover the two things cv2 was used for. `toGrayscale`
   reproduces `cv2.imread(IMREAD_UNCHANGED)` + `cvtColor(BGR2GRAY)` *including the rounding*: grey
   first, then a 0-255 stretch only if the input was not already 8-bit. **The rounding is
   load-bearing** — dropping it moves ~1% of the thousands of LoG candidates across the threshold.

Both published tunings are preserved as presets (`fine` = 725 °C/1 hr, `coarse` = 725 °C/5 hr).
Everywhere the two scripts differed in *control flow* rather than in a threshold — the annulus
inner edge, the minimum blob and local-window radii, whether a too-small window is clamped or
rejected, what a black annulus means, whether the equivalent-diameter gate applies — is a parameter
in `PRESETS`, not a branch on the preset name.

**Fidelity is verified, not assumed:** `test/fidelity/compare_to_original.py` imports the original
modules and compares every statistic. It matched to the last decimal on all four sample images,
both presets, both spacing modes, including identical blob-candidate counts. It needs the research
repo and OpenCV, so it cannot run in CI — run it by hand after touching detection or statistics:

```bash
venv/bin/pip install opencv-python-headless pandas   # reference only, never a plugin dependency
venv/bin/python test/fidelity/compare_to_original.py /path/to/research/repo
```

The analysis stack itself is the `precipitate` extra: `venv/bin/pip install -e '.[precipitate]'`.

## The two execution paths

The computation is a **Celery task** on the `local` queue (`worker_plugin/precipitate.py`), which is
what the brief asked for. But a plain `girder serve` has no broker, and a dashboard that can only
be demonstrated in a full deployment cannot be browser-tested — so `precipitate/jobs.py::schedule`
picks:

| | Celery | Fallback |
|---|---|---|
| When | `workerAvailable()` | otherwise |
| Runs in | the worker process | a daemon thread of the Girder process |
| Reads the image via | `GirderFileId` transform (HTTP download) | `File().open()` into a temp file |
| Writes output via | `RestStore` (`task.girder_client`) | `ModelStore` (Upload/Folder models) |
| Progress via | `task.job_manager.updateProgress` | `Job().updateJob(progress…)` |

Both produce an ordinary Girder job, so the web client polls exactly one thing and never learns
which happened. `runner.py` is the shared body; only the sink differs. **Verified on both paths:**
98/98 browser checks with a real redis + celery worker, and 98/98 with no broker at all, producing
byte-identical numbers. The scale/panel work since then added 20 checks that have been run on the
in-process path only — it changed nothing path-specific (`prepare` also calls `scale`, which is
pure and needs only tifffile, and `analyze` takes one more option), but the Celery path has not
been re-driven at 118.

Points that will bite if changed:

- **`workerAvailable()` also checks that `girder_plugin_worker` is importable.** girder_worker's
  `before_task_publish` hook imports it to create the job document, so without it `.delay()` raises
  instead of scheduling. Core's `is_local_worker_available()` caches for an hour, so a worker
  started just now may not be picked up yet; the capability endpoint reports what it decided.
- **The token passed to the task is created explicitly** with `DATA_READ`/`DATA_WRITE`. The one
  girder_worker attaches by itself is scoped to `jobs.rest.create_job` only, which cannot upload
  results — the task would fail at the last step.
- **The worker needs no database or assetstore access.** Input arrives over HTTP via the transform,
  output leaves over HTTP via `girder_client`. That is what makes it safe to run on a worker that is
  not on this host, and why `RestStore` exists rather than reusing `ModelStore` everywhere.
- **`runLocal` starts a thread and returns.** Girder's local job handler calls it *synchronously*
  from the request thread (`asynchronous=True` on the job document is stored but never acted on by
  core), so running inline would hold the HTTP request open for the whole analysis and give the
  client nothing to poll. The cost is that a restart abandons an in-flight fallback run.
- **Heavy imports stay inside function bodies.** A failure loading a `girder_worker_plugins` entry
  point takes down the *whole worker*, so `worker_plugin/` must not import numpy at module scope.
  Likewise `rest/precipitate.py` imports `presets`, never `analysis` — which is why the preset table
  lives in its own module.

## Storage layout

Everything a run consumes and produces is a Girder object under one folder in the user's own space:

```
<user>/Precipitate Analysis/          <- created on demand, private
    2026-07-26 10-42-13/              <- one folder per run (allowRename handles collisions)
        1_0HR_725C.tif                <- the uploaded micrograph
        preview.png                   <- derived, for picking regions
        results.json                  <- every number the dashboard plots
```

The run folder's `meta.precipitate` is the run's state machine (`new` → `preparing` → `ready` →
`analyzing` → `complete`/`failed`) plus the file ids, `detected` (what the file said about itself —
see below), the `request` that produced the results, and a small
`summary` so the run list need not download every `results.json`. There is deliberately **no new
Mongo model**: a run is a folder, which is what the brief asked for and what gives sharing, ACLs and
deletion for free.

`results.json` is columnar — parallel arrays per region (`x`, `y`, `diameterPx/Nm`,
`spacingPx/Nm`, `nnIndex`) plus stats blocks nested by unit (`mean.px`/`.um`/`.nm`). That shape is
what lets the browser bin histograms and colour a scatter without post-processing. `nnIndex` is
**region-local**: when pooling regions client-side it must be dropped (`ResultsView._particles`
does), or the links would point at the wrong particles.

## Reading the scale and finding the info panel

`precipitate/scale.py`, run once by `prepare` on the array `loadImage` already decoded, and stored
under `state.detected`. Everything it produces is a **finding**, never a decision: it prefills the
form and explains itself, the user overrules it, and `runner._inspect` swallows every exception —
nothing here is needed to analyse anything, so it must never be why a run fails.

**Scale.** Two sources, and they are not equivalent:

| Source | Gives | Notes |
|---|---|---|
| Vendor header | µm/px outright | TESCAN private tag 50431 (`PixelSizeX`), FEI/Thermo tag 34682 (`Scan.PixelWidth`) |
| The drawn bar | pixels only | The length beside it is *text*; `complete: False` says so and the UI asks for it |

`_niceBar` turns a pixel size into the bar the image is actually printed with — if the measured bar
comes to within 3% of a 1/2/5×10ⁿ value, that value is used and the pixel count derived from the
header's exact scale (the Apreo sample: `50 µm = 370.656 px`, not the `20 µm` the range rule alone
would pick). So what the form says is what the user can read off the panel.

The standard `XResolution`/`ResolutionUnit` pair is deliberately **not** read. On all four real
micrographs it holds a screen or print DPI left behind by an export (96, 146, 314 dpi) — a
confidently wrong answer is worse than no answer, which is the rule the whole module follows.

**Info panel.** The header states its height where it has one (TESCAN `ImageStripSize`, FEI's
`Image.ResolutionY` against the file's own height); otherwise it is found in the pixels, on this
discriminator: **specimen has noise, so no single grey level owns more than a few percent of a row;
a drawn panel is a flat fill with text on it, so its background owns most of one.** Measured, that
gap is 0.00-0.02 against 0.47-1.00 — wide enough to sit a threshold in the middle of. Three guards
stop it eating specimen, and each has a test and a real file behind it: a size cap (a white page
margin on an AFM export is 41% of the image), a **sharp boundary** requirement (a panel is pasted
on, a vignette fades in), and a bright-fraction cap on the bar search (an all-black or
white-background panel has a brightest *value*, not brightest *marks*).

**The exclusion crops before the grey conversion**, inside `loadImage(path, excludeBottomPx=)`, and
that is the point rather than an implementation detail: on a 16-bit micrograph the panel's white
bar and black text *are* the array's extremes, so leaving them in means the 0-255 stretch is set by
the panel. Because the crop comes off the bottom, the origin does not move and every region
coordinate still means the pixels it meant on the full image. Verified on the research repo's own
sample: 712 particles with the panel in, 677 with it excluded, against 680 for the hand-cropped
file the published analysis used — 0.02% on mean diameter.

`analyze()` defaults to `excludeBottomPx=0`, so nothing about the fidelity check changes.

## Why a preview PNG

Browsers cannot display the LZW-compressed, sometimes 16-bit TIFFs instrument software writes, but
the user has to see the image to draw regions on it. So `prepare` decodes it and stores a downscaled
grey PNG next to it. It is rendered from `analysis.loadImage`'s output rather than from the raw
file, so the user picks regions on exactly the grey values the detector will work from.

The preview covers the **whole file, info panel included**, and the panel is dimmed by the overlay
rather than cropped out — it is where the instrument printed the scale bar, so hiding it would hide
the one thing the user needs in order to check the scale that was filled in for them. (The scrim is
0.45, chosen to keep white readout text at ~3.5:1 against its background; darker and the printed
bar length stops being readable, which defeats the purpose.)

Regions are stored in **full-resolution image pixels**. The overlay is an SVG whose `viewBox` is the
full-resolution image, so marks are placed in image coordinates and the browser does the scaling;
only pointer input is converted, from the `<img>`'s bounding rectangle. Stroke widths are divided by
the display scale to stay visually constant, which is why a window resize triggers a redraw.

## Charts

Chart.js (~200 kB, tree-shaken to the controllers used), registered in
`dashboards/precipitate/charts.js`. Every chart draws **one** series, so no categorical palette is
in play; magnitude is the only thing colour encodes and it always uses one blue hue, light→dark.
Tokens live in `precipitate/palette.js` and were validated against Girder's white panel (contrast,
CVD separation, lightness band). Mean and median rules are a small `afterDatasetsDraw` plugin rather
than extra datasets, so they stay out of tooltips, and their labels use ink tokens — a colour never
carries text meaning. Histogram bin counts follow the research script's own rule
(`max(10, n // 15)`) so a dataset is divided the way it was in the published figures.

The spacing map's container is given the image's aspect ratio from JS because Chart.js has no
equal-scale option; without it a 512×512 micrograph renders as a 1370×420 smear that reads as a
different microstructure.

## REST API

Precipitate runs live under `/api/v1/precipitate`. Every route is `@access.user` **and** calls
`_dashboard()`, which 403s while the dashboard is disabled or not READable — disabling from the
config page has to disable the functionality, not just hide the card.

| Route | Purpose |
|---|---|
| `GET /precipitate/capability` | dependency probe, worker availability, presets, admin form defaults |
| `GET`/`POST /precipitate/run` | list runs; create a run folder (and the workspace on first use) |
| `GET`/`DELETE /precipitate/run/{id}` | run state; delete the folder |
| `POST /precipitate/run/{id}/prepare` | schedule decode + preview + inspection (`state.detected`) |
| `POST /precipitate/run/{id}/analyze` | schedule the analysis, incl. `excludeBottomPx`; returns the job |

The micrograph is uploaded, and the preview and results downloaded, with **core's own** file
endpoints — this resource never proxies bytes core already serves with the right ACL checks.
`_loadInputFile` insists the file lives in the run's own folder, so a run cannot be pointed at an
arbitrary readable file and have its results stored next to an unrelated image.

## Test-suite gotchas

- The `micrograph` fixture loads `test/browser/micrograph.py` **by path** rather than copying it, so
  the pytest suite and the browser harness analyse the same synthetic image. That generator is
  stdlib-only (it hand-writes an uncompressed TIFF) because `seed.py` must stay dependency-free.
- `micrograph.write(panel=True)` writes a second fixture (`micrographWithPanel`, and
  `fixtures/micrograph-tescan.tif` for the browser): the same specimen plus an info panel with a
  drawn scale bar, and a TESCAN header in tag 50431. `header=False` writes a third
  (`micrograph-stripped.tif`) with the vendor tags left off — the state an image editor leaves a
  file in, where the drawn bar is all there is to go on, and the case four of the six real samples
  were in. Every expected number in
  `test_precipitate_scale.py` is one the generator put there — the panel is `PANEL_HEIGHT` tall and
  the bar is `BAR_PIXELS` post-to-post, which is 1 µm at the `PixelSizeX` the header states. The
  panel's stand-in "text" is deliberately **precipitate-sized dots, not blocks**: with blocks the
  shape gates rejected it all and "excluding the panel changes the numbers" quietly held for the
  wrong reason. It now costs 31 spurious particles, as the real ones do.
- `test_a_run_completes_end_to_end_without_a_worker` waits on a job that a *background thread* is
  running. It polls to a terminal status, which also keeps the thread from touching the database
  after the `db` fixture tears it down.

To drive the **Celery** path in the browser harness instead of the in-process fallback, see
`testing.md`.

## Deployment

Sibling plugins are live-mounted into the `girder` service of the Whole Tale dev stack as
`/girder-plugins/NN-<name>` (see `../deploy-dev/docker-stack.yml`; jsonforms is `05-`). This plugin
**is** mounted into `girder` as `06-girder-dashboards`, but **not into `local_worker`**, which mounts
only `01-`…`05-`. Until it is, `workerAvailable()` will be true (a worker *is* consuming `local`) but
the worker will have no `girder_dashboards` module and the task will fail to dispatch. Two things are
needed to run the Celery path in the dev stack:

1. a `/home/xarth/codes/wholetale-ng/girder-dashboards:/girder-plugins/06-girder-dashboards` volume
   line on the `local_worker` service, and
2. the `precipitate` extra installed in both containers — `wholetale-docker/build_plugins.sh` runs a
   plain `pip install -e .`, so the extra is *not* picked up automatically. Either change that call
   for this plugin or install the wheels into the image.

The base image (`wholetale-docker/Dockerfile.dev`) ships no numpy/scipy/scikit-image; nothing else in
the stack declares them either.

## Verification status (2026-07-26)

- Fidelity: `test/fidelity/compare_to_original.py` reports **ALL MATCH** — all 76 statistics
  identical to the original research scripts (with real OpenCV as the reference decoder) on all four
  sample images, both presets, both spacing modes, down to the blob-candidate counts. Re-run after
  every change to detection or statistics; it caught nothing after the review fixes, which is the
  point.
- API, live against MongoDB: a full run works over HTTP — create run, upload, prepare, analyze with
  two ROIs, `results.json` in the run folder — on **both** execution paths.
- Celery: verified against a real redis broker and `celery -A girder_worker.app worker -Q local`.
  Jobs come back with `handler: celery_handler`, the worker downloads the file through
  `GirderFileId`, writes `preview.png` and `results.json` back over HTTP, and produces numbers
  identical to the in-process path.
- Scale and panel detection: right answers on all six real micrographs available — a TESCAN MIRA3
  with its header (7.7221 nm/px, 90 px panel, drawn bar agreeing to 0.4%), the same image after
  Photoshop stripped the header (bar only, 129 px), another MIRA3 export (120 px panel, 141 px
  bar), an FEI Apreo (134.896 nm/px, 70 px databar, its `|—— 50 µm ——|` measured as one bar) — and
  nothing invented for the two negatives, an already-cropped micrograph and an AFM page export
  whose background is white for 41% of its height.
- Cross-path equivalence: the same run through both paths produces byte-identical summaries
  (`diameterMeanNm` 22.879844961240305, 31 particles) — which is the thing that broke silently
  before the decoder fix below.

## Defects the browser pass found and fixed

Worth knowing about, because none of them were visible to the Python tests or the build:

1. **The precipitate dashboard 401'd for anonymous visitors.** Its card is public (the ACL governs
   who may *open* it), but every endpoint is `@access.user`. It now renders a "sign in" banner with a
   working `g:loginUi` button and makes no requests at all — and `_loadCapability` is single-flight,
   because two overlapping renders fired two capability requests.
2. **Direct file access assumed a session cookie.** The preview `<img>` and the `results.json` fetch
   used bare URLs, which work only because a UI login sets the `girderToken` cookie. In a session
   whose token lives only in `localStorage` — which is exactly how the browser harness authenticates
   — both 401'd, leaving a broken image and no results. The `<img>` now carries `?token=` (as core's
   `EventStream` does) and the fetch goes through `restRequest` so the header is attached.
3. **The spacing map was drawn 1370×420 for a 512×512 image.** Chart.js has no equal-scale option, so
   the particle arrangement was stretched into something that read as a different microstructure.
   The container is now given the image's aspect ratio, and the harness asserts the canvas is square
   for a square micrograph.

The last two were caught by *reading the screenshots and the check output* rather than by an
assertion that already existed — which is the argument for keeping both habits.

## Defects a code review pass found and fixed

Found by review rather than by running the thing, so they are the ones most likely to be
reintroduced. Each has a regression test unless noted.

1. **The Celery path decoded TIFFs with the wrong library.** `GirderFileId` downloads to a temp
   path named after the file's ObjectId — *no extension* — and `loadImage` dispatched on the
   extension, so every worker-path micrograph took the `imageio` branch while the in-process path
   took `tifffile`. For the LZW 16-bit files this pipeline exists for, that is a different decode
   (or an outright failure) depending on where the job happened to run. `loadImage` now sniffs the
   4-byte TIFF signature. Both paths were then re-checked to produce identical numbers to the last
   digit.
2. **An uploaded file name was used to build a local path.** Girder does not reject path separators
   in a file name, so `os.path.join(tempdir, file["name"])` with `name=/etc/cron.d/x.tif` wrote the
   uploaded bytes there as the Girder process user (`os.path.join` discards the directory for an
   absolute second argument). The temp copy is now always `micrograph<ext>`.
3. **A local job could get stuck INACTIVE forever.** `createLocalJob` saves a job as INACTIVE, and
   jobs only allows ERROR from QUEUED/RUNNING — so anything that failed *before* the RUNNING
   transition (a run folder deleted between the POST and the thread starting) made the error report
   itself raise inside a daemon thread: no log, no terminal status, and a client polling forever.
   The transition to RUNNING is now the thread's first act, and every status update in the handler
   is guarded.
4. **A cancelled job was reported as a failed analysis.** Cancelling from Girder's jobs page made
   the final SUCCESS transition illegal, which fell into the `except` and marked the run failed
   even though `results.json` had been written. The success update is now allowed to fail without
   rewriting history.
5. **A failed re-run showed the previous run's results.** `resultFileId`/`summary`/`request`
   survived a failure, so the dashboard rendered a red banner above the *old* run's charts and
   tables. `schedule()` clears them when starting an analysis — and re-reads the folder first,
   because merging into the request's stale snapshot could drop what a concurrent job had written.
6. **Polling never stopped after a failure, and the Run button stayed disabled.** The poll loop
   re-armed while `this.job` was truthy and a failure deliberately kept it truthy: three requests
   and two full re-renders every 1.5 s, forever, with no way to retry but a page reload. The
   failure is now remembered in `jobError`, and `job` is always cleared.
7. **The region rectangle disagreed with what was analysed.** `width` was derived from the
   *unclamped* origin, so a drag ending off the left edge stored a region wider than the image
   while the SVG showed it clipped. Both edges are clamped before the width is taken, and a region
   too small for the server to accept is now rejected at the point of drawing.
8. Smaller ones: `RoiSelector` left `mousemove`/`mouseup` bound to the document after teardown (and
   after a drag that ended outside the window); clicking two runs quickly could render one run's
   preview with another's numbers (request generation stamp); an unblurred scale edit was silently
   reverted by the re-render the first region drag triggers, analysing at the old scale;
   `Math.min(...values)` overflows the argument stack past ~10⁵ particles; a particle with no
   neighbour was painted mid-ramp on the spacing map with a `NaN` tooltip; `DELETE
   /precipitate/run/{id}` deleted a folder subtree under `DATA_WRITE` where core requires
   `DATA_OWN`; the dependency probe omitted `imageio`/`imagecodecs`, so it could report "ok" for an
   environment that cannot decode a compressed TIFF (now asserted against `setup.py`'s extra by a
   test); `presetParams` accepted `None` for parameters used in arithmetic; a 3-page TIFF stack was
   read as three colour channels; and `_localCopy` leaked its temp directory when the assetstore
   read failed.

## The busy state (reported from use, not found by either pass)

Both halves of one omission: **"a step is in flight" was read off `this.job`, which does not exist
until the request that scheduled the step comes back.** Neither the tests nor the review caught it
because on a fast local box that window is a few milliseconds; on a loaded instance it is not.

1. **The upload widget came back while the micrograph was being prepared.** `_watch` re-renders, and
   step 1 chose between the upload widget and the file card on `run.state.inputName` — which the
   *server* sets in the prepare request, so the client's copy of the run says "no file" for the whole
   of the prepare job. The widget was not merely misleading: choosing a file in it creates a *second*
   run and orphans the one being prepared.
2. **The Run button stayed live between the click and the scheduling response**, so a second click
   started a second analysis of the same run.

Both now go through `_busy()` (`job || pending`) and `_lockControls()`, which is applied at the end
of `render()` *and* directly on the click, so the lock lands on the click rather than on the
response. `_pend()` claims the step before the request goes out; `_watch()` hands over to the real
job; `_fail()` releases. `_micrographContext()` is the one place that decides what step 1 shows —
including that a *prepare* failure drops back to the upload widget (no preview, nothing to analyse)
while an *analysis* failure keeps the file (the image was fine). Everything the lock touches
describes what will be run: the form, the preset, the region list and `RoiSelector.setLocked()` —
leave any of it live and the form on screen stops matching the numbers that come back.

## Follow-ups

The detection parameters are overridable through the REST API (`overrides`) but the UI only offers
the two presets — an "advanced" panel would be the natural next step, as would letting a run reuse
an image already in Girder instead of always uploading one. On the scale side,
`scale.readHeaderScale` covers the two vendors there were files to test against; Zeiss (`CZ_SEM`,
tag 34118, which tifffile already parses into `sem_metadata`) is the obvious third and was left out
only because adding an unverifiable branch would undercut the point of the module. Reading the
*printed* bar length would need OCR, which is why it is asked for instead.
