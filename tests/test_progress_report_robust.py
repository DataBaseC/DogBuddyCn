# DogBuddyCn — progress_report 不能因为 NULL summary 整个挂掉（0.10.19）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""圆桌测试里实测到的缺陷（管理员自己报的）：

    progress_report failed: 'NoneType' object is not subscriptable

`AdminService._progress_dict()` 拼最近运行时写的是 `r['summary'][:80]`，
而 `job_runs.summary` 可以是 NULL（"没事可报"的 quiet 运行、被清理过的行）。
一行就让"主管/管理员查全局进度"这个核心工具整个不可用 —— 它还会连带
把 `AdminService._progress_dict` 里 windows/jobs 两段也一起废掉。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.memory.service import MemoryService  # noqa: E402
from dogbuddy.services.admin_service import AdminService  # noqa: E402
from dogbuddy.services.config_service import ConfigService  # noqa: E402
from dogbuddy.services.job_manager import JobManager  # noqa: E402
from dogbuddy.services.session_manager import SessionManager  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.services.workspace_manager import WorkspaceManager  # noqa: E402
from dogbuddy.cron.jobs import JobStore  # noqa: E402


def _admin(tmp_path):
    db = Database(tmp_path / "t.db")
    cfg = AppConfig(llm=LLMConfig(), paths=PathsConfig(
        data_dir=str(tmp_path), workspaces_dir=str(tmp_path / "ws")))
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=None,
                       memory=MemoryService(tmp_path / "mem"), checkpoints_dir=tmp_path / "ckpt")
    jobs = JobManager(JobStore(db), wm)
    admin = AdminService(db=db, llm_factory=lambda: None, windows=wm, jobs=jobs,
                         sessions=SessionManager(wm, db),
                         workspaces=WorkspaceManager(tmp_path / "ws", db),
                         memory=MemoryService(tmp_path / "mem"),
                         config=ConfigService(db, cfg))
    return db, wm, jobs, admin


def test_progress_dict_survives_a_null_summary(tmp_path):
    db, wm, jobs, admin = _admin(tmp_path)
    w = wm.create(name="w1", role="worker_programmer", workspace_path=str(tmp_path / "ws" / "w"))
    job = jobs.create(name="j1", window_id=w.id, schedule="every 30m", prompt="干活")
    run = db.start_run(job.id, w.id)
    # 模拟"没事可报"的 quiet 运行：summary 为 NULL
    db._conn.execute("UPDATE job_runs SET summary=NULL WHERE id=?", (run.id,))
    db._conn.commit()

    data = admin._progress_dict()
    assert "## Windows" in data["text"] and "## Recent runs" in data["text"]
    assert data["windows"] >= 1 and data["jobs"] >= 1
    db.close()


def test_progress_report_tool_returns_text_with_null_summaries(tmp_path):
    from dogbuddy.agents.management.tools import AdminServices, build_admin_tools
    from dogbuddy.tools.registry import ToolContext
    from helpers import run

    db, wm, jobs, admin = _admin(tmp_path)
    w = wm.create(name="w2", role="worker_programmer", workspace_path=str(tmp_path / "ws" / "w2"))
    job = jobs.create(name="j2", window_id=w.id, schedule="every 30m", prompt="干活")
    run_row = db.start_run(job.id, w.id)
    db._conn.execute("UPDATE job_runs SET summary=NULL WHERE id=?", (run_row.id,))
    db._conn.commit()

    tools = {t.name: t for t in build_admin_tools(AdminServices(progress=admin._progress_dict))}
    out = run(tools["progress_report"].execute({}, ToolContext(window_id="admin:default",
                                                              workspace=str(tmp_path), db=db)))
    assert out.ok, out.output
    assert "## Windows" in out.output
    db.close()
