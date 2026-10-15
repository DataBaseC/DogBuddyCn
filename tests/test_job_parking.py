# DogBuddyCn — 任务"挂死"在远期绝对时间回归（v0.10.20）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""圆桌测试（2026-10-15）实测：管理员把 3 个一次性 prompt 任务 park 到
`2030-01-01`，靠手动 `run_now` 触发；跑完后任务依然 `enabled=1`、
`next_run_at=2030`、`last_run_at=null` —— 控制台显示"启用中"但它永不再跑
（qa-ops 原话："启用 ≠ 会跑"，轮次链全靠人肉 run_now 衔接）。

根因：`JobManager.run_now` 只派单，**从不更新任务生命周期**。
修法：一次性任务派单即 consumed（禁用 + 清 next_run）；建/列任务时对
> 30 天的下次运行时间给出人话警告。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.cron.jobs import JobStore  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.services.job_manager import FAR_FUTURE_DAYS, JobManager, far_future_warning  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeWindows:
    """JobManager 只用到 get/dispatch/db。"""

    def __init__(self, db: Database):
        self.db = db

    def get(self, window_id: str):
        return self.db.get_window(window_id)

    def dispatch(self, *args, **kwargs):
        return "dispatched"


def _stack(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.insert_window(Window(id="w1", name="dev", role="worker_programmer",
                            workspace_path=str(tmp_path), model="", mode="BUILD"))
    return db, JobManager(JobStore(db), _FakeWindows(db))


def test_run_now_disables_once_job(tmp_path):
    db, jobs = _stack(tmp_path)
    job = jobs.create("w1", "圆桌R1", "2030-01-01T00:00:00", "讨论")
    assert job.enabled and job.next_run_at and job.next_run_at.startswith("2030-01-01")

    out = jobs.run_now(job.id)
    assert "dispatched" in out
    after = jobs.get(job.id)
    assert after.enabled is False, "一次性任务派单后必须禁用——它永不再自动触发"
    assert after.next_run_at is None
    entries = [e for e in db.list_audit() if e.action == "job:run_now_once"]
    assert entries, "派单即禁用必须留痕"
    db.close()


def test_run_now_keeps_recurring_job(tmp_path):
    db, jobs = _stack(tmp_path)
    job = jobs.create("w1", "每日", "every day at 8am", "推进")
    before_next = job.next_run_at
    jobs.run_now(job.id)
    after = jobs.get(job.id)
    assert after.enabled is True, "周期任务的手动触发不影响它的自动节奏"
    assert after.next_run_at == before_next
    db.close()


def test_far_future_warning(tmp_path):
    db, jobs = _stack(tmp_path)
    once = jobs.create("w1", "远", "2030-01-01T00:00:00", "x")
    warn = far_future_warning(once)
    assert "一次性任务" in warn and "不会自动触发" in warn
    near = jobs.create("w1", "近", "in 30m", "x")
    assert far_future_warning(near) == ""
    recurring = jobs.create("w1", "周", "every monday 9am", "x")
    assert far_future_warning(recurring) == ""
    assert FAR_FUTURE_DAYS == 30
    db.close()


def test_task_create_tool_warns_on_far_future_once(tmp_path):
    """管理员建任务时就该看到警告，而不是等 owner 在控制台发现"启用却不跑"。"""
    from dogbuddy.agents.management.tools import AdminServices, build_admin_tools
    from dogbuddy.tools.registry import ToolContext
    db, jobs = _stack(tmp_path)
    tools = {t.name: t for t in build_admin_tools(AdminServices(jobs=jobs))}
    ctx = ToolContext(window_id="admin", workspace=".", mode="AUTO", actor="admin", db=db)

    r = _run(tools["task_create"].execute(
        {"window_id": "w1", "name": "圆桌R1", "schedule": "2030-01-01T00:00:00",
         "prompt": "讨论"}, ctx))
    assert r.ok and "⚠" in r.output and "不会自动触发" in r.output

    r = _run(tools["task_create"].execute(
        {"window_id": "w1", "name": "近", "schedule": "in 30m", "prompt": "x"}, ctx))
    assert r.ok and "⚠" not in r.output

    r = _run(tools["task_list"].execute({}, ctx))
    assert r.ok and "⚠" in r.output, "task_list 也要给远期任务打标"
    db.close()


def test_task_run_now_tool_disables_once(tmp_path):
    from dogbuddy.agents.management.tools import AdminServices, build_admin_tools
    from dogbuddy.tools.registry import ToolContext
    db, jobs = _stack(tmp_path)
    tools = {t.name: t for t in build_admin_tools(AdminServices(jobs=jobs))}
    ctx = ToolContext(window_id="admin", workspace=".", mode="AUTO", actor="admin", db=db)
    job = jobs.create("w1", "一次性", "2030-01-01T00:00:00", "x")

    r = _run(tools["task_run_now"].execute({"job_id": job.id}, ctx))
    assert r.ok and "不再自动触发" in r.output
    assert jobs.get(job.id).enabled is False
    db.close()
