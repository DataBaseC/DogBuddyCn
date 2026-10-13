#!/usr/bin/env python3
# DogBuddyCn — register this instance as an AegisServerPanel managed app
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Register DogBuddyCn with a running AegisServerPanel so the panel supervises it.

The panel provides crash restart, log rotation, boot autostart and a CLI escape
hatch (`serverpanel --apps …`). On hosts without systemd (PRoot / container) this
is the recommended way to keep DogBuddyCn alive; it is also what makes
`manage.py update` able to restart the service through the panel API.

    python3 scripts/register_panel_app.py \
        --panel http://127.0.0.1:8787 --password '***' \
        --app-dir /opt/dogbuddy --config /etc/dogbuddy/config.yaml \
        --agent-token "$(serverpanel --show-agent-token)"

Requires the panel to be in internal mode (`serverpanel --enable-internal`).
Nothing in the panel is modified: only its published HTTP API is used.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

DEFAULT_NAME = "dogbuddy"


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def _call(opener, method: str, url: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, method=method, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"panel {method} {url} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"panel {method} {url} unreachable: {e}") from e
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError:
        return {"raw": body}


def find_app(opener, base: str, name: str) -> dict | None:
    listing = _call(opener, "GET", f"{base}/api/panel/apps")
    for app in listing.get("apps", []):
        if app.get("name") == name:
            return app
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="register DogBuddyCn as a panel managed app")
    ap.add_argument("--panel", default="http://127.0.0.1:8787")
    ap.add_argument("--password", help="panel login password (or PANEL_PASSWORD env)")
    ap.add_argument("--app-dir", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--config", default="/etc/dogbuddy/config.yaml")
    ap.add_argument("--name", default=DEFAULT_NAME)
    ap.add_argument("--agent-token", default="", help="panel agent token (stored in the app env)")
    ap.add_argument("--port", type=int, default=8100, help="web console port (documentation only)")
    ap.add_argument("--autostart", action="store_true", default=True)
    ap.add_argument("--no-start", action="store_true", help="register but do not start")
    ap.add_argument("--unregister", action="store_true", help="stop and delete the managed app")
    args = ap.parse_args()

    base = args.panel.rstrip("/")
    app_dir = Path(args.app_dir).resolve()
    venv_python = app_dir / ".venv" / "bin" / "python"
    if not venv_python.exists():
        print(f"ERROR: {venv_python} not found — run install.sh first", file=sys.stderr)
        return 1

    import os
    password = args.password or os.environ.get("PANEL_PASSWORD", "")

    opener = _opener()
    if password:
        _call(opener, "POST", f"{base}/api/auth/login", {"password": password})
    else:
        print("WARNING: no --password given; relying on an existing session cookie", file=sys.stderr)

    existing = find_app(opener, base, args.name)

    if args.unregister:
        if not existing:
            print(f"app '{args.name}' not registered")
            return 0
        app_id = existing.get("id")
        try:
            _call(opener, "POST", f"{base}/api/panel/apps/{app_id}/stop")
        except SystemExit as e:
            print(f"stop: {e}", file=sys.stderr)
        _call(opener, "DELETE", f"{base}/api/panel/apps/{app_id}")
        print(f"unregistered {args.name} ({app_id})")
        return 0

    env = {
        "DOGBUDDY_CONFIG": str(args.config),
        "DOGBUDDY_PANEL_URL": base,
        "DOGBUDDY_PANEL_APP_ID": "",  # filled in after the id is known
        "PYTHONUNBUFFERED": "1",
    }
    if args.agent_token:
        env["DOGBUDDY_PANEL_TOKEN"] = args.agent_token

    definition = {
        "name": args.name,
        "description": f"DogBuddyCn multi-agent team (web console on :{args.port})",
        "command": f"{venv_python} -m dogbuddy.main",
        "cwd": str(app_dir),
        "env": env,
        "autostart": bool(args.autostart),
        "restart": "on-failure",
        "max_restarts": 10,
        "restart_delay": 3.0,
    }

    if existing:
        app_id = existing["id"]
        definition["env"]["DOGBUDDY_PANEL_APP_ID"] = app_id
        app = _call(opener, "PUT", f"{base}/api/panel/apps/{app_id}", definition).get("app", {})
        print(f"updated managed app {args.name} ({app_id})")
    else:
        app = _call(opener, "POST", f"{base}/api/panel/apps", definition).get("app", {})
        app_id = app.get("id", "")
        if app_id:
            definition["env"]["DOGBUDDY_PANEL_APP_ID"] = app_id
            _call(opener, "PUT", f"{base}/api/panel/apps/{app_id}", definition)
        print(f"registered managed app {args.name} ({app_id})")

    print(f"   command: {definition['command']}")
    print(f"   cwd    : {definition['cwd']}")
    print(f"   env    : DOGBUDDY_CONFIG={definition['env']['DOGBUDDY_CONFIG']}")
    print(f"          : DOGBUDDY_PANEL_APP_ID={definition['env']['DOGBUDDY_PANEL_APP_ID']}")
    print("   note   : `manage.py update` will restart through the panel API using this id")

    if not args.no_start and app_id:
        _call(opener, "POST", f"{base}/api/panel/apps/{app_id}/start")
        print(f"started {app_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
