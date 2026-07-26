# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`girder-dashboards` — a **Girder 5** plugin adding a new class of entity: **Dashboards**,
lightweight interactive UIs operating on the data gathered in a Girder instance. It ships a
Python server plugin plus a Backbone `web_client` bundled with Vite.

Built to satisfy `TASK.md` (kept in-repo as the original brief):

1. `Dashboards` entry in the left sidebar → dedicated list.
2. Card view: image, name, description, "run" action for READ users, settings gear for admins.
3. Plugin config page where a site admin enables/disables available dashboards.
4. Dashboards render **without** the standard Girder layout, with a "go back" link in their own
   top navbar.

Two dashboards ship with it: **Data Overview** (a small worked example) and **Precipitate
Analysis** (a real analysis pipeline with a Celery backend — see its own section below).

## Girder core architecture (upstream)

For how Girder core works — models (`Model`/`AccessControlledModel`, `exposeFields`), REST
(`Resource`, `autoDescribeRoute`, `@access.*`), the plugin/events/settings systems, and
file:line anchors into upstream source — see the map in the sibling `girder` checkout:

@../girder/CLAUDE.md

`../girder-jsonforms/` is the closest reference for an out-of-tree plugin of this shape;
`../girder/plugins/item_licenses/` is the smallest complete in-tree example (packaging, config
page, `exposePluginConfig`).

## Core design decision

**Dashboards are code-registered and admin-toggled**, *not* admin-created documents. This
follows from the brief: requirement 3 says "enable/disable **available** dashboards" (a fixed
set exists) and requirement 4 says dashboards are "js apps/views" (they are code).

A dashboard has two halves meeting at a shared **key**:

| Half | Where | Owns |
|---|---|---|
| Declaration | Python `registerDashboard()` | That it exists; default card metadata + settings |
| Implementation | web_client `registerDashboard()` | The Backbone view that renders it |

The Python half makes dashboards discoverable server-side, so the config page can list them and
cards can render without parsing every dashboard's JS first.

A `dashboard` **document** (one per registered key) holds only what an *admin* owns: `enabled`,
the ACL, card metadata overrides, and a free-form `settings` object handed to the view at
runtime. Documents are created by an atomic `$setOnInsert` upsert at plugin load — so concurrent
workers can't race into duplicates and a redeploy never clobbers admin edits. New dashboards
start **disabled but publicly readable**: enabling is the only step to offer one to everyone;
narrowing the ACL is how you restrict it.

If this ever needs to change to "admin creates N instances from a type picker", the seam is
`Dashboard.provision()` plus the `available` flag — the rest of the stack is agnostic.

## Layout

```
girder_dashboards/
  __init__.py          # DashboardsPlugin.load() — composition root
  registry.py          # DashboardDefinition, registerDashboard(), listener hook
  builtin.py           # the bundled "data-overview" dashboard + its inline-SVG card image
  models/dashboard.py  # Dashboard(AccessControlledModel) + provision/resetToDefaults/listForUser
  rest/dashboard.py    # Dashboard(Resource) at /api/v1/dashboard
  rest/precipitate.py  # Precipitate(Resource) at /api/v1/precipitate
  precipitate/         # the Precipitate Analysis dashboard's whole server slice
    __init__.py        #   declaration, card SVG, dependency probe
    presets.py         #   the two published tunings + request -> params (no heavy imports)
    analysis.py        #   the algorithm: numpy/scipy/skimage in, plain dicts out
    scale.py           #   pixel scale from the vendor header or the drawn bar; info panel
    preview.py         #   micrograph -> downscaled PNG for region selection
    store.py           #   folder layout + the two write sinks (models / girder_client)
    runner.py          #   the two steps, written once for both execution paths
    jobs.py            #   schedule on Celery, or in a Girder thread when there is no worker
  worker_plugin/       # girder_worker_plugins entry point + the two @app.task functions
  tests/               # conftest.py + test_dashboard.py, test_precipitate_analysis.py,
                       #   test_precipitate_rest.py, test_precipitate_scale.py (120 tests)
test/browser/          # end-to-end browser check: seed.py + micrograph.py + verify.cjs (118 checks)
test/fidelity/         # compare_to_original.py — the port vs the research scripts, by hand
.github/workflows/     # build-test.yaml: `test` job (lint + pytest), `browser` job (e2e)
  web_client/
    main.js            # entry: sidebar wrap, built-in registration, registerPluginNamespace
    routes.js          # #dashboards, #dashboard/:id (EMPTY layout), #plugins/dashboards/config
    registry.js        # client-side key -> view registry
    models/ collections/
    views/             # DashboardListView, DashboardRunView, ConfigView, EditDashboardWidget
    dashboards/
      DataOverviewDashboard.js       # worked example
      PrecipitateDashboard.js        # the stepper: upload -> scale -> regions -> job -> results
      precipitate/
        RoiSelector.js               # image + rubber-band regions + detection/NN overlays,
                                     #   the excluded info panel, the measured scale bar
        ResultsView.js               # tiles, tables, and the charts
        charts.js                    # Chart.js histograms + spacing map
        palette.js                   # validated colour/ink tokens
    templates/*.pug  stylesheets/*.styl
    vite.config.ts     # UMD lib build -> dist/girder-plugin-dashboards.umd.cjs + style.css
```

