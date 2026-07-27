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
- **One bundled dashboard** — *Data Overview*, a small worked example. Real dashboards are
  separate plugins (see below).

## Dashboards live in their own plugins

Beyond the bundled example, a dashboard is a **separate pip-installable plugin** that declares
itself against this one. Installing it adds a card; uninstalling it takes the card away and leaves
the admin's settings behind in case it comes back.

```bash
pip install girder-dashboards girder-dashboards-precipitate
```

[`girder-dashboards-precipitate`](https://github.com/Xarthisius/girder-dashboards-precipitate) is
the worked example of a substantial one: precipitate detection and inter-particle spacing
measurement on SEM/TEM micrographs, with a Celery backend, its own REST resource and its own
scientific-stack extra. None of that is this package's concern — which is the point.

To write your own, see **`docs/extending.md`**. It is a step-by-step recipe with a complete
copy-pasteable example, and it was verified by building that example and driving it in a browser.


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
rather than at the next restart. **Call `getPlugin("dashboards").load(info)` first**, though: that
is what puts this plugin's bundle ahead of yours in the browser, and your entry point reads
`girder.plugins.dashboards` at module scope. `docs/extending.md` has the full story, including
packaging, the Vite config and how to test it.

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
python3 test/browser/seed.py     # admin, the bundled dashboard enabled, sample collections
node test/browser/verify.cjs
```

It drives headless Chrome through the gallery, the runner and the config page as both an anonymous
and an admin user, and fails on any console error or failed request. Screenshots are written to
`test/browser/screenshots/`. Configure with `GIRDER_URL`, `GIRDER_ADMIN`, `GIRDER_PASSWORD`. Both
scripts are idempotent, so they can be re-run against the same instance, and the harness adapts to
whatever other dashboard plugins are installed alongside. Those plugins bring their own harness for
their own dashboard.

CI (`.github/workflows/build-test.yaml`) runs lint, the Python tests, and the browser check on
every push to `main` and every pull request.

## License

BSD-3-Clause — see [LICENSE](LICENSE).
