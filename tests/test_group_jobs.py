# DogBuddyCn — 群组定时任务（v0.10.44）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner：「继续让其完成前端模板任务，每隔 1 小时触发一次。」

以前 cron 只能指向**单个工位**——「方向/程序员/审查」这条流水线没有定时入口
（管理员只能在对话里手动 group_run）。这里给 Job 加 `group_id`：
到点就跑一条完整群组运行，终态映射到 job_runs（partial 记 ok，别把流水线
"连败 3 次自动停掉"）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.cron.jobs import JobStore, parse_schedule  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402
from dogbuddy.ports import Job  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_group_id_persists_and_defaults_empty(tmp_path):
    db = Database(str(tmp_path / "j.db"))
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(jobs)")}
    assert "group_id" in cols, "schema 15 必须给 jobs 加 group_id"
    store = JobStore(db)
    job = store.create("w1", "模板流水线-每小时", "every 1 hour", "继续迭代模板",
                       group_id="g1")
    assert store.repo.get_job(job.id).group_id == "g1"
    plain = store.create("w1", "普通任务", "30m", "x")
    assert store.repo.get_job(plain.id).group_id == "", "普通任务不该带上群组"
    db.close()


def test_every_1_hour_parses_as_interval():
    parsed = parse_schedule("every 1 hour")
    assert parsed["kind"] == "interval"
    assert parsed["minutes"] == 60, "every 1 hour 必须是 60 分钟间隔"
    db = None  # 纯解析，不需要库


class _FakeGroups:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run(self, gid, prompt):
        self.calls.append((gid, prompt))
        return self.result


def _app(result):
    return SimpleNamespace(groups=_FakeGroups(result), windows=None,
                           _rate_limit={"waiting": False})


def _job(gid="g1") -> Job:
    return Job(id="j1", window_id="lead", name="t", schedule_type="interval",
               schedule_raw="every 1 hour", schedule={}, prompt="继续做模板",
               group_id=gid)


def test_group_job_maps_done_and_partial_to_ok():
    """partial（有步骤没完成）也要记 ok——否则调度器连败 3 次会把流水线自动停掉。"""
    for status in ("done", "partial"):
        app = _app({"status": status, "summary": "[完成 3/4 步] 两轮完成"})
        out = _run(DogBuddyApp._run_job(app, _job()))
        assert out.status == "ok", status
        assert "完成 3/4 步" in out.summary
        assert app.groups.calls == [("g1", "继续做模板")]


def test_group_job_maps_rate_limited_and_failed():
    app = _app({"status": "failed", "summary": "全被限速", "rate_limited": 4})
    out = _run(DogBuddyApp._run_job(app, _job()))
    assert out.status == "rate_limited", "全员限速是暂停，不是失败"
    app2 = _app({"status": "failed", "summary": "炸了"})
    assert _run(DogBuddyApp._run_job(app2, _job())).status == "failed"


def test_group_job_survives_group_crash():
    class Boom:
        async def run(self, gid, prompt):
            raise RuntimeError("members vanished")

    app = SimpleNamespace(groups=Boom(), windows=None, _rate_limit={})
    out = _run(DogBuddyApp._run_job(app, _job()))
    assert out.status == "failed" and "群组运行异常" in out.summary


def test_run_now_on_group_job_refuses_single_dispatch():
    """v0.10.45：群组任务点"立即运行"不能把提示词单派给组长（那会白跑一轮）。"""
    calls = []

    class _W:
        def get(self, wid):
            return SimpleNamespace(id=wid)

        def dispatch(self, *a, **k):
            calls.append((a, k))

    class _Store:
        def get(self, jid):
            return Job(id="j1", window_id="lead", name="t", schedule_type="interval",
                       schedule_raw="every 1 hour", schedule={}, prompt="x",
                       group_id="g1")

    from dogbuddy.services.job_manager import JobManager
    msg = JobManager(_Store(), _W()).run_now("j1")
    assert "群组任务" in msg and calls == [], "群组任务不该被单派到组长工位"

def test_run_now_on_group_job_refuses_single_dispatch():
    """v0.10.45：群组任务点"立即运行"不能把提示词单派给组长（那会白跑一轮）。"""
    calls = []

    class _W:
        def get(self, wid):
            return SimpleNamespace(id=wid)

        def dispatch(self, *a, **k):
            calls.append((a, k))

    class _Store:
        def get(self, jid):
            return Job(id="j1", window_id="lead", name="t", schedule_type="interval",
                       schedule_raw="every 1 hour", schedule={}, prompt="x",
                       group_id="g1")

    from dogbuddy.services.job_manager import JobManager
    msg = JobManager(_Store(), _W()).run_now("j1")
    assert "群组任务" in msg and calls == [], "群组任务不该被单派到组长工位"