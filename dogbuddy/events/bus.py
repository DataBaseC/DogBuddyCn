# DogBuddyCn — in-process event bus (L2 Application)
# Event bus / pipeline skeleton derived from AstrBot (https://github.com/AstrBotDevs/AstrBot)
# Copyright (c) AstrBotDevs and contributors — AGPL-3.0
# Modifications (2026-10-12, DogBuddyCn contributors):
#   - simplified to a typed in-process pub/sub (asyncio tasks per handler);
#   - AstrBot pipeline stages dropped — DogBuddyCn routes via services instead.
"""Async pub/sub used to decouple L2 services (report decisions, status changes)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger("dogbuddy.events")

# well-known event types
JOB_COMPLETED = "job.completed"
JOB_FAILED = "job.failed"
JOB_QUIET = "job.quiet"          # T8.2 条件任务：跑完了但"无事可报"，静默记录
RATE_LIMIT_PAUSED = "rate_limit.paused"  # T8.8 封禁级限速：任务已暂停（非失败）
WORKER_QUESTION = "worker.question"
CONFIRM_REQUEST = "confirm.request"
WINDOW_STATUS = "window.status"
RATE_LIMIT_STATUS = "rate_limit.status"
USER_MESSAGE = "user.message"
#: 管理员对话的打字机流（管理员没有工位，不能复用 window.stream）。
#: payload: {conversation, delta} —— 前端按 conversation 过滤，多对话不串屏。
ADMIN_STREAM = "admin.stream"
#: 管理员/系统**写进用户对话流**的汇报（控制台即时追加一条消息）
#: payload: {conversation, text, message_id, created_at}
ADMIN_NOTICE = "admin.notice"


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


Handler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """event_type '*' receives everything."""
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        lst = self._handlers.get(event_type, [])
        if handler in lst:
            lst.remove(handler)

    def emit(self, event: Event) -> None:
        self._queue.put_nowait(event)

    async def publish(self, event: Event) -> None:
        """Dispatch immediately and await handlers (tests / ordered flows)."""
        await self._dispatch(event)

    async def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._dispatch(event)
            except Exception:  # noqa: BLE001
                logger.exception("event dispatch failed: %s", event.type)

    async def _dispatch(self, event: Event) -> None:
        handlers = self._handlers.get(event.type, []) + self._handlers.get("*", [])
        for h in handlers:
            try:
                out = h(event)
                if asyncio.iscoroutine(out):
                    await out
            except Exception:  # noqa: BLE001
                logger.exception("event handler failed for %s", event.type)
