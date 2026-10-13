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
