# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Further reading (load on demand)

These are **not** imported — read the file only when the work calls for it:

| File | Read it when |
|---|---|
| `docs/extending.md` | Adding a **new dashboard**, especially as a separate pip-installable plugin: the two-halves contract in mechanical detail, why `getPlugin("dashboards").load(info)` must come first, what a dashboard view is handed, packaging/Vite/testing for an out-of-tree plugin, and the gaps between today's layout and a "one plugin per dashboard" deployment. |
| `docs/testing.md` | Running or changing the test suite beyond `pytest`/`ruff`: pytest gotchas, the local smoke run, the `test/browser` harness (incl. the Celery path), and the CI workflow. |

## What this is

`girder-dashboards` — a **Girder 5** plugin adding a new class of entity: **Dashboards**,
lightweight interactive UIs operating on the data gathered in a Girder instance. It ships a
Python server plugin plus a Backbone `web_client` bundled with Vite.

It is deliberately **small**: the framework, plus one worked-example dashboard. Real dashboards
are separate pip-installable plugins — see `docs/extending.md`, and
`../girder-dashboards-precipitate` for one.

Built to satisfy the original brief (`TASK.md`, not checked in):

1. `Dashboards` entry in the left sidebar → dedicated list.
2. Card view: image, name, description, "run" action for READ users, settings gear for admins.
3. Plugin config page where a site admin enables/disables available dashboards.
4. Dashboards render **without** the standard Girder layout, with a "go back" link in their own
   top navbar.

One dashboard ships with it: **Data Overview**, a small worked example. *Precipitate Analysis*
lived here until 2026-07-27 and is now `../girder-dashboards-precipitate`.

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
  tests/               # conftest.py + test_dashboard.py (32 tests)
test/browser/          # end-to-end browser check: seed.py + verify.cjs (~60 checks)
.github/workflows/     # build-test.yaml: `test` job (lint + pytest), `browser` job (e2e)
  web_client/
    main.js            # entry: sidebar wrap, built-in registration, registerPluginNamespace
    routes.js          # #dashboards, #dashboard/:id (EMPTY layout), #plugins/dashboards/config
    registry.js        # client-side key -> view registry
    models/ collections/
    views/             # DashboardListView, DashboardRunView, ConfigView, EditDashboardWidget
    dashboards/
      DataOverviewDashboard.js       # the one bundled dashboard, as a worked example
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
  why `EditDashboardWidget.save()` uses `restRequest` rather than `model.save()`. `authors` goes
  the same way (`requireArray=True`), for the same reason.
