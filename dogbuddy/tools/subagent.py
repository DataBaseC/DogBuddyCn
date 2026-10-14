# DogBuddyCn — explore_subagent tool (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Read-only Explore subagent: an isolated mini agent loop with a restricted
toolbox (read-only + web). Independent context — never pollutes the window
session. Design after AiCode's Explore subagent (GPL-3.0, design only)."""
from __future__ import annotations

import logging
from typing import Any

from .registry import ToolContext, ToolResult, build_registry

logger = logging.getLogger("dogbuddy.tools.subagent")

READONLY_TOOLS = ["read_file", "glob", "grep", "web_search", "web_fetch"]
MAX_STEPS = 8

_SUBAGENT_SYSTEM = (
    "You are a read-only research subagent. Investigate the given question inside the "
    "workspace using only your read tools and web search/fetch. Do not attempt writes. "
    "Finish with a compact factual report: findings, file references, open questions."
)


class ExploreSubagentTool:
    name = "explore_subagent"
    description = ("Launch a read-only research subagent with its own context to investigate a "
                   "question (codebase survey, web research). Returns its written report.")
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "what to investigate"},
            "hint": {"type": "string", "description": "optional files/dirs to look at first"},
        },
        "required": ["query"],
    }
    permission = "read"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult.failure("query must not be empty")
        if ctx.llm_factory is None:
            return ToolResult.failure("explore_subagent unavailable: no LLM injected")
        llm = ctx.llm_factory()
        if llm is None:
            return ToolResult.failure("explore_subagent unavailable: LLM factory returned None")

        sub_ctx = ToolContext(
            window_id=ctx.window_id, workspace=ctx.workspace, mode="PLAN",
            actor="explore", todo_repo=ctx.todo_repo, db=ctx.db, panel=None,
            checkpoint_root=ctx.checkpoint_root, extra=dict(ctx.extra),
            # 子代理**继承调用者的作用域**——不得比父级更宽（审查报告 P1-5：
            # 这条路径原来 scope=None，虽然 fail-closed 安全，但语义上应继承）
            scope=ctx.scope,
        )
        registry = build_registry(include=READONLY_TOOLS)
        schemas = registry.schemas()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _SUBAGENT_SYSTEM},
            {"role": "user", "content": query + (f"\nHint: {args['hint']}" if args.get("hint") else "")},
        ]
        report = ""
        for _step in range(MAX_STEPS):
            try:
                resp = await llm.chat(messages, tools=schemas)
            except Exception as e:  # noqa: BLE001
                return ToolResult.failure(f"explore subagent LLM failed: {e}")
            if resp.tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": resp.content or "",
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name, "arguments": __import__("json").dumps(tc.arguments)}}
                        for tc in resp.tool_calls],
                })
                for tc in resp.tool_calls:
                    result = await registry.execute(tc.name, tc.arguments, sub_ctx)
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": result.as_tool_text()[:6000],
                    })
                continue
            report = resp.content or ""
            break
        if not report:
            report = "(subagent produced no report)"
        return ToolResult.success(report[:12000], report=report[:12000])


def all_tools() -> list:
    return [ExploreSubagentTool()]
