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
  tests/               # conftest.py (registry isolation) + test_dashboard.py (22 tests)
test/browser/          # end-to-end browser check: seed.py + verify.cjs (54 checks)
.github/workflows/     # build-test.yaml: `test` job (lint + pytest), `browser` job (e2e)
  web_client/
    main.js            # entry: sidebar wrap, built-in registration, registerPluginNamespace
    routes.js          # #dashboards, #dashboard/:id (EMPTY layout), #plugins/dashboards/config
    registry.js        # client-side key -> view registry
    models/ collections/
    views/             # DashboardListView, DashboardRunView, ConfigView, EditDashboardWidget
    dashboards/        # DataOverviewDashboard.js — worked example
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

## Commands

Python (a repo-local `venv/` already exists with `girder`, `pytest-girder`, `ruff`, and this
package installed editable):

```bash
venv/bin/pytest girder_dashboards/tests -q     # 22 tests; needs MongoDB on :27017
venv/bin/ruff check .
venv/bin/ruff format girder_dashboards
tox -e pytest   /   tox -e lint                # equivalents
```

Web client, from `girder_dashboards/web_client/` (`node_modules/` already installed):

```bash
npm run build     # vite build -> dist/
npm run dev       # vite build --watch
```

**Build before running the Python tests.** `registerPluginStaticContent` md5-hashes every file in
`web_client/dist` at load time to build cache-busting URLs, so with no bundle present `load()`
raises `FileNotFoundError` and 17 of the 22 tests error out (the 5 pure-registry ones still pass,
which makes the cause easy to misread). This is why CI builds the web client before `tox -e pytest`.
`dist/` is gitignored but `MANIFEST.in` ships it, so build before packaging too.

### Test-suite gotchas

- `tests/conftest.py::cleanRegistry` is `autouse` and snapshots/restores `registry._dashboards`
  and `registry._listeners`. Without it the "unregister a dashboard" tests leak into every later
  test. Keep it if you add tests that mutate the registry.
- `pytest-girder` resets `plugin._pluginRegistry` per test, so `load()` (and therefore
  `provisionAll()`) re-runs against each test's fresh database. That is why the `dataOverview`
  fixture can just `findOne` the provisioned doc.
- Tests are marked module-wide via `pytestmark = pytest.mark.plugin("dashboards")`.

### Local smoke run

```bash
GIRDER_MONGO_URI=mongodb://localhost:27017/girder_dashboards_smoke \
  venv/bin/girder serve --host 127.0.0.1 --port 8989
```

The installed girder wheel serves the built core web client at `/`, so the whole UI is
reachable. First `POST /api/v1/user` creates a site admin.

### Browser verification

`test/browser/verify.cjs` drives headless Chrome and asserts all four TASK.md requirements
end to end (54 checks), in both an anonymous and an admin session. It also **fails on any
console error, page error or failed request**, which is how the 401 defect below was caught.

```bash
# one-off: install the harness's own playwright
(cd test/browser && npm ci && npx playwright install chromium)

# then, against a running Girder:
GIRDER_MONGO_URI=mongodb://localhost:27017/girder_dashboards_ci \
  venv/bin/girder serve --host 127.0.0.1 --port 8989 > girder.log 2>&1 &
python3 test/browser/seed.py       # creates the admin, enables the dashboard, adds collections
node test/browser/verify.cjs       # 54/54 expected
```

`seed.py` is stdlib-only (no venv needed) and idempotent, so it works on a fresh *or* dirty
instance. `verify.cjs` leaves the dashboard re-enabled, so it too is repeatable.

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
  is load-bearing, not cosmetic; see the warning in Commands above.
- **`browser`** — `pip install -e .`, build the web client, `npm ci` + `playwright install
  chromium` in `test/browser`, start `girder serve` on 8989 with a wait loop, `seed.py`, then
  `verify.cjs`. Screenshots and `girder.log` upload as an artifact on every run (`if: always()`),
  which is what you want when a headless failure needs diagnosing.

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
`/girder-plugins/NN-<name>` (see `../deploy-dev/docker-stack.yml`; jsonforms is `05-`). This
plugin is **not wired in yet** — adding it means a volume line like
`/home/xarth/codes/wholetale-ng/girder-dashboards:/girder-plugins/06-girder-dashboards` in each
girder-ish service, and `../deploy-dev/CLAUDE.md` documents the stack.

## Status (2026-07-25)

**Feature-complete and verified end to end.**

- Server: 22 pytest tests pass, `ruff check` clean.
- Build: `npm run build` succeeds (28 kB UMD + 6 kB CSS); the bundle references only the
  `girder` global.
- API, live against MongoDB: plugin loads, `system/plugin_static_files` lists both assets (both
  200), `GET /dashboard` is `[]` while disabled, and `PUT enabled=true` makes it visible to
  anonymous callers with `available: true`.
- Browser: **54/54** checks in `test/browser/verify.cjs`, screenshots reviewed. All four TASK.md
  requirements confirmed in a real browser, including that the runner genuinely hides
  `#g-app-header-container` / `#g-global-nav-container` / `#g-app-footer-container`, swaps
  `g-default-layout` → `g-empty-layout`, and restores all of it on the way back.

### Defects the browser pass found and fixed

Worth knowing about, because both were invisible to the Python tests and to the build:

1. **`DataOverviewDashboard` fired `GET /user` anonymously.** That endpoint is `@access.user`, so
   every anonymous visit logged a 401 and showed a `—` tile. Now the tile list is filtered by
   `getCurrentUser()` before any request goes out (`TILES[].requiresUser`); the `.fail()` fallback
   stays for genuine errors. `GET /collection` and `GET /group` *are* `@access.public` — verified,
   don't "fix" those.
2. **The settings dialog outgrew a short viewport,** and Bootstrap 3 scrolls the whole modal,
   pushing the title and Save button off screen. `.modal-body` now has
   `max-height: calc(100vh - 190px)` with internal scroll. The harness asserts both stay in the
   viewport, so a regression here fails the run.

`LICENSE` is BSD-3-Clause, matching `setup.py` and the sibling plugins. Copyright is attributed to
**data-exp-lab, 2026**, following `../girder-jsonforms/LICENSE`; change the holder if that is
wrong. Note that jsonforms' copy has a stale "Neither the name of girder_wholetale" clause from
being copied out of girder-wholetale — ours names `girder-dashboards`, so don't "sync" it back.

**Possible follow-ups:** wire into `../deploy-dev/docker-stack.yml` (see Deployment above); a
release workflow if the package should go to PyPI (see CI above); Codecov upload.
