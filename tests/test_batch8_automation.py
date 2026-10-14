# DogBuddyCn — Batch 8 组一：自助排期 / 条件任务 / 自调度
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""T8.1 工位自己建定时任务 · T8.2 有事才通知 · T8.3 任务自决下次运行时间。

这三项咬合在一起：工位在对话里说"这事每天做" → 落成 job（T8.1）；
job 跑完由工位判断"有事才通知"（T8.2）；并由工位自己决定"下次什么时候再来"（T8.3，
覆盖 cron 默认）。**权限从紧**是硬要求：只能管本工位/本项目。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.runners import PromptRunner, RunnerResult, TaskEnv  # noqa: E402
from dogbuddy.cron.jobs import JobStore  # noqa: E402
from dogbuddy.cron.scheduler import CronScheduler, JobOutcome  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.ports import Job, Project, Window  # noqa: E402
from dogbuddy.tools.automation_ops import (  # noqa: E402
    NotifyOwnerTool, RescheduleTool, ScheduleTool, _parse_when)
from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(tmp_path, window_id="w1", db=None) -> ToolContext:
    (tmp_path / "ws").mkdir(exist_ok=True)
    return ToolContext(window_id=window_id, workspace=str(tmp_path / "ws"),
                       mode="BUILD", db=db, actor="worker",
                       checkpoint_root=str(tmp_path / "ckpt"))


# --------------------------------------------------------------------------- 时间解析
def test_相对时间与绝对时间解析():
    assert _parse_when("30m") is not None
    assert _parse_when("2h") is not None
    assert _parse_when("1d") is not None
    assert _parse_when("2026-10-15T09:00:00+08:00") is not None
    assert _parse_when("2026-10-15T09:00:00") is not None, "不带时区按 UTC 解释"
    assert _parse_when("乱七八糟") is None
    assert _parse_when("") is None


