# DogBuddyCn — per-window serial task queue (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""TaskQueue: one window runs exactly one task at a time (FIFO).

run_now / user interrupts jump to the head of the queue (urgent=True).
The actual execution is an injected async runner — the queue is pure mechanism.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("dogbuddy.windows.queue")


@dataclass
class TaskItem:
    prompt: str
    kind: str = "prompt"  # prompt | script | <registered runner kind>
    job_id: str | None = None
    urgent: bool = False
    llm_profile: str = ""  # profile override for this run; "" = inherit from the window
    meta: dict[str, Any] = field(default_factory=dict)


Runner = Callable[[TaskItem], Awaitable[Any]]


class TaskQueue:
    def __init__(self, window_id: str, runner: Runner | None = None, on_status: Callable[[str], Any] | None = None):
        self.window_id = window_id
        self.runner = runner
        self.on_status = on_status
        self._queue: deque[TaskItem] = deque()
        self._event = asyncio.Event()
        self._current: TaskItem | None = None
        self._paused = False
        self._stopped = False

    # ------------------------------------------------------------- enqueue
    def submit(self, task: TaskItem) -> None:
        if task.urgent:
            self._queue.appendleft(task)
        else:
            self._queue.append(task)
        self._event.set()

    def pending(self) -> list[TaskItem]:
        return list(self._queue)

    @property
    def current(self) -> TaskItem | None:
        return self._current

    @property
    def is_busy(self) -> bool:
        return self._current is not None

    # ------------------------------------------------------------ lifecycle
    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        self._event.set()

    def stop(self) -> None:
        self._stopped = True
        self._event.set()

    def kill_background(self) -> int:
        """Reap background processes registered by the shell tool (window pause/terminate)."""
        killed = 0
        procs = getattr(self, "_bg_processes", None)
        if procs:
            for p in list(procs):
                try:
                    p.kill()
                    killed += 1
                except Exception:  # noqa: BLE001
                    pass
            procs.clear()
        return killed

    # ------------------------------------------------------------- worker
    async def run_loop(self) -> None:
        """The single worker task of this window."""
        while not self._stopped:
            if self._paused or not self._queue:
                self._event.clear()
                try:
                    await asyncio.wait_for(self._event.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                continue
            task = self._queue.popleft()
            self._current = task
            self._status("running")
            try:
                if self.runner is not None:
                    await self.runner(task)
            except Exception:  # noqa: BLE001 — a crashed task must not kill the queue
                logger.exception("window %s task crashed", self.window_id)
            finally:
                self._current = None
                self._status("idle")

    def _status(self, s: str) -> None:
        if self.on_status:
            try:
                self.on_status(s)
            except Exception:  # noqa: BLE001
                pass
