# DogBuddyCn — Resource cards + Watchdog (0.20.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.kernel import KernelStore  # noqa: E402
from dogbuddy.services.run_registry import RunRegistry  # noqa: E402
from dogbuddy.services.watchdog import Watchdog  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402
from dogbuddy.tools.resource_ops import ReadResourceTool  # noqa: E402
from dogbuddy.windows.compactor import Compactor  # noqa: E402
from dogbuddy.windows.context import ContextBuilder  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_resource_is_window_scoped_and_paged(tmp_path):
    db = Database(tmp_path / "r.db")
    store = KernelStore(db)
    r = store.create_resource(window_id="w1", kind="message:tool",
                              title="old pytest failure", summary="first failure",
                              body="x" * 9000)
    tool = ReadResourceTool(store)
    ok = _run(tool.execute({"resource_id": r["id"], "limit": 100},
                           ToolContext(window_id="w1", workspace=str(tmp_path))))
    assert ok.ok and len(ok.output) >= 100 and ok.data["truncated"] is True
    denied = _run(tool.execute({"resource_id": r["id"]},
                               ToolContext(window_id="w2", workspace=str(tmp_path))))
    assert not denied.ok and "无权" in denied.output
    assert store.list_resources("w1")[0]["id"] == r["id"]
    assert store.list_resources("w2") == []
    db.close()


def test_compaction_archives_before_dropping(tmp_path):
    db = Database(tmp_path / "c.db")
    store = KernelStore(db)
    dropped = []

    class _Repo:
        def add_compaction(self, wid, summary, replaced_count=0):
            return SimpleNamespace(id="cmp1", window_id=wid, summary=summary,
                                   replaced_count=replaced_count, created_at="now")

        def audit(self, *a, **k):
            pass

    class _Session:
        window = SimpleNamespace(id="w1", name="w")
        work_item_id = ""

        def __init__(self):
            self.msgs = [SimpleNamespace(id=f"m{i}", role="tool" if i else "user",
                                         content=f"very important old output {i}")
                         for i in range(14)]

        def recent(self, n):
            return self.msgs[-n:]

        def drop_messages(self, last_id):
            dropped.append(last_id)
            self.msgs = self.msgs[-12:]

    sess = _Session()
    comp = Compactor(llm=None, compaction_repo=_Repo(), resources=store)
    comp._summarize = _async_return({"narrative": "done", "file_changes": [],
                                     "todos": [], "decisions": [], "lessons": []})
    rec = _run(comp.compact_now(sess))
    assert rec.id == "cmp1" and dropped
    cards = store.list_resources("w1", limit=20)
    assert len(cards) == 2 and cards[0]["kind"].startswith("message:")
    full = store.get_resource(cards[0]["id"])
    assert "very important old output" in full["body"]


def _async_return(value):
    async def _inner(*a, **k):
        return value
    return _inner


def test_context_injects_resource_cards(tmp_path):
    db = Database(tmp_path / "ctx.db")
    store = KernelStore(db)
    store.create_resource(window_id="w1", kind="message:tool", title="old failure",
                          summary="pytest failed at test_x", body="full traceback")
    session = SimpleNamespace(
        window=SimpleNamespace(id="w1", name="w", role="worker_programmer",
                               workspace_path="/tmp", mode="BUILD"),
        effective_workspace="/tmp", compaction_history=lambda: [],
    )
    block = ContextBuilder(resources=store).system_block(session)
    assert "Resource cards" in block and "old failure" in block
    assert "read_resource" in block and "full traceback" not in block
    db.close()


def test_run_progress_and_stalled(tmp_path):
    db = Database(tmp_path / "runs.db")
    runs = RunRegistry(db)
    r = runs.start(kind="manual", window_id="w1", status="running")
    runs.progress(r.id, iteration=3, last_tool="read_file", ok=True)
    row = runs._load(r.id)
    assert row.last_tool == "read_file" and row.progress_at
    # 手动把进展时间推到过去，模拟“活着但 10 分钟没动”
    db._conn.execute("UPDATE runs SET progress_at='2000-01-01T00:00:00+00:00' "
                     "WHERE id=?", (r.id,))
    db._conn.commit()
    stalled = runs.stalled(300)
    assert [x["id"] for x in stalled] == [r.id]
    db.close()


def test_watchdog_alerts_once_and_reports_health():
    class _Bus:
        def __init__(self):
            self.events = []

        def emit(self, ev):
            self.events.append(ev)

    class _Runs:
        rows = [{"id": "r1", "window_id": "w1", "status": "running",
                 "last_tool": "shell", "progress_at": "2000-01-01"}]

        def stalled(self, seconds):
            return list(self.rows)

    bus = _Bus()
    wd = Watchdog(_Runs(), bus=bus)
    assert len(wd.tick()) == 1
    assert len(wd.tick()) == 0, "同一停滞运行不重复告警"
    assert bus.events[0].type == "run.stalled"
    assert wd.health()["stalled"] == 1 and wd.health()["alerted"] == 1