# --------------------------------------------------------------------------- T8.1
def test_T8_1_建任务并列出(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        store = JobStore(db)
        tool = ScheduleTool(jobs=store)
        ctx = _ctx(tmp_path, db=db)

        res = _run(tool.execute({"action": "create", "name": "每日推进待办",
                                 "when": "every day 9am",
                                 "prompt": "检查 todos 并推进一件"},
                                ctx))
        assert res.ok, res.output
        assert res.data["job_id"]
        jid = res.data["job_id"]
        job = store.get(jid)
        assert job.window_id == "w1", "必须排在当前工位名下"
        assert job.next_run_at, "创建时就要算出下次运行时间"

        listed = _run(tool.execute({"action": "list"}, ctx))
        assert jid in listed.data["jobs"]
        assert "每日推进待办" in listed.output
    finally:
        db.close()


def test_T8_1_排期表达式错误回传模型自改(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        tool = ScheduleTool(jobs=JobStore(db))
        res = _run(tool.execute({"action": "create", "name": "x",
                                 "when": "永不", "prompt": "p"}, _ctx(tmp_path, db=db)))
        assert not res.ok
        assert "英文排期语法" in res.output, res.output
    finally:
        db.close()


def test_T8_1_缺必填参数被明确指出(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        tool = ScheduleTool(jobs=JobStore(db))
        res = _run(tool.execute({"action": "create", "name": "只有名字"}, _ctx(tmp_path, db=db)))
        assert not res.ok and "缺少必填参数" in res.output
    finally:
        db.close()


def test_T8_1_只能管本工位本项目的任务(tmp_path):
    """权限从紧：别人的工位（不同项目）的任务一律碰不到。"""
    db = Database(tmp_path / "t.db")
    try:
        store = JobStore(db)
        # 我：w1，属于 p1；同项目还有 w2；另一个项目的 w9
        db.insert_project(Project(id="p1", name="P1", root_path=str(tmp_path / "p1")))
        db.insert_project(Project(id="p2", name="P2", root_path=str(tmp_path / "p2")))
        for wid, pid in (("w1", "p1"), ("w2", "p1"), ("w9", "p2")):
            db.insert_window(Window(id=wid, name=wid, role="worker_programmer",
                                    workspace_path=str(tmp_path / wid), model="",
                                    project_id=pid))
        mine = store.create("w1", "我的", "every day 9am", "p")
        same_proj = store.create("w2", "同项目", "every day 10am", "p")
        other = store.create("w9", "别的项目", "every day 11am", "p")

        tool = ScheduleTool(jobs=store)
        ctx = _ctx(tmp_path, window_id="w1", db=db)

        # 自己 + 同项目都能管
        assert _run(tool.execute({"action": "pause", "job_id": mine.id}, ctx)).ok
        assert _run(tool.execute({"action": "pause", "job_id": same_proj.id}, ctx)).ok
        # 别的项目 → 越权
        denied = _run(tool.execute({"action": "pause", "job_id": other.id}, ctx))
        assert not denied.ok and "越权" in denied.output
        # 也不能把任务排到别的项目的工位上
        denied2 = _run(tool.execute({"action": "create", "name": "偷排", "when": "30m",
                                     "prompt": "p", "target_window": "w9"}, ctx))
        assert not denied2.ok and "越权" in denied2.output
        # 但可以排给同项目的工位
        ok2 = _run(tool.execute({"action": "create", "name": "给同事排", "when": "30m",
                                 "prompt": "p", "target_window": "w2"}, ctx))
        assert ok2.ok, ok2.output
        assert store.get(ok2.data["job_id"]).window_id == "w2"
    finally:
        db.close()


def test_T8_1_更新_删除_恢复_立即执行(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        store = JobStore(db)
        fired: list[str] = []
        tool = ScheduleTool(jobs=store, runner=fired.append)
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path=str(tmp_path / "w1"), model=""))
        ctx = _ctx(tmp_path, db=db)
        jid = _run(tool.execute({"action": "create", "name": "j", "when": "every day 9am",
                                 "prompt": "旧提示"}, ctx)).data["job_id"]

        upd = _run(tool.execute({"action": "update", "job_id": jid, "when": "every 2h",
                                 "prompt": "新提示",
                                 "report_strategy": "conditional"}, ctx))
        assert upd.ok
        job = store.get(jid)
        assert job.prompt == "新提示"
        assert job.report_strategy == "conditional"
        assert job.schedule_type == "interval"

        assert _run(tool.execute({"action": "pause", "job_id": jid}, ctx)).ok
        assert store.get(jid).enabled is False
        assert _run(tool.execute({"action": "resume", "job_id": jid}, ctx)).ok
        assert store.get(jid).enabled is True

        assert _run(tool.execute({"action": "run_now", "job_id": jid}, ctx)).ok
        assert fired == [jid]

        assert _run(tool.execute({"action": "delete", "job_id": jid}, ctx)).ok
        assert store.get(jid) is None
    finally:
        db.close()


def test_T8_1_按名字也能定位任务(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        store = JobStore(db)
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path="", model=""))
        tool = ScheduleTool(jobs=store)
        ctx = _ctx(tmp_path, db=db)
        _run(tool.execute({"action": "create", "name": "按名找", "when": "30m",
                           "prompt": "p"}, ctx))
        res = _run(tool.execute({"action": "delete", "job_id": "按名找"}, ctx))
        assert res.ok, res.output
        assert store.list() == []
    finally:
        db.close()


# --------------------------------------------------------------------------- T8.3
def test_T8_3_自调度写入下次运行时间(tmp_path):
    ctx = _ctx(tmp_path)
    res = _run(RescheduleTool().execute({"after": "2h", "reason": "构建中，20 分钟后回来"},
                                        ctx))
    assert res.ok, res.output
    assert ctx.extra["next_run_at"], "必须写进 ctx.extra 让 runner 取走"
    assert ctx.extra["reschedule_reason"].startswith("构建中")


def test_T8_3_自调度有时间下限防止空转(tmp_path):
    ctx = _ctx(tmp_path)
    _run(RescheduleTool().execute({"after": "1s"}, ctx))
    import datetime as dt
    target = dt.datetime.fromisoformat(ctx.extra["next_run_at"])
    delta = (target - dt.datetime.now(dt.timezone.utc)).total_seconds()
    assert delta >= 55, f"太近的排期要被推到至少 60s 后，实际 {delta:.0f}s"


def test_T8_3_无法解析的时间被拒(tmp_path):
    ctx = _ctx(tmp_path)
    res = _run(RescheduleTool().execute({"after": "一会儿"}, ctx))
    assert not res.ok and "无法解析" in res.output
    assert "next_run_at" not in ctx.extra


def test_T8_3_调度器用自调度覆盖_cron(tmp_path):
    """端到端：任务自决的时间必须真的写进 job.next_run_at，而不是被 cron 覆盖。"""
    db = Database(tmp_path / "t.db")
    try:
        store = JobStore(db)
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path=str(tmp_path / "w1"), model=""))
        job = store.create("w1", "j", "every 2h", "p")
        default_next = job.next_run_at
        custom = "2031-01-01T00:00:00+00:00"

        async def fake_run(j):
            return JobOutcome(status="ok", summary="done", next_run_at=custom)

        sched = CronScheduler(store=store, run_job=fake_run, bus=EventBus())
        _run(sched._execute(job))
        assert store.get(job.id).next_run_at == custom, "自调度必须赢过 cron 默认"
        assert store.get(job.id).next_run_at != default_next
        assert store.get(job.id).enabled is True
    finally:
        db.close()


