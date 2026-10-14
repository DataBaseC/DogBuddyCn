# DogBuddyCn — assembly root (all layers wired here)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""main.py: DI assembly + lifecycle (gateway / cron / web / bus tasks)."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
from pathlib import Path
from typing import Any

import uvicorn

from . import __version__
from .config import AppConfig, load_config, save_config
from .cron.jobs import JobStore
from .cron.scheduler import CronScheduler
from .db import Database
from .events.bus import CONFIRM_REQUEST, WORKER_QUESTION, Event, EventBus
from .external.mcp_bridge import McpBridge
from .gateway.adapters.onebot_v11 import OneBotV11Adapter
from .gateway.router import Router
from .llm.profile_registry import LLMProfileRegistry
from .memory.service import MemoryService
from .notify.base import build_notifiers
from .services.admin_service import AdminService
from .services.config_service import ConfigService
from .services.file_service import FileService
from .services.job_manager import JobManager
from .services.model_service import ModelService
from .services.report_service import ReportService
from .services.decisions import DecisionService
from .services.group_manager import GroupManager
from .services.session_manager import SessionManager
from .services.window_manager import WindowManager
from .services.workspace_manager import WorkspaceManager
from .web.app import WebServices, create_app
from .web.sse import SseBroker

logger = logging.getLogger("dogbuddy.main")


