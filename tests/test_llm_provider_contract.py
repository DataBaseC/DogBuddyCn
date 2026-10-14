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


# --------------------------------------------------------------------------- model inheritance
def _cfg():
    from dogbuddy.config import AppConfig, LLMConfig
    cfg = AppConfig(llm=LLMConfig(base_url="http://x/v1", api_key="k", model="admin-model"))
    cfg.llm_profiles["cheap"] = LLMConfig(base_url="http://x/v1", api_key="k", model="cheap-model")
    return cfg


def test_effective_model_prefers_override_then_profile_then_default():
    from dogbuddy.config import AppConfig, LLMConfig
    cfg = _cfg()
    assert cfg.effective_model(None, "") == "admin-model"          # 员工留空 → 继承
    assert cfg.effective_model(None, "my-model") == "my-model"     # 手动指定覆盖
    assert cfg.effective_model("cheap", "") == "cheap-model"       # 配置套
    assert cfg.effective_model("cheap", "my-model") == "my-model"  # 指定优先于配置套
    cfg.active_llm_profile = "cheap"
    assert cfg.effective_model(None, "") == "cheap-model"          # 活动配置套 = 管理员所用


def test_registry_honours_model_override_without_bleeding_between_windows():
    from dogbuddy.llm.profile_registry import LLMProfileRegistry
    reg = LLMProfileRegistry(_cfg())

    inherited = reg.provider_for(None, None)
    assert inherited.model_name == "admin-model"

    overridden = reg.provider_for(None, "worker-model")
    assert overridden.model_name == "worker-model"

    # the override must not mutate the shared/default provider
    assert reg.provider_for(None, None).model_name == "admin-model"
    assert reg.provider_for(None, None) is inherited
    assert reg.provider_for(None, "worker-model") is overridden

    # profile + override combinations are cached independently
    assert reg.provider_for("cheap", None).model_name == "cheap-model"
    assert reg.provider_for("cheap", "worker-model").model_name == "worker-model"


def test_registry_invalidate_clears_model_variants():
    from dogbuddy.llm.profile_registry import LLMProfileRegistry
    reg = LLMProfileRegistry(_cfg())
    a = reg.provider_for(None, "m1")
    reg.invalidate(None)
    assert reg.provider_for(None, "m1") is not a
    # profile-scoped invalidation only drops that profile's entries
    b = reg.provider_for("cheap", None)
    reg.invalidate("cheap")
    assert reg.provider_for("cheap", None) is not b
    c = reg.provider_for(None, "kept")
    reg.invalidate("cheap")
    assert reg.provider_for(None, "kept") is c


# --------------------------------------------------------------- streaming (T2.5)
def test_fake_llm_stream_contract():
    """FakeLLM.stream_chat must mirror the real provider chunk protocol."""
    from dogbuddy.llm.provider import StreamChunk
    from tests.helpers import FakeLLM
    llm = FakeLLM()
    llm.push(content="hello world")
    chunks = _run(_collect(llm.stream_chat([{"role": "user", "content": "hi"}])))
    assert all(isinstance(c, StreamChunk) for c in chunks)
    assert "".join(c.delta for c in chunks) == "hello world"
    finals = [c for c in chunks if c.done]
    assert len(finals) == 1 and not finals[0].delta
    assert not chunks[0].done  # deltas stream before the terminal chunk


def test_provider_base_stream_fallback():
    """Providers without native streaming still expose stream_chat (one-shot)."""
    from dogbuddy.llm.provider import AbstractProvider, StreamChunk

    class LegacyProvider(AbstractProvider):
        async def text_chat(self, messages, tools=None, **kw):
            from dogbuddy.llm.provider import LLMResponse
            return LLMResponse(content="legacy reply")

    chunks = _run(_collect(LegacyProvider().stream_chat([])))
    assert len(chunks) == 1 and chunks[0].done
    assert chunks[0].delta == "legacy reply"
    assert isinstance(chunks[0], StreamChunk)


def test_loop_on_delta_fires_for_streaming():
    """AgentLoop delivers fragments through on_delta and stores the whole."""
    from dogbuddy.agents.core.loop import AgentLoop
    from dogbuddy.windows.session import WindowSession
    from dogbuddy.windows.context import ContextBuilder
    from tests.helpers import FakeLLM
    import tempfile
    from dogbuddy.db import Database
    from dogbuddy.ports import Window

    tmp = tempfile.mkdtemp()
    db = Database(f"{tmp}/t.db")
    w = Window(id="w1", name="w", role="worker_programmer", workspace_path=tmp,
               model="", mode="BUILD")
    db.insert_window(w)
    session = WindowSession(w, db, db, db)
    llm = FakeLLM()
    llm.push(content="streamed answer")
    loop = AgentLoop(llm=llm, registry=_empty_registry(), session=session,
                     builder=ContextBuilder(), max_iterations=2)
    got: list[str] = []
    result = _run(loop.run("hi", _ctx(), on_delta=got.append))
    assert "".join(got) == "streamed answer"
    assert result.content == "streamed answer"
    assert result.stop_reason == "done"


def test_loop_stream_without_stream_chat_degrades():
    """A third-party llm lacking stream_chat must not crash the run."""
    from dogbuddy.agents.core.loop import AgentLoop
    from dogbuddy.windows.session import WindowSession
    from dogbuddy.windows.context import ContextBuilder
    from tests.helpers import FakeLLM
    import tempfile
    from dogbuddy.db import Database
    from dogbuddy.ports import Window

    class BareLLM:  # only chat(), like a minimal third-party integration
        async def chat(self, messages, tools=None, **kw):
            from dogbuddy.llm.provider import LLMResponse
            return LLMResponse(content="bare reply")

    tmp = tempfile.mkdtemp()
    db = Database(f"{tmp}/t.db")
    w = Window(id="w1", name="w", role="worker_programmer", workspace_path=tmp,
               model="", mode="BUILD")
    db.insert_window(w)
    session = WindowSession(w, db, db, db)
    loop = AgentLoop(llm=BareLLM(), registry=_empty_registry(), session=session,
                     builder=ContextBuilder(), max_iterations=2)
    got: list[str] = []
    result = _run(loop.run("hi", _ctx(), on_delta=got.append))
    assert result.content == "bare reply"
    assert "".join(got) == "bare reply"


async def _collect(aiter):
    return [c async for c in aiter]


def _empty_registry():
    from dogbuddy.tools.registry import ToolRegistry
    return ToolRegistry()


def _ctx():
    from dogbuddy.tools.registry import ToolContext
    return ToolContext(window_id="w1", workspace="/tmp", mode="BUILD")
