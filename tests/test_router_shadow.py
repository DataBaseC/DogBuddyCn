# DogBuddyCn — Router Fast Path shadow (0.23.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import DURABLE_TOPICS  # noqa: E402
from dogbuddy.kernel import KernelStore  # noqa: E402
from dogbuddy.kernel.intake import ShadowIntake  # noqa: E402
from dogbuddy.kernel.router import KernelRouter  # noqa: E402


def _stack(tmp_path):
    db = Database(tmp_path / "r.db")
    store = KernelStore(db)
    router = KernelRouter(store, ShadowIntake(store), mode="shadow")
    return db, store, router


def _route(router, text, conv="c1"):
    return router.route_web(conversation=conv, text=text, sender="owner")


def test_route_off_does_nothing(tmp_path):
    db, store, _ = _stack(tmp_path)
    router = KernelRouter(store, ShadowIntake(store), mode="off")
    assert _route(router, "/task x") is None
    assert store.count_triggers() == 0
    assert store.count_router_decisions() == 0
    db.close()


def test_chat_does_not_create_work_item(tmp_path):
    db, store, router = _stack(tmp_path)
    rec = _route(router, "解释一下这个报错")
    assert rec["action"] == "chat"
    assert store.count_router_decisions() == 1
    assert store.health()["work_items"] == 0
    db.close()


def test_task_creates_one_router_work_item_and_dedupes(tmp_path):
    db, store, router = _stack(tmp_path)
    a = _route(router, "/task 修复登录页超时")
    b = _route(router, "/task 修复登录页超时")
    assert a["id"] == b["id"], "同一 trigger 只能有一条 decision"
    items = store.list_work_items(origin="router")
    assert len(items) == 1 and items[0]["origin"] == "router"
    assert items[0]["status"] == "open"
    assert store.count_router_decisions() == 1
    db.close()


def test_continue_resumes_only_non_shadow_item(tmp_path):
    db, store, router = _stack(tmp_path)
    thread = store.ensure_thread(source="web", external_id="c1")
    shadow = store.create_work_item(thread_id=thread["id"], title="闲聊",
                                    origin="shadow")
    rec = _route(router, "/continue")
    assert rec["action"] == "ask" and shadow["origin"] == "shadow"

    rec2 = _route(router, "/task 真任务", conv="c2")
    rec3 = _route(router, "/continue", conv="c2")
    assert rec2["action"] == "create"
    assert rec3["action"] == "resume" and rec3["work_item_id"] == rec2["work_item_id"]
    db.close()


def test_control_commands_and_empty(tmp_path):
    db, _store, router = _stack(tmp_path)
    assert _route(router, "/cancel")["action"] == "cancel"
    assert _route(router, "/status")["action"] == "status"
    assert _route(router, "   ") is None       # intake 直接忽略空消息
    db.close()


def test_run_finished_is_durable():
    assert "run.finished" in DURABLE_TOPICS
