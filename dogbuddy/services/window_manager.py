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
from ..events.bus import CONFIRM_REQUEST, WINDOW_STATUS, WORKER_QUESTION, Event
from ..ports import Window
from ..tools.registry import ToolContext
from ..windows.compactor import Compactor
from ..windows.context import ContextBuilder
from ..windows.queue import TaskItem, TaskQueue
from ..windows.session import WindowSession

logger = logging.getLogger("dogbuddy.windows.manager")


def _ErrorResult(msg: str) -> RunnerResult:  # noqa: N802 — mirrors _to_outcome naming
    return RunnerResult(status="failed", summary=msg)


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


class WindowManager:
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
    ):
        self.db = db
        self.llm_factory = llm_factory
        self.runners = runners or default_runners()
        self.bus = bus
        self.panel = panel
        self.memory = memory
        self.checkpoints_dir = str(checkpoints_dir)
        self.max_iterations = max_iterations
        self.builder = ContextBuilder(memory_service=memory)
        self.threshold = threshold
        self.max_context_tokens = max_context_tokens
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

    def _boot(self, w: Window) -> WindowRuntime:
        session = WindowSession(w, self.db, self.db, self.db)
        queue = TaskQueue(w.id, on_status=lambda s: self._set_status(w.id, s))
        registry = build_worker_registry(w.role, gate=self.gate, audit=self.db)
        rt = WindowRuntime(window=w, session=session, queue=queue, registry=registry)
        self._runtime[w.id] = rt
        rt.queue.runner = self._make_default_runner(rt)
        self._ensure_worker(rt)
        return rt

    def _set_status(self, window_id: str, status: str) -> None:
        self.db.update_window(window_id, status=status)
        if self.bus:
            self.bus.emit(Event(type=WINDOW_STATUS, payload={"window_id": window_id, "status": status}))

    def _llm(self, profile: str | None = None) -> Any:
        """Resolve a provider. Accepts both `factory(profile)` (profile registry)
        and legacy zero-arg factories (tests / simple wiring)."""
        if profile:
            try:
                return self.llm_factory(profile)
            except TypeError:
                pass
        return self.llm_factory()

    def _compactor_for(self, w: Window) -> Compactor:
        return Compactor(self._llm(w.llm_profile or None), self.db, self.memory,
                         threshold=self.threshold, max_context_tokens=self.max_context_tokens)

    # ---------------------------------------------------------- crud facade
    def create(self, name: str, role: str = "worker_programmer", workspace_path: str = "",
               model: str = "", mode: str = "BUILD", llm_profile: str = "") -> Window:
        if self.db.get_window_by_name(name):
            raise ValueError(f"window name already used: {name}")
        from ..db import new_id
        spec = get_role(role)
        if spec is None:
            raise ValueError(f"unknown role: {role}")
        ws = Path(workspace_path).expanduser()
        ws.mkdir(parents=True, exist_ok=True)
        w = Window(id=new_id(), name=name, role=role, workspace_path=str(ws.resolve()),
                   model=model, mode=mode if mode in ("BUILD", "PLAN", "AUTO") else spec.default_mode,
                   status="idle", llm_profile=llm_profile or "")
        self.db.insert_window(w)
        self._boot(w)
        self.db.audit("system", "window:create", f"{w.id} {name} role={role} profile={llm_profile or 'default'}")
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
        return rt

    def update(self, window_id: str, **fields: Any) -> Window:
        rt = self._rt(window_id)
        if "mode" in fields and fields["mode"] not in ("BUILD", "PLAN", "AUTO"):
            raise ValueError(f"invalid mode: {fields['mode']}")
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
            "status": w.status, "model": w.model, "workspace": w.workspace_path,
            "current_task": rt.current_task_label,
            "queue_len": len(rt.queue.pending()),
            "tokens": rt.session.token_usage(),
            "todos_open": sum(1 for t in rt.session.todo_list() if not t.done),
            "last_output": (rt.last_output or "")[:400],
        }

    # ------------------------------------------------------------- dispatch
    def dispatch(self, window_id: str, prompt: str, urgent: bool = True,
                 job_id: str | None = None, kind: str = "prompt",
                 llm_profile: str = "") -> None:
        rt = self._rt(window_id)
        rt.queue.submit(TaskItem(prompt=prompt, job_id=job_id, urgent=urgent,
                                 kind=kind or "prompt", llm_profile=llm_profile or ""))
        self.db.audit("system", "window:dispatch",
                      f"{rt.window.id} job={job_id} kind={kind} prompt={prompt[:80]}")

    async def dispatch_and_wait(self, window_id: str, prompt: str,
                                job_id: str | None = None, kind: str = "prompt",
                                llm_profile: str = "") -> JobOutcome:
        """Cron path: inject into the persistent session and await the run result."""
        rt = self._rt(window_id)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        rt.queue.submit(TaskItem(prompt=prompt, job_id=job_id, urgent=False,
                                 kind=kind or "prompt", llm_profile=llm_profile or "",
                                 meta={"future": future}))
        try:
            result = await asyncio.wait_for(future, timeout=1800.0)
        except asyncio.TimeoutError:
            return JobOutcome(status="failed", summary="task timed out (30min)")
        return self._to_outcome(result)

    def dispatch_manual(self, window_id: str, prompt: str) -> None:
        """Manual (human-initiated) run — head-of-queue, fire-and-forget."""
        self.dispatch(window_id, prompt, urgent=True)

    def _make_default_runner(self, rt: WindowRuntime) -> Callable[[TaskItem], Any]:
        async def runner(task: TaskItem) -> None:
            result = await self._execute(rt, task)
            fut = task.meta.get("future")
            if fut is not None and not fut.done():
                fut.set_result(result)
        return runner

    async def _execute(self, rt: WindowRuntime, task: TaskItem) -> Any:
        w = rt.window
        role = get_role(w.role)
        profile = task.llm_profile or w.llm_profile or None
        ctx = ToolContext(
            window_id=w.id, workspace=w.workspace_path, mode=w.mode, actor="worker",
            todo_repo=self.db, db=self.db, panel=self.panel,
            llm_factory=(lambda p=None: self._llm(p or profile)),
            ask_user_fn=self._make_ask_user(w.id),
            checkpoint_root=self.checkpoints_dir,
            extra={"scheduled": bool(task.job_id), "manual": not task.job_id,
                   "bg_processes": rt.bg_processes},
        )
        env = TaskEnv(
            window=w, session=rt.session, registry=rt.registry,
            llm_factory=lambda p=None: self._llm(p or profile),
            builder=self.builder, compactor=self._compactor_for(w),
            ctx=ctx, tool_names=role.tool_names if role else None,
            max_iterations=self.max_iterations)

        runner = self.runners.get(task.kind or "prompt")
        if runner is None:
            logger.error("no runner registered for task kind %r", task.kind)
            result = _ErrorResult(f"unknown task kind: {task.kind}")
        else:
            rt.current_task_label = task.prompt[:80]
            self._set_status(w.id, "running")
            try:
                result = await runner.run(task, env)
            except Exception as e:  # noqa: BLE001 — a crashed runner must not kill the queue
                logger.exception("window %s run failed", w.id)
                result = _ErrorResult(str(e))
            finally:
                rt.current_task_label = ""
                self._set_status(w.id, "idle")
        rt.last_output = result.summary

        if result.pending_questions and self.bus:
            for q in result.pending_questions:
                self.bus.emit(Event(type=WORKER_QUESTION, payload={
                    "window_id": w.id, "window_name": w.name, "question": q}))
        if result.pending_confirms and self.bus:
            for c in result.pending_confirms:
                self.bus.emit(Event(type=CONFIRM_REQUEST, payload={
                    "window_id": w.id, "window_name": w.name, "reason": c}))
        return result

    def _make_ask_user(self, window_id: str) -> Callable[..., str]:
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
        return JobOutcome(status=result.status, summary=(result.summary or "")[:1000])

    # -------------------------------------------------------------- views
    def recent_messages(self, window_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rt = self._rt(window_id)
        return [{"id": m.id, "role": m.role, "content": m.content,
                 "created_at": m.created_at, "tokens": m.tokens}
                for m in rt.session.recent(limit)]