def test_T8_3_一次性任务自调度后仍启用(tmp_path):
    """一次性任务若自决"过一会儿再来"，不能被当成跑完就结束。"""
    db = Database(tmp_path / "t.db")
    try:
        store = JobStore(db)
        job = store.create("w1", "once", "2026-10-15T09:00:00+00:00", "p")
        assert job.schedule_type == "once"
        store.mark_success(job, next_run_at="2031-01-01T00:00:00+00:00")
        again = store.get(job.id)
        assert again.enabled is True, "自调度后必须保持启用"
        assert again.next_run_at == "2031-01-01T00:00:00+00:00"
        # 没给覆盖值时，一次性任务仍然按老行为关掉
        store.mark_success(store.get(job.id))
        assert store.get(job.id).enabled is False
    finally:
        db.close()


# --------------------------------------------------------------------------- T8.2
def test_T8_2_通知工具写入待通知内容(tmp_path):
    ctx = _ctx(tmp_path)
    res = _run(NotifyOwnerTool().execute({"text": "依赖挂了，需要人看一眼",
                                          "level": "alert"}, ctx))
    assert res.ok and res.data["notify"] is True
    assert ctx.extra["notify"]["level"] == "alert"
    assert "依赖挂了" in ctx.extra["notify"]["text"]


def test_T8_2_通知需要文本(tmp_path):
    res = _run(NotifyOwnerTool().execute({"text": "  "}, _ctx(tmp_path)))
    assert not res.ok and "不能为空" in res.output


def test_T8_2_条件任务无事则静默(tmp_path):
    """"有事才通知"：conditional 且没调 notify_owner → 不发完成事件。"""
    db = Database(tmp_path / "t.db")
    try:
        store = JobStore(db)
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path=str(tmp_path / "w1"), model=""))
        job = store.create("w1", "每日检查", "every 2h", "p",
                           report_strategy="conditional")
        bus = EventBus()
        events: list = []
        bus.emit = lambda ev: events.append(ev)  # type: ignore[assignment]

        async def quiet_run(j):
            return JobOutcome(status="ok", summary="一切正常，无需打扰", notify=False)

        sched = CronScheduler(store=store, run_job=quiet_run, bus=bus)
        _run(sched._execute(job))
        assert not [e for e in events if e.type == "job.completed"], \
            "conditional 且无事可报时不该发完成事件"
        assert [e for e in events if e.type == "job.quiet"], "应留一条静默记录"
        # 跑动记录仍然要有（控制台可查）
        runs = store.repo.list_runs(job_id=job.id)
        assert runs and runs[0].status == "ok"
        assert "无需打扰" in runs[0].summary
    finally:
        db.close()


def test_T8_2_条件任务有事则用给管理员的话报告(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        store = JobStore(db)
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path=str(tmp_path / "w1"), model=""))
        job = store.create("w1", "每日检查", "every 2h", "p",
                           report_strategy="conditional")
        bus = EventBus()
        events: list = []
        bus.emit = lambda ev: events.append(ev)  # type: ignore[assignment]

        async def noisy_run(j):
            return JobOutcome(status="ok",
                               summary="我读了一堆日志，分析了 30 行，结论如下……",
                               notify=True, notify_text="磁盘只剩 3%，需要清理",
                               notify_level="alert")

        sched = CronScheduler(store=store, run_job=noisy_run, bus=bus)
        _run(sched._execute(job))
        done = [e for e in events if e.type == "job.completed"]
        assert done, "有事必须报告"
        assert done[0].payload["summary"] == "磁盘只剩 3%，需要清理", \
            "报告要用工位写给人的那段话，而不是整段执行输出"
        assert done[0].payload["notify_level"] == "alert"
    finally:
        db.close()


