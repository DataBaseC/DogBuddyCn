# DogBuddyCn — window runtime manager (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""WindowManager: window lifecycle + the per-window serial queue runtime.

Owns the process-side runtime of every window (queue, worker task, registry)
and orchestrates L3 domain objects (WindowSession, AgentLoop, Compactor).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..agents.core.loop import AgentResult
from ..agents.core.permissions import PermissionGate
from ..agents.core.roles import get_role
from ..agents.core.runners import RunnerRegistry, RunnerResult, TaskEnv, default_runners
from ..agents.worker_programmer.tools import build_worker_registry
from ..cron.scheduler import JobOutcome
from ..events.bus import (CONFIRM_REQUEST, JOB_COMPLETED, JOB_FAILED, WINDOW_STATUS,
                            WORKER_QUESTION, Event)
from ..ports import Window
from ..tools.registry import ToolContext
from ..windows.compactor import Compactor
from ..windows.context import ContextBuilder
from ..windows.queue import TaskItem, TaskQueue
from ..windows.session import WindowSession

logger = logging.getLogger("dogbuddy.windows.manager")


def _ErrorResult(msg: str, code: str = "internal") -> RunnerResult:  # noqa: N802
    return RunnerResult(status="failed", summary=msg)


def _run_status(result: Any) -> str:
    """一次执行 → `job_runs.status`。

    审查发现（2026-10-14 线上）：这里原来写
    `"ok" if getattr(result, "ok", True) else "failed"`，而 `AgentResult`
    **没有 `ok` 属性** → 默认 True → **每次执行都记成 ok**，哪怕
    `stop_reason == "error"`（LLM 调用失败会出现这种情况）。
    结果就是运行历史里一条"status=ok、summary 是报错"的假成功行。

    现在按 `stop_reason` 判定：
      done / max_iterations → ok（跑到上限不是失败）
      rate_limited          → rate_limited（限速等待，不是失败）
      其余（error 等）       → failed

    v11：真实 runner 返回的是 `RunnerResult`（既没有 `stop_reason` 也没有 `ok`，
    只有 `status`），上面两条分支都命中不了 → **恒判 failed**，于是每次 cron
    都多出一行"假失败"，仪表盘的运行次数翻倍、成功率被腰斩。所以先认 `status`。
    """
    status = str(getattr(result, "status", "") or "")
    if status in ("ok", "failed", "rate_limited"):
        return status
    stop = str(getattr(result, "stop_reason", "") or "")
    if stop:
        return {"done": "ok", "max_iterations": "ok",
                "rate_limited": "rate_limited"}.get(stop, "failed")
    return "ok" if getattr(result, "ok", False) else "failed"


def _run_terminal(result: Any) -> tuple[str, str, str]:
    """一次执行 → **runs 台账**终态 `(status, summary_prefix, error_code)`。

    v0.10.40：与 `_run_status`（job_runs 口径，"跑到上限算 ok"）分开——台账要
    对 owner 诚实：
      * `rate_limited`    → 不写终态（调用方记 pause）；
      * `max_iterations`  → **partial**（撞上限 ≠ 完成，owner 以为活干完了）；
      * `timeout`         → timeout；其余失败 → failed；ok → done。
    """
    st = _run_status(result)
    if st == "rate_limited":
        return ("paused", "", "")
    stop = str(getattr(result, "stop_reason", "") or "")
    raw_status = str(getattr(result, "status", "") or "")
    if stop == "max_iterations":
        return ("partial", "[撞迭代上限·未完成，可 run_retry 续跑] ",
                "max_iterations_reached")
    # `_run_status` 是 job_runs 口径、不认 timeout（falls through 到 failed）——
    # 台账这边要如实记 timeout（`task_timeout` 到点 = 超时，不是"失败"）。
    if st == "timeout" or stop == "timeout" or raw_status == "timeout":
        return ("timeout", "", str(getattr(result, "error_code", "") or ""))
    if st == "ok":
        return ("done", "", "")
    return ("failed", "", str(getattr(result, "error_code", "") or ""))


