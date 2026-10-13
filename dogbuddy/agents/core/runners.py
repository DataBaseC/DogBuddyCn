# DogBuddyCn — task runner registry (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""TaskRunner registry — the execution side of the `Task.kind` extension point.

V1 ships two runners:
  prompt — the full AgentLoop (LLM tool loop in the window session)
  script — headless shell script execution (no agent turn)

Register more (deploy-only, test-only, MCP-driven…) via register_runner():
the window manager dispatches purely on task.kind.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ...tools.registry import ToolContext, ToolResult
from .loop import AgentLoop, AgentResult

logger = logging.getLogger("dogbuddy.agents.runners")


@dataclass
class TaskEnv:
    """Everything a runner may need (injected — runners hold no globals)."""

    window: Any
    session: Any
    registry: Any                 # ToolRegistry
    llm_factory: Any              # (profile=None) -> provider
    builder: Any                  # ContextBuilder
    compactor: Any = None
    ctx: ToolContext = None  # type: ignore[assignment]
    tool_names: list[str] | None = None
    max_iterations: int = 25
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunnerResult:
    status: str = "ok"  # ok | failed | rate_limited
    summary: str = ""
    pending_confirms: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TaskRunner(Protocol):
    kind: str

    async def run(self, task: Any, env: TaskEnv) -> RunnerResult: ...


class PromptRunner:
    """The classic agent turn: AgentLoop inside the persistent window session."""

    kind = "prompt"

    async def run(self, task: Any, env: TaskEnv) -> RunnerResult:
        loop = AgentLoop(
            llm=env.llm_factory(getattr(task, "llm_profile", "") or None),
            registry=env.registry, session=env.session,
            builder=env.builder, compactor=env.compactor,
            tool_names=env.tool_names, max_iterations=env.max_iterations)
        result: AgentResult = await loop.run(task.prompt, env.ctx)
        status = "failed" if result.stop_reason == "error" else "ok"
        if "rate limited" in (result.content or "").lower():
            status = "rate_limited"
        return RunnerResult(
            status=status, summary=result.content or "",
            pending_confirms=result.pending_confirms,
            pending_questions=result.pending_questions,
            meta={"iterations": result.iterations, "tool_calls": result.tool_calls})


class ScriptRunner:
    """Headless shell task (no agent): task.prompt is a shell command line.

    Still gated by the PermissionGate through the registered shell tool."""

    kind = "script"
    timeout = 600

    async def run(self, task: Any, env: TaskEnv) -> RunnerResult:
        shell = env.registry.get("shell")
        if shell is None:
            return RunnerResult(status="failed", summary="script runner: shell tool unavailable")
        args = {"command": task.prompt, "timeout": self.timeout}
        result: ToolResult = await env.registry.execute("shell", args, env.ctx)
        status = "ok" if result.ok else "failed"
        return RunnerResult(status=status, summary=result.output[:1000],
                            meta={"returncode": result.data.get("returncode")})


# ------------------------------------------------------------- registry


class RunnerRegistry:
    def __init__(self) -> None:
        self._runners: dict[str, TaskRunner] = {}

    def register(self, runner: TaskRunner) -> None:
        self._runners[runner.kind] = runner

    def get(self, kind: str) -> TaskRunner | None:
        return self._runners.get(kind or "prompt")

    def kinds(self) -> list[str]:
        return sorted(self._runners)


def default_runners() -> RunnerRegistry:
    reg = RunnerRegistry()
    reg.register(PromptRunner())
    reg.register(ScriptRunner())
    return reg
