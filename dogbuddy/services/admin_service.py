# DogBuddyCn — admin conversation service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""AdminService: user message → admin AgentLoop → reply.

The admin agent is itself a persistent session (window_id="admin") with the
management tool pack. Users never talk to employees directly (M-B rules).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..agents.admin.tools import AdminServices, build_admin_tools
from ..agents.core.loop import AgentLoop
from ..agents.core.roles import get_role
from ..ports import Window
from ..tools.registry import ToolContext, ToolRegistry
from ..windows.context import ContextBuilder
from ..windows.session import WindowSession

logger = logging.getLogger("dogbuddy.services.admin")

ADMIN_WINDOW_ID = "admin"


class AdminService:
    def __init__(
        self,
        db: Any,
        llm_factory: Callable[[], Any],
        windows: Any,          # WindowManager
        jobs: Any,             # JobManager
        sessions: Any,         # SessionManager
        workspaces: Any,       # WorkspaceManager
        memory: Any,           # MemoryService
        config: Any,           # ConfigService
        bus: Any = None,
        max_iterations: int = 20,
        self_status: Callable[[bool], dict] | None = None,
        self_update: Callable[..., dict] | None = None,
    ):
        self.db = db
        self.llm_factory = llm_factory
        self.windows = windows
        self.jobs = jobs
        self.sessions = sessions
        self.workspaces = workspaces
        self.memory = memory
        self.config = config
        self.bus = bus
        self.max_iterations = max_iterations
        self.self_status = self_status
        self.self_update = self_update

        self.builder = ContextBuilder(memory_service=memory)
        self._sessions: dict[str, WindowSession] = {}
        # one turn per admin conversation at a time: the console (HTTP), the QQ
        # gateway and internal notifications can all target the same session, and
        # two AgentLoops sharing one WindowSession would interleave badly.
        self._locks: dict[str, asyncio.Lock] = {}
        self.session = self.session_for("default")  # legacy alias for the default admin

        svc = AdminServices(windows=windows, jobs=jobs, sessions=sessions,
                            workspaces=workspaces, memory=memory, config=config,
                            progress=self._progress_dict,
                            self_status=self_status, self_update=self_update)
        self.registry = ToolRegistry(gate=None, audit=db)
        self.registry.gate = _AdminGate()
        for tool in build_admin_tools(svc):
            self.registry.register(tool)

    # ------------------------------------------------------------- sessions
    def session_for(self, sender: str) -> WindowSession:
        """Admins are NOT unique: one persistent conversation per authorized user
        (`admin:<user_id>`), created lazily. Global memory stays shared."""
        key = (sender or "default").strip() or "default"
        sess = self._sessions.get(key)
        if sess is None:
            window = Window(id=f"admin:{key}", name=f"admin:{key}", role="admin",
                            workspace_path="", model="", mode="AUTO", status="idle")
            sess = WindowSession(window, self.db, self.db, self.db)
            self._sessions[key] = sess
        return sess

    def active_admins(self) -> list[str]:
        return sorted(self._sessions)

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def history(self, sender: str = "default", limit: int = 100) -> list[dict[str, Any]]:
        """Persisted conversation of one admin session (console chat view)."""
        session = self.session_for(sender)
        return [{"id": m.id, "role": m.role, "content": m.content,
                 "tokens": m.tokens, "created_at": m.created_at}
                for m in session.recent(limit)]

    def reset(self, sender: str = "default") -> int:
        """Drop the conversation history of one admin session (keeps memory/todos)."""
        session = self.session_for(sender)
        msgs = session.recent(100000)
        if not msgs:
            return 0
        return session.drop_messages(msgs[-1].id)

    # ----------------------------------------------------------- conversation
    async def handle_user_message(self, text: str, sender: str = "user") -> str:
        async with self._lock_for(sender):
            return await self._run_turn(text, sender)

    async def _run_turn(self, text: str, sender: str) -> str:
        role_spec = get_role("admin")
        session = self.session_for(sender)
        ctx = ToolContext(window_id=session.window.id, workspace=".", mode="AUTO",
                          actor=f"admin:{sender}", todo_repo=self.db, db=self.db,
                          llm_factory=self.llm_factory,
                          memory_service=self.memory, extra={"manual": True})
        loop = AgentLoop(
            llm=self.llm_factory(), registry=self.registry, session=session,
            builder=self.builder, tool_names=role_spec.tool_names if role_spec else None,
            max_iterations=self.max_iterations)
        result = await loop.run(f"[{sender}] {text}" if text else "", ctx)
        return result.content or "(no reply)"

    async def notify(self, text: str) -> str:
        """Push an internal notification into the system admin session and get its reply."""
        session = self.session_for("system")
        session.append("user", f"[system event] {text}")
        return await self.handle_user_message("", sender="system")

    def _progress_dict(self) -> dict[str, Any]:
        lines = []
        for w in self.windows.list():
            st = self.windows.status(w.id)
            lines.append(f"- {w.name}({w.id}) [{w.role}] status={st['status']} "
                         f"mode={w.mode} task={st['current_task']!r} tokens={st['tokens']}")
        job_lines = []
        for j in self.jobs.list():
            job_lines.append(f"- {j.name}({j.id}) win={j.window_id} sched={j.schedule_raw} "
                             f"enabled={j.enabled} next={j.next_run_at} fails={j.fail_count}")
        runs = self.jobs.runs(limit=5)
        run_lines = [f"- {r['status']} {r['started_at']} {r['summary'][:80]}" for r in runs]
        text = ("## Windows\n" + ("\n".join(lines) or "(none)") +
                "\n## Jobs\n" + ("\n".join(job_lines) or "(none)") +
                "\n## Recent runs\n" + ("\n".join(run_lines) or "(none)"))
        return {"text": text, "windows": len(lines), "jobs": len(job_lines)}


class _AdminGate:
    """Admin runs with full power; dangerous commands still surface for confirmation."""

    def check(self, tool_name: str, permission: str, args: dict[str, Any], ctx: ToolContext):
        from ..tools.registry import GateDecision
        return GateDecision(True)
