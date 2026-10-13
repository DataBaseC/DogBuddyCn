# DogBuddyCn — task queue tests (serial FIFO + urgent insert + pause)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.windows.queue import TaskItem, TaskQueue


def test_serial_fifo_with_urgent_insert():
    async def main():
        order: list[str] = []

        async def runner(task: TaskItem) -> None:
            order.append(task.prompt)
            await asyncio.sleep(0.01)

        q = TaskQueue("w", runner=runner)
        worker = asyncio.get_event_loop().create_task(q.run_loop())
        q.submit(TaskItem(prompt="a"))
        q.submit(TaskItem(prompt="b"))
        q.submit(TaskItem(prompt="urgent", urgent=True))
        await asyncio.sleep(0.15)
        q.stop()
        worker.cancel()
        assert order[0] == "urgent"   # urgent jumps to the head
        assert order[1:] == ["a", "b"]  # rest keeps FIFO

    asyncio.get_event_loop().run_until_complete(main())


def test_crashed_task_does_not_kill_queue():
    async def main():
        done = []

        async def runner(task: TaskItem) -> None:
            if task.prompt == "boom":
                raise RuntimeError("crash")
            done.append(task.prompt)

        q = TaskQueue("w", runner=runner)
        worker = asyncio.get_event_loop().create_task(q.run_loop())
        q.submit(TaskItem(prompt="boom"))
        q.submit(TaskItem(prompt="after"))
        await asyncio.sleep(0.1)
        q.stop()
        worker.cancel()
        assert done == ["after"]

    asyncio.get_event_loop().run_until_complete(main())


def test_pause_blocks_execution():
    async def main():
        done = []

        async def runner(task: TaskItem) -> None:
            done.append(task.prompt)

        q = TaskQueue("w", runner=runner)
        worker = asyncio.get_event_loop().create_task(q.run_loop())
        q.pause()
        q.submit(TaskItem(prompt="x"))
        await asyncio.sleep(0.05)
        assert done == []
        q.resume()
        await asyncio.sleep(0.05)
        q.stop()
        worker.cancel()
        assert done == ["x"]

    asyncio.get_event_loop().run_until_complete(main())