### Notable implementation points

- **`registry.addRegistrationListener`** — `load()` registers `DashboardModel().provision` as a
  listener *before* calling `provisionAll()`. That is what makes a plugin loading **after** this
  one get provisioned immediately instead of only at the next restart. `provisionAll()` covers
  anything registered earlier. Do not remove one without the other.
- **Sidebar** — `main.js` wraps `GlobalNavView.initialize` and pushes into `defaultNavItems`
  (deliberately *not* the post-render DOM surgery `girder-jsonforms/web_client/main.js` uses):
  it gets active-link highlighting for free and survives the re-renders that login/logout fire.
  `routes.js` triggers `g:highlightItem` with `'DashboardsView'` (core slices off the last 4
  chars to match `g-name="Dashboards"`).
- **No-layout runner** — `routes.js` passes `{layout: Layout.EMPTY}` to `g:navigateTo`.
  `App._defaultLayout.hide()` hides `#g-app-header-container,#g-global-nav-container,
  #g-app-footer-container`; `DashboardRunView` then owns the viewport and supplies the back link.
  `#g-dialog-container` is a sibling of the body container, so modals still work in EMPTY layout.
- **`available` flag** — computed in `rest/dashboard.py::_serialize`, not stored. `False` means
  the key has no registered implementation any more (plugin uninstalled). Such dashboards are
  hidden from the gallery, still listed on the config page, and are the *only* ones `DELETE`
  accepts (registered ones would just be re-provisioned at startup — disable them instead).
- **Card images** are inline SVG data URIs in `builtin.py`, avoiding static-asset plumbing.
  Admins can point `image` at any URL; empty string falls back to the fontello `icon`.
- **`public`/`publicFlags` are exposed** at READ level and the access endpoint accepts
  `publicFlags`, purely so core's `AccessWidget` works unmodified.
- **Settings travel as a JSON-encoded form param** (`jsonParam(requireObject=True)`), which is
  why `EditDashboardWidget.save()` uses `restRequest` rather than `model.save()`.

## The Precipitate Analysis dashboard

A port of the research code at
`https://github.com/Taheri-Mousavi-Laboratory/Image-analysis-precipitate-detection-and-particle-spacing-estimation`
(kept locally at `/tmp/Image-analysis-...` while this was written — clone it again if you need to
re-run the fidelity check). Detects precipitates in an SEM/TEM micrograph and measures equivalent
diameter and nearest-neighbour spacing.

### What changed relative to the original scripts

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
repo and OpenCV, so it cannot run in CI — run it by hand after touching detection or statistics.

### The two execution paths

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

### Storage layout

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

### Reading the scale and finding the info panel

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

### Why a preview PNG

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

### Charts

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

All under `/api/v1/dashboard`. Every response carries `available`.

| Route | Access | Purpose |
|---|---|---|
| `GET /dashboard` | public | READ-visible dashboards; enabled-only. `includeDisabled`/`includeUnavailable` are site-admin only (403 otherwise) |
| `GET /dashboard/{id}` | READ | One dashboard |
| `PUT /dashboard/{id}` | doc ADMIN | name, description, image, icon, enabled, settings |
| `PUT /dashboard/{id}/reset` | doc ADMIN | Restore declared defaults; leaves `enabled` + ACL alone |
| `DELETE /dashboard/{id}` | site admin | Only when `available` is false |
| `GET`/`PUT /dashboard/{id}/access` | doc ADMIN | ACL |

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

## Commands

