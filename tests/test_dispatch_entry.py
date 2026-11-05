# DogBuddyCn — single dispatch entry (0.21.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.kernel import KernelStore  # noqa: E402
from dogbuddy.memory.service import MemoryService  # noqa: E402
from dogbuddy.services.run_registry import RunRegistry  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402


def _stack(tmp_path):
    db = Database(tmp_path / "d.db")
    mem = MemoryService(tmp_path / "mem")
    wm = WindowManager(db=db, llm_factory=lambda *a, **k: None,
                       bus=EventBus(), memory=mem,
                       checkpoints_dir=tmp_path / "ckpt",
                       queue_store=KernelStore(db))
    wm.runs = RunRegistry(db)
    w = wm.create(name="w", role="worker_programmer",
                  workspace_path=str(tmp_path / "ws"))
    return db, wm, w


def test_dispatch_run_creates_queued_run_and_task(tmp_path):
    db, wm, w = _stack(tmp_path)
    handle = wm.dispatch_manual(w.id, "do the thing")
    assert handle is not None and handle.run_id
    run = wm.runs.get(handle.run_id)
    assert run is not None and run.status == "queued" and run.prompt == "do the thing"
    assert wm.params_for(w)  # window still intact
    # 持久队列里也有，且带上 run_id
    rows = wm.queue_store.queued_tasks(w.id)
    assert len(rows) == 1 and rows[0]["meta"]["run_id"] == handle.run_id
    # 取消排队中的 run：直接终态，不死等
    out = handle.cancel()
    assert out["ok"] and wm.runs.get(handle.run_id).status == "cancelled"
    db.close()


def test_dispatch_run_requires_active_mandate_for_work_item(tmp_path):
    db, wm, w = _stack(tmp_path)
    thread = wm.queue_store.ensure_thread(source="web", external_id="c1")
    wi = wm.queue_store.create_work_item(thread_id=thread["id"], title="t")
    import pytest
    with pytest.raises(ValueError):
        wm.dispatch_run(window_id=w.id, prompt="x", work_item_id=wi["id"])
    wm.queue_store.create_mandate(work_item_id=wi["id"], scope="workspace",
                                  allowed_actions=["read", "write", "exec"])
    handle = wm.dispatch_run(window_id=w.id, prompt="x", work_item_id=wi["id"])
    assert handle.work_item_id == wi["id"] and handle.run_id
    db.close()


def test_dispatch_run_rejects_unknown_window(tmp_path):
    db, wm, _w = _stack(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        wm.dispatch_run(window_id="nope", prompt="x")
    db.close()
