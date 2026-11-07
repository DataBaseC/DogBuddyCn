# DogBuddyCn — cache diagnostics (0.28.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.kernel import KernelStore  # noqa: E402


def _store(tmp_path):
    db = Database(tmp_path / "cache.db")
    return db, KernelStore(db)


def _rec(store, messages, *, window="w1", model="m", tool="t",
         parts=None, reason=""):
    return store.record_cache_diag(
        run_id="r1", window_id=window, model=model,
        system_hash="sys", dynamic_hash="dyn", prefix_hash="whole",
        tool_hash=tool, cache_hit=0, cache_miss=0,
        message_hashes=messages,
        message_roles=[m[0] for m in messages],
        dynamic_parts=parts or {}, reset_reason=reason)


def test_shared_prefix_and_changed_role(tmp_path):
    db, store = _store(tmp_path)
    _rec(store, [("system", "s"), ("user", "u1"), ("assistant", "a1")])
    rec = _rec(store, [("system", "s"), ("user", "u1"), ("user", "u2")])
    assert rec["shared_prefix_messages"] == 2
    assert rec["first_changed_index"] == 2
    row = store.latest_cache_diag(window_id="w1")
    assert row["first_changed_role"] == "user"
    assert row["shared_prefix_messages"] == 2
    db.close()


def test_memory_change_reason(tmp_path):
    db, store = _store(tmp_path)
    _rec(store, [("system", "s")], parts={"memory": "m1"})
    rec = _rec(store, [("system", "s")], parts={"memory": "m2"})
    assert rec["reset_reason"] == "memory_changed"
    db.close()


def test_skill_and_tool_change_reason(tmp_path):
    db, store = _store(tmp_path)
    _rec(store, [("system", "s")], parts={"skills": "a"}, tool="t1")
    assert _rec(store, [("system", "s")], parts={"skills": "b"},
                tool="t1")["reset_reason"] == "skill_changed"
    assert _rec(store, [("system", "s")], parts={"skills": "b"},
                tool="t2")["reset_reason"] == "tool_order_changed"
    db.close()


def test_cache_hit_miss_backfill(tmp_path):
    db, store = _store(tmp_path)
    rec = _rec(store, [("system", "s")])
    store.update_cache_diag(rec["id"], cache_hit=80, cache_miss=20)
    row = store.latest_cache_diag(window_id="w1")
    assert row["cache_hit"] == 80 and row["cache_miss"] == 20
    db.close()


def test_diagnostics_switch_skips_hook(tmp_path):
    from types import SimpleNamespace
    from dogbuddy.services.window_manager import WindowManager

    db, store = _store(tmp_path)
    wm = WindowManager.__new__(WindowManager)
    wm.queue_store = store
    wm.cache_diagnostics = False
    w = SimpleNamespace(id="w1")
    assert wm._shape_hook(w, "r1", "m") is None
    wm.cache_diagnostics = True
    assert wm._shape_hook(w, "r1", "m") is not None
    db.close()
