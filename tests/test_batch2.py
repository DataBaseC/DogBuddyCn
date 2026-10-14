# DogBuddyCn — Batch 2 tests: decisions queue, SSE, ledger, notification minimum
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from dogbuddy.db import Database
from dogbuddy.events.bus import Event, EventBus
from dogbuddy.services.decisions import (DECISION_ANSWERED, DECISION_CREATED,
                                         DECISION_TIMEOUT, DecisionService)


def _db(tmp_path):
    return Database(str(tmp_path / "t.db"))


def _flush(bus):
    """Drain the EventBus queue synchronously (emit only enqueues)."""
    async def _d():
        while not bus._queue.empty():
            await bus._dispatch(bus._queue.get_nowait())
    asyncio.get_event_loop().run_until_complete(_d())


# ---------------------------------------------------------------- decisions
def test_ask_persists_and_emits(tmp_path):
    db = _db(tmp_path)
    bus = EventBus()
    seen = []
    bus.subscribe(DECISION_CREATED, lambda e: seen.append(e))
    svc = DecisionService(db, bus=bus)
    d = svc.ask("w1", "用哪个数据库？", options=["sqlite", "pg"])
    _flush(bus)
    assert d.status == "pending"
    assert db.get_pending_decision(d.id).question == "用哪个数据库？"
    assert seen and seen[0].payload["options"] == ["sqlite", "pg"]


def test_ask_supersedes_older_open_questions(tmp_path):
    db = _db(tmp_path)
    svc = DecisionService(db)
    a = svc.ask("w1", "问题A")
    b = svc.ask("w1", "问题B")
    assert db.get_pending_decision(a.id).status == "cancelled"
    assert db.get_pending_decision(b.id).status == "pending"


def test_answer_flips_and_emits(tmp_path):
    db = _db(tmp_path)
    bus = EventBus()
    seen = []
    bus.subscribe(DECISION_ANSWERED, lambda e: seen.append(e))
    svc = DecisionService(db, bus=bus)
    d = svc.ask("w1", "问题")
    out = svc.answer(d.id, "选A")
    _flush(bus)
    assert out.status == "answered" and out.answer == "选A"
    assert seen and seen[0].payload["answer"] == "选A"
    assert svc.answer(d.id, "再答") is None  # 已关闭不再改写


def test_wait_returns_answer(tmp_path):
    db = _db(tmp_path)
    svc = DecisionService(db, poll_interval=0.01)
    d = svc.ask("w1", "问题")

    async def scenario():
        async def answer_later():
            await asyncio.sleep(0.03)
            svc.answer(d.id, "42")
        t = asyncio.get_event_loop().create_task(answer_later())
        reply = await asyncio.wait_for(svc.wait(d.id), timeout=2)
        await t
        return reply

    assert asyncio.get_event_loop().run_until_complete(scenario()) == "42"


def test_wait_times_out(tmp_path):
    db = _db(tmp_path)
    svc = DecisionService(db, default_timeout=0.05, poll_interval=0.01)
    bus = EventBus()
    seen = []
    bus.subscribe(DECISION_TIMEOUT, lambda e: seen.append(e))
    svc.bus = bus
    d = svc.ask("w1", "问题")

    async def scenario():
        return await asyncio.wait_for(svc.wait(d.id), timeout=2)

    reply = asyncio.get_event_loop().run_until_complete(scenario())
    _flush(bus)
    assert reply.startswith("[timeout]")
    assert db.get_pending_decision(d.id).status == "timeout"
    assert seen, "timeout 必须发事件（走通知最小集）"


def test_sweep_expires_overdue(tmp_path):
    from datetime import datetime, timedelta, timezone
    db = _db(tmp_path)
    svc = DecisionService(db)
    overdue = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    d = db.add_pending_decision("w1", "问题", expires_at=overdue)
    expired = svc.sweep()
    assert [e.id for e in expired] == [d.id]
    assert db.get_pending_decision(d.id).status == "timeout"
    assert svc.sweep() == []  # idempotent


def test_answered_decisions_listed(tmp_path):
    db = _db(tmp_path)
    svc = DecisionService(db)
    a = svc.ask("w1", "Q1")
    svc.ask("w2", "Q2")
    svc.answer(a.id, "done")
    pend = svc.list("pending")
    assert [d.question for d in pend] == ["Q2"]
    done = svc.list("answered")
    assert [d.question for d in done] == ["Q1"]


# ---------------------------------------------------------------- persistence
def test_decisions_survive_restart(tmp_path):
    p = str(tmp_path / "t.db")
    db = Database(p)
    svc = DecisionService(db)
    d = svc.ask("w1", "重启前的问题")
    db.close()
    db2 = Database(p)
    got = db2.get_pending_decision(d.id)
    assert got is not None and got.question == "重启前的问题"
    assert got.status == "pending"


# ---------------------------------------------------------------- ledger
def test_ledger_summary_groups(tmp_path):
    db = _db(tmp_path)
    db.add_message("w1", "assistant", "a", tokens=100)
    db.add_message("w1", "assistant", "b", tokens=50)
    db.add_message("w2", "assistant", "c", tokens=7)
    s = db.ledger_summary(days=30)
    assert s["total_tokens"] == 157
    assert s["total_messages"] == 3
    w1 = next(r for r in s["by_window"] if r["window_id"] == "w1")
    assert w1["tokens"] == 150 and w1["messages"] == 2
    assert len(s["by_day"]) == 1  # all today


# ---------------------------------------------------------------- sse broker
def test_sse_broker_fans_out(tmp_path):
    from dogbuddy.web.sse import SseBroker
    bus = EventBus()
    broker = SseBroker(bus)
    broker.start()
    q1, q2 = broker.open(), broker.open()
    bus.emit(Event(type="window.status", payload={"x": 1}))
    asyncio.get_event_loop().run_until_complete(bus.publish(
        Event(type="window.status", payload={"x": 2})))
    got = q1.get_nowait()
    assert got["type"] == "window.status" and got["payload"] == {"x": 2}
    assert q2.get_nowait()["payload"] == {"x": 2}
    broker.close(q1)
    assert broker.client_count == 1


def test_sse_slow_client_dropped_not_stalling(tmp_path):
    from dogbuddy.web.sse import SseBroker, _MAX_QUEUE
    bus = EventBus()
    broker = SseBroker(bus)
    broker.start()
    q = broker.open()
    for i in range(_MAX_QUEUE + 10):
        broker._on_event(Event(type="t", payload={"i": i}))
    assert q.qsize() == _MAX_QUEUE  # oldest dropped, newest kept
    assert q.get_nowait()["payload"]["i"] == 10