- **`authors`** is a list of names credited on the card, declared by `registerDashboard()` and
  overridable by an admin like the rest of the card metadata. `registry.normalizeAuthors()` is
  the single definition of what a name list is — it trims and drops empties (so a blank line in
  the admin's textarea is not an error) but raises on a bare string or a non-string entry, since
  `"Ada Lovelace"` iterated character-by-character would credit nobody. The dialog takes **one
  name per line**, not comma-separated: a name may contain a comma. `provision()` also
  `$set`s the declared authors when the field is *absent*, which backfills documents written
  before it existed; a present-but-empty list is an admin's decision and is left alone.

## REST API

All under `/api/v1/dashboard`. Every response carries `available`.

| Route | Access | Purpose |
|---|---|---|
| `GET /dashboard` | public | READ-visible dashboards; enabled-only. `includeDisabled`/`includeUnavailable` are site-admin only (403 otherwise) |
| `GET /dashboard/{id}` | READ | One dashboard |
| `PUT /dashboard/{id}` | doc ADMIN | name, description, authors, image, icon, enabled, settings |
| `PUT /dashboard/{id}/reset` | doc ADMIN | Restore declared defaults; leaves `enabled` + ACL alone |
| `DELETE /dashboard/{id}` | site admin | Only when `available` is false |
| `GET`/`PUT /dashboard/{id}/access` | doc ADMIN | ACL |

## Commands

Python (a repo-local `venv/` already exists with `girder`, `pytest-girder`, `ruff`, and this
package installed editable):

```bash
venv/bin/pytest girder_dashboards/tests -q     # 131 tests; needs MongoDB on :27017
venv/bin/ruff check .
venv/bin/ruff format girder_dashboards
tox -e pytest   /   tox -e lint                # equivalents (pytest env pulls the extra)
```

Dependencies are installed from **PyPI**, not as editable installs from the sibling `../girder`
checkout. `girder-dashboards-precipitate` is installed editable from `../girder-dashboards-precipitate`,
so the venv holds both plugins; a test run here loads only this one (`pytest.mark.plugin("dashboards")`),
but a `girder serve` from this venv loads both.

Web client, from `girder_dashboards/web_client/` (`node_modules/` already installed):

```bash
npm run build     # vite build -> dist/
npm run dev       # vite build --watch
```

**Build before running the Python tests** — with no bundle in `web_client/dist`, `load()` raises
`FileNotFoundError` and every test using the `server` fixture errors out. See `docs/testing.md`
for that and the rest of the harness (pytest gotchas, smoke run, browser checks, CI).

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
**is** mounted into `girder` as `06-girder-dashboards`. It needs nothing in `local_worker` — it has
no Celery work of its own. `girder-dashboards-precipitate` does; see that repo's CLAUDE.md.

## Status (2026-07-27)

**Feature-complete and verified end to end.** Precipitate Analysis was extracted into
`../girder-dashboards-precipitate` on 2026-07-27, following `docs/extending.md`; this repo is now
the framework plus one worked example.

- Server: **32** pytest tests pass, `ruff check` clean.
- Build: `npm run build` succeeds — **29 kB UMD / 7 kB gzipped** plus 7 kB CSS, down from 232 kB,
  which is Chart.js leaving with the dashboard that needed it. `chart.js` is no longer a dependency.
  The bundle references only the `girder` global.
- Packaging: `pip wheel` produces `girder_dashboards-0.2.0-py3-none-any.whl` carrying exactly the
  two `web_client/dist` artifacts and no tests, and declaring only the `girder.plugin` entry point.
- Live, with **both** plugins installed: both dashboards provision, `system/plugin_static_files`
  lists this bundle *before* the precipitate one (which is what the load-order rule buys), and
  `GET /dashboard` returns both.
- Browser: run **both** ways, screenshots reviewed.
  - **62/62** against an instance that also had `girder-dashboards-precipitate` installed, so the
    "disabling one dashboard leaves the others enabled" check had a real second dashboard to act on.
  - **61/61** with this plugin alone, where that one check logs
    `SKIP  REQ3 disabling one leaves the others enabled  [only one dashboard installed — install
    another plugin to cover this]` rather than passing quietly. Both configurations matter: the
    harness has to work for someone who installed only this.
- The precipitate half is verified separately — **74/74** checks in that repo's harness, against
  the same running instance, and 74/74 again with a real Celery worker. Total across both: 136.
- Authors byline: verified on the gallery card, the config table and the settings dialog, including
  the admin edit round trip (edit → reload → reset). Run against a database provisioned *before*
  the field existed, so the `provision()` backfill is verified too, not just asserted.

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

## Licence

`LICENSE` is BSD-3-Clause, matching `setup.py` and the sibling plugins. Copyright is attributed to
**data-exp-lab, 2026**, following `../girder-jsonforms/LICENSE`; change the holder if that is
wrong. Note that jsonforms' copy has a stale "Neither the name of girder_wholetale" clause from
being copied out of girder-wholetale — ours names `girder-dashboards`, so don't "sync" it back.

## Possible follow-ups

A release workflow if the package should go to PyPI (see `docs/testing.md`); Codecov upload.

The **0.2.0 version bump is a breaking change** and should be released as one: an existing
deployment that upgrades without also installing `girder-dashboards-precipitate` keeps its
`precipitate-analysis` *document* (with the admin's settings and ACL intact) but the key no longer
resolves, so the dashboard goes `available: false` — hidden from the gallery, still listed on the
config page, and deletable. Installing the new package brings it back exactly as it was. That is
the `available` flag working as designed, but it will look like data loss if nobody says so first.
