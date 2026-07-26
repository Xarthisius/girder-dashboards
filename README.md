# girder-dashboards

A [Girder 5](https://girder.readthedocs.io/) plugin that adds a new class of entity:
**Dashboards** — lightweight, interactive UIs operating on the data gathered in a Girder
instance.

A dashboard is a small JS app. It gets a card in a gallery reachable from a **Dashboards**
entry in the left sidebar, and when opened it takes over the whole window: none of Girder's
usual header, navigation or footer, just the dashboard and a top bar with the way back.

## What you get

- **Sidebar entry** — `Dashboards` in the global navigation, routing to `#dashboards`.
- **Card gallery** (`#dashboards`) — image, name, description, an *Open* action for users with
  `READ` access, and a settings gear for users with `ADMIN` access.
- **Config page** (`#plugins/dashboards/config`, linked from *Admin console → Plugins*) — a site
  admin enables/disables each installed dashboard, edits its card, edits its settings, and
  controls who may open it.
- **Full-window runner** (`#dashboard/<id>`) — the dashboard renders under Girder's
  `Layout.EMPTY`, with a *Dashboards* back link in its own top navbar.
- **Two bundled dashboards** — *Data Overview*, a small worked example, and
  *Precipitate Analysis*, a real analysis pipeline (see below).

## The Precipitate Analysis dashboard

An implementation of the precipitate-detection and inter-particle-spacing pipeline from
[Taheri-Mousavi Laboratory / Image-analysis-precipitate-detection-and-particle-spacing-estimation](https://github.com/Taheri-Mousavi-Laboratory/Image-analysis-precipitate-detection-and-particle-spacing-estimation),
turned into something you can run from a browser:

1. **Upload** an SEM/TEM micrograph (TIFF, including the LZW and 16-bit variants instrument
   software emits).
2. **Set the scale** — the length of the image's scale bar in µm, and how many pixels it spans.
3. **Choose the spacing measure** — centre-to-centre or edge-to-edge.
4. **Select regions of interest** by dragging on the image, as many as you like, or select none
   and the whole image is analysed as one region. Each region is detected and measured on its
   own and then pooled, exactly as the original treated its three separate ROI files.
5. **Wait** — the computation is a Celery task, reported as a normal Girder job with progress.
6. **Read the numbers** — size and spacing histograms with mean/median rules, a spacing map, the
   detection and nearest-neighbour overlays on the micrograph itself, and pooled and per-region
   statistics tables. Everything is drawn in the browser from stored numbers; the backend
   produces no figures.

Every input and output is a Girder object in a folder of the user's own — `Precipitate
Analysis/<run>/` in their user space, holding the uploaded micrograph, the preview the backend
rendered for region selection, and `results.json`, which carries per-particle arrays
(`x`, `y`, `diameterNm`, `spacingNm`, `nnIndex`, …) plus per-region and pooled statistics.

The two detection tunings published with the research code are offered as presets: **fine** for
small dim precipitates (725 °C, 1 hr) and **coarse** for large bright ones (725 °C, 5 hr). The
port is numerically faithful — see `test/fidelity/compare_to_original.py`, which compares every
reported statistic against the original scripts.

### Installing it

The analysis stack is an **extra**, so a Girder that only wants the other dashboards is not made
to carry scikit-image:

```bash
pip install 'girder-dashboards[precipitate]'
```

Install it in **both** the Girder environment and the Celery worker environment. The dashboard
reports missing dependencies on its own page rather than failing a run, and falls back to running
the computation in the Girder process when no Celery worker is consuming the `local` queue — so it
also works on a plain `girder serve`.

## How a dashboard is put together

A dashboard has two halves that meet at a shared **key**:

| Half | Where | Responsibility |
|---|---|---|
| Declaration | Python, `registerDashboard()` | That the dashboard exists, and what its card says |
| Implementation | `web_client`, `registerDashboard()` | The Backbone view that renders it |

The Python half is what makes dashboards discoverable server-side, so the config page can list
them and cards can render without every dashboard's JS having to be parsed first.

Persistent state lives in a `dashboard` document, one per registered key. It holds the parts an
admin owns — `enabled`, the ACL, the card metadata, and a free-form `settings` object handed to
the view at runtime. Documents are created by an atomic upsert at plugin load, so a restart or a
redeploy never clobbers an admin's edits, and concurrent workers can't race into duplicates.

New dashboards start **disabled but publicly readable**: enabling one is the only step needed to
offer it to everyone, and narrowing the ACL is how you restrict it.

## Adding a dashboard from your own plugin

Server side, from your plugin's `load()`:

```python
from girder.plugin import GirderPlugin, getPlugin
from girder_dashboards import registerDashboard


class MyPlugin(GirderPlugin):
    def load(self, info):
        getPlugin("dashboards").load(info)
        registerDashboard(
            "sample-throughput",
            name="Sample Throughput",
            description="Samples registered per week, by instrument.",
            image="https://example.org/card.png",  # optional; falls back to `icon`
            icon="icon-chart-line",
            settings={"weeks": 12},
        )
```

Client side, from your `web_client` entry point:

```js
import SampleThroughputView from './dashboards/SampleThroughputView';

girder.plugins.dashboards.registerDashboard('sample-throughput', {
    view: SampleThroughputView
});
```

The view is instantiated with `{el, parentView, dashboard, settings}`, where `dashboard` is the
`DashboardModel` and `settings` is its (admin-editable) settings object. Render into `el` and you
own the viewport below the top bar.

Registering after this plugin has loaded is fine — new registrations are provisioned immediately
rather than at the next restart.

## REST API

### Dashboards

All routes are under `/api/v1/dashboard`.

| Route | Access | Purpose |
|---|---|---|
| `GET /dashboard` | public | Dashboards readable by the caller. `includeDisabled` / `includeUnavailable` are site-admin only. |
| `GET /dashboard/{id}` | `READ` | A single dashboard. |
| `PUT /dashboard/{id}` | `ADMIN` | Change name, description, image, icon, enabled, settings. |
| `PUT /dashboard/{id}/reset` | `ADMIN` | Restore the card metadata and settings the plugin declared. |
| `DELETE /dashboard/{id}` | site admin | Prune a document whose implementation is gone. |
| `GET`/`PUT /dashboard/{id}/access` | `ADMIN` | Read/set the ACL. |

Every response carries an extra `available` flag: `false` means the document's key no longer has a
registered implementation, e.g. the plugin that shipped it was uninstalled. Such dashboards are
hidden from the gallery but still listed on the config page so an admin can remove them.

### Precipitate analysis runs

Under `/api/v1/precipitate`. Every route requires a signed-in user with `READ` on the dashboard,
and refuses to work while the dashboard is disabled.

| Route | Purpose |
|---|---|
| `GET /precipitate/capability` | Whether the analysis dependencies are installed, whether a Celery worker is available, the detection presets, and the admin-set form defaults |
| `GET`/`POST /precipitate/run` | List runs; create a folder for a new one |
| `GET`/`DELETE /precipitate/run/{id}` | One run's state; delete it and its contents |
| `POST /precipitate/run/{id}/prepare` | Schedule the decode + preview step for an uploaded image |
| `POST /precipitate/run/{id}/analyze` | Schedule the analysis: scale, spacing mode, preset, regions |

Both `POST`s return a Girder job to follow. The micrograph is uploaded with Girder's own file
endpoints, and the preview and results are downloaded with them too — this resource never proxies
bytes that core already serves with the right ACL checks.

## Development

Server side:

```bash
tox -e lint          # ruff check .
tox -e pytest        # needs a running MongoDB
```

Web client, from `girder_dashboards/web_client/`:

```bash
npm ci
npm run build        # vite build -> dist/, required before the server can serve the assets
npm run dev          # vite build --watch
```

`registerPluginStaticContent` in `girder_dashboards/__init__.py` serves the built
`dist/girder-plugin-dashboards.umd.cjs` and `dist/style.css`, so rebuild after changing
`web_client` source and reload Girder.

Build the web client **before** running the Python tests: the plugin hashes the files in
`web_client/dist` at load time, so without them every test that starts a server fails.

End-to-end browser check, against a running Girder:

```bash
(cd test/browser && npm ci && npx playwright install chromium)   # once
python3 test/browser/seed.py     # admin, assetstore, enabled dashboards, sample data
node test/browser/verify.cjs
```

It drives headless Chrome through the gallery, the runner and the config page as both an anonymous
and an admin user, then runs a whole precipitate analysis through the UI — upload, region
selection, job, plots and tables — and fails on any console error or failed request. Screenshots
are written to `test/browser/screenshots/`. Configure with `GIRDER_URL`, `GIRDER_ADMIN`,
`GIRDER_PASSWORD`. Both scripts are idempotent, so they can be re-run against the same instance.

To exercise the Celery path rather than the in-process fallback, point Girder and a worker at the
same broker before seeding:

```bash
export GIRDER_WORKER_BROKER=redis://127.0.0.1:6379/1
export GIRDER_WORKER_BACKEND=redis://127.0.0.1:6379/1
girder serve --host 127.0.0.1 --port 8989 &
celery -A girder_worker.app worker -Q local -c 2 -l INFO &
```

The dashboard says which path it is using, and the harness asserts on that line either way.

CI (`.github/workflows/build-test.yaml`) runs lint, the Python tests, and the browser check on
every push to `main` and every pull request.

## License

BSD-3-Clause — see [LICENSE](LICENSE).
