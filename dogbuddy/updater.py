# DogBuddyCn — self-update plumbing (shared by manage.py / main.py / admin tools)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Everything needed for DogBuddyCn to upgrade its own code.

Two halves:

* **inspection** — where the code came from (`git_info`), what the remote has
  (`remote_head`), and what the last upgrade attempt did (`read_result`).
  Read-only, safe to call from a tool or a web route.
* **launch** — `spawn_update()` starts ``manage.py update`` as a **detached**
  process so the upgrade survives the very restart it performs. That is the
  whole trick behind "让 DBC 自己升级自己": the agent cannot upgrade itself
  in-process, because the update restarts the process running the agent.

The running update writes `update_result.json` into the data dir (see
`write_result`), so after the restart the agent can answer "升级成功了吗".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent
RESULT_NAME = "update_result.json"
DEFAULT_REF = "origin/main"

IS_WINDOWS = sys.platform == "win32"


# --------------------------------------------------------------------------- paths
def app_dir(path: str | Path | None = None) -> Path:
    return Path(path).resolve() if path else APP_DIR


def result_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / RESULT_NAME


def log_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "logs" / "update.log"


# --------------------------------------------------------------------------- result file
def write_result(data_dir: str | Path, **fields: Any) -> Path:
    """Atomically record the outcome of an upgrade (survives the restart)."""
    path = result_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields.setdefault("finished_at", time.time())
    fields.setdefault("finished_at_iso", time.strftime("%Y-%m-%d %H:%M:%S"))
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(fields, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def read_result(data_dir: str | Path) -> dict[str, Any] | None:
    try:
        with open(result_path(data_dir), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- git
def _git(app: Path, *argv: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *argv], cwd=str(app), capture_output=True,
                          text=True, timeout=timeout)


def git_info(app: str | Path | None = None) -> dict[str, Any]:
    """Where this deployment's code came from. Never raises."""
    root = app_dir(app)
    info: dict[str, Any] = {"app_dir": str(root), "is_git": (root / ".git").exists()}
    if not info["is_git"]:
        return info
    for key, argv in (("ref", ("rev-parse", "HEAD")),
                      ("short", ("rev-parse", "--short", "HEAD")),
                      ("describe", ("describe", "--tags", "--always", "--dirty")),
                      ("branch", ("rev-parse", "--abbrev-ref", "HEAD")),
                      ("remote", ("remote", "get-url", "origin"))):
        try:
            out = _git(root, *argv)
            info[key] = out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            info[key] = ""
    return info


def remote_head(ref: str = DEFAULT_REF, app: str | Path | None = None,
                timeout: int = 45) -> dict[str, Any]:
    """Ask the remote what it has, without changing anything locally."""
    root = app_dir(app)
    if not (root / ".git").exists():
        return {"ok": False, "error": "not a git checkout"}
    try:
        out = _git(root, "ls-remote", "origin", ref.split("/", 1)[-1] if "/" in ref else ref,
                   timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": f"ls-remote failed: {e}"}
    if out.returncode != 0:
        return {"ok": False, "error": out.stderr.strip()[:300]}
    for line in out.stdout.splitlines():
        sha = line.split("\t", 1)[0].strip()
        if sha:
            local = git_info(root).get("ref", "")
            return {"ok": True, "remote": sha, "local": local,
                    "update_available": bool(local) and sha != local}
    return {"ok": True, "remote": "", "update_available": False,
            "error": "remote ref not found (empty branch?)"}


# --------------------------------------------------------------------------- launch
def build_update_command(config_path: str | Path, *, ref: str = "", force: bool = False,
                         start_delay: int = 0, bundle: str = "", url: str = "",
                         app: str | Path | None = None) -> list[str]:
    python = str(_venv_python(app_dir(app)))
    cmd = [python, str(app_dir(app) / "manage.py"), "--config", str(config_path), "update"]
    if bundle:
        cmd += ["--from-bundle", bundle]
    elif url:
        cmd += ["--from-url", url]
    elif ref:
        cmd += ["--ref", ref]
    if force:
        cmd.append("--force")
    if start_delay:
        cmd += ["--start-delay", str(start_delay)]
    return cmd


def _venv_python(root: Path) -> Path:
    exe = root / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    return exe if exe.exists() else Path(sys.executable)


def spawn_update(config_path: str | Path, data_dir: str | Path, *, ref: str = "",
                 force: bool = False, start_delay: int = 45, bundle: str = "", url: str = "",
                 app: str | Path | None = None) -> dict[str, Any]:
    """Start an upgrade in a detached process and return immediately.

    `start_delay` gives the calling agent time to finish its turn and persist
    its reply before the service is restarted underneath it.
    """
    root = app_dir(app)
    log = log_path(data_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_update_command(config_path, ref=ref, force=force, start_delay=start_delay,
                               bundle=bundle, url=url, app=root)
    fh = open(log, "ab")
    try:
        kwargs: dict[str, Any] = {"cwd": str(root), "stdout": fh, "stderr": fh,
                                  "stdin": subprocess.DEVNULL}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **kwargs)
    finally:
        fh.close()
    return {"started": True, "pid": proc.pid, "command": " ".join(cmd), "log": str(log),
            "start_delay": start_delay}
