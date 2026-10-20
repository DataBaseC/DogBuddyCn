# DogBuddyCn — compactor tests (anti-loss structured facts)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database
from dogbuddy.memory.service import MemoryService
from dogbuddy.ports import Window
from dogbuddy.windows.compactor import Compactor
from dogbuddy.windows.session import WindowSession

from helpers import FakeLLM


def _session(tmp_path, n_messages=40):
    db = Database(tmp_path / "t.db")
    w = Window(id="w1", name="w1", role="worker_programmer",
               workspace_path=str(tmp_path), model="m")
    db.insert_window(w)
    for i in range(n_messages):
        db.add_message("w1", "user" if i % 2 == 0 else "assistant", f"message {i} " + "x" * 50)
    return db, WindowSession(w, db, db, db)


def test_should_compress_threshold(tmp_path):
    db, sess = _session(tmp_path, 5)
    c = Compactor(FakeLLM(), db, threshold=0.8, max_context_tokens=1000)
    assert not c.should_compress(700, 1000)
    assert c.should_compress(850, 1000)
    db.close()


def test_compact_preserves_structured_facts(tmp_path):
    db, sess = _session(tmp_path, 40)
    mem = MemoryService(tmp_path / "memory")
    summary = {
        "narrative": "worked on parser",
        "file_changes": [{"path": "src/a.py", "action": "modified", "note": "fixed"}],
        "todos": ["write tests"],
        "decisions": ["use recursive descent"],
        "lessons": ["regex was too fragile"],
    }
    llm = FakeLLM()
    llm.push(content=json.dumps(summary))
    c = Compactor(llm, db, mem, threshold=0.0, max_context_tokens=100)
    import asyncio
    rec = asyncio.get_event_loop().run_until_complete(c.compact_now(sess))

    assert rec.replaced_count > 0
    # only the tail stays verbatim
    remaining = sess.recent(1000)
    assert len(remaining) <= 12
    # facts survived in the compaction record
    comps = db.list_compactions("w1")
    assert comps and comps[0].summary["decisions"] == ["use recursive descent"]
    # facts precipitated into window memory
    mem_text = mem.recall_window("w1")
    assert "use recursive descent" in mem_text or "decision: use recursive descent" in mem_text
    db.close()


def test_compact_falls_back_when_llm_breaks(tmp_path):
    db, sess = _session(tmp_path, 30)

    class BoomLLM:
        async def chat(self, *a, **k):
            raise RuntimeError("llm down")

    c = Compactor(BoomLLM(), db, threshold=0.0, max_context_tokens=10)
    import asyncio
    rec = asyncio.get_event_loop().run_until_complete(c.compact_now(sess))
    assert rec is not None  # compaction still happens with dumb summary
    assert len(sess.recent(1000)) <= 12
    db.close()


# ---------------------------------------------------------- v0.10.38 消息数触发
def test_message_count_triggers_compaction_even_with_huge_token_budget(tmp_path):
    """token 阈值在长上下文库上永不触发（0.8×1,048,576），但会话一超过
    `recent(60)` 窗口就开始滑动 → 提供商缓存前缀匹配不上 → 长会话工位
    每次全价重发（实测 95 条会话的工位缓存命中 13.8%，56 条的 97.5%）。
    消息数触发在 60 之前压缩，窗口不再滑动，缓存恢复。"""
    import asyncio
    db, sess = _session(tmp_path, 60)
    llm = FakeLLM()
    llm.push(content='{"narrative": "sum", "file_changes": [], "todos": [], '
                     '"decisions": [], "lessons": []}')
    # token 预算巨大 → 光看 token 永远不该压
    c = Compactor(llm, db, threshold=0.8, max_context_tokens=1048576, max_messages=48)
    assert c.should_compress(30000, 1048576) is False, "token 阈值不应触发"
    assert c.should_compress_by_count(sess) is True, "60 条消息必须触发计数维度"
    rec = asyncio.get_event_loop().run_until_complete(c.maybe_compact(sess))
    assert rec is not None, "消息数超线必须压缩"
    # 压完回到 12 条 → recent(60) 窗口不再滑动 → 前缀稳定 → 缓存可命中
    assert len(sess.recent(1000)) <= 12
    db.close()


def test_short_session_never_compacts_by_count(tmp_path):
    """短会话（≤ 触发线）不动——压缩本身要花一次 LLM 调用，不能滥用。"""
    import asyncio
    db, sess = _session(tmp_path, 20)
    c = Compactor(FakeLLM(), db, threshold=0.8, max_context_tokens=1048576, max_messages=48)
    assert c.should_compress_by_count(sess) is False
    assert asyncio.get_event_loop().run_until_complete(c.maybe_compact(sess)) is None
    db.close()


def test_window_never_slides_after_count_compaction(tmp_path):
    """回归根因本身：压缩后持续追加消息，到达触发线会被再次压缩，
    消息数峰值不超过触发线 → recent(60) 窗口永远不滑动 → 缓存前缀不被打断。"""
    import asyncio
    db, sess = _session(tmp_path, 0)
    llm = FakeLLM()
    for _ in range(6):
        llm.push(content='{"narrative": "sum", "file_changes": [], "todos": [], '
                         '"decisions": [], "lessons": []}')
    c = Compactor(llm, db, threshold=0.8, max_context_tokens=1048576, max_messages=48)
    loop = asyncio.get_event_loop()
    max_seen = 0
    for i in range(70):
        sess.append("user", f"m{i} " + "x" * 50)
        rec = loop.run_until_complete(c.maybe_compact(sess))
        max_seen = max(max_seen, len(sess.recent(1000)))
        if rec is not None:
            assert len(sess.recent(1000)) <= 12
    assert max_seen <= 49, f"消息数峰值 {max_seen} 超过触发线 48，窗口会滑动"
    db.close()
