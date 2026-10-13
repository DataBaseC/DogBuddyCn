# DogBuddyCn — M1 end-to-end smoke: build window → dispatch → worker tools → compact
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Runs WITHOUT IM/LLM network: scripted FakeLLM drives the full loop."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database
from dogbuddy.events.bus import EventBus
from dogbuddy.llm.provider import LLMResponse
from dogbuddy.memory.service import MemoryService
from dogbuddy.services.session_manager import SessionManager
from dogbuddy.services.window_manager import WindowManager

from helpers import FakeLLM, tc


def test_m1_full_loop(tmp_path):
    async def main():
        db = Database(tmp_path / "data" / "t.db")
        mem = MemoryService(tmp_path / "data" / "memory")
        bus = EventBus()

        script = [
            # 1) create file via tool
            LLMResponse(content=None, tool_calls=[tc("write_file", path="hello.txt", content="hello world")]),
            # 2) add a todo via tool
            LLMResponse(content=None, tool_calls=[tc("todo_add", content="write tests")]),
            # 3) done
            LLMResponse(content="created hello.txt and added todo"),
        ]
        llm = FakeLLM(script)
        wm = WindowManager(db=db, llm_factory=lambda: llm, bus=bus, memory=mem,
                           checkpoints_dir=tmp_path / "data" / "checkpoints")
        await wm.start()
        w = wm.create(name="dev", role="worker_programmer",
                      workspace_path=str(tmp_path / "ws"), model="fake", mode="BUILD")

        outcome = await wm.dispatch_and_wait(w.id, "create hello.txt and plan tests")
        assert outcome.status == "ok"
        assert (tmp_path / "ws" / "hello.txt").read_text() == "hello world"
        todos = db.list_todos(w.id)
        assert any("write tests" in t.content for t in todos)

        # persistence: assistant/tool messages survived
        msgs = db.list_messages(w.id)
        roles = [m.role for m in msgs]
        assert "tool" in roles and "assistant" in roles

        # ---- compaction path (M1 acceptance: build→dispatch→work→compact) ----
        for i in range(40):
            db.add_message(w.id, "user" if i % 2 else "assistant", f"filler {i}")
        compactor_llm = FakeLLM()
        compactor_llm.push(content='{"narrative":"did work","file_changes":'
                                   '[{"path":"hello.txt","action":"created"}],'
                                   '"todos":["write tests"],"decisions":["keep it small"],'
                                   '"lessons":[]}')
        # force compaction with a low threshold
        wm.threshold = 0.0001
        wm.max_context_tokens = 10
        sessions = SessionManager(wm, db)
        rec = await sessions.compress(w.id)
        assert rec.replaced_count > 0
        assert db.list_compactions(w.id)

        await wm.stop()
        db.close()

    asyncio.get_event_loop().run_until_complete(main())


def test_m1_rate_limit_not_failure(tmp_path):
    """RetryExhausted surfaces as a failed run, but retry waits themselves never do."""
    from dogbuddy.cron.jobs import JobStore
    from dogbuddy.cron.scheduler import CronScheduler, JobOutcome

    async def main():
        db = Database(tmp_path / "t.db")
        from dogbuddy.ports import Window
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path=str(tmp_path), model="m"))
        store = JobStore(db)
        job = store.create("w1", "j", "every 2h", "work")
        job.next_run_at = "2000-01-01T00:00:00+00:00"
        db.upsert_job(job)

        outcomes = iter([JobOutcome(status="rate_limited", summary="waiting"),
                         JobOutcome(status="ok", summary="done")])

        async def run_job(j):
            return next(outcomes)

        sched = CronScheduler(store, run_job=run_job, bus=None, tick_seconds=9999)
        await sched.tick()
        await asyncio.sleep(0.05)  # let the task run
        j = store.get(job.id)
        assert j.fail_count == 0 and j.enabled  # rate limit doesn't count
        assert db.list_runs(job.id)[0].status == "rate_limited"
        assert j.next_run_at is not None  # rescheduled ~5min later, not failed

        # simulate the 5-minute wait elapsed → job due again and succeeds
        j = store.get(job.id)
        j.next_run_at = "2000-01-01T00:00:00+00:00"
        db.upsert_job(j)
        await sched.tick()
        await asyncio.sleep(0.05)
        j = store.get(job.id)
        assert j.fail_count == 0
        assert db.list_runs(job.id)[0].status == "ok"
        db.close()

    asyncio.get_event_loop().run_until_complete(main())
