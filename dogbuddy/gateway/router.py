# DogBuddyCn — message router (L1 Interface)
# Pipeline concept derived from AstrBot (https://github.com/AstrBotDevs/AstrBot)
# Copyright (c) AstrBotDevs and contributors — AGPL-3.0
# Modifications (2026-10-12): reduced to user→admin routing and out-push fan-out.
"""Route inbound user messages to the admin service; fan out replies/pushes."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .adapters.base import BaseAdapter, MessageEvent, PushTarget

logger = logging.getLogger("dogbuddy.gateway.router")


class Router:
    def __init__(self, adapter: BaseAdapter, admin_service: Any,
                 kernel_router: Any = None):
        self.adapter = adapter
        self.admin = admin_service
        #: v0.27.0：QQ 也只走现有 KernelRouter；None 时完全回退旧路径。
        self.kernel_router = kernel_router
        self._session_queues: dict[str, asyncio.Queue] = {}
        self._session_tasks: dict[str, asyncio.Task] = {}

    def _sid(self, event: MessageEvent) -> str:
        return f"qq:{event.session_id}"

    async def run(self) -> None:
        """Consume adapter events forever; one serial queue per QQ session."""
        try:
            while True:
                event = await self.adapter.recv()
                sid = self._sid(event)
                q = self._session_queues.get(sid)
                if q is None:
                    q = asyncio.Queue()
                    self._session_queues[sid] = q
                q.put_nowait(event)
                task = self._session_tasks.get(sid)
                if task is None or task.done():
                    self._session_tasks[sid] = asyncio.get_event_loop().create_task(
                        self._drain(sid, q))
        finally:
            for task in self._session_tasks.values():
                task.cancel()

    async def _drain(self, sid: str, q: asyncio.Queue) -> None:
        while True:
            event = await q.get()
            try:
                await self.route_in(event)
            except Exception:  # noqa: BLE001 — one bad event must not kill the session
                logger.exception("route_in failed session=%s", sid)

    async def route_in(self, event: MessageEvent) -> None:
        # QQ → KernelRouter（/task、/continue、auto 都复用 Web 同一套语义）。
        # 非 handled/未开 Router 时仍回旧管理员路径，行为可回退。
        if self.kernel_router is not None:
            try:
                decision = await self.kernel_router.route_web_async(
                    conversation=self._sid(event), text=event.text,
                    sender=event.user_id, source=event.platform or "onebot_v11",
                    session_kind="group" if event.group_id else "private",
                    title=f"QQ {'群' if event.group_id else '私聊'} {event.session_id}")
                if decision and decision.get("handled"):
                    reply = str(decision.get("reply") or "")
                    if reply:
                        await self.adapter.send(
                            PushTarget.from_session(event.session_id), reply)
                    return
            except Exception:  # noqa: BLE001 — Router 失败必须回退，不能丢消息
                logger.warning("gateway kernel route failed; fallback admin",
                               exc_info=True)
        reply = await self.admin.handle_user_message(event.text, sender=event.user_id)
        if reply:
            await self.adapter.send(PushTarget.from_session(event.session_id), reply)

    async def route_out(self, target: PushTarget, content: str) -> None:
        await self.adapter.push(target, content)
