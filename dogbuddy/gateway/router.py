# DogBuddyCn — message router (L1 Interface)
# Pipeline concept derived from AstrBot (https://github.com/AstrBotDevs/AstrBot)
# Copyright (c) AstrBotDevs and contributors — AGPL-3.0
# Modifications (2026-10-12): reduced to user→admin routing and out-push fan-out.
"""Route inbound user messages to the admin service; fan out replies/pushes."""
from __future__ import annotations

import logging
from typing import Any

from .adapters.base import BaseAdapter, MessageEvent, PushTarget

logger = logging.getLogger("dogbuddy.gateway.router")


class Router:
    def __init__(self, adapter: BaseAdapter, admin_service: Any):
        self.adapter = adapter
        self.admin = admin_service

    async def run(self) -> None:
        """Consume adapter events forever."""
        while True:
            event = await self.adapter.recv()
            try:
                await self.route_in(event)
            except Exception:  # noqa: BLE001 — a bad message must not kill the loop
                logger.exception("route_in failed")

    async def route_in(self, event: MessageEvent) -> None:
        reply = await self.admin.handle_user_message(event.text, sender=event.user_id)
        if reply:
            await self.adapter.send(PushTarget.from_session(event.session_id), reply)

    async def route_out(self, target: PushTarget, content: str) -> None:
        await self.adapter.push(target, content)
