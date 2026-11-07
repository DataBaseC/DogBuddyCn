# DogBuddyCn — RuntimeSnapshot append-only (0.29.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.kernel import KernelStore  # noqa: E402
from dogbuddy.windows.context import ContextBuilder  # noqa: E402


class Memory:
    def __init__(self, value="m1"):
        self.value = value

    def recall(self, scope):
        return self.value

    def recall_window(self, wid):
        return ""


def _msg(mid, role, content):
    return SimpleNamespace(id=mid, window_id="w1", role=role, content=content,
                           tokens=0, created_at=f"t{mid}", meta={})


class Session:
    def __init__(self, recent):
        self.window = SimpleNamespace(id="w1", name="coder",
                                      role="worker_programmer",
                                      workspace_path="/tmp", mode="BUILD")
        self.effective_workspace = "/tmp"
        self._recent = recent

    def compaction_history(self):
        return []

    def recent(self, limit):
        return self._recent[-limit:]


def _stack(tmp_path):
    db = Database(tmp_path / "snap.db")
    store = KernelStore(db)
    return db, store


def test_snapshot_appended_only_when_hash_changes(tmp_path):
    db, store = _stack(tmp_path)
    mem = Memory("m1")
    b = ContextBuilder(memory_service=mem, runtime_snapshot=True,
                       snapshot_store=store)
    s1 = Session([_msg(1, "user", "one")])
    msgs1 = b.build(s1)
    assert [m["role"] for m in msgs1] == ["system", "user", "user"]
    snap1 = store.latest_runtime_snapshot("w1")
    assert snap1["before_message_id"] == 1

    # same dynamic content: no second snapshot
    s2 = Session([_msg(1, "user", "one"), _msg(2, "assistant", "ok")])
    msgs2 = b.build(s2)
    assert len(store.list_runtime_snapshots("w1")) == 1
    assert msgs2[:3] == msgs1[:3]

    # memory changes at turn 3: old prefix must stay, new snapshot before msg3
    mem.value = "m2"
    s3 = Session([_msg(1, "user", "one"), _msg(2, "assistant", "ok"),
                  _msg(3, "user", "three")])
    msgs3 = b.build(s3)
    snaps = store.list_runtime_snapshots("w1")
    assert len(snaps) == 2
    assert snaps[0]["superseded_by"] == snaps[1]["id"]
    assert msgs3[:3] == msgs2[:3]
    assert msgs3[-2]["role"] == "user"
    assert "m2" in msgs3[-2]["content"]
    assert msgs3[-1]["content"] == "three"
    db.close()