Python (a repo-local `venv/` already exists with `girder`, `pytest-girder`, `ruff`, and this
package installed editable):

```bash
venv/bin/pytest girder_dashboards/tests -q     # 77 tests; needs MongoDB on :27017
venv/bin/ruff check .
venv/bin/ruff format girder_dashboards
tox -e pytest   /   tox -e lint                # equivalents (pytest env pulls the extra)
```

Dependencies are installed from **PyPI**, not as editable installs from the sibling `../girder`
checkouts (`girder-jobs` and `girder-plugin-worker` came from PyPI at 5.0.13). The analysis stack is
the `precipitate` extra: `venv/bin/pip install -e '.[precipitate]'`.

The fidelity check against the original research code, which needs that repo and OpenCV and so is
not part of the suite:

```bash
venv/bin/pip install opencv-python-headless pandas   # reference only, never a plugin dependency
venv/bin/python test/fidelity/compare_to_original.py /path/to/research/repo
```

Web client, from `girder_dashboards/web_client/` (`node_modules/` already installed):

```bash
npm run build     # vite build -> dist/
npm run dev       # vite build --watch
```

**Build before running the Python tests.** `registerPluginStaticContent` md5-hashes every file in
`web_client/dist` at load time to build cache-busting URLs, so with no bundle present `load()`
raises `FileNotFoundError` and every test using the `server` fixture errors out (the pure-registry
and pure-algorithm ones still pass, which makes the cause easy to misread). This is why CI builds
the web client before `tox -e pytest`. `dist/` is gitignored but `MANIFEST.in` ships it, so build
before packaging too.

### Test-suite gotchas

- `tests/conftest.py::cleanRegistry` is `autouse` and snapshots/restores `registry._dashboards`
  and `registry._listeners`. Without it the "unregister a dashboard" tests leak into every later
  test. Keep it if you add tests that mutate the registry.
- `pytest-girder` resets `plugin._pluginRegistry` per test, so `load()` (and therefore
  `provisionAll()`) re-runs against each test's fresh database. That is why the `dataOverview`
  fixture can just `findOne` the provisioned doc.
- Tests are marked module-wide via `pytestmark = pytest.mark.plugin("dashboards")`.
- **Assertions about one dashboard must be keyed, not positional.** Two dashboards now ship, so
  `dashboards[0]` and `len(...) == 1` are wrong; `test_dashboard.py::_byKey` and `verify.cjs`'s
  `cardFor`/`configRowFor` exist for that reason.
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

### Local smoke run

```bash
GIRDER_MONGO_URI=mongodb://localhost:27017/girder_dashboards_smoke \
  venv/bin/girder serve --host 127.0.0.1 --port 8989
```

The installed girder wheel serves the built core web client at `/`, so the whole UI is
reachable. First `POST /api/v1/user` creates a site admin.

### Browser verification

`test/browser/verify.cjs` drives headless Chrome and asserts all four TASK.md requirements end to
end, plus a whole precipitate analysis run and its failure path (98 checks), across an anonymous
session, an admin session and an analysis session. It also **fails on any console error, page
error or failed request**, which is how both 401 defects below were caught.

```bash
# one-off: install the harness's own playwright
(cd test/browser && npm ci && npx playwright install chromium)

# then, against a running Girder:
GIRDER_MONGO_URI=mongodb://localhost:27017/girder_dashboards_ci \
  venv/bin/girder serve --host 127.0.0.1 --port 8989 > girder.log 2>&1 &
python3 test/browser/seed.py       # admin, assetstore, both dashboards enabled, sample data
node test/browser/verify.cjs       # 118/118 expected
```

`seed.py` is stdlib-only (no venv needed) and idempotent, so it works on a fresh *or* dirty
instance. It also creates a **filesystem assetstore** (under `build/assetstore`) — a fresh Girder
has none, and without one the micrograph upload 500s — and writes the synthetic micrograph to
`test/browser/fixtures/`. `verify.cjs` leaves both dashboards re-enabled, so it too is repeatable.

To drive the **Celery** path instead of the in-process fallback, start Girder and a worker against
the same broker before seeding:

```bash
export GIRDER_WORKER_BROKER=redis://127.0.0.1:6379/1 GIRDER_WORKER_BACKEND=redis://127.0.0.1:6379/1
venv/bin/girder serve --host 127.0.0.1 --port 8989 > girder.log 2>&1 &
venv/bin/celery -A girder_worker.app worker -Q local -c 2 -l INFO > worker.log 2>&1 &
```

