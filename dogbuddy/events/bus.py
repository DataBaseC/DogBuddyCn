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
import uuid
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
#: v0.10.51：管理员**每次调用工具**的实时回执（控制台边跑边显示，可折叠）。
#: payload: {conversation, name, args, ok, output, pending, created_at}
ADMIN_TOOL = "admin.tool"


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    id: str = field(default_factory=lambda: f"ev-{uuid.uuid4().hex[:12]}")


Handler = Callable[[Event], Awaitable[None] | None]

#: 不可丢的事件：终态/汇报/提问/审批。入队前先写 outbox；消费成功再 ack。
DURABLE_TOPICS = frozenset({
    JOB_COMPLETED, JOB_FAILED, JOB_QUIET, RATE_LIMIT_PAUSED,
    WORKER_QUESTION, CONFIRM_REQUEST, "window.run_finished",
    "supervisor.digest", "worker.report", "supervisor.command",
})


class EventBus:
    def __init__(self, outbox: Any = None, durable_topics: Any = None) -> None:
        self._handlers: dict[str, list[Handler]] = {}
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        #: 可选 outbox（KernelStore）。None = 纯内存（测试/旧部署）。
        self.outbox = outbox
        self.durable_topics = frozenset(durable_topics or DURABLE_TOPICS)
        self._stopping = False
        self._queued_ids: set[str] = set()

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """event_type '*' receives everything."""
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        lst = self._handlers.get(event_type, [])
        if handler in lst:
            lst.remove(handler)

    def emit(self, event: Event) -> None:
        # 终态类事件：先落库，再入内存队列。即使紧接着 SIGKILL，重启后也能补发。
        if self.outbox is not None and event.type in self.durable_topics:
            try:
                if self.outbox.has_outbox(event.id):
                    return          # 同一事件重复 emit：不重复排队
                self.outbox.enqueue_outbox(event.type, event.payload,
                                           ref_id=str(event.payload.get("run_id") or ""),
                                           event_id=event.id)
            except Exception:  # noqa: BLE001 — 落库失败也不能阻断旧路径
                logger.exception("outbox enqueue failed: %s", event.type)
        if event.id in self._queued_ids:
            return
        self._queued_ids.add(event.id)
        self._queue.put_nowait(event)

    async def publish(self, event: Event) -> None:
        """Dispatch immediately and await handlers (tests / ordered flows)."""
        await self._dispatch(event)

    async def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self._run())
        if self.outbox is not None:
            # 重启恢复：把上次没 ack 的终态事件重新投递一次（至少一次）。
            try:
                for row in self.outbox.pending_outbox(200):
                    eid = str(row.get("id") or "")
                    if eid in self._queued_ids:
                        continue
                    payload = row.get("payload") or {}
                    ev = Event(type=str(row.get("topic") or ""), payload=payload, id=eid)
                    self._queued_ids.add(eid)
                    self._queue.put_nowait(ev)
            except Exception:  # noqa: BLE001
                logger.exception("outbox replay failed")

    async def stop(self) -> None:
        # 先停止接新事件，再给已有事件一个有限 drain 窗口。超时未完成的事件
        # 留在 outbox 的 pending 状态，下次启动补发。
        self._stopping = True
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("event bus drain timed out; pending events stay in outbox")
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            ok = False
            try:
                ok = await self._dispatch(event)
            except Exception:  # noqa: BLE001
                logger.exception("event dispatch failed: %s", event.type)
            finally:
                if self.outbox is not None and event.type in self.durable_topics:
                    try:
                        if ok:
                            self.outbox.mark_outbox_sent(event.id)
                        else:
                            attempts = self.outbox.mark_outbox_failed(event.id, 30)
                            # 同一条事件最多重试 8 次，避免毒消息无限循环。
                            if attempts >= 8:
                                logger.error("outbox event %s exceeded retries: %s",
                                             event.id, event.type)
                    except Exception:  # noqa: BLE001
                        logger.exception("outbox ack failed: %s", event.id)
                self._queued_ids.discard(event.id)
                self._queue.task_done()

    async def _dispatch(self, event: Event) -> bool:
        """Run all handlers; return False if any failed (for durable retry)."""
        ok = True
        handlers = self._handlers.get(event.type, []) + self._handlers.get("*", [])
        for h in handlers:
            try:
                out = h(event)
                if asyncio.iscoroutine(out):
                    await out
            except Exception:  # noqa: BLE001
                ok = False
                logger.exception("event handler failed for %s", event.type)
        return ok