class DogBuddyApp:
    """Holds all wired components; started by manage.py / uvicorn factory."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._rate_limit = {"waiting": False, "attempt": 0, "window_id": ""}
        # T8.8：最近一次限速严重度/等待时长（UI 徽标 + 项目迭代降速都用它）
        self._rate_limit_severity = ""
        self._last_rate_limit_wait = 0.0
        self._setup_logging()
        self.db = Database(config.db_path)
        self.memory = MemoryService(config.memory_dir)
        self.bus = EventBus()
        self.panel = self._build_panel()
        # T8.7：退避记忆落盘位置（每 profile "上次退避多久才成功"）
        from .llm.retry import RetryMemory
        self.retry_memory = RetryMemory(Path(config.paths.data_dir) / "retry_memory.json")
        # named LLM config sets → providers (windows/jobs may bind a profile)
        self.profiles = LLMProfileRegistry(config, on_wait_hook=self._on_llm_wait,
                                           retry_memory=self.retry_memory,
                                           on_severity_hook=self._on_llm_severity)
        # callable accepting an optional profile and returning a *provider*.
        # Binding `profiles.factory` here (v1.3) returned a lambda instead of a
        # provider → every real LLM call failed with
        # "'function' object has no attribute 'chat'".
        self.llm_factory = self.profiles.provider_for
        # durable pending-decision queue (T2.3): ask_user lands here and waits
        self.decisions = DecisionService(self.db, bus=self.bus)
        # SSE fan-out for the console live feed (T2.1)
        self.sse = SseBroker(self.bus)
        # MCP bridge (T5): external tool servers, permissions default to read
        self.mcp = McpBridge(config_get=self._mcp_config_get)

        # IM adapters first (gateway notifier channels need the adapter)
        self.adapter: Any = None
        self.router: Any = None
        if config.gateway.enabled:
            self.adapter = OneBotV11Adapter(
                host=config.gateway.host, port=config.gateway.port,
                access_token=config.gateway.access_token,
                whitelist=config.gateway.whitelist or self._whitelist_ids())

        self.windows = WindowManager(
            db=self.db, llm_factory=self.llm_factory, bus=self.bus, panel=self.panel,
            memory=self.memory, checkpoints_dir=config.checkpoints_dir,
            max_iterations=config.max_iterations,
            threshold=config.llm.compact_threshold,
            max_context_tokens=config.llm.max_context_tokens,
            model_resolver=config.effective_model,
            param_resolver=config.effective_params,
            decisions=self.decisions,
            approval_config=lambda k, d=None: self.config_svc.get(k, d),
            mcp_bridge=self.mcp)
        self.sessions = SessionManager(self.windows, self.db)
        self.groups = GroupManager(self.db, self.windows, bus=self.bus)
        self.workspaces = WorkspaceManager(config.paths.workspaces, self.db)
        self.job_store = JobStore(self.db)
        self.jobs = JobManager(self.job_store, self.windows)
        # T8.1：把 JobStore 与"立即执行"入口交给工位，schedule 工具才能真正排期
        self.windows.job_store = self.job_store
        self.windows.run_job_now = self.jobs.run_now
        self.config_svc = ConfigService(self.db, config, persist=self.persist_config)
        # 模型目录（模型管理页）：目录是备选清单，activate() 才写运行时配置并落盘
        self.models = ModelService(self.db, config, persist=self.persist_config,
                                   fetch_models=self.config_svc.fetch_models)
        self.files = FileService(self._file_roots())
        # T8.5/T8.6：项目迭代计划 + 执行器（到点拉项目团队进共享工作区跑一轮）
        from .services.iteration_executor import IterationExecutor
        from .services.iteration_service import IterationService
        self.iterations = IterationService(
            self.db, windows=self.windows, models=self.models,
            rate_limit_status=lambda: dict(self._rate_limit))
        self.iteration_exec = IterationExecutor(
            service=self.iterations, db=self.db, windows=self.windows,
            groups=self.groups, bus=self.bus)
        self.admin = AdminService(
            db=self.db, llm_factory=self.llm_factory, windows=self.windows, jobs=self.jobs,
            sessions=self.sessions, workspaces=self.workspaces, memory=self.memory,
            config=self.config_svc, bus=self.bus,
            self_status=self.update_status, self_update=self.start_self_update)
        self.notifiers = build_notifiers(config.notify, self.adapter, config.gateway.push_target)
        # worker questions / confirm requests must reach the admin conversation:
        # in v1.3 `AdminService.notify()` was never called, so a worker could ask
        # and nobody would ever answer. The admin decides what to do with them.
        self.bus.subscribe(WORKER_QUESTION, self._on_worker_question)
        self.bus.subscribe(CONFIRM_REQUEST, self._on_worker_confirm)
        self.report = ReportService(
            bus=self.bus, notifiers=self.notifiers, llm_factory=self.llm_factory,
            default_strategy=self.config_svc.get("report.default_strategy", "brief"))
        self.report.decisions = self.decisions
        self.scheduler = CronScheduler(
            store=self.job_store, run_job=self._run_job, bus=self.bus, tick_seconds=60)
        if self.adapter is not None:
            self.router = Router(self.adapter, self.admin)
        self.web_services = WebServices(
            db=self.db, windows=self.windows, jobs=self.jobs, sessions=self.sessions,
            workspaces=self.workspaces, config=self.config_svc, memory=self.memory,
            files=self.files, admin=self.admin, app_config=config,
            decisions=self.decisions, sse=self.sse, groups=self.groups, models=self.models,
            iterations=self.iterations, iteration_exec=self.iteration_exec,
            rate_limit_status=lambda: dict(self._rate_limit),
            on_startup=self.start, on_shutdown=self.stop,
            persist_config=self.persist_config)
        self.web_app = create_app(self.web_services)
        self._tasks: list[asyncio.Task] = []
        self._started = False

    # ------------------------------------------------------------- builders
    def _setup_logging(self) -> None:
        self.config.logs_dir.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
            fh = logging.handlers.RotatingFileHandler(
                self.config.logs_dir / "dogbuddy.log", maxBytes=5 * 1024 * 1024,
                backupCount=5, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
            root.addHandler(fh)
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
            root.addHandler(sh)

    def _build_panel(self):
        from .external.panel_client import build_panel_client
        return build_panel_client(self.config.panel.base_url,
                                  self.config.panel.agent_token,
                                  enabled=self.config.panel.enabled)

    def persist_config(self) -> str:
        """Single write path for configuration: save YAML, then drop cached LLM providers.

        Every settings mutation (console routes, admin tools) funnels here, so a
        change survives a restart and takes effect without one.
        """
        path = save_config(self.config)
        try:
            self.profiles.invalidate()
        except Exception:  # noqa: BLE001 - cache invalidation must never break a save
            logger.warning("LLM provider cache invalidation failed", exc_info=True)
        logger.info("configuration saved to %s", path)
        return str(path)

    # ------------------------------------------------------------ self upgrade
    def update_status(self, check_remote: bool = False) -> dict:
        """What version we are, where the code came from, and how the last upgrade went."""
        from .config import config_path
        from .updater import git_info, read_result, remote_head

        stamp_file = Path(__file__).resolve().parent.parent / "VERSION"
        try:
            build = stamp_file.read_text(encoding="utf-8").strip()
        except OSError:
            build = ""
        info: dict = {
            "version": __version__,
            "build": build,
            "config": str(config_path()),
            "git": git_info(),
            "last_update": read_result(self.config.paths.data),
        }
        if check_remote:
            info["remote"] = remote_head()
        return info

    def start_self_update(self, ref: str = "", force: bool = False,
                          start_delay: int = 45) -> dict:
        """Launch `manage.py update` detached; it restarts this very process.

        The agent cannot upgrade itself in-process — the update replaces the code
        and restarts the service — so we hand the work to a new session and let
        the outcome land in `update_result.json` for the next turn to report.
        """
        from .config import config_path
        from .updater import spawn_update

        try:
            return spawn_update(config_path(), self.config.paths.data, ref=ref,
                                force=force, start_delay=max(0, int(start_delay)))
        except Exception as e:  # noqa: BLE001
            logger.exception("failed to start self-update")
            return {"started": False, "error": str(e)}

    def _mcp_config_get(self, key: str, default: Any = None) -> Any:
        """Deferred config read — config_svc is wired later in __init__."""
        try:
            return self.config_svc.get(key, default)
        except AttributeError:
            return default

    def _on_llm_wait(self, profile: str, attempt: int, delay: float) -> None:
        """Rate-limit backoff surfaced for the console badge (per profile)."""
        self._rate_limit = {"waiting": True, "attempt": attempt, "window_id": profile or ""}
        self.bus.emit(Event(type="rate_limit.status",
                            payload={"attempt": attempt, "delay": delay, "profile": profile}))
        # T8.8: 记下最近一次等待时长，供迭代速度计算"模型越慢→迭代越慢"
        self._last_rate_limit_wait = float(delay or 0.0)

    def _on_llm_severity(self, profile: str, severity: Any, delay: float,
                         exc: Any = None) -> None:
        """T8.8：限速严重度分档上报 + 联动。

        * light  秒级：仅刷新徽标
        * heavy  分钟级：刷新徽标 + 打出"建议放慢迭代"标记（T8.5 会读）
        * banned 小时级：发出 `rate_limit.paused`，UI 明确显示"已暂停，等待重试"
          ——**区别于 fail 暂停**，调度器把该任务标成 `rate_limited`
        """
        name = getattr(severity, "value", str(severity))
        self._rate_limit_severity = name
        # 供 T8.5 迭代加速/降速判断："模型越慢 → 迭代越慢"
        self._last_rate_limit_wait = float(delay or 0.0)
        self._rate_limit = {"waiting": name != "banned", "attempt": 0,
                            "window_id": profile or "", "severity": name,
                            "delay": float(delay or 0.0)}
        if name == "banned":
            self.bus.emit(Event(type="rate_limit.paused",
                                payload={"profile": profile, "severity": name,
                                         "delay": float(delay or 0.0),
                                         "reason": str(exc or "")[:300]}))
            logger.warning("限速封禁级：profile=%s 需等待 %.0fs，任务已暂停（非失败）",
                           profile or "(默认)", delay or 0.0)
        elif name == "heavy":
            logger.warning("限速重档：profile=%s 需等待 %.0fs，建议放慢迭代节奏",
                           profile or "(默认)", delay or 0.0)

    def _llm_ready(self) -> bool:
        return bool(self.config.llm.api_key) or self.config.llm.provider == "ollama"

    async def _on_worker_question(self, event: Event) -> None:
        """Feed a worker's pending question into the admin session and let it react."""
        if not self._llm_ready():
            logger.info("worker question (no LLM configured): %s", event.payload.get("question", ""))
            return
        payload = event.payload or {}
        question = str(payload.get("question") or "").strip()
        if not question:
            return
        who = payload.get("window_name") or payload.get("window_id") or "?"
        try:
            await self.admin.notify(f"工位「{who}」提问：{question}")
        except Exception:  # noqa: BLE001
            logger.exception("admin notify failed for worker question")

    async def _on_worker_confirm(self, event: Event) -> None:
        """A dangerous command needs a human decision — surface it to the admin."""
        payload = event.payload or {}
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            return
        who = payload.get("window_name") or payload.get("window_id") or "?"
        logger.warning("confirm requested by %s: %s", who, reason)
        if not self._llm_ready():
            return
        try:
            await self.admin.notify(
                f"工位「{who}」请求确认危险操作：{reason}。请判断是否放行，并说明理由。")
        except Exception:  # noqa: BLE001
            logger.exception("admin notify failed for confirm request")

    def _file_roots(self) -> list[str]:
        roots = [self.config.paths.workspaces]
        for w in self.db.list_windows():
            if w.workspace_path and w.workspace_path not in roots:
                roots.append(w.workspace_path)
        return roots

    def _whitelist_ids(self) -> list[str]:
        return [row["qq_id"] for row in self.db.whitelist_list()]

    # ------------------------------------------------------------- pipelines
    async def _run_job(self, job: Any):
        outcome = await self.windows.dispatch_and_wait(
            job.window_id, job.prompt, job_id=job.id,
            kind=getattr(job, "kind", "prompt") or "prompt",
            llm_profile=getattr(job, "llm_profile", "") or "")
        # reset rate-limit badge when a run actually completes
        if outcome.status == "ok":
            self._rate_limit = {"waiting": False, "attempt": 0, "window_id": ""}
        return outcome

    # report delivery now flows through dogbuddy.notify channels (see build_notifiers)

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.bus.emit(Event(type="system.ready", payload={}))
        # C5: 收尸——上次崩溃/被重启时卡在 running 的任务与工位
        try:
            swept = self.db.sweep_orphan_runs()
            if swept.get("runs") or swept.get("windows"):
                logger.warning("孤儿清扫：修正 %d 条任务运行记录、%d 个工位状态",
                               swept["runs"], swept["windows"])
                self.db.audit("system", "orphan:sweep",
                              f"runs={swept['runs']} windows={swept['windows']}")
        except Exception:  # noqa: BLE001 — 清扫失败不能挡住启动
            logger.warning("orphan sweep failed", exc_info=True)
        try:
            await self.mcp.start()  # T5: bridge before windows boot their registries
        except Exception:  # noqa: BLE001 — MCP never blocks startup
            logger.warning("mcp bridge start failed", exc_info=True)
        await self.windows.start()
        self._tasks.append(asyncio.get_event_loop().create_task(self.bus.start()))
        self._tasks.append(asyncio.get_event_loop().create_task(self.scheduler.run_loop()))
        self._tasks.append(asyncio.get_event_loop().create_task(self.iteration_exec.run_loop()))
        self._tasks.append(self.decisions.start_sweeper())
        if self.adapter is not None and self.router is not None:
            self._tasks.append(asyncio.get_event_loop().create_task(self.adapter.run()))
            self._tasks.append(asyncio.get_event_loop().create_task(self.router.run()))
        logger.info("DogBuddyCn %s started (web on %s:%s)", __version__,
                    self.config.web.host, self.config.web.port)

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self.scheduler.stop()
        await self.windows.stop()
        try:
            await self.mcp.close()
        except Exception:  # noqa: BLE001
            logger.debug("mcp close failed", exc_info=True)
        for t in self._tasks:
            t.cancel()
        self.db.close()


def run(config_path: str | None = None) -> None:
    config = load_config(config_path)
    app = DogBuddyApp(config)
    # startup/shutdown run through FastAPI's lifespan (create_app hooks)
    server = uvicorn.Server(uvicorn.Config(
        app=app.web_app, host=config.web.host, port=config.web.port, log_level="info"))
    try:
        server.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