The harness asserts on the dashboard's own "where this runs" line, so it passes either way and the
log says which path was exercised. Both were run at 98/98, before the scale/panel checks were added
— re-running the Celery path at 118 is worth doing next time a broker is to hand.

Configure both via `GIRDER_URL` / `GIRDER_ADMIN` / `GIRDER_PASSWORD`, plus `SHOTS` for
screenshots, which land in `test/browser/screenshots/` (gitignored). **Read the screenshots** —
DOM assertions alone won't catch a collapsed grid or a clipped dialog, which is exactly how the
modal defect below was found.

`playwright` resolves from `test/browser/node_modules` automatically (Node walks up from the
script's own directory); `PLAYWRIGHT_PATH` overrides, and the sibling `girder/girder/web`
copy is the last fallback. The script prefers the system Chrome and falls back to playwright's
bundled chromium, logging which one it used. Both paths are verified. Either way it is a
throwaway profile, never the user's own Chrome session.

## CI

`.github/workflows/build-test.yaml`, modelled on `../girder-jsonforms/.github/workflows/build-test.yaml`,
runs on pushes to `main`, on PRs, and on demand. Two jobs, both on `ubuntu-24.04` with a
`mongo:4.4` service (matching the version everything here was verified against):

- **`test`** — `npm ci && npm run build`, then `tox -e lint`, then `tox -e pytest`. The build step
  is load-bearing, not cosmetic; see the warning in Commands above. `[testenv:pytest]` declares
  `extras = precipitate`, without which the analysis tests cannot import numpy.
- **`browser`** — `pip install -e '.[precipitate]'`, build the web client, `npm ci` + `playwright
  install chromium` in `test/browser`, start `girder serve` on 8989 with a wait loop, `seed.py`, then
  `verify.cjs`. Screenshots and `girder.log` upload as an artifact on every run (`if: always()`),
  which is what you want when a headless failure needs diagnosing. There is no broker in CI, so this
  job exercises the in-process fallback; the Celery path has to be driven locally (see Browser
  verification above).

Both job sequences were rehearsed locally command-for-command against a fresh database before
being committed. No secrets are used, so the workflow runs on forks. Coverage XML is produced
(`--cov-report=xml`) but nothing uploads it — add a Codecov step plus `CODECOV_TOKEN` if wanted.

There is deliberately **no release workflow**. jsonforms has one that publishes to PyPI via
trusted publishing on tag pushes; adding the equivalent here means claiming the
`girder-dashboards` PyPI name and configuring a `pypi` environment, which is the maintainer's
call to make, not something to wire up unasked.

## Conventions

- **Python**: double quotes, ruff (line-length 88, `select = ["E4","E7","E9","F","I"]`), camelCase
  method names to match Girder core. Mirrors `../girder-jsonforms`.
- **JS**: `const X = girder.<ns>.<Thing>` at module top — the bundle takes **no** `@girder/core`
  import; core is the runtime `girder` global, injected before plugin scripts load. `$.fn.girderModal`
  and `$.fn.girderEnable` are available without importing anything.
- **Styles**: Stylus, tokens in `stylesheets/variables.styl`, all class names `g-`-prefixed.

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

## Status (2026-07-26)

**Feature-complete and verified end to end, including the Precipitate Analysis dashboard.**

- Server: **120** pytest tests pass, `ruff check` clean, `tox -e lint,pytest` rehearsed as CI runs
  it.
- Fidelity: `test/fidelity/compare_to_original.py` reports **ALL MATCH** — all 76 statistics
  identical to the original research scripts (with real OpenCV as the reference decoder) on all four
  sample images, both presets, both spacing modes, down to the blob-candidate counts. Re-run after
  every change to detection or statistics; it caught nothing after the review fixes, which is the
  point.
- Build: `npm run build` succeeds (232 kB UMD / 74 kB gzipped, up from 28 kB — that is Chart.js —
  plus 14 kB CSS); the bundle still references only the `girder` global.
- API, live against MongoDB: both dashboards provision, `system/plugin_static_files` lists both
  assets, and a full run works over HTTP — create run, upload, prepare, analyze with two ROIs,
  `results.json` in the run folder — on **both** execution paths.
- Celery: verified against a real redis broker and `celery -A girder_worker.app worker -Q local`.
  Jobs come back with `handler: celery_handler`, the worker downloads the file through
  `GirderFileId`, writes `preview.png` and `results.json` back over HTTP, and produces numbers
  identical to the in-process path.
- Browser: **118/118** checks in `test/browser/verify.cjs`, screenshots reviewed, run against a
  brand-new database. The 98 that predate the scale/panel work were run twice, once with a Celery
  worker and once without; the 20 new ones, on the in-process path only.
- Scale and panel detection: right answers on all six real micrographs available — a TESCAN MIRA3
  with its header (7.7221 nm/px, 90 px panel, drawn bar agreeing to 0.4%), the same image after
  Photoshop stripped the header (bar only, 129 px), another MIRA3 export (120 px panel, 141 px
  bar), an FEI Apreo (134.896 nm/px, 70 px databar, its `|—— 50 µm ——|` measured as one bar) — and
  nothing invented for the two negatives, an already-cropped micrograph and an AFM page export
  whose background is white for 41% of its height.
- Cross-path equivalence: the same run through both paths produces byte-identical summaries
  (`diameterMeanNm` 22.879844961240305, 31 particles) — which is the thing that broke silently
  before the decoder fix below.

### Defects the browser pass found and fixed

Worth knowing about, because none of them were visible to the Python tests or the build:

1. **`DataOverviewDashboard` fired `GET /user` anonymously.** That endpoint is `@access.user`, so
   every anonymous visit logged a 401 and showed a `—` tile. Now the tile list is filtered by
   `getCurrentUser()` before any request goes out (`TILES[].requiresUser`); the `.fail()` fallback
   stays for genuine errors. `GET /collection` and `GET /group` *are* `@access.public` — verified,
   don't "fix" those.
2. **The settings dialog outgrew a short viewport,** and Bootstrap 3 scrolls the whole modal,
   pushing the title and Save button off screen. `.modal-body` now has
   `max-height: calc(100vh - 190px)` with internal scroll. The harness asserts both stay in the
   viewport, so a regression here fails the run.
3. **The precipitate dashboard 401'd for anonymous visitors.** Its card is public (the ACL governs
   who may *open* it), but every endpoint is `@access.user`. It now renders a "sign in" banner with a
   working `g:loginUi` button and makes no requests at all — and `_loadCapability` is single-flight,
   because two overlapping renders fired two capability requests.
