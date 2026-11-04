# DogBuddyCn — per-window serial task queue (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""TaskQueue: one window runs exactly one task at a time (FIFO).

run_now / user interrupts jump to the head of the queue (urgent=True).
The actual execution is an injected async runner — the queue is pure mechanism.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
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
    #: v0.19.0：持久队列身份/去重/优先级（旧调用方不传也能工作）
    id: str = ""
    window_id: str = ""
    priority: int = 0
    dedupe_key: str = ""
    work_item_id: str = ""


Runner = Callable[[TaskItem], Awaitable[Any]]


class TaskQueue:
    def __init__(self, window_id: str, runner: Runner | None = None,
                 on_status: Callable[[str], Any] | None = None,
                 store: Any = None, maxsize: int = 1000):
        self.window_id = window_id
        self.runner = runner
        self.on_status = on_status
        #: v0.19.0：KernelStore；None = 纯内存（测试/旧部署）
        self.store = store
        self.maxsize = max(1, int(maxsize))
        self._queue: deque[TaskItem] = deque()
        self._event = asyncio.Event()
        self._current: TaskItem | None = None
        self._paused = False
        self._stopped = False
        #: 正在执行的那一项的任务句柄（v0.10.37：让"取消这一次运行"能真的打断它）
        self._current_handle: asyncio.Task | None = None
        # 后台 shell 进程表：`shell(background=true)` 登记，窗口暂停/终止时统一回收。
        # 审查报告 P1-15：初版 `kill_background()` 读的就是这个字段，但**全仓库
        # 没有任何地方给它赋值**（工具写的是 `ctx.extra["bg_processes"]`），
        # 于是它恒返回 0 —— 生产无 systemd/PRoot，后台 shell 永不回收。
        # 现在这个列表是唯一的真相来源，由 `WindowRuntime.bg_processes` 引用同一对象。
        self._bg_processes: list = []

    @property
    def bg_processes(self) -> list:
        return self._bg_processes

    # ------------------------------------------------------------- enqueue
    def submit(self, task: TaskItem) -> bool:
        """Enqueue one task. Returns False when the bounded queue is full.

        v0.19.0：先写 task_queue 再入内存；旧调用方忽略返回值也能继续工作。
        """
        if len(self._queue) >= self.maxsize:
            logger.warning("window %s queue full (%d); rejecting task",
                           self.window_id, self.maxsize)
            return False
        task.window_id = task.window_id or self.window_id
        task.id = task.id or f"tq-{uuid.uuid4().hex[:12]}"
        # v0.22.0：ephemeral 任务带进程内闭包（群组步骤），不能跨重启恢复，
        # 因此不写 task_queue；正常任务仍然先落库。
        if self.store is not None and not (task.meta or {}).get("ephemeral"):
            try:
                self.store.enqueue_task(task)
            except Exception:  # noqa: BLE001 — 持久化失败不能阻断执行
                logger.exception("persist task failed")
        if task.urgent:
            self._queue.appendleft(task)
        else:
            self._queue.append(task)
        self._event.set()
        return True

    def restore(self, rows: list[dict[str, Any]]) -> int:
        """Rebuild queued items after a restart (do NOT re-persist)."""
        n = 0
        for row in rows:
            try:
                task = TaskItem(
                    prompt=str(row.get("prompt") or ""),
                    kind=str(row.get("kind") or "prompt"),
                    job_id=(row.get("job_id") or None),
                    urgent=bool(row.get("urgent")),
                    llm_profile=str(row.get("llm_profile") or ""),
                    meta=row.get("meta") or {},
                    id=str(row.get("id") or ""),
                    window_id=str(row.get("window_id") or self.window_id),
                    priority=int(row.get("priority") or 0),
                    dedupe_key=str(row.get("dedupe_key") or ""),
                    work_item_id=str(row.get("work_item_id") or ""),
                )
            except Exception:  # noqa: BLE001
                continue
            self._queue.append(task)
            n += 1
        if n:
            self._event.set()
        return n

    def pending(self) -> list[TaskItem]:
        return list(self._queue)

    def rebind_loop(self) -> None:
        """换 event loop 后重建 Event（旧 Event 已绑到关闭的 loop）。"""
        self._event = asyncio.Event()
        if self._queue:
            self._event.set()

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

    def drop(self, predicate: Callable[[TaskItem], bool]) -> int:
        """把还没开跑的项从队列里拿掉（取消排队中的运行）。返回拿掉几项。"""
        keep = deque(t for t in self._queue if not predicate(t))
        n = len(self._queue) - len(keep)
        dropped = [t for t in self._queue if predicate(t)]
        self._queue = keep
        if self.store is not None:
            for t in dropped:
                try:
                    if t.id:
                        self.store.finish_task(t.id)
                except Exception:  # noqa: BLE001
                    logger.debug("drop persisted task failed", exc_info=True)
        return n

    def cancel_current(self) -> bool:
        """取消**正在执行**的那一项（不关停队列本身）。返回是否真的发了取消。"""
        h = self._current_handle
        if h is None or h.done():
            return False
        h.cancel()
        return True

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
            if self.store is not None and task.id:
                try:
                    self.store.mark_task_running(task.id)
                except Exception:  # noqa: BLE001
                    logger.debug("mark task running failed", exc_info=True)
            self._status("running")
            # v0.10.37：每一项跑在**自己的任务**里 —— "取消这一次运行"只打断这一项，
            # 队列本身继续活着（以前 runner 直接 inline 在循环任务里，取消它 = 杀死队列）。
            self._current_handle = (asyncio.ensure_future(self.runner(task))
                                    if self.runner is not None else None)
            try:
                if self._current_handle is not None:
                    await self._current_handle
            except asyncio.CancelledError:
                if self._stopped:
                    raise                       # 队列被关停（关窗口/停服务）
                logger.info("window %s: 当前任务已取消，队列继续", self.window_id)
            except Exception:  # noqa: BLE001 — a crashed task must not kill the queue
                logger.exception("window %s task crashed", self.window_id)
            finally:
                if self.store is not None and task.id:
                    try:
                        self.store.finish_task(task.id)
                    except Exception:  # noqa: BLE001
                        logger.debug("finish persisted task failed", exc_info=True)
                self._current_handle = None
                self._current = None
                self._status("idle")

    def _status(self, s: str) -> None:
        if self.on_status:
            try:
                self.on_status(s)
            except Exception:  # noqa: BLE001
                pass
