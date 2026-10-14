# DogBuddyCn — SSE fan-out broker (L1 Interface, ROADMAP T2.1)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Turns the in-process EventBus into a multi-client Server-Sent Events stream.

Why SSE and not WebSocket (PROPOSALS #3 decision): the console is a dashboard
that receives facts — one direction, auto-reconnect, no extra protocol. The
broker subscribes to the bus once and fans each event out to every connected
browser queue; a slow client gets its own queue and can never stall the bus or
the other clients (bounded queue, oldest dropped = "live view", not a log).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..events.bus import Event

logger = logging.getLogger("dogbuddy.web.sse")

_MAX_QUEUE = 500  # per client; beyond this the oldest events are dropped


class SseBroker:
    def __init__(self, bus: Any):
        self.bus = bus
        self._clients: set[asyncio.Queue] = set()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.bus.subscribe("*", self._on_event)

    def _on_event(self, event: Event) -> None:
        frame = {"type": event.type, "payload": event.payload,
                 "created_at": event.created_at}
        dead: list[asyncio.Queue] = []
        for q in list(self._clients):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:  # slow consumer: drop oldest, keep live
                try:
                    q.get_nowait()
                    q.put_nowait(frame)
                except Exception:  # noqa: BLE001
                    dead.append(q)
        for q in dead:
            self._clients.discard(q)

    # ---------------------------------------------------------- client side
    def open(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._clients.add(q)
        return q

    def close(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    @property
    def client_count(self) -> int:
        return len(self._clients)
