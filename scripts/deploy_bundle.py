#!/usr/bin/env python3
# DogBuddyCn — push a release bundle to a remote instance and trigger the self-update
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""One-command remote upgrade over the AegisServerPanel API.

Runs on the development machine:

    python scripts/deploy_bundle.py \
        --bundle dist/dogbuddy-0.1.6.tar.gz \
        --panel http://192.168.1.2:8787 \
        --password '<面板密码>' --agent-token '<Agent 令牌>' \
        --app-dir /opt/dogbuddy --config /etc/dogbuddy/config.yaml

What it does (nothing else — the panel is never modified):
  1. login → session cookie           (POST /api/auth/login)
  2. upload the bundle                (POST /api/files/upload)
  3. run `manage.py update --from-bundle …` on the server
                                      (POST /api/terminal/exec, X-Agent-Token)

Step 3 is the real upgrade: the server-side chain does lock → config backup →
code switch → dependency install → restart → /healthz version assertion →
automatic rollback. This script only transports the artifact.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path


def _post_json(opener, url: str, payload: dict, timeout: int = 60, headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, method="POST", data=data, headers=hdrs)
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _upload(opener, base: str, remote_dir: str, file: Path) -> dict:
    """Minimal multipart/form-data POST (stdlib only)."""
    boundary = "----dogbuddy" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                     f"{value}\r\n".encode("utf-8"))

    field("path", remote_dir)
    field("overwrite", "true")
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{file.name}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n".encode("utf-8"))
    parts.append(file.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    req = urllib.request.Request(f"{base}/api/files/upload", method="POST", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                                          "Content-Length": str(len(body))})
    with opener.open(req, timeout=600) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _exec(opener, base: str, token: str, command: str, timeout: int) -> dict:
    req = urllib.request.Request(
        f"{base}/api/terminal/exec", method="POST",
        data=json.dumps({"command": command, "timeout": min(max(timeout, 1), 600)}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Agent-Token": token})
    with opener.open(req, timeout=timeout + 30) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def main() -> int:
    ap = argparse.ArgumentParser(description="upload a bundle and trigger the remote self-update")
    ap.add_argument("--bundle", required=True, help="local dist/dogbuddy-<ver>.tar.gz")
    ap.add_argument("--panel", default="http://127.0.0.1:8787")
    ap.add_argument("--password", default=os.environ.get("PANEL_PASSWORD", ""))
    ap.add_argument("--agent-token", default=os.environ.get("PANEL_AGENT_TOKEN", ""))
    ap.add_argument("--remote-dir", default="/root/dogbuddy-releases")
    ap.add_argument("--app-dir", default="/opt/dogbuddy")
    ap.add_argument("--config", default="/etc/dogbuddy/config.yaml")
    ap.add_argument("--panel-app-id", default=os.environ.get("DOGBUDDY_PANEL_APP_ID", ""),
                    help="panel managed-app id → the updater restarts through the panel")
    ap.add_argument("--no-update", action="store_true", help="upload only, do not run the upgrade")
    ap.add_argument("--exec-timeout", type=int, default=600)
    args = ap.parse_args()

    bundle = Path(args.bundle)
    if not bundle.is_file():
        print(f"ERROR: bundle not found: {bundle}", file=sys.stderr)
        return 1
    base = args.panel.rstrip("/")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    if args.password:
        _post_json(opener, f"{base}/api/auth/login", {"password": args.password})
        print("== logged in")
    else:
        print("WARNING: no --password; upload will fail unless a session exists", file=sys.stderr)

    _exec(opener, base, args.agent_token, f"mkdir -p {args.remote_dir} && echo ok", 60)
    res = _upload(opener, base, args.remote_dir, bundle)
    if not res.get("ok"):
        print(f"ERROR: upload failed: {res}", file=sys.stderr)
        return 1
    print(f"== uploaded {bundle.name} ({bundle.stat().st_size / 1024 / 1024:.2f} MB) "
          f"-> {res.get('saved')}")

    if args.no_update:
        return 0

    remote_pkg = f"{args.remote_dir}/{bundle.name}"
    env = f"DOGBUDDY_CONFIG={args.config}"
    if args.panel_app_id:
        env += f" DOGBUDDY_PANEL_APP_ID={args.panel_app_id}"
    command = (f"cd {args.app_dir} && {env} ./.venv/bin/python manage.py update "
               f"--from-bundle {remote_pkg} 2>&1")
    print("== triggering self-update on the server (this restarts the service)")
    out = _exec(opener, base, args.agent_token, command, args.exec_timeout)
    code = out.get("code")
    sys.stdout.write(out.get("stdout", ""))
    sys.stderr.write(out.get("stderr", ""))
    print(f"== remote command exit code: {code}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
