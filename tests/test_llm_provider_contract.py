# DogBuddyCn — LLM provider contract regression tests
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""The two defects that made every *real* LLM call fail while the whole test
suite stayed green (every test injected a FakeLLM that happened to satisfy a
different contract than the real providers):

1. consumers call `llm.chat(...)`; providers implemented `text_chat` only
2. `_parse` did `int(v)` over the whole `usage` dict, but real endpoints return
   nested detail objects (`prompt_tokens_details`, …)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.llm.openai_compat import OpenAICompatProvider, _usage_dict  # noqa: E402
from dogbuddy.llm.provider import AbstractProvider, LLMResponse, parse_tool_arguments  # noqa: E402
from dogbuddy.ports import LLMProvider  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_provider_exposes_the_port_method_chat():
    """`chat` is what AgentLoop/Compactor/ReportService/subagent actually call."""
    assert hasattr(LLMProvider, "chat"), "the port declares chat"
    assert hasattr(AbstractProvider, "chat"), "concrete providers must satisfy the port"
    assert hasattr(AbstractProvider, "text_chat")


def test_chat_delegates_to_text_chat():
    seen: dict = {}

    class Probe(AbstractProvider):
        async def text_chat(self, messages, tools=None, **kwargs):
            seen["messages"] = messages
            seen["tools"] = tools
            return LLMResponse(content="ok")

    p = Probe()
    out = _run(p.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}]))
    assert isinstance(out, LLMResponse) and out.content == "ok"
    assert seen["messages"][0]["content"] == "hi"
    assert seen["tools"] is not None


def test_usage_with_nested_details_does_not_crash():
    """Real relays send nested usage objects — int(dict) used to raise TypeError."""
    raw = {
        "prompt_tokens": 128,
        "completion_tokens": 42,
        "total_tokens": 170,
        "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 12},
        "cost": "0.0001",
        "is_byok": False,
    }
    usage = _usage_dict(raw)
    assert usage["prompt_tokens"] == 128
    assert usage["total_tokens"] == 170
    assert usage["cost"] == "0.0001"     # non-integer values are preserved verbatim
    assert usage["is_byok"] == 0
    assert usage["prompt_tokens_details"] == {"cached_tokens": 0, "audio_tokens": 0}
    assert _usage_dict({"cached_tokens": "12"})["cached_tokens"] == 12  # integer strings coerce


def test_parse_end_to_end_with_nested_usage_and_dict_tool_args():
    data = {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "window_create",
                                 "arguments": {"name": "探针工位"}},  # dict, not JSON text
                }],
            },
        }],
        "usage": {"total_tokens": 9, "prompt_tokens_details": {"cached_tokens": 0}},
    }
    resp = OpenAICompatProvider._parse(data)
    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls[0].name == "window_create"
    assert resp.tool_calls[0].arguments == {"name": "探针工位"}
    assert resp.usage["total_tokens"] == 9
    assert resp.usage["prompt_tokens_details"] == {"cached_tokens": 0}


def test_parse_handles_empty_and_string_tool_arguments():
    assert parse_tool_arguments(None) == {}
    assert parse_tool_arguments("") == {}
    assert parse_tool_arguments('{"a": 1}') == {"a": 1}
    assert parse_tool_arguments({"already": "dict"}) == {"already": "dict"}
    resp = OpenAICompatProvider._parse({"choices": [{"message": {"content": "hi"}}]})
    assert resp.content == "hi" and resp.usage == {} and resp.tool_calls == []
