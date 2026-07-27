# Writing a dashboard in a separate plugin

How to ship one dashboard as its own pip-installable Girder plugin, so a deployment is
`pip install girder-dashboards girder-dashboard-foo girder-dashboard-bar` and nothing in *this*
repository has to change.

Everything below was verified twice: by building a toy plugin (`hello-world`) out of tree and
driving it in a browser, and then by using this recipe for real — `girder-dashboards-precipitate`
was extracted from this repository by following it. See
[Verification](#verification-of-this-recipe) for exactly what was checked and what was not.

Read `../CLAUDE.md` first for the two-halves design; this file is the mechanics.

---

## The contract, in one paragraph

Your plugin declares a **key** twice: once in Python (`registerDashboard`, so the server knows the
dashboard exists and what its card says) and once in JavaScript
(`girder.plugins.dashboards.registerDashboard`, so the browser knows what view renders it). The
dashboards plugin owns everything in between — the document, the ACL, the enable toggle, the
gallery card, the settings dialog, the `#dashboard/:id` route and the full-viewport chrome. You
write a Backbone view and a `load()`.

You do **not** subclass anything, register a model, add a route, or touch the config page.

---

## Step 1 — package skeleton

```
girder-dashboard-hello/
  setup.py
  MANIFEST.in
  girder_dashboard_hello/
    __init__.py                     # GirderPlugin subclass — the whole server half
    web_client/
      package.json
      vite.config.ts
      main.js                       # entry point: registers the view
      dashboards/HelloDashboard.js
      templates/helloDashboard.pug
      stylesheets/helloDashboard.styl
      dist/                         # built, gitignored, shipped by MANIFEST.in
```

`setup.py`:

```python
from setuptools import find_packages, setup

setup(
    name="girder-dashboard-hello",
    version="0.1.0",
    description="A Hello World dashboard for girder-dashboards",
    license="BSD-3-Clause",
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=["girder-dashboards>=0.1.1"],
    entry_points={
        "girder.plugin": [
            "dashboard_hello = girder_dashboard_hello:HelloDashboardPlugin"
        ]
    },
    zip_safe=False,
)
```

The entry-point **name** (`dashboard_hello`) is the Girder plugin name. It is what
`@pytest.mark.plugin(...)` takes and what `registerPluginStaticContent(plugin=...)` must match. It
is *not* the dashboard key.

`MANIFEST.in` — same shape as this repo's, because `dist/` is a build artifact that still has to
end up in the wheel:

```
prune girder_dashboard_hello/web_client
include girder_dashboard_hello/web_client/dist/girder-plugin-dashboard-hello.umd.cjs
include girder_dashboard_hello/web_client/dist/style.css
```

## Step 2 — the server half

```python
from pathlib import Path

from girder.plugin import GirderPlugin, getPlugin, registerPluginStaticContent
from girder_dashboards import registerDashboard

KEY = "hello-world"


class HelloDashboardPlugin(GirderPlugin):
    DISPLAY_NAME = "Hello Dashboard"

    def load(self, info):
        # Must be first. See "Load order" below — this one line is what makes both
        # the provisioning and the browser script order work.
        getPlugin("dashboards").load(info)

        registerDashboard(
            KEY,
            name="Hello World",
            description="The smallest possible dashboard.",
            authors=["Example Author"],
            icon="icon-globe",
            settings={"who": "world"},
        )

        registerPluginStaticContent(
            plugin="dashboard_hello",
            css=["/style.css"],
            js=["/girder-plugin-dashboard-hello.umd.cjs"],
            staticDir=Path(__file__).parent / "web_client" / "dist",
            tree=info["serverRoot"],
        )
```

That is the entire server half of a dashboard that only reads existing Girder endpoints. Adding
your own REST resource is ordinary Girder (`info["apiRoot"].hello = Hello()`) and is orthogonal to
being a dashboard — see [Step 5](#step-5--optional-your-own-rest-resource).

### `registerDashboard` arguments

Signature in `girder_dashboards/registry.py:110`.

| Arg | Notes |
|---|---|
| `key` | Must match `^[a-z0-9][a-z0-9._-]*$` and must equal the JS key. Appears in no URL directly — the route is `#dashboard/<mongo id>` — but it is the join between the halves, so treat it as permanent: changing it orphans the existing document (it goes `available: false`) and provisions a fresh one with default settings. |
| `name` | Required, non-empty. Card title. |
| `description` | Card body text. |
| `authors` | **List** of names, in reading order. A bare string raises — see the note in `../CLAUDE.md`. |
| `image` | Card image URL or data URI. `None` falls back to `icon`. This repo inlines base64 SVG data URIs (`builtin.py`) to avoid static-asset plumbing; do the same or point at a URL your plugin serves. |
| `icon` | Fontello class, default `icon-gauge`. Core's fontello set only — you get no new glyphs by shipping CSS. |
| `settings` | Dict of admin-editable defaults, handed to your view at runtime. Must be JSON round-trippable: the admin edits it as raw JSON in a textarea (`EditDashboardWidget`), and the endpoint takes it as `jsonParam(requireObject=True)`. |

All of these are **defaults**, not fixed values. Once the document exists, an admin's edits win and
`registerDashboard` never overwrites them — `provision()` is a `$setOnInsert` upsert. To push a
changed default to an existing deployment you have to hit `PUT /dashboard/{id}/reset`, per
dashboard. Plan your `settings` keys accordingly: adding a key later means every existing
deployment has a document without it, so **your view must tolerate a missing key** rather than
assume the declared default is present.

### Load order

`getPlugin("dashboards").load(info)` as the first statement of your `load()` is load-bearing twice
over:

1. **Provisioning.** `DashboardsPlugin.load()` installs `DashboardModel().provision` as a
   registration listener *before* calling `provisionAll()`. If dashboards loads first, your later
   `registerDashboard()` call is provisioned immediately. If your plugin somehow ran first, your
   registration is still picked up — by `provisionAll()` — so this half is forgiving.
2. **Browser script order.** This half is not forgiving. `registerPluginStaticContent` appends to
   an `OrderedDict` (`girder/plugin.py:30`), `GET /system/plugin_static_files` flattens it in that
   order, and the web app loads plugin `<script>`s strictly sequentially in that order
   (`girder/web/src/main.ts`, "they already come to us in topologically sorted order"). Your
   `main.js` reads `girder.plugins.dashboards` at module scope, so the dashboards bundle must have
   run first. `getPlugin(...).load(info)` forces exactly that, because a wrapped `load()` runs once
   and synchronously.

Without that call the order is whatever `importlib.metadata` hands back — observed here as
`['dashboards', 'dashboard_hello', 'jobs', 'worker']`, which is neither alphabetical nor
install-order, i.e. it is not something to rely on. Don't rely on it.

## Step 3 — the client half

`web_client/main.js` — the whole entry point:

```js
import HelloDashboard from './dashboards/HelloDashboard';

const { registerDashboard } = girder.plugins.dashboards;

registerDashboard('hello-world', { view: HelloDashboard });
```

`web_client/dashboards/HelloDashboard.js`:

```js
import template from '../templates/helloDashboard.pug';

import '../stylesheets/helloDashboard.styl';

const View = girder.views.View;
const { restRequest } = girder.rest;

var HelloDashboard = View.extend({
    initialize: function (settings) {
        this.dashboard = settings.dashboard;   // the DashboardModel
        this.settings = settings.settings || {};
    },

    render: function () {
        restRequest({ url: 'hello/greeting', method: 'GET', error: null })
            .done((resp) => {
                this.$el.html(template({
                    greeting: resp.greeting,
                    who: this.settings.who || 'world'
                }));
            });
        return this;
    }
});

export default HelloDashboard;
```

### What your view is handed

`DashboardRunView.render()` constructs it as:

```js
new registration.view({
    el: this.$('.g-dashboard-mount'),
    parentView: this,
    dashboard: this.model,                    // DashboardModel — name, key, settings, _accessLevel
    settings: this.model.get('settings') || {}
});
```

and then calls `render()` on it. Notes that matter:

- **`el` is given to you.** Don't set `tagName`/`className` expecting a wrapper; style inside
  `.g-dashboard-mount`.
- **`render()` may be called again.** Saving the settings dialog from the running dashboard's gear
  re-renders the chrome, which `destroy()`s your view and builds a fresh one. Put teardown
  (intervals, `Chart.js` instances, socket handles) in `destroy()`, calling
  `View.prototype.destroy.call(this)`.
- **`settings` is the document's settings, not your declared defaults.** Read defensively
  (`this.settings.who || 'world'`), because an old document may predate a key.
- **You own the viewport below the top bar.** `Layout.EMPTY` means no Girder header, nav or footer;
  the back link lives in the chrome `DashboardRunView` renders above you. `#g-dialog-container` is
  outside the body container, so `$.fn.girderModal` still works.
- **Anonymous users reach dashboards.** New dashboards are provisioned `public: true`, so unless an
  admin narrows the ACL your view runs with no session. Filter out requests to `@access.user`
  endpoints with `girder.auth.getCurrentUser()` *before* firing them — an anonymous `GET /user` is a
  guaranteed 401, and that exact bug shipped once here (`../CLAUDE.md`, "Defects the browser pass
  found"). `GET /collection` and `GET /group` are public.
- **Failing to register is a handled state, not a crash.** If the server has a document for `key`
  but nothing registered a view, `DashboardRunView` renders "This dashboard is not installed"
  instead. That is what a user sees when your bundle 404s or throws at import.

### `web_client/vite.config.ts`

Copy this repo's and change three strings. Nothing is bundled from core *or* from
girder-dashboards — both are on the page already as the `girder` global.

```ts
import { resolve } from 'path';

import { defineConfig } from 'vite';
import { compileClient } from 'pug';

function pugPlugin() {
  return {
    name: 'pug',
    transform(src: string, id: string) {
      if (id.endsWith('.pug')) {
        return {
          code: `${compileClient(src, { filename: id, compileDebug: false })}\nexport default template`,
          map: null,
        };
      }
    },
  };
}

export default defineConfig({
  plugins: [pugPlugin()],
  build: {
    lib: {
      entry: resolve(__dirname, 'main.js'),
      name: 'GirderPluginDashboardHello',        // UMD global name — must be unique
      fileName: 'girder-plugin-dashboard-hello', // must match registerPluginStaticContent(js=)
    },
    rollupOptions: {
      output: {
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && assetInfo.name.endsWith('.css')) {
            return 'style.css';
          }
          return '[name].[ext]';
        },
      },
    },
  },
});
```

`package.json` needs only `vite`, `pug` and `stylus` as devDependencies plus
`"scripts": {"build": "vite build", "dev": "vite build --watch"}`. Pug and Stylus are conveniences,
not requirements — a template literal and a plain `.css` import work fine.

### Styling

Your `style.css` is a separate `<link>`; there is no shared token file at runtime. Prefix classes
`g-` and namespace them to your dashboard (`.g-hello-dashboard ...`) so two dashboards installed
side by side can't collide. If you want this repo's Stylus variables, either copy
`stylesheets/variables.styl` or depend on the published
`@girder/girder-plugin-dashboards` npm package — copying is the honest choice for two colours.

## Step 4 — build, install, enable

```bash
(cd girder_dashboard_hello/web_client && npm install && npm run build)
pip install -e .
```

Then restart Girder. The dashboard is provisioned **disabled**; a site admin turns it on at
`#plugins/dashboards/config` (or `PUT /api/v1/dashboard/{id}` with `enabled=true`). It is
`public: true` from birth, so enabling is the only step needed to offer it to everyone; narrowing
the ACL is how you restrict it.

**Build before you package or test.** `registerPluginStaticContent` md5-hashes every listed file at
load time, so a missing `dist/` makes `load()` raise `FileNotFoundError` and every test using the
`server` fixture error out. Same trap as this repo — see `testing.md`.

## Step 5 — optional: your own REST resource

Only if your dashboard needs server-side work. Ordinary Girder, no dashboards involvement:

```python
from girder.api import access
from girder.api.describe import Description, autoDescribeRoute
from girder.api.rest import Resource


class Hello(Resource):
    def __init__(self):
        super().__init__()
        self.resourceName = "hello"
        self.route("GET", ("greeting",), self.getGreeting)

    @access.public
    @autoDescribeRoute(Description("Return a greeting."))
    def getGreeting(self):
        return {"greeting": "Hello from a dashboard plugin"}
```

plus `info["apiRoot"].hello = Hello()` in `load()`. Pick a resource name unlikely to collide;
attaching to `info["apiRoot"]` is a plain attribute assignment with no conflict detection.

Guidance from the one real example, `../girder-dashboards-precipitate` (read its `CLAUDE.md`):

- **Access-check against the dashboard document, not the key.** A dashboard's ACL is on its
  document; an endpoint that skips it is a way around the admin's ACL. See how its `rest.py`
  resolves and checks.
- **Long work belongs in a job.** Depend on `girder-jobs`, `getPlugin("jobs").load(info)`, and give
  the client something to poll. Its `jobs.py` is the worked example, including the in-process
  fallback for deployments with no Celery worker. Note that girder-dashboards itself does *not*
  depend on jobs — a dashboard that needs it says so in its own `setup.py`.
- **Heavy Python deps go in an extra**, with a capability probe endpoint so the dashboard can say
  "not installed here" instead of dying mid-run — its `__init__.py::missingRequirements`, whose
  correspondence with `setup.py`'s extra is asserted by a test rather than maintained by hand.

## Step 6 — testing your plugin

`pytest-girder` loads plugins by marker, and marking yours pulls dashboards in through
`getPlugin(...)`:

```python
import pytest
from pytest_girder.assertions import assertStatusOk

pytestmark = pytest.mark.plugin("dashboard_hello")


@pytest.fixture
def helloDoc(server, db):
    from girder_dashboards.models.dashboard import Dashboard

    return Dashboard().findOne({"key": "hello-world"})


def testProvisionedDisabledButPublic(helloDoc):
    assert helloDoc["enabled"] is False
    assert helloDoc["public"] is True
    assert helloDoc["settings"] == {"who": "world"}
```

Three things to copy from this repo's suite:

- **A registry-cleaning `autouse` fixture.** The registry is module-level state that outlives the
  `db` fixture, so any test that unregisters leaks into later tests. Copy
  `girder_dashboards/tests/conftest.py::cleanRegistry`.
- **Key your assertions, never index them.** Every dashboard installed in the environment appears
  in `GET /dashboard`; `resp.json[0]` is a bug waiting for the next plugin.
- **Build the web client first**, per Step 4.

The one thing pytest cannot tell you is whether your bundle actually loads and renders — that needs
a browser. `test/browser/verify.cjs` in this repo is the pattern: fail the run on any console
error, page error or failed request, and read the screenshots. Both defects listed in
`../CLAUDE.md` were invisible to Python tests and to the build.

---

## Verification of this recipe

Done on 2026-07-27 by building the `girder-dashboard-hello` plugin exactly as written above, in a
scratch directory outside this repository, installing it into `venv/` alongside `girder-dashboards`
and removing it afterwards.

Verified:

- `npm run build` with only vite/pug/stylus produces the UMD bundle and `style.css`.
- 6 pytest cases pass against the `server` fixture: Python registration, provisioning
  (`enabled=False`, `public=True`, declared settings), absence from `GET /dashboard` while
  disabled and presence with `available: true` once enabled, the admin override →
  `PUT /reset` round trip, the plugin's own REST endpoint, and that
  `GET /system/plugin_static_files` lists the dashboards bundle **before** the extension's.
- Live browser run against `girder serve`: the card appears in the gallery,
  `girder.plugins.dashboards.listDashboards()` returns `['data-overview',
  'precipitate-analysis', 'hello-world']`, `#dashboard/<id>` renders the view with the extension's
  own stylesheet applied and Girder's nav hidden, and there were no console errors, page errors or
  failed requests. Screenshot reviewed.

Then, by applying it for real (2026-07-27): `girder-dashboards-precipitate` was extracted from
this repository following these steps. That run additionally verified:

- The **packaged-wheel path**. `pip wheel` on both packages produces wheels carrying exactly the
  two `web_client/dist` artifacts and no tests, with the right entry points — so the `MANIFEST.in`
  above is tested, not just reasoned about.
- **Two plugins installed at once**, live against MongoDB: both dashboards provision, both cards
  render, `system/plugin_static_files` orders the bundles correctly, and enabling/disabling one
  does not touch the other. 62/62 core browser checks + 74/74 in the extracted plugin's own
  harness, against the same instance.
- That an extracted dashboard can carry **its own REST resource, Celery tasks, jobs dependency and
  dependency extra** without any of it leaking back into this package — `girder-dashboards`
  `install_requires` is now just `girder`.

Still not verified: publishing to PyPI.

## What the real extraction cost

Recorded because the next one will look much the same. Moving Precipitate Analysis out took:

- **Six import rewrites in library code**, all mechanical — `..precipitate` → `.`,
  `..models.dashboard` → `girder_dashboards.models.dashboard`, and the reverse-direction
  `..worker_plugin.precipitate` → `.worker_plugin.tasks`.
- **One string that is persisted in a database.** `jobs.py` stores `module=` on local job
  documents; it had to change with the package. `JOB_TYPE` and the run folder's `STATE_KEY` did
  *not* change, deliberately, so existing runs and job filters survive. Check for this class of
  thing before you move anything — a grep for the old package name in string literals, not just in
  imports.
- **A judgement call about `__init__.py`.** The extracted package's `__init__` holds the
  declaration and imports no Girder at all, with the `GirderPlugin` subclass in `plugin.py`. That
  is stricter than the toy example above and worth copying whenever a Celery worker imports your
  code: an entry point that fails to load takes the whole worker down.
- **Splitting the browser harness.** The core one now adapts to whatever else is installed rather
  than hard-coding two dashboards, and logs a SKIP for the one check (per-dashboard toggle
  isolation) that needs a second dashboard to mean anything.
- **A version bump with a migration note.** girder-dashboards went 0.1.1 → 0.2.0. Upgrading
  without installing the extracted plugin leaves the `precipitate-analysis` document in place but
  `available: false` — hidden from the gallery, still on the config page, settings and ACL intact.
  Installing the new package restores it exactly. That is the `available` flag working as designed,
  but it reads as data loss if nobody says so first.

What it did *not* cost: any change to `registry.py`, `models/dashboard.py`, `rest/dashboard.py`, or
any view in this package. The seams held.
