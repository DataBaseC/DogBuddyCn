# DogBuddyCn — 迭代/调度的健壮性回归（审查报告 P1-8 / P1-10 / P2-6 / P2-7）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""四条互不相干但都在"跑很久之后才发作"的缺陷：

* **P1-8** 手动推一轮绕过并发锁 → 同一迭代并发跑两轮、轮次号重复
* **P1-10** 迭代停摆只发事件不触达人 → 项目停了没人知道、也不知道停因
* **P2-6** 重启后 `iteration_rounds` 永久卡在 `running` → 控制台永远显示"运行中"
* **P2-7** 30 分钟以上的长任务被误判失败 → 连败 3 次自动暂停，而任务其实成功了
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database, Window  # noqa: E402
from dogbuddy.events.bus import Event  # noqa: E402
from dogbuddy.ports import Project  # noqa: E402
from dogbuddy.services.iteration_executor import (  # noqa: E402
    ITERATION_STOPPED, IterationExecutor)
from dogbuddy.services.iteration_service import IterationService  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _db(tmp_path) -> Database:
    return Database(tmp_path / "t.db")


def _project(db, pid="p1", tmp=None):
    p = Project(id=pid, name=pid, root_path=str(tmp / pid))
    db.insert_project(p)
    return p


# ------------------------------------------------------------------ P1-8
def test_手动推一轮与自动轮次共用并发锁(tmp_path):
    """★ 路由原来直接调 `run_one()`，绕过 `_inflight`。"""
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path=str(tmp_path / "w1"), model="",
                                mode="BUILD", project_id="p1"))
        it = db.insert_iteration({"project_id": "p1", "goal": "g",
                                  "status": "active", "round": 0, "window_ids": ["w1"]})
        started = []

        async def slow_round(entry, plan, round_no):
            started.append(round_no)
            await asyncio.sleep(0.15)
            return {"status": "ok", "summary": "ok", "progress": 10, "goal_achieved": False}

        ex = IterationExecutor(service=IterationService(db, windows=None), db=db,
                               run_round=slow_round)

        async def scenario():
            a, b = await asyncio.gather(ex.run_now(it["id"]), ex.run_now(it["id"]))
            return a, b

        a, b = _run(scenario())

        assert len(started) == 1, f"同一个迭代被并发跑了两轮：{started}"
        assert any(r.get("skipped") for r in (a, b)), "第二次必须被并发锁挡下"
    finally:
        db.close()


def test_同一迭代同一轮只落一行(tmp_path):
    """迁移 v10 的 UNIQUE(iteration_id, round) 兜底 + start_iteration_round 幂等。"""
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        it = db.insert_iteration({"project_id": "p1", "goal": "g",
                                  "status": "active", "round": 0, "window_ids": []})
        r1 = db.start_iteration_round(it["id"], 1)
        r2 = db.start_iteration_round(it["id"], 1)
        assert r1 == r2, "重复开同一轮应当幂等返回同一行"
        rows = db.list_iteration_rounds(it["id"], limit=10)
        assert len(rows) == 1, f"不该出现重复轮次行：{rows}"
        # 索引确实存在
        idx = {r[1] for r in db._conn.execute("PRAGMA index_list(iteration_rounds)")}
        assert "uq_iter_round" in idx
    finally:
        db.close()


# ------------------------------------------------------------------ P1-10
def test_迭代停摆会推送而不是只发事件(tmp_path):
    """★ 初版 `iteration.stopped` 全仓库只有 `web/sse.py` 的 `subscribe("*")`
    一个订阅者——只有"当时开着浏览器标签"的人知道，没走 notify。"""
    from dogbuddy.events.bus import EventBus
    from dogbuddy.services.report_service import ReportService

    class _Notifier:
        name = "rec"

        def __init__(self):
            self.sent = []

        async def send(self, text):
            self.sent.append(text)

    bus = EventBus()
    rec = _Notifier()
    ReportService(bus=bus, notifiers=[rec])

    async def scenario():
        await bus.publish(Event(ITERATION_STOPPED, {
            "iteration_id": "it1", "project_id": "p1", "round": 4,
            "reason": "连续 3 轮无进展", "goal": "把登录做完", "summary": "卡在鉴权"}))

    _run(scenario())

    assert rec.sent, "迭代停摆必须触达人（P1-10）"
    assert "连续 3 轮无进展" in rec.sent[0], "必须写明停因，否则用户不知道该改什么"
    assert "把登录做完" in rec.sent[0]


