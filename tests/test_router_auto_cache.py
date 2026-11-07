# DogBuddyCn — Router auto + cache diagnostics (0.26.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.kernel import KernelStore  # noqa: E402
from dogbuddy.kernel.domain import RunHandle  # noqa: E402
from dogbuddy.kernel.intake import ShadowIntake  # noqa: E402
from dogbuddy.kernel.router import KernelRouter  # noqa: E402
from dogbuddy.windows.context import ContextBuilder  # noqa: E402


class FakeWindows:
    def __init__(self):
        self.calls = []
        self.w = SimpleNamespace(id="w1", name="coder", role="worker_programmer",
                                 status="idle", project_id="")
        self._runtime = {}

    def list(self):
        return [self.w]

    def get(self, wid):
        return self.w if wid == "w1" else None

    def dispatch_run(self, **kw):
        self.calls.append(kw)
        return RunHandle(run_id="r-auto", work_item_id=kw.get("work_item_id", ""))


class FakeLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return SimpleNamespace(content=json.dumps(self.payload))


def _stack(tmp_path):
    db = Database(tmp_path / "a.db")
    store = KernelStore(db)
    return db, store, ShadowIntake(store)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_auto_creates_task_only_above_threshold(tmp_path):
    db, store, intake = _stack(tmp_path)
    fw, llm = FakeWindows(), FakeLLM({
        "action": "create_task", "confidence": 0.91,
        "prompt": "修复登录页", "title": "修登录", "reason": "明确任务"})
    router = KernelRouter(store, intake, mode="auto", windows=fw,
                          llm_factory=lambda: llm)
    r = _run(router.route_web_async(conversation="c1", text="帮我修一下登录页", sender="u"))
    assert r["handled"] and r["action"] == "create" and len(fw.calls) == 1
    assert llm.calls == 1
    db.close()


def test_auto_low_confidence_asks_without_task(tmp_path):
    db, store, intake = _stack(tmp_path)
    fw, llm = FakeWindows(), FakeLLM({
        "action": "create_task", "confidence": 0.4, "prompt": "随便看看"})
    router = KernelRouter(store, intake, mode="auto", windows=fw,
                          llm_factory=lambda: llm)
    r = _run(router.route_web_async(conversation="c1", text="帮我随便看看", sender="u"))
    assert r["handled"] and r["action"] == "ask"
    assert not fw.calls and store.health()["work_items"] == 0
    db.close()


def test_route_conversations_supports_qq_wildcard(tmp_path):
    db, store, intake = _stack(tmp_path)
    fw = FakeWindows()
    router = KernelRouter(store, intake, mode="explicit", windows=fw,
                          route_conversations=["qq:*"])
    assert router.enabled_for("qq:private:123")
    assert not router.enabled_for("web:default")
    r = _run(router.route_web_async(
        conversation="qq:private:123", text="/task 修登录", sender="123",
        source="onebot_v11", session_kind="private"))
    assert r["handled"] and r["action"] == "create" and len(fw.calls) == 1
    db.close()


def test_auto_smalltalk_skips_router_llm(tmp_path):
    db, store, intake = _stack(tmp_path)
    fw, llm = FakeWindows(), FakeLLM({"action": "chat", "confidence": 0.9})
    router = KernelRouter(store, intake, mode="auto", windows=fw,
                          llm_factory=lambda: llm)
    r = _run(router.route_web_async(conversation="c1", text="你好呀", sender="u"))
    assert r["handled"] is False and llm.calls == 0
    assert not fw.calls and store.health()["work_items"] == 0
    db.close()


def test_explicit_command_does_not_call_router_llm(tmp_path):
    db, store, intake = _stack(tmp_path)
    fw, llm = FakeWindows(), FakeLLM({"action": "chat", "confidence": 0.9})
    router = KernelRouter(store, intake, mode="auto", windows=fw,
                          llm_factory=lambda: llm)
    r = _run(router.route_web_async(conversation="c1", text="/task 修登录", sender="u"))
    assert r["handled"] and r["action"] == "create" and llm.calls == 0
    db.close()


def test_runtime_snapshot_splits_dynamic_system(tmp_path):
    class Memory:
        def recall(self, scope):
            return "GLOBAL-FACT"

        def recall_window(self, wid):
            return ""

    window = SimpleNamespace(id="w1", name="coder", role="worker_programmer",
                             workspace_path=str(tmp_path), mode="BUILD")
    session = SimpleNamespace(window=window, effective_workspace=str(tmp_path),
                              compaction_history=lambda: [], recent=lambda n: [])
    normal = ContextBuilder(memory_service=Memory()).build(session)
    assert normal[0]["role"] == "system" and "GLOBAL-FACT" in normal[0]["content"]
    snap = ContextBuilder(memory_service=Memory(), runtime_snapshot=True).build(session)
    assert snap[0]["role"] == "system" and "GLOBAL-FACT" not in snap[0]["content"]
    assert snap[1]["role"] == "system" and "GLOBAL-FACT" in snap[1]["content"]


def test_cache_diag_roundtrip(tmp_path):
    db, store, _intake = _stack(tmp_path)
    store.record_cache_diag(run_id="r1", window_id="w1", model="m",
                            system_hash="s", dynamic_hash="d",
                            prefix_hash="p", tool_hash="t")
    got = store.latest_cache_diag("r1")
    assert got["system_hash"] == "s" and got["tool_hash"] == "t"
    db.close()
