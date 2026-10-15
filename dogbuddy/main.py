# DogBuddyCn — assembly root (all layers wired here)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""main.py: DI assembly + lifecycle (gateway / cron / web / bus tasks)."""
from __future__ import annotations

import time

import shutil

import os

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


#: 快照保留天数（P2-2：每次整工作区拷贝，上限 200MB，且原来没有 prune）
SNAPSHOT_KEEP_DAYS = 14


def _prune_snapshots(checkpoints_dir: Any, keep_days: int = 0) -> int:
    """删掉过期的工作区快照目录（`snapshots/<UTC 时间戳>/`）。返回删除个数。"""
    days = int(keep_days or os.environ.get("DOGBUDDY_SNAPSHOT_DAYS") or SNAPSHOT_KEEP_DAYS)
    root = Path(checkpoints_dir or "") / "snapshots"
    if not root.is_dir():
        return 0
    cutoff = time.time() - max(1, days) * 86400
    removed = 0
    try:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        return removed
    return removed


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
                                   fetch_models=self.config_svc.fetch_models,
                                   fetch_meta=self.config_svc.fetch_models_meta)
        # P2-3：传**函数**而不是当前列表——运行中新建的工位目录要能立刻被允许
        self.files = FileService(self._file_roots)
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
        # T9.3：主管工位装配（只给项目级管理工具，system 级永不下放）；
        # P1-3：管理员工位也要装（否则是个带着管理员提示词却没有工具的壳）。
        self.windows.management_tools_factory = lambda w: self.admin.tools_for(w)
        # T9.4：三层指挥链服务（管理员→主管→员工；无主管时退化直连）
        from .services.supervision_service import SupervisionService
        self.supervision = SupervisionService(
            db=self.db, windows=self.windows, bus=self.bus,
            admin_notify=self._notify_admin_up, route_reports=True)
        self.supervision.start()
        # T9.6：审批逐级路由 —— 员工的高危操作先找主管；不可逆动作仍直接升级管理员
        self.windows.approval_router = self.supervision.approval_target
        self.windows.supervision_dispatch = self.supervision.dispatch_text
        # T9.4（审查报告 P1-1）：把指挥链交给"上报生产者"与管理侧入口。
        # 没有这一步，`worker.report`/`supervisor.command` 在生产路径上**无人投递**：
        # 处理器与单测都是好的，但功能从不发生。
        self.admin.set_supervision(self.supervision)
        # v0.10.14：console 渠道不再只写日志——汇报要**写进用户的对话流**，
        # 否则"员工干完活逐级汇报"用户永远看不见（他要看的是控制台，不是日志）。
        self.notifiers = build_notifiers(config.notify, self.adapter,
                                         config.gateway.push_target,
                                         chat_sink=self.admin.notify_user)
        # worker questions / confirm requests must reach the admin conversation:
        # in v1.3 `AdminService.notify()` was never called, so a worker could ask
        # and nobody would ever answer. The admin decides what to do with them.
        self.bus.subscribe(WORKER_QUESTION, self._on_worker_question)
        self.bus.subscribe(CONFIRM_REQUEST, self._on_worker_confirm)
        self.report = ReportService(
            bus=self.bus, notifiers=self.notifiers, llm_factory=self.llm_factory,
            default_strategy=self.config_svc.get("report.default_strategy", "brief"))
        self.report.decisions = self.decisions
        # T9.4（审查报告 P1-1）：逐级收敛的生产者入口
        self.report.supervision = self.supervision
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
        """装日志处理器。**先装 stderr，再尝试装文件**（审查报告 P2-5）。

        初版第一步就是 `logs_dir.mkdir()`——磁盘/目录不可写时直接抛，而那时
        文件处理器还没装上，于是**磁盘上没有任何日志可查**，只能看 stdout。
        现在文件日志失败只降级，启动不受影响。
        """
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
            sh = logging.StreamHandler()
            sh.setFormatter(fmt)
            root.addHandler(sh)
        if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
            return
        try:
            self.config.logs_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                self.config.logs_dir / "dogbuddy.log", maxBytes=5 * 1024 * 1024,
                backupCount=5, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError as e:
            logging.getLogger("dogbuddy").warning(
                "日志目录不可写（%s），仅输出到 stderr：%s", self.config.logs_dir, e)

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

    def _notify_admin_up(self, text: str, level: str = "info") -> None:
        """T9.4 退化路径：无主管时员工上报直连管理员。

        与既有通知链保持一致（走 AdminService.notify → 通知器），
        只是加一个层级前缀，让人一眼看出这条是"没有主管才直达"的。
        """
        prefix = {"alert": "【需处理】", "warn": "【需关注】"}.get(level, "")
        msg = f"{prefix}{text}"
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.admin.notify(msg))
            else:  # pragma: no cover — 非事件循环上下文（脚本/测试）
                loop.run_until_complete(self.admin.notify(msg))
        except Exception:  # noqa: BLE001 — 退化通知失败不能炸总线
            logger.warning("admin notify failed for hierarchical fallback", exc_info=True)

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
        """Feed a worker's pending question into the admin session and let it react.

        T9.4（审查报告 P1-1）：项目**有主管**时，员工提问应由指挥链先给主管
        （逐级收敛）。初版这里无条件直推管理员，于是"员工→主管→管理员"这条
        链上永远少一跳——即使 `SupervisionService` 的处理器完全正确。
        """
        payload = event.payload or {}
        wid = str(payload.get("window_id") or "")
        if self.supervision is not None and wid:
            try:
                if self.supervision.routes_to_supervisor(wid):
                    logger.info("worker question routed to supervisor (hierarchical): %s", wid)
                    return
            except Exception:  # noqa: BLE001 — 判定失败就按 v1 直连，不吞掉提问
                logger.exception("hierarchical check failed for worker question")
        if not self._llm_ready():
            logger.info("worker question (no LLM configured): %s", event.payload.get("question", ""))
            return
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

    def _prune_snapshots(self, checkpoints_dir: Any, keep_days: int = 0) -> int:
        return _prune_snapshots(checkpoints_dir, keep_days)

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
            if swept.get("runs") or swept.get("windows") or swept.get("iteration_rounds"):
                logger.warning("孤儿清扫：修正 %d 条任务运行记录、%d 个工位状态、%d 个迭代轮次",
                               swept["runs"], swept["windows"], swept.get("iteration_rounds", 0))
                self.db.audit("system", "orphan:sweep",
                              f"runs={swept['runs']} windows={swept['windows']} "
                              f"iteration_rounds={swept.get('iteration_rounds', 0)}")
        except Exception:  # noqa: BLE001 — 清扫失败不能挡住启动
            logger.warning("orphan sweep failed", exc_info=True)
        # P2-6：重启后把已经超时的待决事项翻成 timeout（否则它们永远挂着）
        try:
            expired = self.decisions.sweep()
            if expired:
                logger.warning("启动清扫：%d 条待决事项已过期", len(expired))
        except Exception:  # noqa: BLE001
            logger.warning("decision sweep failed", exc_info=True)
        # P2-2：无界增长清理——历史表 + WAL 收回 + 快照目录
        try:
            pruned = self.db.prune_history()
            self.db.wal_checkpoint()
            freed = _prune_snapshots(self.config.checkpoints_dir)
            if any(pruned.values()) or freed:
                logger.info("历史清理：%s；快照目录删除 %d 个", pruned, freed)
                self.db.audit("system", "history:prune", f"{pruned} snapshots={freed}")
        except Exception:  # noqa: BLE001 — 清理失败不能挡住启动
            logger.warning("history prune failed", exc_info=True)
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

    #: 关停时每一步的最长等待（秒）。**必须有界**：
    #: 线上实测（2026-10-15）SIGTERM 之后进程卡住不退，于是端口一直被旧进程占着，
    #: 外部守护启动的新进程 `[Errno 98] address already in use` 秒退 →
    #: 面板进入"启动-退出码3-再启动"的重启循环，升级看起来失败。
    #: 卡死不影响数据安全（DB 用 WAL，重启时会自动恢复），所以宁可强制退出。
    SHUTDOWN_STEP_TIMEOUT: float = 8.0

    async def _shutdown_step(self, coro, what: str) -> None:
        """有界地执行一个关停步骤：超时只记日志，绝不把整个进程挂住。"""
        try:
            await asyncio.wait_for(coro, timeout=self.SHUTDOWN_STEP_TIMEOUT)
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning("关停步骤 %s 超过 %.0fs 未完成，强制继续（端口必须让出来）",
                           what, self.SHUTDOWN_STEP_TIMEOUT)
        except Exception:  # noqa: BLE001 — 关停失败不该阻断后面的清理
            logger.warning("关停步骤 %s 失败", what, exc_info=True)

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self.scheduler.stop()
        await self._shutdown_step(self.windows.stop(), "windows.stop")
        await self._shutdown_step(self.mcp.close(), "mcp.close")
        for t in self._tasks:
            t.cancel()
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            logger.warning("db.close failed", exc_info=True)


def build_uvicorn_config(app: DogBuddyApp, config: AppConfig) -> uvicorn.Config:
    """uvicorn 配置。`timeout_graceful_shutdown` 是关停的**第二道保险**：
    即使某个请求/任务不肯放手，10 秒后也强制退出——否则 SIGTERM 会"看起来没反应"。
    """
    return uvicorn.Config(app=app.web_app, host=config.web.host, port=config.web.port,
                          log_level="info", timeout_graceful_shutdown=10)


def run(config_path: str | None = None) -> None:
    config = load_config(config_path)
    app = DogBuddyApp(config)
    # startup/shutdown run through FastAPI's lifespan (create_app hooks)
    server = uvicorn.Server(build_uvicorn_config(app, config))
    try:
        server.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