def test_T8_2_brief_任务照旧总是报告(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        store = JobStore(db)
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path=str(tmp_path / "w1"), model=""))
        job = store.create("w1", "普通任务", "every 2h", "p",
                           report_strategy="brief")
        bus = EventBus()
        events: list = []
        bus.emit = lambda ev: events.append(ev)  # type: ignore[assignment]

        async def plain(j):
            return JobOutcome(status="ok", summary="干完了")

        sched = CronScheduler(store=store, run_job=plain, bus=bus)
        _run(sched._execute(job))
        assert [e for e in events if e.type == "job.completed"], "brief 必须报告（老行为不变）"
    finally:
        db.close()


# --------------------------------------------------------------------------- runner 贯通
def test_runner_把工具信号带进_runnerresult(tmp_path):
    """工具写 ctx.extra，PromptRunner 必须把它变成 RunnerResult 的字段——
    这是"工位意图"能到达调度器的唯一通道。"""
    from dogbuddy.agents.core.loop import AgentResult

    captured: dict = {}

    class CaptureLoop:
        def __init__(self, *a, **k):
            pass

        async def run(self, prompt, ctx, on_delta=None):
            # 模拟工位调用 reschedule + notify_owner
            await RescheduleTool().execute({"after": "3h"}, ctx)
            await NotifyOwnerTool().execute({"text": "有事", "level": "warn"}, ctx)
            return AgentResult(content="完成", stop_reason="done")

    import dogbuddy.agents.core.runners as rmod
    orig = rmod.AgentLoop
    rmod.AgentLoop = CaptureLoop
    try:
        runner = PromptRunner()
        db = Database(tmp_path / "t.db")
        w = Window(id="w1", name="w1", role="worker_programmer",
                   workspace_path=str(tmp_path / "ws"), model="")
        from dogbuddy.windows.session import WindowSession
        session = WindowSession(w, db, db, db)
        ctx = _ctx(tmp_path, db=db)
        env = TaskEnv(window=w, session=session, registry=build_registry(),
                      llm_factory=lambda p=None: None, builder=None, ctx=ctx)
        task = SimpleNamespace(prompt="干活", llm_profile="", job_id="j1")
        out = _run(runner.run(task, env))
        captured = out
        db.close()
    finally:
        rmod.AgentLoop = orig

    assert captured.next_run_at, "自调度必须传到 RunnerResult"
    assert captured.notify is True
    assert captured.notify_level == "warn"
    assert captured.notify_text == "有事"


def test_失败的任务不带自调度(tmp_path):
    """任务失败时不该安排下次运行——失败有失败的处理（重试/暂停）。"""
    from dogbuddy.agents.core.loop import AgentResult
    import dogbuddy.agents.core.runners as rmod

    class FailLoop:
        def __init__(self, *a, **k):
            pass

        async def run(self, prompt, ctx, on_delta=None):
            await RescheduleTool().execute({"after": "1h"}, ctx)
            return AgentResult(content="炸了", stop_reason="error")

    orig = rmod.AgentLoop
    rmod.AgentLoop = FailLoop
    try:
        db = Database(tmp_path / "t.db")
        w = Window(id="w1", name="w1", role="worker_programmer",
                   workspace_path=str(tmp_path / "ws"), model="")
        from dogbuddy.windows.session import WindowSession
        env = TaskEnv(window=w, session=WindowSession(w, db, db, db),
                      registry=build_registry(), llm_factory=lambda p=None: None,
                      builder=None, ctx=_ctx(tmp_path, db=db))
        out = _run(PromptRunner().run(SimpleNamespace(prompt="干活", llm_profile="",
                                                      job_id="j1"), env))
        db.close()
    finally:
        rmod.AgentLoop = orig
    assert out.status == "failed"
    assert out.next_run_at is None, "失败不能带自调度"
