# DogBuddyCn — gateway routing tests (whitelist + user→admin→reply)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.gateway.adapters.base import BaseAdapter, MessageEvent, PushTarget
from dogbuddy.gateway.router import Router


class FakeAdapter(BaseAdapter):
    def __init__(self):
        super().__init__()
        self._q: asyncio.Queue = asyncio.Queue()
        self.sent: list[tuple] = []

    async def recv(self) -> MessageEvent:
        return await self._q.get()

    async def send(self, target: PushTarget, content: str) -> None:
        self.sent.append(("send", target, content))

    async def push(self, target: PushTarget, content: str) -> None:
        self.sent.append(("push", target, content))


class FakeAdmin:
    def __init__(self):
        self.seen = []

    async def handle_user_message(self, text, sender="user"):
        self.seen.append((sender, text))
        return f"echo: {text}"


def test_route_in_replies_to_session():
    async def main():
        adapter = FakeAdapter()
        admin = FakeAdmin()
        router = Router(adapter, admin)
        task = asyncio.get_event_loop().create_task(router.run())
        adapter._q.put_nowait(MessageEvent(
            platform="onebot_v11", session_id="private:123", user_id="123", text="你好"))
        await asyncio.sleep(0.05)
        task.cancel()
        assert admin.seen == [("123", "你好")]
        kind, target, content = adapter.sent[0]
        assert kind == "send"
        assert target.type == "private" and target.id == "123"
        assert content == "echo: 你好"

    asyncio.get_event_loop().run_until_complete(main())


class FakeKernelRouter:
    def __init__(self, reply="已创建任务 wi-1"):
        self.calls = []
        self.reply = reply

    async def route_web_async(self, **kw):
        self.calls.append(kw)
        return {"handled": True, "reply": self.reply,
                "work_item_id": "wi-1", "run_id": "r-1", "action": "create"}


def test_gateway_uses_kernel_router_before_admin():
    async def main():
        adapter = FakeAdapter()
        admin = FakeAdmin()
        kernel = FakeKernelRouter()
        router = Router(adapter, admin, kernel_router=kernel)
        event = MessageEvent(platform="onebot_v11", session_id="private:123",
                             user_id="123", text="/task 修登录")
        await router.route_in(event)
        assert kernel.calls and kernel.calls[0]["conversation"] == "qq:private:123"
        assert kernel.calls[0]["source"] == "onebot_v11"
        assert kernel.calls[0]["session_kind"] == "private"
        assert admin.seen == []
        assert adapter.sent[0][2] == "已创建任务 wi-1"

    asyncio.get_event_loop().run_until_complete(main())


def test_gateway_falls_back_to_admin_when_kernel_not_handled():
    async def main():
        adapter = FakeAdapter()
        admin = FakeAdmin()
        router = Router(adapter, admin, kernel_router=None)
        await router.route_in(MessageEvent(
            platform="onebot_v11", session_id="group:9", user_id="123",
            group_id="9", text="普通聊天"))
        assert admin.seen == [("123", "普通聊天")]
        assert adapter.sent[0][2] == "echo: 普通聊天"

    asyncio.get_event_loop().run_until_complete(main())


def test_intake_generic_qq_source(tmp_path):
    from dogbuddy.db import Database
    from dogbuddy.kernel import KernelStore
    from dogbuddy.kernel.intake import ShadowIntake

    db = Database(tmp_path / "qq.db")
    store = KernelStore(db)
    intake = ShadowIntake(store)
    tr = intake.accept_message(source="onebot_v11", external_id="private:123",
                               kind="private", text="你好", sender="123")
    assert tr and tr.get("created") is True
    row = db._conn.execute("SELECT source FROM threads WHERE id=?",
                           (str(tr.get("thread_id")),)).fetchone()
    assert row and row[0] == "onebot_v11"
    db.close()


def test_push_target_from_session():
    t = PushTarget.from_session("group:456")
    assert t.type == "group" and t.id == "456"
    t2 = PushTarget.from_session("private:789")
    assert t2.type == "private" and t2.id == "789"


def test_onebot_text_extraction():
    from dogbuddy.gateway.adapters.onebot_v11 import OneBotV11Adapter
    text = OneBotV11Adapter._extract_text(
        [{"type": "text", "data": {"text": "hello"}}, {"type": "image", "data": {}}], "")
    assert text == "hello[image]"
    assert OneBotV11Adapter._extract_text("plain", "") == "plain"


def test_onebot_whitelist_filter():
    async def main():
        from dogbuddy.gateway.adapters.onebot_v11 import OneBotV11Adapter
        ad = OneBotV11Adapter(whitelist=["111"])
        await ad._dispatch({"post_type": "message", "user_id": 222,
                            "message": [{"type": "text", "data": {"text": "hi"}}]}, ws=None)
        assert ad._events.qsize() == 0  # dropped
        await ad._dispatch({"post_type": "message", "user_id": 111,
                            "message": [{"type": "text", "data": {"text": "hi"}}]}, ws=None)
        assert ad._events.qsize() == 1

    asyncio.get_event_loop().run_until_complete(main())
