# DogBuddyCn — admin agent tests (natural-language management loop + admin tools)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Acceptance ① 的可测部分: admin loop drives window/task creation via tools."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig
from dogbuddy.db import Database
from dogbuddy.events.bus import EventBus
from dogbuddy.llm.provider import LLMResponse
from dogbuddy.memory.service import MemoryService
from dogbuddy.services.admin_service import AdminService
from dogbuddy.services.config_service import ConfigService
from dogbuddy.services.job_manager import JobManager
from dogbuddy.services.session_manager import SessionManager
from dogbuddy.services.window_manager import WindowManager
from dogbuddy.services.workspace_manager import WorkspaceManager
from dogbuddy.cron.jobs import JobStore

from helpers import FakeLLM, tc


def _stack(tmp_path, llm):
    db = Database(tmp_path / "t.db")
    mem = MemoryService(tmp_path / "memory")
    bus = EventBus()
    cfg = AppConfig(llm=LLMConfig(), paths=PathsConfig(
        data_dir=str(tmp_path), workspaces_dir=str(tmp_path / "ws")))
    wm = WindowManager(db=db, llm_factory=lambda: llm, bus=bus, memory=mem,
                       checkpoints_dir=tmp_path / "ckpt")
    sessions = SessionManager(wm, db)
    workspaces = WorkspaceManager(tmp_path / "ws", db)
    jobs = JobManager(JobStore(db), wm)
    cfg_svc = ConfigService(db, cfg)
    admin = AdminService(db=db, llm_factory=lambda: llm, windows=wm, jobs=jobs,
                         sessions=sessions, workspaces=workspaces, memory=mem,
                         config=cfg_svc, bus=bus)
    return db, wm, jobs, admin


def test_admin_natural_language_creates_window_and_job(tmp_path):
    async def main():
        llm = FakeLLM([
            LLMResponse(content=None, tool_calls=[tc("window_create", name="dev",
                        role="worker_programmer", workspace=str(tmp_path / "proj"))]),
            LLMResponse(content=None, tool_calls=[tc("task_create",
                        window_id="dev", name="morning", schedule="every day at 8am",
                        prompt="推进待办", report_strategy="brief")]),
            LLMResponse(content="好，已建工位 dev 并设置每天早8点任务。"),
        ])
        db, wm, jobs, admin = _stack(tmp_path, llm)
        await wm.start()

        reply = await admin.handle_user_message(
            "每天早8点在 proj 项目推进待办，完成后简要汇报")
        assert "工位" in reply or "done" in reply or reply

        windows = wm.list()
        assert len(windows) == 1 and windows[0].name == "dev"
        job_list = jobs.list()
        assert job_list, "admin tool task_create did not create a job"
        assert job_list[0].schedule_raw == "every day at 8am"
        assert job_list[0].report_strategy == "brief"
        await wm.stop()
        db.close()

    asyncio.get_event_loop().run_until_complete(main())


def test_admin_tools_progress_report(tmp_path):
    async def main():
        llm = FakeLLM()
        db, wm, jobs, admin = _stack(tmp_path, llm)
        await wm.start()
        wm.create(name="dev", workspace_path=str(tmp_path / "proj"))
        from dogbuddy.tools.registry import ToolContext
        ctx = ToolContext(window_id="admin", workspace=".", mode="AUTO", actor="admin")
        r = await admin.registry.execute("progress_report", {}, ctx)
        assert r.ok and "dev" in r.output
        r = await admin.registry.execute("window_list", {}, ctx)
        assert r.ok and "dev" in r.output
        r = await admin.registry.execute("memory_write", {"scope": "global", "fact": "user likes terse replies"}, ctx)
        assert r.ok
        r = await admin.registry.execute("memory_recall", {"scope": "global"}, ctx)
        assert "terse" in r.output
        await wm.stop()
        db.close()

    asyncio.get_event_loop().run_until_complete(main())