4. **Direct file access assumed a session cookie.** The preview `<img>` and the `results.json` fetch
   used bare URLs, which work only because a UI login sets the `girderToken` cookie. In a session
   whose token lives only in `localStorage` — which is exactly how the browser harness authenticates
   — both 401'd, leaving a broken image and no results. The `<img>` now carries `?token=` (as core's
   `EventStream` does) and the fetch goes through `restRequest` so the header is attached.
5. **The spacing map was drawn 1370×420 for a 512×512 image.** Chart.js has no equal-scale option, so
   the particle arrangement was stretched into something that read as a different microstructure.
   The container is now given the image's aspect ratio, and the harness asserts the canvas is square
   for a square micrograph.

Only 4 and 5 were caught by *reading the screenshots and the check output* rather than by an
assertion that already existed — which is the argument for keeping both habits.

### Defects a code review pass found and fixed

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

`LICENSE` is BSD-3-Clause, matching `setup.py` and the sibling plugins. Copyright is attributed to
**data-exp-lab, 2026**, following `../girder-jsonforms/LICENSE`; change the holder if that is
wrong. Note that jsonforms' copy has a stale "Neither the name of girder_wholetale" clause from
being copied out of girder-wholetale — ours names `girder-dashboards`, so don't "sync" it back.

**Possible follow-ups:** mount the plugin into `local_worker` and install the `precipitate` extra in
the dev stack (see Deployment above); a release workflow if the package should go to PyPI (see CI
above); Codecov upload. For the precipitate dashboard specifically: the detection parameters are
overridable through the REST API (`overrides`) but the UI only offers the two presets — an "advanced"
panel would be the natural next step, as would letting a run reuse an image already in Girder instead
of always uploading one. On the scale side, `scale.readHeaderScale` covers the two vendors there
were files to test against; Zeiss (`CZ_SEM`, tag 34118, which tifffile already parses into
`sem_metadata`) is the obvious third and was left out only because adding an unverifiable branch
would undercut the point of the module. Reading the *printed* bar length would need OCR, which is
why it is asked for instead.
