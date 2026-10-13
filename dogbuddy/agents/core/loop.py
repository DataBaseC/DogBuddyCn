# DogBuddyCn — agent tool loop (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""AgentLoop: context assembly → LLM → tool execution → repeat until done.

Platform-agnostic — the same loop serves IM chat, scheduled runs and panel
dispatch. All capabilities (LLM, tools, session, compactor) are injected.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ...tools.registry import ToolContext, ToolRegistry
from ...windows.context import ContextBuilder
from ...windows.session import WindowSession

logger = logging.getLogger("dogbuddy.agents.loop")


@dataclass
class AgentResult:
    content: str = ""
    stop_reason: str = "done"  # done | max_iterations | error
    iterations: int = 0
    tool_calls: int = 0
    pending_confirms: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)


class AgentLoop:
    def __init__(
        self,
        llm: Any,
        registry: ToolRegistry,
        session: WindowSession,
        builder: ContextBuilder | None = None,
        compactor: Any = None,
        tool_names: list[str] | None = None,
        max_iterations: int = 25,
    ):
        self.llm = llm
        self.registry = registry
        self.session = session
        self.builder = builder or ContextBuilder()
        self.compactor = compactor
        self.tool_names = tool_names
        self.max_iterations = max_iterations

    # ------------------------------------------------------------------ run
    async def run(self, user_input: str, ctx: ToolContext) -> AgentResult:
        result = AgentResult()
        if user_input:
            self.session.append("user", user_input)
        schemas = self.registry.schemas(only=self.tool_names)

        for step in range(1, self.max_iterations + 1):
            result.iterations = step
            messages = self.session.build_context(self.builder)
            try:
                resp = await self.llm.chat(messages, tools=schemas or None)
            except Exception as e:  # noqa: BLE001 — record and stop; retry policy already ran
                logger.exception("LLM chat failed")
                self.session.append("system", f"[LLM error] {e}")
                result.stop_reason = "error"
                result.content = f"LLM call failed: {e}"
                break

            if not resp.tool_calls:
                result.content = resp.content or ""
                self.session.append("assistant", result.content)
                result.stop_reason = "done"
                break

            # assistant turn with tool calls (persisted with meta for rebuild)
            llm_tool_calls = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in resp.tool_calls
            ]
            self.session.append("assistant", resp.content or "",
                                meta={"tool_calls": llm_tool_calls})
            for tc in resp.tool_calls:
                result.tool_calls += 1
                tool_result = await self.registry.execute(tc.name, tc.arguments, ctx)
                self.session.append(
                    "tool", tool_result.as_tool_text(),
                    meta={"tool_call_id": tc.id, "tool_name": tc.name})
                if tool_result.needs_confirm:
                    result.pending_confirms.append(tool_result.output)
                if tc.name == "ask_user" and not tool_result.data.get("answered", True):
                    q = tool_result.data.get("question", "")
                    if q:
                        result.pending_questions.append(q)
        else:
            result.stop_reason = "max_iterations"
            result.content = result.content or "(stopped: max iterations reached)"

        # post-run compaction (anti-loss); never breaks the run
        if self.compactor is not None:
            try:
                await self.compactor.maybe_compact(self.session)
            except Exception:  # noqa: BLE001
                logger.warning("compaction failed", exc_info=True)
        return result
