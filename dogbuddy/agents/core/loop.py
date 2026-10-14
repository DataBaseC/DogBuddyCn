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
    async def run(self, user_input: str, ctx: ToolContext,
                  on_delta: Callable[[str], None] | None = None) -> AgentResult:
        """Run one task. `on_delta` (optional, T2.5) receives text fragments as
        the model streams them — wired to the SSE feed for the typewriter UI."""
        result = AgentResult()
        if user_input:
            self.session.append("user", user_input)
        schemas = self.registry.schemas(only=self.tool_names)

        for step in range(1, self.max_iterations + 1):
            result.iterations = step
            messages = self.session.build_context(self.builder)
            try:
                if on_delta is not None:
                    resp = await self._stream_turn(messages, schemas, on_delta)
                else:
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
                if tool_result.needs_confirm:
                    tool_result = await self._approval_flow(tc, tool_result, ctx)
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

    async def _stream_turn(self, messages: list, schemas: list,
                           on_delta: Callable[[str], None]) -> Any:
        """Consume provider stream chunks → one LLMResponse (T2.5).

        Identical output shape to `llm.chat` so the rest of the loop does not
        care which path ran; the only difference is `on_delta` seeing fragments.
        """
        from ...llm.provider import LLMResponse

        stream = getattr(self.llm, "stream_chat", None)
        if stream is None:  # third-party llm without streaming: degrade gracefully
            resp = await self.llm.chat(messages, tools=schemas or None)
            if resp.content:
                try:
                    on_delta(resp.content)
                except Exception:  # noqa: BLE001
                    logger.debug("on_delta callback failed", exc_info=True)
            return resp

        parts: list[str] = []
        tool_calls: list = []
        finish_reason = "stop"
        usage: dict = {}
        async for chunk in stream(messages, tools=schemas or None):
            if chunk.delta:
                parts.append(chunk.delta)
                try:
                    on_delta(chunk.delta)
                except Exception:  # noqa: BLE001 — UI feedback never breaks a run
                    logger.debug("on_delta callback failed", exc_info=True)
            if getattr(chunk, "done", False):
                tool_calls = list(chunk.tool_calls or [])
                finish_reason = chunk.finish_reason or "stop"
                usage = dict(chunk.usage or {})
        return LLMResponse(content="".join(parts) or None, tool_calls=tool_calls,
                           finish_reason=finish_reason, usage=usage)

    async def _approval_flow(self, tc: Any, tool_result: Any, ctx: ToolContext) -> Any:
        """T4.1/T4.3 — a dangerous call is PAUSED here until approved.

        三道保险 (three safeguards):
        1. irreversible commands are NEVER auto-approved — the owner decides;
        2. 托管代审: with an `approval_reviewer` injected (admin delegation mode),
           an independent LLM reviews the call; approve → replay, deny → refuse;
        3. every decision is audited (留痕) and turns into a pending decision so
           the owner sees it in the console/QQ either way.
        """
        from ...tools.registry import ToolResult

        reason = tool_result.output
        irreversible = bool((tool_result.data or {}).get("irreversible"))
        preview = json.dumps(tc.arguments, ensure_ascii=False)[:300]
        audit = getattr(ctx, "db", None)

        def _log(action: str) -> None:
            if audit is not None:
                try:
                    audit.audit("owner", f"approval:{action}",
                                f"tool={tc.name} irreversible={irreversible} args={preview}")
                except Exception:  # noqa: BLE001
                    logger.warning("approval audit failed", exc_info=True)

        question = (f"高危操作待审批：{tc.name}\n参数：{preview}\n风险：{reason}\n"
                    f"批准请回复「批准」，拒绝回复「拒绝」。")

        # safeguard 2 — 托管代审 (skipped entirely for irreversible commands)
        reviewer = ctx.extra.get("approval_reviewer")
        if reviewer is not None and not irreversible:
            try:
                verdict = await reviewer(tc.name, tc.arguments, reason)
            except Exception as e:  # noqa: BLE001 — reviewer failure → ask the human
                logger.warning("approval reviewer failed: %s", e)
                verdict = None
            if verdict is not None:
                if verdict:
                    _log("auto_approved")
                    return await self.registry.execute(tc.name, tc.arguments, ctx,
                                                       approved=True)
                _log("auto_denied")
                return ToolResult.failure(
                    f"[托管代审拒绝] {reason}；操作已拦截并记录，未执行。")

        # safeguard 3 — ask the owner (durable decision queue when available)
        ask = getattr(ctx, "ask_user_fn", None)
        if ask is None:
            return tool_result  # no channel to ask — keep the old pending-confirm report
        answer = ask(question, options=["批准", "拒绝"])
        if hasattr(answer, "__await__"):
            answer = await answer
        text = str(answer or "")
        if text.strip().startswith("批准") or text.strip().lower() in ("yes", "y", "ok"):
            _log("approved")
            return await self.registry.execute(tc.name, tc.arguments, ctx, approved=True)
        _log("denied")
        return ToolResult.failure(f"[用户拒绝] {reason}；操作已拦截，未执行。回答：{text[:200]}")