# ------------------------------------------------------------------ P2-6
def test_重启清扫也收拾卡住的迭代轮次(tmp_path):
    """初版 `sweep_orphan_runs` 不碰 `iteration_rounds` → 永久"运行中"。"""
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        it = db.insert_iteration({"project_id": "p1", "goal": "g",
                                  "status": "active", "round": 0, "window_ids": []})
        rid = db.start_iteration_round(it["id"], 1)
        assert [r for r in db.list_iteration_rounds(it["id"], 10)
                if r["status"] == "running"], "先制造一个卡住的轮次"

        swept = db.sweep_orphan_runs()

        assert swept.get("iteration_rounds") == 1
        rows = db.list_iteration_rounds(it["id"], 10)
        assert rows and rows[0]["status"] == "interrupted", "卡住的轮次必须被收尸"
        assert rows[0]["finished_at"], "要有结束时间，否则前端仍认为是进行中"
        assert rid
        # 幂等
        assert db.sweep_orphan_runs().get("iteration_rounds") == 0
    finally:
        db.close()


# ------------------------------------------------------------------ P2-7
def test_超时不计入连败(tmp_path):
    """★ 初版把"30 分钟没跑完"当 failed → 长任务连败 3 次被自动暂停。"""
    from dogbuddy.cron.jobs import JobStore
    from dogbuddy.cron.scheduler import JobOutcome

    db = _db(tmp_path)
    try:
        store = JobStore(db)
        job = store.create(window_id="w1", name="长任务", prompt="p", schedule="30m")
        before = db.get_job(job.id)
        assert before.fail_count == 0

        store.mark_timeout(before)

        after = db.get_job(job.id)
        assert after.fail_count == before.fail_count == 0, "超时绝不能计入连败（P2-7）"
        assert after.enabled is True, "超时不该暂停任务"
        assert after.next_run_at, "应当照常排下一次"
    finally:
        db.close()


def test_超时的调度分支不发失败事件(tmp_path):
    """调度器要认得 timeout 状态：不是失败、不暂停、不推"失败"通知。"""
    from dogbuddy.cron.jobs import JobStore
    from dogbuddy.cron.scheduler import CronScheduler, JobOutcome
    from dogbuddy.events.bus import EventBus, JOB_FAILED, JOB_QUIET

    db = _db(tmp_path)
    try:
        store = JobStore(db)
        job = store.create(window_id="w1", name="长任务", prompt="p", schedule="30m")
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe("*", lambda e: seen.append(e.type))

        async def run_job(_job):
            return JobOutcome(status="timeout", summary="超过 1800s 仍未结束")

        sched = CronScheduler(store=store, run_job=run_job, bus=bus)

        async def scenario():
            # bus.emit 是入队，必须有跑着的总线才会派发
            await bus.start()
            try:
                await sched._execute(db.get_job(job.id))
                await asyncio.sleep(0.05)
            finally:
                await bus.stop()

        _run(scenario())

        assert JOB_FAILED not in seen, f"超时不该报失败：{seen}"
        assert JOB_QUIET in seen, "应当作为『无事可报』记录，而不是火警"
        assert db.get_job(job.id).fail_count == 0
        assert db.get_job(job.id).enabled is True
    finally:
        db.close()


def test_超时判定只在真超时时触发(tmp_path):
    """`_wait_timeout` 可被工位参数覆盖；读不出来就退回默认值。"""
    from dogbuddy.services.window_manager import WindowManager

    assert WindowManager.CRON_WAIT_TIMEOUT == 1800.0

    class _Fake:
        CRON_WAIT_TIMEOUT = 1800.0

        def __init__(self, params):
            self._params = params

        def get(self, wid):
            return Window(id=wid, name=wid, role="worker_programmer",
                          workspace_path="", model="", mode="BUILD")

        def params_for(self, w):
            return self._params

    assert WindowManager._wait_timeout(_Fake({}), "w1") == 1800.0
    assert WindowManager._wait_timeout(_Fake({"task_timeout": 7200}), "w1") == 7200.0
    assert WindowManager._wait_timeout(_Fake({"task_timeout": 0}), "w1") == 1800.0
    assert WindowManager._wait_timeout(_Fake({"task_timeout": "bad"}), "w1") == 1800.0
