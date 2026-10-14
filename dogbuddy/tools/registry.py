# DogBuddyCn — Tool registry & permission choke point (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""ToolRegistry: uniform schema + permission adjudication for every tool.

Rules (ARCHITECTURE.md):
  - tools never import agents/; tools are capabilities, not business logic
  - permission DECISION LOGIC (mode matrix, dangerous-command blacklist) lives in
    L3 (agents/core/permissions.py) behind the PermissionGate port defined here;
    the registry only calls the injected gate — so Domain rules stay testable
    without IO and the registry stays business-free.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

logger = logging.getLogger("dogbuddy.tools")


@dataclass
class ToolResult:
    ok: bool
    output: str
    data: dict[str, Any] = field(default_factory=dict)
    needs_confirm: bool = False  # dangerous action awaiting user IM confirmation

    @classmethod
    def success(cls, output: str = "", **data: Any) -> "ToolResult":
        return cls(ok=True, output=output, data=data)

    @classmethod
    def failure(cls, output: str, **data: Any) -> "ToolResult":
        return cls(ok=False, output=output, data=data)

    @classmethod
    def confirm(cls, output: str, **data: Any) -> "ToolResult":
        return cls(ok=False, output=output, data=data, needs_confirm=True)

    def as_tool_text(self) -> str:
        if self.ok:
            return self.output or "OK"
        return f"[DENIED/ERROR] {self.output}" if not self.needs_confirm else f"[NEEDS_CONFIRM] {self.output}"


