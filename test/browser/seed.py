#!/usr/bin/env python3
"""Prepare a running Girder for test/browser/verify.cjs.

The browser harness asserts against real content, so it needs an admin account,
the bundled dashboards enabled, at least one readable collection, and a micrograph
to upload. This script puts a fresh instance into that state and is safe to re-run
against a dirty one.

    python3 test/browser/seed.py

Environment: GIRDER_URL, GIRDER_ADMIN, GIRDER_PASSWORD, GIRDER_EMAIL.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.environ.get("GIRDER_URL", "http://127.0.0.1:8989").rstrip("/")
API = f"{BASE}/api/v1"
ADMIN = os.environ.get("GIRDER_ADMIN", "admin")
PASSWORD = os.environ.get("GIRDER_PASSWORD", "adminpassword")
EMAIL = os.environ.get("GIRDER_EMAIL", "admin@example.com")

DASHBOARD_KEYS = ["data-overview", "precipitate-analysis"]
COLLECTIONS = ["Alpha", "Beta", "Gamma"]

#: Uploading a micrograph needs somewhere to put the bytes, and a fresh Girder has
#: no assetstore at all. Kept under build/ so it is gitignored and easy to discard.
ASSETSTORE_ROOT = os.environ.get(
    "GIRDER_ASSETSTORE",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "build",
        "assetstore",
    ),
)


def request(method, path, params=None, token=None, auth=None):
    url = f"{API}/{path}"
    body = None
    headers = {}
    if params:
        encoded = urllib.parse.urlencode(params)
        if method == "GET":
            url = f"{url}?{encoded}"
        else:
            body = encoded.encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Girder-Token"] = token
    if auth:
        raw = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {raw}"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


def ensure_admin():
    """Create the first user (Girder makes it a site admin), or reuse it."""
    try:
        resp = request(
            "POST",
            "user",
            {
                "login": ADMIN,
                "password": PASSWORD,
                "email": EMAIL,
                "firstName": "Test",
                "lastName": "Admin",
            },
        )
        print(f"created admin user '{ADMIN}'")
        return resp["authToken"]["token"]
    except RuntimeError as exc:
        # Already registered: just log in. Any other failure is fatal.
        if "already" not in str(exc).lower():
            raise
        resp = request("GET", "user/authentication", auth=(ADMIN, PASSWORD))
        print(f"reusing existing admin user '{ADMIN}'")
        return resp["authToken"]["token"]


def ensure_enabled(token):
    dashboards = request("GET", "dashboard", {"includeDisabled": "true"}, token=token)
    byKey = {d["key"]: d for d in dashboards}

    ids = {}
    for key in DASHBOARD_KEYS:
        match = byKey.get(key)
        if match is None:
            raise SystemExit(
                f"dashboard '{key}' is not registered (found: {sorted(byKey)}). "
                "Is the plugin loaded?"
            )
        if match["enabled"]:
            print(f"dashboard '{key}' already enabled")
        else:
            request(
                "PUT", f"dashboard/{match['_id']}", {"enabled": "true"}, token=token
            )
            print(f"enabled dashboard '{key}'")
        ids[key] = match["_id"]
    return ids


def ensure_assetstore(token):
    """Make sure uploads have somewhere to land."""
    existing = request("GET", "assetstore", {"limit": "0"}, token=token)
    current = next((a for a in existing if a.get("current")), None)
    if current:
        print(f"assetstore '{current['name']}' already current")
        return current["_id"]

    os.makedirs(ASSETSTORE_ROOT, exist_ok=True)
    created = request(
        "POST",
        "assetstore",
        {"type": "0", "name": "Test filesystem", "root": ASSETSTORE_ROOT},
        token=token,
    )
    print(f"created filesystem assetstore at {ASSETSTORE_ROOT}")
    return created["_id"]


def ensure_micrograph():
    """Write the synthetic TIFF the precipitate dashboard test uploads."""
    import micrograph

    path, count = micrograph.write()
    print(f"wrote {path} ({count} synthetic precipitates)")
    return path


def ensure_collections(token):
    """The demo dashboard's table needs rows to be worth asserting on."""
    existing = {c["name"] for c in request("GET", "collection", {"limit": "0"})}
    for name in COLLECTIONS:
        if name in existing:
            continue
        request(
            "POST",
            "collection",
            {"name": name, "description": f"Collection {name}", "public": "true"},
            token=token,
        )
        print(f"created collection '{name}'")
    print(f"{len(existing | set(COLLECTIONS))} collection(s) present")


def main():
    print(f"seeding {BASE}")
    token = ensure_admin()
    ensure_assetstore(token)
    ensure_enabled(token)
    ensure_collections(token)

    ensure_micrograph()

    visible = {d["key"] for d in request("GET", "dashboard")}
    missing = set(DASHBOARD_KEYS) - visible
    if missing:
        raise SystemExit(f"dashboards not publicly visible: {sorted(missing)}")
    print("ready for test/browser/verify.cjs")


if __name__ == "__main__":
    sys.exit(main())
