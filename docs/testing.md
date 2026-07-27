# Testing, verification and CI

The full detail behind the short Commands section in `../CLAUDE.md`: the pytest gotchas, the
smoke run, the browser harness and the GitHub Actions workflow. Dashboards shipped by other
plugins bring their own suite and their own harness — see
`../girder-dashboards-precipitate/docs/testing.md`.

## Test-suite gotchas

- `tests/conftest.py::cleanRegistry` is `autouse` and snapshots/restores `registry._dashboards`
  and `registry._listeners`. Without it the "unregister a dashboard" tests leak into every later
  test. Keep it if you add tests that mutate the registry.
- `pytest-girder` resets `plugin._pluginRegistry` per test, so `load()` (and therefore
  `provisionAll()`) re-runs against each test's fresh database. That is why the `dataOverview`
  fixture can just `findOne` the provisioned doc.
- Tests are marked module-wide via `pytestmark = pytest.mark.plugin("dashboards")`.
- **Assertions about one dashboard must be keyed, not positional.** Only one dashboard ships here
  now, but other plugins install their own into the same listing, so `dashboards[0]` and
  `len(...) == 1` are still wrong; `test_dashboard.py::_byKey` and `verify.cjs`'s
  `cardFor`/`configRowFor` exist for that reason. The suite is run with other dashboard plugins
  installed in the same venv — `pytest.mark.plugin("dashboards")` keeps them out of the *server*
  fixture, but nothing keeps them out of a developer's `girder serve`.
- **Build the web client first.** `registerPluginStaticContent` md5-hashes every file in
  `web_client/dist` at load time to build cache-busting URLs, so with no bundle present `load()`
  raises `FileNotFoundError` and every test using the `server` fixture errors out (the
  pure-registry ones still pass, which makes the cause easy to misread). This
  is why CI builds the web client before `tox -e pytest`. `dist/` is gitignored but `MANIFEST.in`
  ships it, so build before packaging too.

## Local smoke run

```bash
GIRDER_MONGO_URI=mongodb://localhost:27017/girder_dashboards_smoke \
  venv/bin/girder serve --host 127.0.0.1 --port 8989
```

The installed girder wheel serves the built core web client at `/`, so the whole UI is
reachable. First `POST /api/v1/user` creates a site admin.

## Browser verification

`test/browser/verify.cjs` drives headless Chrome and asserts all four brief requirements end to
end, across an anonymous session and an admin session. It also **fails on any console error, page
error or failed request**, which is how the `GET /user` 401 was caught.

It **adapts to whatever else is installed**: it requires only `data-overview`, counts cards against
the API listing rather than a fixed number, and re-enables everything it disabled. The one check
that needs a second dashboard — that a config-page toggle acts on one dashboard at a time — logs a
`SKIP` line when there isn't one, rather than passing quietly. Install
`girder-dashboards-precipitate` (or any other dashboard plugin) to cover it.

```bash
# one-off: install the harness's own playwright
(cd test/browser && npm ci && npx playwright install chromium)

# then, against a running Girder:
GIRDER_MONGO_URI=mongodb://localhost:27017/girder_dashboards_ci \
  venv/bin/girder serve --host 127.0.0.1 --port 8989 > girder.log 2>&1 &
python3 test/browser/seed.py       # admin, the bundled dashboard enabled, sample collections
node test/browser/verify.cjs       # 62/62 expected
```

`seed.py` is stdlib-only (no venv needed) and idempotent, so it works on a fresh *or* dirty
instance. `verify.cjs` re-enables every dashboard it disabled, so it too is repeatable — and so
another plugin's harness can run against the same instance afterwards.

Configure both via `GIRDER_URL` / `GIRDER_ADMIN` / `GIRDER_PASSWORD`, plus `SHOTS` for
screenshots, which land in `test/browser/screenshots/` (gitignored). **Read the screenshots** —
DOM assertions alone won't catch a collapsed grid or a clipped dialog, which is exactly how the
modal defect was found.

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
  is load-bearing, not cosmetic; see the warning above.
- **`browser`** — `pip install -e .`, build the web client, `npm ci` + `playwright
  install chromium` in `test/browser`, start `girder serve` on 8989 with a wait loop, `seed.py`, then
  `verify.cjs`. Screenshots and `girder.log` upload as an artifact on every run (`if: always()`),
  which is what you want when a headless failure needs diagnosing. There is no broker in CI, so this
  job exercises the in-process fallback; the Celery path has to be driven locally (see above).

Both job sequences were rehearsed locally command-for-command against a fresh database before
being committed. No secrets are used, so the workflow runs on forks. Coverage XML is produced
(`--cov-report=xml`) but nothing uploads it — add a Codecov step plus `CODECOV_TOKEN` if wanted.

There is deliberately **no release workflow**. jsonforms has one that publishes to PyPI via
trusted publishing on tag pushes; adding the equivalent here means claiming the
`girder-dashboards` PyPI name and configuring a `pypi` environment, which is the maintainer's
call to make, not something to wire up unasked.