@dataclass
class ToolContext:
    """Everything a tool may touch, injected — tools hold no globals."""

    window_id: str
    workspace: str  # workspace root path (absolute)
    mode: str = "BUILD"  # BUILD | PLAN | AUTO
    actor: str = "worker"  # worker | admin | system
    # injected collaborators (ports; None when a capability is unavailable)
    todo_repo: Any = None
    db: Any = None
    panel: Any = None
    llm_factory: Callable[[], Any] | None = None  # for explore_subagent
    ask_user_fn: Callable[[str], Any] | None = None
    checkpoint_root: str = ""
    memory_service: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Tool(Protocol):
    """A capability unit: one tool does one thing."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for function calling
    permission: str  # read | write | shell | network

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...


@dataclass
class GateDecision:
    allowed: bool
    reason: str = ""
    needs_confirm: bool = False
    irreversible: bool = False  # T4.3: never auto-approved, even in托管代审


@runtime_checkable
class PermissionGate(Protocol):
    """Implemented in L3 (agents/core/permissions.py), injected into the registry."""

    def check(self, tool_name: str, permission: str, args: dict[str, Any], ctx: ToolContext) -> GateDecision: ...


class AllowAllGate:
    """Default gate for tests / admin windows."""

    def check(self, tool_name: str, permission: str, args: dict[str, Any], ctx: ToolContext) -> GateDecision:
        return GateDecision(True)


class ToolRegistry:
    def __init__(self, gate: PermissionGate | None = None, audit: Any = None):
        self._tools: dict[str, Tool] = {}
        self.gate = gate or AllowAllGate()
        self.audit = audit  # AuditSink-compatible (actor, action, detail)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, only: list[str] | None = None) -> list[dict[str, Any]]:
        out = []
        for name, tool in sorted(self._tools.items()):
            if only and name not in only:
                continue
            out.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return out

    async def execute(self, name: str, args: dict[str, Any], ctx: ToolContext,
                      approved: bool = False) -> ToolResult:
        """Run one tool through the gate. `approved=True` (T4.1) replays a
        previously confirmed call — the gate lets the danger check pass but
        everything else (workspace writes, PLAN mode) still applies."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(
                f"tool not found: {name}. 可用工具见能力清单；不要臆造工具名。",
                tool_failure=True)
        if approved:
            ctx.extra["confirmed"] = True  # human-approved replay (T4.1)
        decision = self.gate.check(name, tool.permission, args, ctx)
        if not decision.allowed:
            if decision.needs_confirm:
                self._audit(ctx, name, f"needs_confirm: {decision.reason}", args)
                return ToolResult.confirm(
                    decision.reason or "dangerous command, confirmation required",
                    irreversible=bool(getattr(decision, "irreversible", False)))
            self._audit(ctx, name, f"denied: {decision.reason}", args)
            return ToolResult.failure(decision.reason or f"permission denied for {name}",
                                      policy_denied=True)
        # C3: 坏参数不抛异常，而是回传"哪里错了 + 正确 schema"，让模型自改
        invalid = self._validate_args(tool, args)
        if invalid is not None:
            self._audit(ctx, name, f"bad_args: {invalid[:160]}", args if isinstance(args, dict) else {})
            return ToolResult.failure(invalid, tool_failure=True, bad_arguments=True)
        try:
            result = await tool.execute(args, ctx)
        except Exception as e:  # noqa: BLE001 — tool errors become tool results
            from ..errors import action_hint, classify, describe
            cls = classify(e)
            logger.exception("tool %s crashed (%s)", name, cls.value)
            return ToolResult.failure(
                f"tool crashed: {describe(e)}. {action_hint(e)}",
                tool_failure=True, error_class=cls.value)
        if self.audit and tool.permission in ("write", "shell"):
            self._audit(ctx, name, "executed" if result.ok else f"failed: {result.output[:200]}", args)
        return result

    # ---- C3 参数校验（Batch 6） -------------------------------------------
    @staticmethod
    def _validate_args(tool: Any, args: Any) -> str | None:
        """返回错误说明（给模型自改用）或 None（合法）。

        只做"能明确判定为错"的检查：不是对象 / 缺必填 / 有未声明参数。
        不做完整 JSON-Schema 校验（避免误杀模型合理的额外字段写法之外的情况）。
        """
        if not isinstance(args, dict):
            return (f"参数必须是 JSON 对象，收到 {type(args).__name__}。"
                    f"该工具的参数 schema：{json.dumps(tool.parameters or {}, ensure_ascii=False)[:400]}")
        params = tool.parameters or {}
        props = params.get("properties") or {}
        required = [r for r in (params.get("required") or [])]
        missing = [r for r in required if r not in args or args.get(r) in (None, "")]
        if missing:
            return (f"缺少必填参数 {missing}。请按该工具的参数 schema 重新调用："
                    f"{json.dumps(params, ensure_ascii=False)[:500]}")
        if props:
            unknown = [k for k in args if k not in props]
            if unknown:
                return (f"包含未声明的参数 {unknown}；该工具只接受 {sorted(props)}。"
                        f"请去掉多余的参数后重新调用。")
        return None

    def _audit(self, ctx: ToolContext, name: str, detail: str, args: dict[str, Any]) -> None:
        if not self.audit:
            return
        try:
            safe_args = {k: str(v)[:120] for k, v in args.items()}
            self.audit.audit(ctx.actor, f"tool:{name}", f"{detail} args={safe_args}")
        except Exception:  # audit must never break tooling
            logger.warning("audit sink failed", exc_info=True)


# ---- registry assembly helpers -------------------------------------------


def build_registry(gate: PermissionGate | None = None, audit: Any = None,
                   include: list[str] | None = None,
                   jobs: Any = None, run_job_now: Any = None) -> ToolRegistry:
    """Register the standard programmer toolbox. Import kept local so individual
    tool modules can be tested without pulling the whole package in.

    `jobs` (JobStore) + `run_job_now` 是 T8.1 自助排期需要的注入点——不注入时
    `schedule` 工具仍会注册，但执行时返回"排期服务不可用"（便于纯工具测试）。
    """
    from . import ask_user as _ask
    from . import automation_ops as _auto
    from . import checkpoint as _ckpt
    from . import file_ops as _file
    from . import search_ops as _search
    from . import server_exec as _srv
    from . import shell_ops as _shell
    from . import subagent as _sub
    from . import todo_ops as _todo

    registry = ToolRegistry(gate=gate, audit=audit)
    tools: list[Tool] = [
        *_file.all_tools(), *_shell.all_tools(), *_search.all_tools(),
        *_todo.all_tools(), *_ckpt.all_tools(), *_sub.all_tools(),
        *_auto.all_tools(jobs=jobs, runner=run_job_now),
        _ask.AskUserTool(), _srv.ServerExecTool(),
    ]
    for t in tools:
        if include and t.name not in include:
            continue
        registry.register(t)
    return registry
