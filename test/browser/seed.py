#!/usr/bin/env python3
"""Prepare a running Girder for test/browser/verify.cjs.

The browser harness asserts against real content, so it needs an admin account,
the bundled dashboard enabled, and at least one readable collection. This script
puts a fresh instance into that state and is safe to re-run against a dirty one.

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

BASE = os.environ.get("GIRDER_URL", "http://127.0.0.1:8989").rstrip("/")
API = f"{BASE}/api/v1"
ADMIN = os.environ.get("GIRDER_ADMIN", "admin")
PASSWORD = os.environ.get("GIRDER_PASSWORD", "adminpassword")
EMAIL = os.environ.get("GIRDER_EMAIL", "admin@example.com")

DASHBOARD_KEY = "data-overview"
COLLECTIONS = ["Alpha", "Beta", "Gamma"]


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
    match = next((d for d in dashboards if d["key"] == DASHBOARD_KEY), None)
    if match is None:
        keys = [d["key"] for d in dashboards]
        raise SystemExit(
            f"dashboard '{DASHBOARD_KEY}' is not registered (found: {keys}). "
            "Is the plugin loaded?"
        )
    if match["enabled"]:
        print(f"dashboard '{DASHBOARD_KEY}' already enabled")
    else:
        request("PUT", f"dashboard/{match['_id']}", {"enabled": "true"}, token=token)
        print(f"enabled dashboard '{DASHBOARD_KEY}'")
    return match["_id"]


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
    ensure_enabled(token)
    ensure_collections(token)

    visible = request("GET", "dashboard")
    if len(visible) != 1:
        raise SystemExit(
            f"expected exactly 1 publicly visible dashboard, got {visible}"
        )
    print("ready for test/browser/verify.cjs")


if __name__ == "__main__":
    sys.exit(main())
