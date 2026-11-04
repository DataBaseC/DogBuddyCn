# DogBuddyCn — 0.19.0 任务内核/安全回归
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""PR-1/2/3 的钉子：安全默认、持久队列、Outbox、Shadow intake。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, GatewayConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import JOB_COMPLETED, Event, EventBus  # noqa: E402
from dogbuddy.kernel import KernelStore  # noqa: E402
from dogbuddy.kernel.intake import ShadowIntake  # noqa: E402
from dogbuddy.services.admin_service import _AdminGate  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402
from dogbuddy.windows.queue import TaskItem, TaskQueue  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_gateway_defaults_are_fail_closed():
    cfg = AppConfig()
    assert cfg.gateway.host == "127.0.0.1"
    assert cfg.gateway.whitelist == []
    assert cfg.gateway.allow_all is False


def test_trigger_dedupe_and_work_item(tmp_path):
    db = Database(tmp_path / "k.db")
    store = KernelStore(db)
    th = store.ensure_thread(source="web", external_id="owner")
    tr1, created1 = store.record_trigger(
        source="web", external_id="owner", thread_id=th["id"], kind="message",
        payload={"text": "hi"}, dedupe_key="web:owner:hi")
    tr2, created2 = store.record_trigger(
        source="web", external_id="owner", thread_id=th["id"], kind="message",
        payload={"text": "hi"}, dedupe_key="web:owner:hi")
    assert created1 is True and created2 is False
    assert tr1["id"] == tr2["id"]
    wi = store.create_work_item(thread_id=th["id"], title="do it")
    assert wi["status"] == "open" and store.get_work_item(wi["id"])["title"] == "do it"
    store.create_mandate(work_item_id=wi["id"], scope="web")
    store.subscribe(work_item_id=wi["id"], thread_id=th["id"])
    assert store.health()["triggers"] == 1
    db.close()


def test_task_queue_persists_and_restores(tmp_path):
    db = Database(tmp_path / "q.db")
    store = KernelStore(db)
    q = TaskQueue("w1", store=store, maxsize=1)
    assert q.submit(TaskItem(prompt="first")) is True
    assert q.submit(TaskItem(prompt="second")) is False      # 有界队列拒绝
    rows = store.queued_tasks("w1")
    assert len(rows) == 1 and rows[0]["prompt"] == "first"
    q2 = TaskQueue("w1", store=store)
    assert q2.restore(rows) == 1
    assert q2.pending()[0].prompt == "first"
    db.close()


def test_outbox_dedupes_and_replays(tmp_path):
    db = Database(tmp_path / "o.db")
    store = KernelStore(db)
    bus = EventBus(outbox=store)
    seen = []

    async def handler(ev):
        seen.append(ev.payload)

    bus.subscribe(JOB_COMPLETED, handler)

    async def main():
        bus.emit(Event(type=JOB_COMPLETED, payload={"job_id": "j1"}, id="ev-1"))
        assert store.count_pending_outbox() == 1
        # 第二次相同事件 id 不重复落库（至少一次投递要靠后续幂等键，不是重复 insert）
        bus.emit(Event(type=JOB_COMPLETED, payload={"job_id": "j1"}, id="ev-1"))
        await bus.start()
        await asyncio.sleep(0.1)
        assert seen == [{"job_id": "j1"}]
        assert store.count_pending_outbox() == 0
        await bus.stop()

    _run(main())
    db.close()


def test_shadow_intake_dedupes_same_second(tmp_path):
    db = Database(tmp_path / "s.db")
    store = KernelStore(db)
    intake = ShadowIntake(store)
    a = intake.accept_web(conversation="c1", text="同一句", sender="u1")
    b = intake.accept_web(conversation="c1", text="同一句", sender="u1")
    assert a["created"] is True and b["created"] is False
    assert a.get("work_item_id"), "shadow 也必须落 WorkItem/Mandate"
    assert store.health()["triggers"] == 1 and store.health()["work_items"] == 1
    db.close()


def test_admin_gate_requires_confirmation_for_host_commands():
    gate = _AdminGate()
    ctx = ToolContext(window_id="admin:default", workspace="/tmp",
                      mode="AUTO", actor="admin")
    d1 = gate.check("server_exec", "shell", {"command": "echo hi"}, ctx)
    assert not d1.allowed and d1.needs_confirm and d1.irreversible
    ctx.extra["confirmed"] = True
    d2 = gate.check("server_exec", "shell", {"command": "echo hi"}, ctx)
    assert d2.allowed
    ctx.extra.pop("confirmed", None)
    d3 = gate.check("shell", "shell", {"command": "rm -rf /"}, ctx)
    assert not d3.allowed and d3.needs_confirm and d3.irreversible


def test_sse_uses_short_lived_ticket():
    root = Path(__file__).resolve().parents[1] / "dogbuddy/web"
    events = (root / "routes/events.py").read_text(encoding="utf-8")
    api = (root / "static/js/api.js").read_text(encoding="utf-8")
    assert '@r.post("/events/ticket"' in events
    assert "_TICKET_TTL" in events and "secrets.token_urlsafe" in events
    assert "/api/events/ticket" in api and "ticket:" in api
    assert "token: getToken()" not in api


def test_shell_does_not_leak_dogbuddy_env(tmp_path, monkeypatch):
    import os
    from dogbuddy.tools.registry import ToolContext
    from dogbuddy.tools.shell_ops import ShellTool

    monkeypatch.setenv("DOGBUDDY_PANEL_TOKEN", "super-secret")
    ctx = ToolContext(window_id="w1", workspace=str(tmp_path), mode="BUILD")
    # 不用 ${VAR}（边界守卫会判成 E_BOUNDARY_UNEVALUABLE）；直接列环境。
    cmd = "set" if os.name == "nt" else "env"
    res = _run(ShellTool().execute({"command": cmd}, ctx))
    assert res.ok, res.output
    assert "super-secret" not in res.output
    assert "DOGBUDDY_PANEL_TOKEN" not in res.output


def test_web_refuses_public_bind_without_auth():
    import pytest
    from dogbuddy.main import validate_web_bind
    cfg = AppConfig()
    cfg.web.host = "0.0.0.0"
    cfg.web.auth_token = ""
    cfg.web.password = ""
    with pytest.raises(RuntimeError):
        validate_web_bind(cfg.web)
    cfg.web.auth_token = "secret"
    validate_web_bind(cfg.web)          # 有鉴权才允许公网监听
