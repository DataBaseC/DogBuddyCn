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
from ..updater import read_result
from ..windows.compactor import compaction_stats as _compaction_stats
from .routes import auth as auth_routes
from .routes import capabilities as capabilities_routes
from .routes import chat as chat_routes
from .routes import costs as costs_routes
from .routes import decisions as decisions_routes
from .routes import events as events_routes
from .routes import files as files_routes
from .routes import gateway as gateway_routes
from .routes import groups as groups_routes
from .routes import iterations as iterations_routes
from .routes import runs as runs_routes
from .routes import ledger as ledger_routes
from .routes import jobs as jobs_routes
from .routes import logs as logs_routes
from .routes import models as models_routes
from .routes import news as news_routes
from .routes import industry as industry_routes
from .routes import pipelines as pipelines_routes
from .routes import ops as ops_routes
from .routes import qc as qc_routes
from .routes import projects as projects_routes
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
        #: v0.10.37：运行台账（`/api/runs`、`/api/status.active_runs` 用）。
        #: 注意 Web 层拿到的是**独立服务包**（`WebServices`），不是 app 本身 ——
        #: 忘了在这里登记就会 503「运行台账未启用」，而运行其实跑得好好的。
        self.runs = kw.get("runs")
        self.jobs = kw.get("jobs")
        self.sessions = kw.get("sessions")
        self.workspaces = kw.get("workspaces")
        self.config = kw.get("config")
        self.memory = kw.get("memory")
        self.files = kw.get("files")
        self.admin = kw.get("admin")
        self.decisions = kw.get("decisions")
        self.groups = kw.get("groups")
        self.sse = kw.get("sse")
        #: v0.10.50：每日备份状态（`/healthz` 的 `backups` 字段）
        self.backup = kw.get("backup")
        #: v0.10.50：网关状态（`/api/gateway/status`；QQ 只接管理员）
        self.adapter = kw.get("adapter")
        #: v0.11.0：成本账本（`/api/costs/*` 与 `/healthz.costs`）
        self.cost = kw.get("cost")
        #: v0.12.0：模型分级路由的样本台账（`/api/costs/routes`、`/healthz.routing`）
        self.routing = kw.get("routing")
        #: v0.12.1：流水线引擎（`/api/pipelines`、`/healthz.pipelines`）
        self.pipelines = kw.get("pipelines")
        #: v0.12.2：抽检服务（`/api/qc/*`、`/healthz.qc`）
        self.qc = kw.get("qc")
        #: v0.13.0：指标推送与视觉服务（`/api/metrics/*`、`/api/ingress/*`）
        self.metrics = kw.get("metrics")
        self.cv = kw.get("cv")
        #: v0.14.0：运营回合（`/api/ops/*`、`/healthz.ops`）
        self.ops = kw.get("ops")
        #: v0.15.0：站点运营（`/news` 静态站、`/api/news/status`）
        self.news = kw.get("news")
        self.site_dir = str(kw.get("site_dir") or "")
        self.models = kw.get("models")
        self.iterations = kw.get("iterations")
        self.iteration_exec = kw.get("iteration_exec")
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
            # v0.10.50：每日备份的最后一口气（没有备份 = 出事就全丢，必须能一眼看到）
            "backups": (svc.backup.status() if getattr(svc, "backup", None) else None),
            # v0.10.55：压缩的健康度（降级率/最近一次 token）——降级=上下文被截断，
            # 会一路推高后续成本，必须能一眼看到
            "compactions": _compaction_stats(),
            # v0.11.0：今天的钱（没配单价时会明说"金额会是 0"）
            "costs": (svc.cost.health() if getattr(svc, "cost", None) else None),
            # v0.12.0：分级路由有多少样本（冷启动期只降不升，样本数是判断依据）
            "routing": (svc.routing.health() if getattr(svc, "routing", None) else None),
            # v0.12.1：流水线（"有没有卡住/失败"要一眼看到）
            "pipelines": (svc.pipelines.health()
                          if getattr(svc, "pipelines", None) else None),
            # v0.12.2：抽检（缺陷率/漏检估计是工业线的核心指标）
            "qc": (svc.qc.health() if getattr(svc, "qc", None) else None),
            # v0.13.0：指标新鲜度 + 视觉服务是否配好（"数据过期"是工业现场最常见故障）
            "metrics": (svc.metrics.health() if getattr(svc, "metrics", None) else None),
            "cv": (svc.cv.health() if getattr(svc, "cv", None) else None),
            # v0.14.0：运营回合（卡住的回合要能一眼看到）
            "ops": (svc.ops.health() if getattr(svc, "ops", None) else None),
            # v0.15.0：站点（文章数/更新时间；"站还活着吗"一眼可见）
            "news": (svc.news.status() if getattr(svc, "news", None) else None),
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
        if getattr(svc, "sse", None) is not None:
            svc.sse.start()
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
    app.include_router(capabilities_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(chat_routes.router(svc, auth_deps), prefix="/api")
    # v0.11.0：成本账本（工业自动化的第一步：先把钱变成数字）
    app.include_router(costs_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(decisions_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(events_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(ledger_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(groups_routes.router(svc, auth_deps), prefix="/api")
    # v0.10.50：网关状态（QQ 只接管理员之后，"通没通"要一眼可见）
    app.include_router(gateway_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(windows_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(jobs_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(files_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(models_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(projects_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(iterations_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(runs_routes.router(svc, auth_deps), prefix="/api")
    # v0.12.1：流水线编排（定义/起跑/状态/续跑）
    app.include_router(pipelines_routes.router(svc, auth_deps), prefix="/api")
    # v0.12.2：抽检与复核
    app.include_router(qc_routes.router(svc, auth_deps), prefix="/api")
    # v0.13.0：工业信号与事件接入
    app.include_router(industry_routes.router(svc, auth_deps), prefix="/api")
    # v0.14.0：运营回合（目标/回合/日志/实验）
    app.include_router(ops_routes.router(svc, auth_deps), prefix="/api")
    # v0.15.0：站点运营（状态查询；站点本体是静态文件）
    app.include_router(news_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(settings_routes.router(svc, auth_deps), prefix="/api")
    app.include_router(logs_routes.router(svc, auth_deps), prefix="/api")

    # ---- status & overview (kept here: trivial DTOs) ----
    from fastapi import APIRouter
    base = APIRouter()

    @base.get("/api/status", dependencies=auth_deps)
    def status() -> dict:
        app_cfg = getattr(svc, "app_config", None)
        default_model = app_cfg.effective_model(None, "") if app_cfg is not None else ""
        # P0（v0.10.37）："此刻谁在跑什么" —— 前端顶栏读的就是这里。
        # 注意 `/api/status` 与 `/api/overview` 是**两个**接口，别改错地方（实测踩过一次）。
        active_runs: list = []
        run_summary: dict = {}
        reg = getattr(svc, "runs", None)
        if reg is not None:
            try:
                active_runs = [x.as_dict() for x in reg.active()]
                run_summary = reg.summary()
            except Exception:  # noqa: BLE001 — 状态页不能因为台账抖动而 500
                logger.warning("status: runs lookup failed", exc_info=True)
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
            "last_update": (read_result(app_cfg.paths.data) if app_cfg is not None else None),
            "active_runs": active_runs,
            "run_summary": run_summary,
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
        # P0（v0.10.37）：顶部指示器一次拿全"有什么在跑"
        active_runs = []
        run_summary = {}
        reg = getattr(svc, "runs", None)
        if reg is not None:
            try:
                active_runs = [x.as_dict() for x in reg.active()]
                run_summary = reg.summary()
            except Exception:  # noqa: BLE001 — 状态页不能因为台账抖动而 500
                pass
        return {"windows": win_rows, "jobs": job_rows, "recent_runs": runs,
                "active_runs": active_runs, "run_summary": run_summary}

    app.include_router(base)

    # ---- static console ----
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(static_dir)), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(str(static_dir / "index.html"))

    # v0.15.0：运营站点（静态）—— `/news/` 直接访问，不需要登录
    if getattr(svc, "site_dir", ""):
        app.mount("/news", StaticFiles(directory=str(svc.site_dir), html=True,
                                       check_dir=False), name="news")

    return app
