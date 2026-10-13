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