@dataclass
class WindowRuntime:
    window: Window
    session: WindowSession
    queue: TaskQueue
    registry: Any
    worker: asyncio.Task | None = None
    bg_processes: list = field(default_factory=list)
    current_task_label: str = ""
    last_output: str = ""
    # T3.4: execute / compact / reset all touch the same message stream —
    # one lock per window, so compaction can never drop messages under a run
    session_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WindowManager:
    """Manages window runtimes (queue + session + registry) and dispatch."""

    #: 限速暂停后自动续跑的最大次数（之后交回给人决定）
    RATE_LIMIT_REQUEUE_MAX = 15
    #: 自动续跑的间隔（秒）。限速窗口通常是分钟级，早点回来试更划算；
    #: 每次尝试被打回也只是一次"立刻暂停"，不占着调用栈。
    RATE_LIMIT_REQUEUE_DELAY = 60.0
    def __init__(
        self,
        db: Any,
        llm_factory: Callable[..., Any],
        bus: Any,
        panel: Any = None,
        memory: Any = None,
        checkpoints_dir: str | Path = "/etc/dogbuddy/checkpoints",
        max_iterations: int = 25,
        threshold: float = 0.8,
        max_context_tokens: int = 128000,
        runners: RunnerRegistry | None = None,
        model_resolver: Callable[[str | None, str], str] | None = None,
        param_resolver: Callable[[Any], dict] | None = None,
        decisions: Any = None,
        #: v0.11.0：成本账本（每一位员工的每次 LLM 调用都要记账）
        cost: Any = None,
        approval_config: Callable[..., Any] | None = None,
        mcp_bridge: Any = None,
    ):
        self.db = db
        self.llm_factory = llm_factory
        self.runners = runners or default_runners()
        self.bus = bus
        self.panel = panel
        self.memory = memory
        self.decisions = decisions
        self.cost = cost
        # T4.2/T4.3: approval knobs (allowlist / 托管代审) read through here
        self.approval_config = approval_config
        # T5.1: bridged MCP tools are registered into every window registry
        self.mcp_bridge = mcp_bridge
        self.checkpoints_dir = str(checkpoints_dir)
        # T8.1：自助排期工具需要 JobStore + "立即执行"入口。
        # 由 main 在 JobManager 建好后 setattr 注入（窗口在 start() 才 boot，时序安全）。
        self.job_store: Any = None
        self.run_job_now: Callable[[str], Any] | None = None
        # T9.3/T9.1：主管工位的管理工具装配 + 作用域解析（由 main 注入）
        self.management_tools_factory: Callable[[Any], list] | None = None
        # T9.6：审批逐级路由 + 向主管投递（由 main 注入 SupervisionService 的两个方法）
        self.approval_router: Callable[[str, bool], dict] | None = None
        self.supervision_dispatch: Callable[[str, str], Any] | None = None
        from ..agents.core.scope import ScopeResolver
        self.scope_resolver = ScopeResolver(db)
        self.max_iterations = max_iterations
        self.builder = ContextBuilder(memory_service=memory)
        self.threshold = threshold
        self.max_context_tokens = max_context_tokens
        #: v0.10.32：人工派单的"最后一次任务"，限速暂停后据此自动续跑
        #: （worker_id → {"prompt":…, "attempts": n}）
        self._manual_last: dict[str, dict[str, Any]] = {}
        # injected rule for "which model will this window actually use"
        # (AppConfig.effective_model) — keeps the console from inventing its own
        self.model_resolver = model_resolver
        # injected rule for per-window params (AppConfig.effective_params)
        self.param_resolver = param_resolver
        self._runtime: dict[str, WindowRuntime] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self.gate = PermissionGate(confirm_available=True)

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Boot runtime for persisted windows (idempotent)."""
        self._loop = asyncio.get_running_loop()
        for w in self.db.list_windows():
            rt = self._runtime.get(w.id) or self._boot(w)
            self._ensure_worker(rt)

    async def stop(self) -> None:
        for rt in list(self._runtime.values()):
            rt.queue.stop()
            if rt.worker and not rt.worker.done():
                rt.worker.cancel()
            rt.queue.kill_background()

    def _ensure_worker(self, rt: WindowRuntime) -> None:
        """Start the queue worker if a running loop is available; otherwise defer
        until start() (sync routes run outside the event loop)."""
        if rt.worker is not None and not rt.worker.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = getattr(self, "_loop", None)
        if loop is not None and loop.is_running():
            rt.worker = loop.create_task(rt.queue.run_loop())

    def _run_job_now(self, job_id: str) -> Any:
        """T8.1：让工位触发的 run_now 真正跑起来（委派给 main 注入的入口）。"""
        if self.run_job_now is None:
            raise RuntimeError("立即执行不可用（未注入 run_job_now）")
        return self.run_job_now(job_id)

    def _boot(self, w: Window) -> WindowRuntime:
        # v0.10.36：每条落库消息都推给控制台（含 tool/system），
        # 否则"员工调用工具"只有刷新才看得见（owner 实测）。
        session = WindowSession(w, self.db, self.db, self.db,
                                on_message=lambda m: self._emit_message(w.id, m))
        queue = TaskQueue(w.id, on_status=lambda s: self._set_status(w.id, s))
        registry = build_worker_registry(w.role, gate=self.gate, audit=self.db,
                                         jobs=self.job_store, run_job_now=self._run_job_now,
                                         # v0.10.33：组长派活给组员要走 WindowManager
                                         coordinator=self)
        # T9.3：主管工位额外装上"项目级管理工具"（system 级永远不给）。
        # 装配只是"少给"，真正的越权拦截在 PermissionGate.check_scope（fail-closed）。
        #
        # 审查报告 P1-3：这里原来只认 `role == "supervisor"`，而 UI 的角色下拉里却有
        # `admin` → 管理员工位一个管理工具都没装，而 `role.tool_names`（=ADMIN_TOOLS）
        # 又拿去过滤只含通用 L4 工具的注册表 → **交集为空**，这个工位带着
        # "你是管理员，去管理工位/任务"的提示词，却一个工具都没有。
        role_name = (getattr(w, "role", "") or "")
        if role_name in ("supervisor", "admin") and self.management_tools_factory:
            try:
                for tool in self.management_tools_factory(w) or []:
                    registry.register(tool)
            except Exception:  # noqa: BLE001 — 装配失败不能让工位起不来
                logger.warning("management tools failed to assemble for role=%s",
                               role_name, exc_info=True)
        if self.mcp_bridge is not None and getattr(self.mcp_bridge, "started", False):
            try:
                self.mcp_bridge.register_into(registry)  # T5.3: external tools on the shelf
            except Exception:  # noqa: BLE001 — bridged tools never break a boot
                logger.warning("mcp tool registration failed", exc_info=True)
        # P1-15：让运行时与队列**共用同一个后台进程表**——工具登记进
        # ctx.extra["bg_processes"]（= rt.bg_processes），而回收走
        # queue.kill_background()（= queue._bg_processes）。同一个列表才收得掉。
        rt = WindowRuntime(window=w, session=session, queue=queue, registry=registry,
                           bg_processes=queue.bg_processes)
        self._runtime[w.id] = rt
        rt.queue.runner = self._make_default_runner(rt)
        self._ensure_worker(rt)
        return rt

    def _set_status(self, window_id: str, status: str) -> None:
        self.db.update_window(window_id, status=status)
        if self.bus:
            self.bus.emit(Event(type=WINDOW_STATUS, payload={"window_id": window_id, "status": status}))

    def _emit_message(self, window_id: str, m: Any) -> None:
        """v0.10.36：把一条新消息推给控制台（`window.message`）。

        为什么需要：后端以前只推 `window.stream`（助手**文本增量**），
        `tool`（工具调用）与 `system`（`[限速暂停]`/`[越权拦截]` 等）消息**从来不推**，
        于是界面必须刷新才能看到"员工在调什么工具"。
        载荷与 `recent_messages()` 的 DTO 保持同一形状 —— 前端可以直接塞进列表。
        """
        if not self.bus:
            return
        if getattr(m, "_pushed", False):
            return
        meta = dict(getattr(m, "meta", None) or {})
        self.bus.emit(Event(type="window.message", payload={
            "window_id": window_id,
            "message": {
                "id": getattr(m, "id", 0),
                "role": getattr(m, "role", "") or "",
                "content": getattr(m, "content", "") or "",
                "created_at": getattr(m, "created_at", "") or "",
                "tokens": int(getattr(m, "tokens", 0) or 0),
                "tool_name": meta.get("tool_name", ""),
                "tool_call_id": meta.get("tool_call_id", ""),
                "group_id": getattr(m, "group_id", "") or "",
                "meta": meta,
            },
        }))

    def _llm(self, profile: str | None = None, model: str | None = None,
             overrides: dict | None = None) -> Any:
        """Resolve a provider for a profile and/or a model override.

        Accepts `factory(profile, model, overrides)` (v2), `factory(profile, model)`,
        `factory(profile)` and legacy zero-arg factories (tests / simple wiring).
        """
        if profile or model or overrides:
            if overrides:
                try:
                    return self.llm_factory(profile, model, overrides)
                except TypeError:
                    pass
            try:
                return self.llm_factory(profile, model)
            except TypeError:
                try:
                    return self.llm_factory(profile)
                except TypeError:
                    pass
        return self.llm_factory()

    def _llm_overrides(self, p: dict) -> dict:
        return {k: p[k] for k in ("temperature", "request_timeout", "retry")
                if k in p and p[k] is not None}

    # ------------------------------------------------------------- approval (T4)
    def _approval_allowlist(self) -> list:
        if self.approval_config is None:
            return []
        try:
            return list(self.approval_config("approval.allowlist", []) or [])
        except Exception:  # noqa: BLE001 — config read never breaks a run
            return []

    def _approval_reviewer(self, w: Any) -> Any:
        """T4.3 托管代审: an independent LLM reviewer when the owner delegates.

        Returns None (= ask the owner) unless `approval.escalation_review` is on.
        The reviewer gets ONLY the call and the risk — no conversation context —
        so it cannot be talked into approving by a poisoned prompt.
        """
        if self.approval_config is None:
            return None
        try:
            enabled = bool(self.approval_config("approval.escalation_review", False))
        except Exception:  # noqa: BLE001
            return None
        if not enabled:
            return None

        async def review(name: str, args: dict, reason: str) -> bool:
            import json as _json
            llm = self._llm(w.llm_profile or None, w.model or None)
            prompt = (
                "你是独立的安全审查代理。判断下面这条即将在服务器上执行的命令是否安全。\n"
                f"工具：{name}\n参数：{_json.dumps(args, ensure_ascii=False)[:600]}\n"
                f"风险标注：{reason}\n\n"
                "只回答两个词之一：「批准」或「拒绝」。拿不准一律拒绝。")
            resp = await llm.chat([{"role": "user", "content": prompt}])
            verdict = (getattr(resp, "content", "") or "").strip()
            logger.info("托管代审 verdict for %s: %r", name, verdict[:20])
            return verdict.startswith("批准") or verdict.lower().startswith("approve")
        return review

    def resolved_model(self, w: Window) -> str:
        """The model this window will actually talk to (override → profile → global)."""
        if self.model_resolver is not None:
            try:
                return self.model_resolver(w.llm_profile or None, w.model or "")
            except Exception:  # noqa: BLE001
                pass
        return w.model or ""

    def project_for(self, w: Window) -> Any:
        """工位挂载的项目（T7.1）；没挂或查不到 → None。"""
        pid = getattr(w, "project_id", "") or ""
        if not pid:
            return None
        try:
            return self.db.get_project(pid)
        except Exception:  # noqa: BLE001 — 项目层绝不阻塞执行
            logger.warning("project lookup failed for %s", pid, exc_info=True)
            return None

    def effective_workspace(self, w: Window) -> str:
        """工位真正生效的工作区（T7.2）。

        挂了项目 → `项目根 / 工位子目录`；没挂 → 老行为（`workspace_path`）。
        这是文件工具与 shell 越界守卫的**唯一边界来源**，所以改项目根后
        越界判定自动跟着变，不需要各处改代码。
        """
        proj = self.project_for(w)
        root = str(getattr(proj, "root_path", "") or "").strip() if proj else ""
        if not root:
            return w.workspace_path or ""
        base = Path(root).expanduser()
        sub = str(getattr(w, "subdir", "") or "").strip().strip("/\\")
        target = (base / sub) if sub else base
        try:
            target.mkdir(parents=True, exist_ok=True)
            return str(target.resolve())
        except OSError:
            logger.warning("cannot prepare workspace %s", target, exc_info=True)
            return str(target)

    def params_for(self, w: Window) -> dict:
        """Effective params for this window (T7.2: 工位→项目→profile→全局)."""
        if self.param_resolver is not None:
            project = self.project_for(w)
            try:
                return dict(self.param_resolver(w, project=project) or {})
            except TypeError:
                # 兼容只接受单参数的旧解析器（第三方注入）
                try:
                    return dict(self.param_resolver(w) or {})
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                logger.warning("param resolver failed", exc_info=True)
        return {"max_iterations": self.max_iterations,
                "max_context_tokens": self.max_context_tokens,
                "compact_threshold": self.threshold}

    def _usage_hook(self, w: Window, run_id: str, model: str | None):
        """v0.11.0：成本账本钩子 —— 员工每轮 LLM 调用记一笔。

        挂法与 `on_tool` 同构（`ctx.extra`），所以群组步骤、子代理、续跑都自动覆盖。
        幂等键 `run_id:轮次` 防重放重复计费；provider 拿不到时由 CostService
        按 model 名兜底匹配单价，不会静默记成 0。
        """
        cost = getattr(self, "cost", None)
        if cost is None:
            return None
        wid = str(getattr(w, "id", "") or "")
        wname = str(getattr(w, "name", "") or "")
        default_model = str(model or "")
        seq = {"n": 0}

        def hook(resp: Any, llm: Any = None) -> None:
            seq["n"] += 1
            usage = getattr(resp, "usage", None) or {}
            model = (str(getattr(llm, "model_name", "") or "")
                     or str(getattr(resp, "model", "") or "") or default_model)
            cost.record(
                run_id=run_id, window_id=wid, kind="llm",
                provider=str(getattr(resp, "provider", "") or ""),
                model=model,
                in_tokens=int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                out_tokens=int(usage.get("completion_tokens")
                               or usage.get("output_tokens") or 0),
                cache_hit_tokens=int(usage.get("cache_read_input_tokens")
                                     or usage.get("prompt_cache_hit_tokens") or 0),
                idem_key=f"{run_id or wid}:{seq['n']}",
                meta={"phase": "window", "window_name": wname},
            )

        return hook

    def _compactor_for(self, w: Window) -> Compactor:
        p = self.params_for(w)
        ovr = self._llm_overrides(p)
        return Compactor(self._llm(w.llm_profile or None, w.model or None, ovr or None),
                         self.db, self.memory,
                         threshold=p.get("compact_threshold", self.threshold),
                         max_context_tokens=p.get("max_context_tokens", self.max_context_tokens),
                         # v0.11.0：压缩摘要也是钱，进同一本账
                         cost=getattr(self, "cost", None))

    # ---------------------------------------------------------- crud facade
    #: 工位级参数的范围（与参数面板一致）。越界**直接报错**——否则一个 "compact_threshold: 5"
    #: 会让压缩永不触发（或每次都触发），而且界面上看不出来。业务规则住这里（L3），
    #: 不在路由/工具里各写一遍。
    _PARAM_RANGES: dict[str, tuple[float, float]] = {
        "compact_threshold": (0.3, 0.95),
        "max_context_tokens": (2000, 10_000_000),
        "task_timeout": (30, 86400),
        "max_iterations": (1, 200),
        "temperature": (0.0, 2.0),
        "request_timeout": (5.0, 3600.0),
    }
    _PARAM_INT_KEYS = ("max_context_tokens", "task_timeout", "max_iterations")

    @classmethod
    def _check_params(cls, params: dict | None) -> dict:
        """校验/规范化工位参数覆盖。未知键原样保留（可能给扩展用），已知键必须合法。"""
        out = dict(params or {})
        for key, (lo, hi) in cls._PARAM_RANGES.items():
            if key not in out or out[key] is None or out[key] == "":
                continue
            try:
                val = float(out[key])
            except (TypeError, ValueError):
                raise ValueError(f"参数 {key} 不是数字：{out[key]!r}") from None
            if not (lo <= val <= hi):
                raise ValueError(f"参数 {key} 超出范围 {lo}~{hi}：{val}")
            out[key] = int(val) if key in cls._PARAM_INT_KEYS else val
        return out

    def create(self, name: str, role: str = "worker_programmer", workspace_path: str = "",
               model: str = "", mode: str = "BUILD", llm_profile: str = "",
               params: dict | None = None) -> Window:
        if self.db.get_window_by_name(name):
            raise ValueError(f"window name already used: {name}")
        from ..db import new_id
        spec = get_role(role)
        if spec is None:
            raise ValueError(f"unknown role: {role}")
        checked_params = self._check_params(params)
        ws = Path(workspace_path).expanduser()
        ws.mkdir(parents=True, exist_ok=True)
        w = Window(id=new_id(), name=name, role=role, workspace_path=str(ws.resolve()),
                   model=model, mode=mode if mode in ("BUILD", "PLAN", "AUTO") else spec.default_mode,
                   status="idle", llm_profile=llm_profile or "",
                   params=checked_params)
        self.db.insert_window(w)
        self._boot(w)
        self.db.audit("system", "window:create",
                      f"{w.id} {name} role={role} profile={llm_profile or 'default'} "
                      f"params={checked_params or '{}'}")
        return w

    def list(self) -> list[Window]:
        return [w for w in self.db.list_windows() if w.role != "admin"]

    def get(self, window_id: str) -> Window | None:
        return self._resolve(window_id)

    def _resolve(self, window_id: str) -> Window | None:
        w = self.db.get_window(window_id) or self.db.get_window_by_name(window_id)
        return w

    def _rt(self, window_id: str) -> WindowRuntime:
        w = self._resolve(window_id)
        if w is None:
            raise ValueError(f"window not found: {window_id}")
        rt = self._runtime.get(w.id)
        if rt is None:
            rt = self._boot(w)
        rt.window = w
        rt.session.window = w  # fresh row (group_id etc. — sessions follow the DB)
        return rt

    def update(self, window_id: str, **fields: Any) -> Window:
        rt = self._rt(window_id)
        if "mode" in fields and fields["mode"] not in ("BUILD", "PLAN", "AUTO"):
            raise ValueError(f"invalid mode: {fields['mode']}")
        if "params" in fields and fields["params"] is not None:
            fields["params"] = self._check_params(fields["params"])
        w = self.db.update_window(rt.window.id, **fields)
        if w:
            rt.window = w
        self.db.audit("system", "window:update", f"{rt.window.id} {fields}")
        return rt.window

    def pause(self, window_id: str) -> bool:
        rt = self._rt(window_id)
        rt.queue.pause()
        rt.queue.kill_background()
        self._set_status(rt.window.id, "paused")
        return True

    def resume(self, window_id: str) -> bool:
        rt = self._rt(window_id)
        rt.queue.resume()
        self._set_status(rt.window.id, "idle")
        return True

    def delete(self, window_id: str) -> bool:
        w = self._resolve(window_id)
        if w is None:
            return False
        rt = self._runtime.pop(w.id, None)
        if rt:
            rt.queue.stop()
            if rt.worker:
                rt.worker.cancel()
            rt.queue.kill_background()
        self.db.audit("system", "window:delete", w.id)
        return self.db.delete_window(w.id)

    def status(self, window_id: str) -> dict[str, Any]:
        rt = self._rt(window_id)
        w = self.db.update_window(rt.window.id) or rt.window
        return {
            "window_id": w.id, "name": w.name, "role": w.role, "mode": w.mode,
            "status": w.status, "model": w.model,
            "workspace": self.effective_workspace(w),
            "workspace_path": w.workspace_path,
            "project_id": getattr(w, "project_id", "") or "",
            "subdir": getattr(w, "subdir", "") or "",
            "current_task": rt.current_task_label,
            "queue_len": len(rt.queue.pending()),
            "tokens": rt.session.token_usage(),
            "todos_open": sum(1 for t in rt.session.todo_list() if not t.done),
            "last_output": (rt.last_output or "")[:400],
        }

    # ------------------------------------------------------------- dispatch
    def dispatch(self, window_id: str, prompt: str, urgent: bool = True,
                 job_id: str | None = None, kind: str = "prompt",
                 llm_profile: str = "", run_id: str = "",
                 sender: str = "") -> None:
        rt = self._rt(window_id)
        meta: dict[str, Any] = {}
        if run_id:
            meta["run_id"] = run_id
        if sender:  # v0.10.43：这条任务是谁派的（前端据此显示"组长·方向"而非"我"）
            meta["sender"] = sender
        rt.queue.submit(TaskItem(prompt=prompt, job_id=job_id, urgent=urgent,
                                 kind=kind or "prompt", llm_profile=llm_profile or "",
                                 meta=meta))
        self.db.audit("system", "window:dispatch",
                      f"{rt.window.id} job={job_id} kind={kind} run={run_id or '-'} "
                      f"prompt={prompt[:80]}")

    def _child_run(self, window_id: str, parent_run_id: str, kind: str,
                   prompt: str = "") -> str:
        """给"派给某个工位的活"记一条**子运行**（P0：界面要能看到谁在干什么）。"""
        runs = getattr(self, "runs", None)
        if runs is None or not parent_run_id:
            return ""
        try:
            w = self.db.get_window(window_id)
            run = runs.start(kind=kind, window_id=window_id, parent_run_id=parent_run_id,
                             project_id=getattr(w, "project_id", "") or "",
                             window_name=getattr(w, "name", "") or "", status="queued",
                             # v0.10.40：子运行的任务原文也落库（可单独重试）
                             prompt=prompt or "")
            return run.id
        except Exception:  # noqa: BLE001 — 台账失败不能拦住派单
            logger.warning("child run start failed", exc_info=True)
            return ""

    def dispatch_pending(self, window_id: str, prompt: str, *,
                         coordinated: bool = True, kind: str = "prompt",
                         llm_profile: str = "", run_parent_id: str = "",
                         sender: str = "") -> "asyncio.Future":
        """派单但**不等**：返回一个可 await 的 future（v0.10.36）。

        为什么需要它：组长用 `member_dispatch(wait=false)` 把活排给组员时，如果只有
        "扔进队列" 没有句柄，群组运行就**没法等它干完** —— 收尾汇总会在组员还没开工时
        就跑（owner 那个前端项目差点因此拿到一份"提前的结论"）。
        现在"不等"只是"不阻塞组长这一轮"，收尾前照样能 join。
        """
        rt = self._rt(window_id)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        meta: dict[str, Any] = {"future": future, "run_owned_by_caller": True}
        if coordinated:
            meta["coordinated"] = True
        child = self._child_run(window_id, run_parent_id, "coordinated", prompt=prompt)
        if child:
            meta["run_id"] = child
        if sender:
            meta["sender"] = sender
        rt.queue.submit(TaskItem(prompt=prompt, job_id=None, urgent=False,
                                 kind=kind or "prompt", llm_profile=llm_profile or "",
                                 meta=meta))
        self.db.audit("system", "window:dispatch",
                      f"{rt.window.id} job=None kind={kind} (pending) prompt={prompt[:80]}")
        return future

    async def dispatch_and_wait(self, window_id: str, prompt: str,
                                job_id: str | None = None, kind: str = "prompt",
                                llm_profile: str = "", coordinated: bool = False,
                                timeout: float | None = None,
                                run_parent_id: str = "",
                                sender: str = "") -> JobOutcome:
        """Cron / 组长派活路径：投进队列并等这一轮结束。

        `coordinated=True`（v0.10.33）：这是**组长派给组员**的活，`window.run_finished`
        里 `manual=False` —— 免得"限速自动续跑"与组长的等待变成两次执行；
        `timeout` 可覆盖默认的单轮等待上限。
        """
        rt = self._rt(window_id)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        meta: dict[str, Any] = {"future": future, "run_owned_by_caller": True}
        if coordinated:
            meta["coordinated"] = True
        child = self._child_run(window_id, run_parent_id, "coordinated", prompt=prompt)
        if child:
            meta["run_id"] = child
        if sender:
            meta["sender"] = sender
        rt.queue.submit(TaskItem(prompt=prompt, job_id=job_id, urgent=False,
                                 kind=kind or "prompt", llm_profile=llm_profile or "",
                                 # 调用方（CronScheduler / GroupManager）已经开好 job_runs 行
                                 # 并负责收尾，运行时不要重复开行（见 `_execute` 的同一处判断）
                                 meta=meta))
        timeout = float(timeout) if timeout else self._wait_timeout(window_id)
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            # P2-7（审查报告）：超时**不是失败**。任务还在后台队列里跑，只是本轮
            # 不再等它。初版返回 status="failed" → 调度器计连败 → 30 分钟以上的
            # 长任务连续 3 轮被自动暂停并通知用户"失败 3 次"，而任务其实成功了。
            logger.warning("job %s 等待 %.0fs 仍未结束：本轮停止等待（任务继续在后台运行，"
                           "不计失败）", job_id, timeout)
            return JobOutcome(
                status="timeout",
                summary=f"超过 {int(timeout)}s 仍未结束：本轮已停止等待，任务仍在后台继续运行"
                        f"（不算失败，也不计入连败）。")
        return self._to_outcome(result)

    #: cron 路径等待单轮结束的默认上限（秒）。工位参数 `task_timeout` 可覆盖。
    CRON_WAIT_TIMEOUT: float = 1800.0

    def _wait_timeout(self, window_id: str) -> float:
        """本轮等待上限。长任务（构建/大批量跑测试）可用工位参数 `task_timeout` 调大。

        P2-7：这个值只决定"等多久就不再等"，**不决定成败**——超时不计失败。
        """
        try:
            w = self.get(window_id)
            if w is not None:
                raw = (self.params_for(w) or {}).get("task_timeout")
                if raw:
                    val = float(raw)
                    if val > 0:
                        return val
        except Exception:  # noqa: BLE001 — 参数读不出来就用默认值
            logger.warning("reading task_timeout failed for %s", window_id, exc_info=True)
        return float(self.CRON_WAIT_TIMEOUT)


    def _emit_run_finished(self, w: Any, task: Any, result: Any) -> None:
        """一次运行**结束**（成功/失败/限速暂停）都发终态事件。

        为什么必须发：前端原来只能"轮询有没有新的 assistant 消息"来判断结束。
        LLM 掉线时 loop 落库的是 **system** 消息（`[LLM error/xxx]`），永远不满足
        "新的 assistant 消息"，于是"正在输入…"一直挂到 180s 超时——
        owner 看到的就是这个（"显示的还是正在输入…但确实掉线了，也没有错误代码"）。
        """
        if not self.bus:
            return
        status = _run_status(result)
        payload = {
            "window_id": w.id,
            "window_name": w.name,
            "status": status,
            "stop_reason": str(getattr(result, "meta", {}).get("stop_reason", "") or ""),
            "error_code": str(getattr(result, "error_code", "") or ""),
            "error": str(getattr(result, "error_detail", "") or
                         (getattr(result, "summary", "") if status != "ok" else ""))[:400],
            "manual": (not bool(getattr(task, "job_id", None))
                       # v0.10.33：组长派给组员的活"不算人工派单"——否则限速自动续跑
                       # 会和组长的等待变成两次执行
                       and not bool((getattr(task, "meta", None) or {}).get("coordinated"))),
            "job_id": getattr(task, "job_id", None) or "",
        }
        try:
            self.bus.emit(Event(type="window.run_finished", payload=payload))
        except Exception:  # noqa: BLE001 — 终态推送失败不能影响队列
            logger.debug("run_finished emit failed", exc_info=True)

    def _report_manual_run(self, w: Any, task: Any, result: Any) -> None:
        """人工派单跑完 → 发一条完成/失败事件，让汇报链（→主管→管理员→用户）动起来。

        定时任务不发（调度器自己发，发了就重复）。
        """
        if not self.bus or getattr(task, "job_id", None):
            return
        if str(getattr(task, "kind", "prompt") or "prompt") != "prompt":
            return
        status = _run_status(result)
        summary = (getattr(result, "summary", "") or "")[:1500]
        payload = {
            "job_id": "",
            "window_id": w.id,
            "window_name": w.name,
            "name": w.name,
            "summary": summary,
            "prompt": str(getattr(task, "prompt", ""))[:200],
            "report_strategy": "brief",     # 用户要的就是"简短汇报"
            "manual": True,
        }
        if status == "ok":
            self.bus.emit(Event(type=JOB_COMPLETED, payload=payload))
        elif status in ("failed", "rate_limited"):
            payload["auto_paused"] = False
            self.bus.emit(Event(type=JOB_FAILED, payload=payload))

    def dispatch_manual(self, window_id: str, prompt: str, sender: str = "") -> None:
        """Manual (human-initiated) run — head-of-queue, fire-and-forget.

        v0.10.32：记住这次人工派单，供**限速暂停后自动续跑**用。以前只有定时任务
        会被调度器重排（`mark_rate_limited` → 60s 后再来），人工派单被限速暂停后
        **没人再叫醒它** —— worker-1 因此停在 14:03 半个小时，owner 看到的就是
        "他好像卡住了"。
        """
        self._manual_last[window_id] = {"prompt": prompt, "attempts": 0}
        # v0.10.37（P0）：登记一条**运行台账** —— "此刻谁在跑什么"的唯一真相。
        run_id = ""
        if getattr(self, "runs", None) is not None:
            try:
                w = self.db.get_window(window_id)
                run = self.runs.start(kind="manual", window_id=window_id,
                                      project_id=getattr(w, "project_id", "") or "",
                                      window_name=getattr(w, "name", "") or "",
                                      # v0.10.40：原始任务文本落库——重启后管理员
                                      # 仍能"唤醒"这条未完成的任务（以前只在内存）
                                      prompt=prompt, status="queued")
                run_id = run.id
                self._manual_last[window_id]["run_id"] = run_id
            except Exception:  # noqa: BLE001 — 台账失败不能拦住派单
                logger.warning("run start failed for manual dispatch", exc_info=True)
        self.dispatch(window_id, prompt, urgent=True, run_id=run_id, sender=sender)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        """取消一次运行（人工派单 / 组员工位上的活）。幂等，返回给人看的结果。

        v0.10.37（P0）：以前**没有任何取消入口**（owner："停掉重来"无处可停）。
        排队中 → 从队列拿掉；正在跑 → 打断那一项的执行任务（`TaskQueue.cancel_current`，
        队列本身继续活着）。
        """
        runs = getattr(self, "runs", None)
        if runs is None:
            return {"ok": False, "reason": "运行台账未启用"}
        run = runs.get(run_id)
        if run is None:
            return {"ok": False, "reason": f"没有这个运行：{run_id}"}
        out: dict[str, Any] = {"ok": True, "run_id": run_id}
        wid = getattr(run, "window_id", "") or ""
        if wid:
            try:
                rt = self._rt(wid)
                cur = rt.queue.current
                if cur is not None and (cur.meta or {}).get("run_id") == run_id:
                    out["interrupted"] = rt.queue.cancel_current()
                else:
                    out["dropped"] = rt.queue.drop(
                        lambda t: (t.meta or {}).get("run_id") == run_id)
            except Exception as e:  # noqa: BLE001
                out["queue_error"] = str(e)
        out.update(runs.cancel(run_id))
        self.db.audit("system", "run:cancel", f"{run_id} {out}")
        return out

    def requeue_after_rate_limit(self, window_id: str, delay: float | None = None) -> str:
        """限速暂停后把人工派单**再排一次**（自动续跑）。

        返回给人看的一句话（已排队 / 已放弃），由调用方（main）如实告诉用户。
        放弃条件：`RATE_LIMIT_REQUEUE_MAX` 次之后仍被限速 —— 那时硬撑只是烧配额，
        应该让人来决定（换模型/换 key/等）。
        """
        delay = self.RATE_LIMIT_REQUEUE_DELAY if delay is None else float(delay)
        rec = self._manual_last.get(window_id)
        if not rec:
            return "没有可续跑的人工派单（可能是定时任务，调度器会自己重排）"
        if not rec.get("prompt"):
            return "上次派单内容为空，不自动续跑"
        attempts = int(rec.get("attempts", 0)) + 1
        if attempts > self.RATE_LIMIT_REQUEUE_MAX:
            self._manual_last.pop(window_id, None)
            self.db.audit("system", "window:requeue_giveup",
                          f"{window_id} 限速持续，已停止自动续跑"
                          f"（试了 {self.RATE_LIMIT_REQUEUE_MAX} 次）")
            return (f"限速持续（已自动续跑 {self.RATE_LIMIT_REQUEUE_MAX} 次仍被限速），"
                    f"**停止自动续跑**。请换模型/换 key，或稍后手动重发。")
        rec["attempts"] = attempts
        prompt = rec["prompt"]
        # v0.10.41：续跑要**继续同一条运行台账**，并且开火前要检查它是否已被
        # 取消/结束。修的是两个真 bug：
        #  ① 以前 `_later()` 无条件 `dispatch(prompt)`（不带 run_id）——管理员
        #     取消/唤醒之后 60 秒，定时器仍会把同一任务再跑一遍（双跑、双烧
        #     token，还可能并发写同一批文件）；
        #  ② 续跑的执行没有 run_id → 台账里旧 run 永远停在 paused（顶栏一直显示
        #     "限速暂停"），实际工作却在无台账状态下跑完。
        rid = str(rec.get("run_id") or "")
        self.db.audit("system", "window:requeue_rate_limited",
                      f"{window_id} 第 {attempts} 次自动续跑（{delay:.0f}s 后）")
        _TERMINAL = ("done", "partial", "failed", "cancelled", "timeout", "interrupted")

        def _resume_still_wanted() -> bool:
            runs = getattr(self, "runs", None)
            if not rid or runs is None:
                return True
            r = runs.get(rid)
            if r is None or r.status in _TERMINAL:
                self.db.audit("system", "window:requeue_skipped",
                              f"{window_id} 运行 {rid} 已结束（{getattr(r, 'status', '缺失')}），"
                              f"不再自动续跑")
                return False
            return True

        async def _later() -> None:
            try:
                await asyncio.sleep(max(0.0, float(delay)))
                if not _resume_still_wanted():
                    return
                runs = getattr(self, "runs", None)
                if rid and runs is not None:
                    runs.update(rid, status="running")
                self.dispatch(window_id, prompt, urgent=True, run_id=rid)
            except asyncio.CancelledError:  # pragma: no cover — 关停时取消
                raise
            except Exception:  # noqa: BLE001 — 续跑失败不能炸事件循环
                logger.warning("rate-limit requeue failed for %s", window_id, exc_info=True)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有事件循环（脚本/测试）→ 直接同步重排，不假装延迟
            if not _resume_still_wanted():
                return "该运行已结束，不再续跑"
            self.dispatch(window_id, prompt, urgent=True, run_id=rid)
            return f"已立即续跑（第 {attempts} 次；当前无事件循环，延迟被跳过）"
        loop.create_task(_later())
        return f"已自动续跑（第 {attempts} 次，{delay:.0f}s 后）"

    def _make_default_runner(self, rt: WindowRuntime) -> Callable[[TaskItem], Any]:
        async def runner(task: TaskItem) -> None:
            result = await self._execute(rt, task)
            fut = task.meta.get("future")
            if fut is not None and not fut.done():
                fut.set_result(result)
        return runner

    async def _execute(self, rt: WindowRuntime, task: TaskItem) -> Any:
        w = rt.window
        # v0.10.37（P0）：把这一轮记进运行台账（排队 → 运行中 → 终态）。
        # 取消（`TaskQueue.cancel_current`）抛的 `CancelledError` 也必须收尾成
        # `cancelled`（不是 failed、不计连败），否则界面上会留下永远"运行中"的行。
        run_id = str((getattr(task, "meta", None) or {}).get("run_id") or "")
        _runs = getattr(self, "runs", None)
        if run_id and _runs is not None:
            try:
                if _runs.get(run_id) is not None and _runs.get(run_id).cancel_requested:
                    _runs.finish(run_id, "cancelled", summary="已取消（还没开始执行就被取消）")
                    raise asyncio.CancelledError()
                _runs.update(run_id, status="running", phase="work")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — 台账失败不能影响运行
                logger.warning("run start-bookkeeping failed for %s", run_id, exc_info=True)
        try:
            return await self._execute_inner(rt, task)
        except asyncio.CancelledError:
            if run_id and _runs is not None:
                try:
                    _runs.finish(run_id, "cancelled", summary="已取消（人为停止）")
                except Exception:  # noqa: BLE001
                    logger.debug("run cancel bookkeeping failed", exc_info=True)
            raise
        except BaseException as e:  # noqa: BLE001 — 未预期异常也要给台账一个终态
            if run_id and _runs is not None:
                try:
                    _runs.finish(run_id, "failed", summary=f"{type(e).__name__}: {e}"[:400])
                except Exception:  # noqa: BLE001
                    logger.debug("run failure bookkeeping failed", exc_info=True)
            raise

    async def _execute_inner(self, rt: WindowRuntime, task: TaskItem) -> Any:
        w = rt.window
        role = get_role(w.role)
        profile = task.llm_profile or w.llm_profile or None
        # per-window model override: was decorative in v1.3 (never read), so users
        # had to set the model in several places to get any effect
        model = w.model or None
        #: v0.11.0：成本账本的幂等键要用到本次 run（重放不重复计费）
        run_id = str((getattr(task, "meta", None) or {}).get("run_id") or "")
        # per-window params (T1.1/T7.2): workbench → project → profile → global
        p = self.params_for(w)
        ovr = self._llm_overrides(p) or None
        llm_factory = (lambda pp=None: self._llm(pp or profile, model, ovr))
        # T7.2: 挂载项目后工作区=项目根(+子目录)。这是文件工具与 shell 边界
        # 的唯一来源；同时写进会话，提示词里才能告诉模型真实目录。
        ws = self.effective_workspace(w)
        rt.session.workspace_override = ws
        ctx = ToolContext(
            window_id=w.id, workspace=ws, mode=w.mode, actor="worker",
            todo_repo=self.db, db=self.db, panel=self.panel,
            llm_factory=llm_factory,
            ask_user_fn=self._make_ask_user(w.id),
            checkpoint_root=self.checkpoints_dir,
            # 工位记忆（window_memory_read/write）要能拿到自己的记忆文件
            memory_service=self.memory,
            # T9.1：作用域由角色推导后注入（模型无法在参数里自报身份）
            scope=self.scope_resolver.for_window(w),
            extra={"scheduled": bool(task.job_id), "manual": not task.job_id,
                   "bg_processes": rt.bg_processes,
                   # v0.10.43：这条任务**谁派的**（组长名；空 = owner/系统）
                   "sender": str(((getattr(task, "meta", None) or {}).get("sender")) or ""),
                   "window_name": getattr(w, "name", "") or "",
                   # T4.2: trust level from workbench params + allowlist from config
                   "trust": (w.params or {}).get("trust", "normal"),
                   "approval_allowlist": self._approval_allowlist(),
                   # T9.1：作用域校验的兜底依据（主管给自己项目建任务时用）
                   "project_id": getattr(w, "project_id", "") or "",
                   # T9.6：审批逐级路由（员工→主管→管理员）+ 向主管投递的通道
                   "approval_router": self.approval_router,
                   # v0.11.0：成本账本（每轮 LLM 调用记一笔，幂等键 = run + 轮次）
                   "on_usage": self._usage_hook(w, run_id, model),
                   "supervision_dispatch": self.supervision_dispatch,
                   # P1-6：升级给主管的待决事项要落 queue、主管要用工具裁决它们
                   "decisions": self.decisions,
                   # T4.3: 托管代审 reviewer (None = ask the owner directly)
                   "approval_reviewer": self._approval_reviewer(w)},
        )
        # T2.5: stream text fragments to the console (SSE typewriter)
        def _on_delta(delta: str) -> None:
            if self.bus:
                self.bus.emit(Event(type="window.stream",
                                    payload={"window_id": w.id, "delta": delta}))

        env = TaskEnv(
            window=w, session=rt.session, registry=rt.registry,
            llm_factory=llm_factory,
            builder=self.builder, compactor=self._compactor_for(w),
            ctx=ctx,
            # 角色白名单。**未知角色（老库残留）不能退化成 None**：`schemas(only=None)`
            # 是"不过滤"，等于把注册表全量交给模型（审查报告 P1-7 的同类入口）。
            tool_names=role.tool_names if role else [],
            max_iterations=p.get("max_iterations", self.max_iterations),
            on_delta=_on_delta)

        runner = self.runners.get(task.kind or "prompt")
        if runner is None:
            logger.error("no runner registered for task kind %r", task.kind)
            result = _ErrorResult(f"unknown task kind: {task.kind}")
        else:
            rt.current_task_label = task.prompt[:80]
            self._set_status(w.id, "running")
            # T2.7: every scheduled/manual job run lands in job_runs (was cron-only).
            # v11：**cron 路径的账本由调度器持有**（scheduler.py 已 start_run 并负责
            # 写 ok/rate_limited/timeout/failed），这里再写一行就是"一次触发两本账"：
            # 运行历史显示两条相邻记录、仪表盘次数翻倍、成功率被那条永远不可能是
            # timeout 的运行时行拉低。所以调用方已持有时不再开行。
            run_id = None
            if (task.job_id and not task.meta.get("run_owned_by_caller")
                    and hasattr(self.db, "start_run")):
                try:
                    run_id = self.db.start_run(task.job_id, w.id).id
                except Exception:  # noqa: BLE001 — bookkeeping never breaks a run
                    logger.warning("job run bookkeeping failed", exc_info=True)
            try:
                async with rt.session_lock:  # T3.4: compaction never runs under a task
                    result = await runner.run(task, env)
            except Exception as e:  # noqa: BLE001 — a crashed runner must not kill the queue
                logger.exception("window %s run failed", w.id)
                result = _ErrorResult(str(e), code="runner_crash")
            finally:
                if run_id is not None:
                    try:
                        # P0：`AgentResult` **没有** `ok` 属性，原来写
                        # `"ok" if getattr(result, "ok", True) else "failed"` ——
                        # 默认值 True 让**每次执行都记成 ok**，哪怕
                        # `stop_reason == "error"`（LLM 调用失败）。
                        # 表现：运行历史里出现一条 status=ok、summary 却是报错的行，
                        # "失败"被记成"成功"。
                        self.db.finish_run(
                            run_id, _run_status(result),
                            (getattr(result, "summary", "") or "")[:500])
                    except Exception:  # noqa: BLE001
                        logger.warning("job run finish failed", exc_info=True)
                rt.current_task_label = ""
                self._set_status(w.id, "idle")
        rt.last_output = result.summary

        # v0.10.14：**人工/对话派给员工的活跑完也要汇报**。
        # 以前只有 cron 调度器发 `job.completed`，于是"让两个员工做测试"这种
        # 对话派单跑完后，管理员那边什么都不知道，用户也就永远等不到汇报
        # （owner 原话："两个员工测试完成后，管理员没有向用户做简短汇报"）。
        # 定时任务那一侧由调度器发（task.job_id 非空），这里只补人工那条，避免重复。
        self._emit_run_finished(w, task, result)
        self._report_manual_run(w, task, result)
        # 运行台账收尾（限速暂停单独记 paused，便于界面显示"暂停中 · 已等 N 秒"）
        run_id = str((getattr(task, "meta", None) or {}).get("run_id") or "")
        _runs = getattr(self, "runs", None)
        if run_id and _runs is not None:
            try:
                status, prefix, code = _run_terminal(result)
                if status == "paused":
                    _runs.note_pause(run_id, 0.0)
                else:
                    _runs.finish(run_id, status,
                                 summary=(prefix + str(getattr(result, "summary", "") or ""))[:800],
                                 error_code=code)
            except Exception:  # noqa: BLE001
                logger.debug("run finish bookkeeping failed", exc_info=True)

        if result.pending_questions and self.bus:
            for q in result.pending_questions:
                self.bus.emit(Event(type=WORKER_QUESTION, payload={
                    "window_id": w.id, "window_name": w.name, "question": q}))
        if result.pending_confirms and self.bus:
            for c in result.pending_confirms:
                self.bus.emit(Event(type=CONFIRM_REQUEST, payload={
                    "window_id": w.id, "window_name": w.name, "reason": c}))
        return result

    def _make_ask_user(self, window_id: str) -> Callable[..., Any]:
        """Ask the owner a question.

        With a DecisionService wired (v2 T2.3) the question lands in the durable
        `pending_decisions` queue and the agent *waits* for the answer (with a
        timeout) — questions asked at 3am survive until morning. Without one
        (tests / minimal wiring) it falls back to the v1.3 fire-and-forget event.
        """
        if getattr(self, "decisions", None) is not None:
            async def ask(question: str, options: list[str] | None = None) -> str:
                d = self.decisions.ask(window_id, question, options=options)
                return await self.decisions.wait(d.id)
            return ask

        def ask(question: str, options: list[str] | None = None) -> str:
            if self.bus:
                w = self.db.get_window(window_id)
                self.bus.emit(Event(type=WORKER_QUESTION, payload={
                    "window_id": window_id, "window_name": w.name if w else window_id,
                    "question": question, "options": options or []}))
            return ""
        return ask

    @staticmethod
    def _to_outcome(result: Any) -> JobOutcome:
        """RunnerResult → JobOutcome。T8.2/T8.3 的信号必须原样带过去，
        否则工位的"有事才通知"和"下次何时跑"会在最后一跳丢掉。"""
        return JobOutcome(
            status=result.status, summary=(result.summary or "")[:1000],
            next_run_at=getattr(result, "next_run_at", None),
            notify=bool(getattr(result, "notify", False)),
            notify_text=getattr(result, "notify_text", "") or "",
            notify_level=getattr(result, "notify_level", "info") or "info",
            detail=dict(getattr(result, "meta", None) or {}))

    # -------------------------------------------------------------- views
    def recent_messages(self, window_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rt = self._rt(window_id)
        # meta carries tool_name/tool_call_id (T2.6) and tool_calls for rebuild
        # v0.10.38：`group_id` 暴露给前端——员工聊天页要能标出"这是群组协作消息"
        # （群频道与成员会话是同一批消息的两份投影；以前 DTO 不带这个字段，
        # 前端无法区分，owner 在工人聊天记录里看不到群组上下文）。
        return [{"id": m.id, "role": m.role, "content": m.content,
                 "created_at": m.created_at, "tokens": m.tokens,
                 "tool_name": (m.meta or {}).get("tool_name", ""),
                 "tool_call_id": (m.meta or {}).get("tool_call_id", ""),
                 "group_id": getattr(m, "group_id", "") or "",
                 # v0.10.43：这条 user 消息是谁派的（组长名；空 = owner/系统）
                 "sender": (m.meta or {}).get("sender", "") or "",
                 # 本轮缓存命中（气泡下的小字），没拿到字段时为空
                 "cache_hit": (m.meta or {}).get("cache_hit"),
                 "cache_miss": (m.meta or {}).get("cache_miss"),
                 "meta": m.meta or {}}
                for m in rt.session.recent(limit)]
