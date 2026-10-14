# DogBuddyCn — test helpers
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
from typing import Any

from dogbuddy.llm.provider import LLMResponse, StreamChunk, ToolCall


class FakeLLM:
    """Scripted LLM: each chat() pops the next preset LLMResponse.

    Mirrors the real provider contract, including `stream_chat` (T2.5):
    deltas first, then one final chunk carrying tool_calls/usage/done.
    """

    def __init__(self, script: list[LLMResponse] | None = None):
        self.script = list(script or [])
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, tools=None, **kw) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools})
        if self.script:
            return self.script.pop(0)
        return LLMResponse(content="(fake llm exhausted)")

    async def stream_chat(self, messages, tools=None, **kw):
        resp = await self.chat(messages, tools=tools, **kw)
        content = resp.content or ""
        third = max(1, len(content) // 3)
        for i in range(0, len(content), third):
            yield StreamChunk(delta=content[i:i + third])
        yield StreamChunk(tool_calls=list(resp.tool_calls),
                          finish_reason=resp.finish_reason,
                          usage=dict(resp.usage), done=True)

    def push(self, content: str | None = None, tool_calls: list[ToolCall] | None = None):
        self.script.append(LLMResponse(content=content, tool_calls=tool_calls or []))


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def make_llm_factory(script: list[LLMResponse]):
    llm = FakeLLM(script)
    return llm, (lambda: llm)


def tc(tool_name: str, **args) -> ToolCall:
    return ToolCall(id=f"call_{tool_name}_{len(args)}", name=tool_name, arguments=args)
