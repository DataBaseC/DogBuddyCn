# DogBuddyCn — 定时任务的"谁停的"必须留痕（v0.10.48）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""2026-10-22 排障实录：生产上「模板流水线-每小时」在 13:24:51 被停用，
`jobs.enabled=0`、`updated_at` 有值，但 `audit_log` 里**一条痕迹都没有**——
只能去面板的 HTTP 访问日志里翻 `POST /api/jobs/<id>/pause` 才确认是控制台点的。

create / update / remove / run_now 都写了审计，唯独 pause / resume 没有。
它们恰恰是"流水线突然不跑了"最常见的原因，必须留痕，并记下停用前的 next_run_at
（恢复时才知道错过了哪几轮）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.cron.jobs import JobStore  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.job_manager import JobManager  # noqa: E402


def _mgr(db: Database) -> tuple[JobManager, JobStore]:
    store = JobStore(db)
    # JobManager 只用 windows.db 写审计，这里给个最小替身即可
    return JobManager(store, SimpleNamespace(db=db)), store


def _audit(db: Database) -> list[tuple[str, str, str]]:
    return [(r[0], r[1], r[2]) for r in
            db._conn.execute("select actor, action, detail from audit_log order by id")]


def test_pause_and_resume_are_audited(tmp_path):
    db = Database(str(tmp_path / "job.db"))
    mgr, store = _mgr(db)
    job = store.create("w1", "模板流水线-每小时", "every 1 hour", "继续迭代模板")

    assert mgr.pause(job.id) == "paused"
    assert store.get(job.id).enabled is False
    assert mgr.resume(job.id) == "resumed"
    assert store.get(job.id).enabled is True

    rows = _audit(db)
    actions = [a for _, a, _ in rows]
    assert "job:pause" in actions, "停用任务没写审计，排障时查不到是谁停的"
    assert "job:resume" in actions, "恢复任务也要留痕"

    # 停用记录要带上停用前的 next_run_at，恢复时才知道错过了哪一轮
    pause_detail = [d for _, a, d in rows if a == "job:pause"][0]
    assert job.id in pause_detail
    assert "next_run_at" in pause_detail
    assert all(actor == "system" for actor, a, _ in rows if a.startswith("job:"))
    db.close()


def test_pause_is_idempotent_and_still_audited(tmp_path):
    """重复点"暂停"不该报错，但每次都要有记录（别把审计做成"只在状态变化时写"）。"""
    db = Database(str(tmp_path / "job2.db"))
    mgr, store = _mgr(db)
    job = store.create("w1", "x", "30m", "p")

    assert mgr.pause(job.id) == "paused"
    assert mgr.pause(job.id) == "paused"
    count = len([1 for _, a, _ in _audit(db) if a == "job:pause"])
    assert count == 2
    assert store.get(job.id).enabled is False
    db.close()
