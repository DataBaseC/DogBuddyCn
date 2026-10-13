# DogBuddyCn — web console API (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""FastAPI app assembly: auth middleware + thin route modules + static console.

Handlers only validate params → call L2 services → return DTOs (no business).
"""
from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .routes import auth as auth_routes
from .routes import chat as chat_routes
from .routes import files as files_routes
from .routes import jobs as jobs_routes
from .routes import logs as logs_routes
from .routes import settings as settings_routes
from .routes import windows as windows_routes

STARTED_AT = time.time()
# repo root = parent of the package directory (…/DogBuddyCn)
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def build_stamp() -> str:
    """Deployment marker written by `manage.py update`/`install`; not the version."""
    p = BASE_DIR / "VERSION"
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return "source"



class WebServices:
    """Everything the routes may call (L2 services + config)."""

    def __init__(self, **kw: Any):
        self.db = kw.get("db")
        self.windows = kw.get("windows")
        self.jobs = kw.get("jobs")
        self.sessions = kw.get("sessions")
        self.workspaces = kw.get("workspaces")
        self.config = kw.get("config")
        self.memory = kw.get("memory")
        self.files = kw.get("files")
        self.admin = kw.get("admin")
        self.app_config = kw.get("app_config")
        self.rate_limit_status = kw.get("rate_limit_status", lambda: {"waiting": False, "attempt": 0})
        self.on_startup = kw.get("on_startup")    # async callable (DogBuddyApp.start)
        self.on_shutdown = kw.get("on_shutdown")  # async callable (DogBuddyApp.stop)
        self.persist_config = kw.get("persist_config")  # callable() -> str (save + invalidate)

    def persist(self) -> str:
        """Write the live AppConfig to disk.

        Wired in `main.py` so it can also invalidate the LLM provider cache.
        Every settings mutation must call this or the change is lost on
        restart. Returns the config file path, or "" when nothing was saved.
        """
        if callable(self.persist_config):
            return str(self.persist_config())
        if self.app_config is not None:
            from ..config import save_config

            return str(save_config(self.app_config))
        return ""


def create_app(svc: WebServices) -> FastAPI:
    app = FastAPI(title="DogBuddyCn", version=__version__)
    app.state.svc = svc

    # ---- health & version (UNAUTHENTICATED on purpose) ----
    # The self-update chain polls these two endpoints: `/healthz` to know the
    # process is up, `/api/version` to assert the new version is actually the
    # one serving traffic. They must never require auth or they would break
    # upgrades on instances that have a token configured.
    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "ok": True,
            "version": __version__,
            "build": build_stamp(),
            "schema_version": (svc.db.schema_version if getattr(svc, "db", None) else None),
            "pid": os.getpid(),
            "python": platform.python_version(),
            "uptime_s": round(time.time() - STARTED_AT, 1),
        }

    @app.get("/api/version")
    def api_version() -> dict:
        return {
            "name": "DogBuddyCn",
            "version": __version__,
            "build": build_stamp(),
            "schema_version": (svc.db.schema_version if getattr(svc, "db", None) else None),
            "python": platform.python_version(),
            "executable": sys.executable,
            "pid": os.getpid(),
        }

    @app.on_event("startup")
    async def _startup() -> None:
        if svc.on_startup is not None:
            await svc.on_startup()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if svc.on_shutdown is not None:
            await svc.on_shutdown()

    def require_auth(x_auth_token: str = Header(default="")) -> str:
        expected = getattr(svc.app_config.web, "auth_token", "") or getattr(svc.app_config.web, "password", "")
        if expected and x_auth_token != expected:
            raise HTTPException(status_code=401, detail="unauthorized")
        return x_auth_token

    auth_deps = [Depends(require_auth)]

    app.include_router(auth_routes.router(svc), prefix="/api")
    app.include_router(chat_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(windows_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(jobs_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(files_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(settings_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(logs_routes.router(svc, auth_deps), prefix="/api")

    # ---- status & overview (kept here: trivial DTOs) ----
    from fastapi import APIRouter
    base = APIRouter()

    @base.get("/api/status", dependencies=auth_deps)
    def status() -> dict:
        app_cfg = getattr(svc, "app_config", None)
        default_model = app_cfg.effective_model(None, "") if app_cfg is not None else ""
        return {
            "running": True,
            "rate_limit": svc.rate_limit_status(),
            "windows": len(svc.windows.list()),
            "jobs": len(svc.jobs.list()),
            "llm_configured": bool(svc.config and svc.config.llm_configured()),
            # what a window/job without an override will actually use — the admin
            # agent uses this same default, so workers inherit the admin's model
            "llm_model": default_model,
            "llm_profile": (app_cfg.active_llm_profile if app_cfg is not None else ""),
        }

    @base.get("/api/overview", dependencies=auth_deps)
    def overview() -> dict:
        win_rows = []
        for w in svc.windows.list():
            st = svc.windows.status(w.id)
            win_rows.append({
                "id": w.id, "name": w.name, "role": w.role, "model": w.model,
                "mode": w.mode, "status": w.status,
                "current_task": st.get("current_task", ""),
                "workspace_path": w.workspace_path,
                "last_output": st.get("last_output", ""),
            })
        job_rows = []
        for j in svc.jobs.list():
            w = svc.windows.get(j.window_id)
            job_rows.append({
                "id": j.id, "name": j.name, "window_name": w.name if w else j.window_id,
                "enabled": j.enabled, "next_run_at": j.next_run_at,
                "last_status": "", "fail_count": j.fail_count,
            })
        runs = svc.jobs.runs(limit=10)
        names = {w.id: w.name for w in svc.windows.list()}
        for r in runs:
            r["window_name"] = names.get(r["window_id"], r["window_id"])
            r["job_name"] = (svc.jobs.get(r["job_id"]).name if svc.jobs.get(r["job_id"]) else r["job_id"])
        return {"windows": win_rows, "jobs": job_rows, "recent_runs": runs}

    app.include_router(base)

    # ---- static console ----
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(static_dir)), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(str(static_dir / "index.html"))

    return app
