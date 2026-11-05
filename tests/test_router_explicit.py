# DogBuddyCn — Router explicit task takeover (0.23.1)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.kernel import KernelStore  # noqa: E402
from dogbuddy.kernel.domain import RunHandle  # noqa: E402
from dogbuddy.kernel.intake import ShadowIntake  # noqa: E402
from dogbuddy.kernel.router import KernelRouter  # noqa: E402


class FakeWindows:
    def __init__(self):
        self.calls = []
        self.cancelled = []
        self._runtime = {}
        self.w = SimpleNamespace(id="w1", name="coder", role="worker_programmer",
                                 status="idle", project_id="p1")

    def list(self):
        return [self.w]

    def get(self, wid):
        return self.w if wid == "w1" else None

    def dispatch_run(self, **kw):
        self.calls.append(kw)
        return RunHandle(run_id=f"r{len(self.calls)}",
                         work_item_id=kw.get("work_item_id", ""))

    def cancel_run(self, rid):
        self.cancelled.append(rid)
        return {"ok": True}


def _stack(tmp_path, mode="explicit", windows=None, conversations=None):
    db = Database(tmp_path / "r.db")
    store = KernelStore(db)
    router = KernelRouter(store, ShadowIntake(store), mode=mode,
                          windows=windows, route_conversations=conversations)
    return db, store, router


def test_explicit_task_dispatches_once_and_creates_mandate(tmp_path):
    fw = FakeWindows()
    db, store, router = _stack(tmp_path, windows=fw)
    r = router.route_web(conversation="c1", text="/task 修登录", sender="u")
    assert r["handled"] and r["action"] == "create" and r["run_id"] == "r1"
    wi = store.get_work_item(r["work_item_id"])
    assert wi["origin"] == "router" and wi["status"] == "working"
    assert int(wi["review_required"]) == 1
    assert store.get_active_mandate(wi["id"]) is not None
    assert fw.calls[0]["work_item_id"] == wi["id"]
    # 重复投递：不再次 dispatch
    r2 = router.route_web(conversation="c1", text="/task 修登录", sender="u")
    assert r2.get("duplicate") and len(fw.calls) == 1
    db.close()


def test_explicit_chat_falls_back_to_old_path(tmp_path):
    fw = FakeWindows()
    db, _store, router = _stack(tmp_path, windows=fw)
    r = router.route_web(conversation="c1", text="解释报错", sender="u")
    assert r["handled"] is False and r["action"] == "chat"
    assert fw.calls == []
    db.close()


def test_explicit_continue_reuses_work_item(tmp_path):
    fw = FakeWindows()
    db, _store, router = _stack(tmp_path, windows=fw)
    first = router.route_web(conversation="c1", text="/task 做站", sender="u")
    second = router.route_web(conversation="c1", text="/continue 补测试", sender="u")
    assert second["handled"] and second["action"] == "resume"
    assert second["work_item_id"] == first["work_item_id"]
    assert len(fw.calls) == 2 and fw.calls[1]["work_item_id"] == first["work_item_id"]
    db.close()


def test_explicit_without_worker_asks_not_fake_success(tmp_path):
    class NoWindows(FakeWindows):
        def list(self):
            return []

    fw = NoWindows()
    db, store, router = _stack(tmp_path, windows=fw)
    r = router.route_web(conversation="c1", text="/task 做站", sender="u")
    assert r["handled"] and r["action"] == "ask"
    assert store.health()["work_items"] == 0 and fw.calls == []
    db.close()


def test_explicit_allowlist_and_shadow_behavior(tmp_path):
    fw = FakeWindows()
    db, _store, router = _stack(tmp_path, windows=fw, conversations=["c2"])
    assert router.route_web(conversation="c1", text="/task 做站") is None
    r = router.route_web(conversation="c2", text="/task 做站")
    assert r["handled"] is True
    db.close()

    fw2 = FakeWindows()
    db2, _s2, shadow = _stack(tmp_path, mode="shadow", windows=fw2)
    r2 = shadow.route_web(conversation="c1", text="/task 做站")
    assert r2["handled"] is False and fw2.calls == []
    db2.close()
