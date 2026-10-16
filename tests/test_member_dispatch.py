# DogBuddyCn — 组长派活给组员（子代理）回归（v0.10.33）
# Copyright (C) 2026 DogBuddy contributors — AGPL-3.0
"""owner 实测："组长没给组员派活，按理说组长可以让组员成为他的子代理进行工作。"

两处缺口：
1. 员工工具箱里**没有**"派活给别的工位"的工具（`explore_subagent` 只是进程内只读
   研究助手，不是组员工位）；
2. 群组运行把组长**排在最后**（`order = 其他成员 + [组长]`）→ 组长那轮只能汇总。

修法：新增 `member_dispatch`（组长 → 组员，可等结果）+ 组长开场编排轮。
安全口径全部在工具内部 fail-closed 裁决（员工作用域是 none，用 `required_scope`
会把组长自己也挡住）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.services.group_manager import GroupManager  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.tools.member_ops import MemberDispatchTool  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402


class FakeCoordinator:
    """记录派活的假调度器（真实现是 WindowManager）。"""

    def __init__(self, status="ok", summary="组员产出：做完了 X"):
        self.calls: list[tuple] = []
        self.dispatched: list[tuple] = []
        self.status = status
        self.summary = summary

    def dispatch(self, window_id, prompt):
        self.dispatched.append((window_id, prompt))

    async def dispatch_and_wait(self, window_id, prompt, coordinated=False, timeout=None):
        self.calls.append((window_id, prompt, coordinated, timeout))
        from dogbuddy.cron.scheduler import JobOutcome
        return JobOutcome(status=self.status, summary=self.summary)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _stack(tmp_path):
    db = Database(str(tmp_path / "g.db"))
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=None,
                       checkpoints_dir=tmp_path / "ckpt")
    ws = tmp_path / "ws"
    ws.mkdir()
    for wid, name in (("lead", "worker-1"), ("m2", "worker-2"), ("m3", "worker-3"),
                      ("out", "outsider")):
        db.insert_window(Window(id=wid, name=name, role="worker_programmer", mode="BUILD",
                                workspace_path=str(ws), model=""))
    gm = GroupManager(db, wm, bus=None)
    g = gm.create("组")
    gm.assign(g.id, "lead", role="lead")
    gm.assign(g.id, "m2")
    gm.assign(g.id, "m3")
    return db, gm, g


def _ctx(gid, window_id="lead", is_lead=True, dispatched=None):
    return ToolContext(window_id=window_id, workspace=".", mode="BUILD", actor="worker",
                       db=None, extra={"group_id": gid, "is_lead": is_lead,
                                       "dispatched_windows": dispatched if dispatched is not None else []})


def test_组长可以派活并拿回结果(tmp_path):
    db, gm, g = _stack(tmp_path)
    coord = FakeCoordinator()
    tool = MemberDispatchTool(coord)
    ctx = _ctx(g.id)
    ctx.db = db
    res = _run(tool.execute({"window": "worker-2", "task": "做风格 A"}, ctx))
    assert res.ok, res.output
    assert "worker-2" in res.output and "做完了 X" in res.output, res.output
    assert coord.calls and coord.calls[0][0] == "m2"
    assert coord.calls[0][2] is True, "组内派活要标 coordinated（免得限速自动续跑重复执行）"
    assert ctx.extra["dispatched_windows"] == ["m2"], "要让接力轮知道该成员已被派过活"
    db.close()


def test_不等结果只排队(tmp_path):
    db, gm, g = _stack(tmp_path)
    coord = FakeCoordinator()
    tool = MemberDispatchTool(coord)
    ctx = _ctx(g.id)
    ctx.db = db
    res = _run(tool.execute({"window": "worker-3", "task": "做风格 B", "wait": False}, ctx))
    assert res.ok and "不等结果" in res.output
    assert coord.dispatched == [("m3", "做风格 B")] and coord.calls == []
    db.close()


def test_组员不能派活给别的组员(tmp_path):
    db, gm, g = _stack(tmp_path)
    tool = MemberDispatchTool(FakeCoordinator())
    ctx = _ctx(g.id, window_id="m2", is_lead=False)
    ctx.db = db
    res = _run(tool.execute({"window": "worker-3", "task": "x"}, ctx))
    assert not res.ok and "只有组长" in res.output, res.output
    db.close()


def test_不在群组运行里不能派活(tmp_path):
    db, gm, g = _stack(tmp_path)
    tool = MemberDispatchTool(FakeCoordinator())
    ctx = ToolContext(window_id="lead", workspace=".", mode="BUILD", actor="worker",
                      db=db, extra={})
    res = _run(tool.execute({"window": "worker-2", "task": "x"}, ctx))
    assert not res.ok and "群组" in res.output, res.output
    db.close()


def test_不能派给群外工位_fail_closed(tmp_path):
    db, gm, g = _stack(tmp_path)
    tool = MemberDispatchTool(FakeCoordinator())
    ctx = _ctx(g.id)
    ctx.db = db
    res = _run(tool.execute({"window": "outsider", "task": "x"}, ctx))
    assert not res.ok and "不在本群组" in res.output, res.output
    db.close()


def test_不能给自己派活(tmp_path):
    db, gm, g = _stack(tmp_path)
    tool = MemberDispatchTool(FakeCoordinator())
    ctx = _ctx(g.id)
    ctx.db = db
    res = _run(tool.execute({"window": "worker-1", "task": "x"}, ctx))
    assert not res.ok and "给自己" in res.output, res.output
    db.close()


def test_限速暂停的结果如实回报(tmp_path):
    db, gm, g = _stack(tmp_path)
    tool = MemberDispatchTool(FakeCoordinator(status="rate_limited", summary="限速暂停"))
    ctx = _ctx(g.id)
    ctx.db = db
    res = _run(tool.execute({"window": "worker-2", "task": "x"}, ctx))
    assert res.ok and "限速暂停" in res.output, res.output
    db.close()


def test_群组运行把组长身份与群组上下文交给工具(tmp_path, monkeypatch):
    """集成点：**群组运行**必须把 group_id / is_lead / dispatched_windows 放进 ToolContext。

    否则工具在真实运行时永远报"不在群组里"—— 单元测试全绿但功能是死的
    （v0.10.29/0.10.30 就是两次同类装配漏洞）。
    """
    import dogbuddy.agents.core.loop as loop_mod
    from dogbuddy.agents.core.loop import AgentResult

    db, gm, g = _stack(tmp_path)
    coord = FakeCoordinator()
    seen: list[dict] = []

    class CaptureLoop:
        def __init__(self, *a, **k):
            pass

        async def run(self, prompt, ctx):
            seen.append(dict(ctx.extra))
            # 用产品自己的"已派过活"清单当守卫：它在各步之间是**共享的同一个 list**
            if ctx.extra.get("is_lead") and not ctx.extra.get("dispatched_windows"):
                tool = MemberDispatchTool(coord)
                res = await tool.execute({"window": "worker-2", "task": "分给你做 A"}, ctx)
                assert res.ok, f"组长在群组运行里派活失败：{res.output}"
            return AgentResult(content="ok", stop_reason="done")

    monkeypatch.setattr(loop_mod, "AgentLoop", CaptureLoop)
    out = _run(gm.run(g.id, "做一个站"))
    # 3 名成员（lead + m2 + m3）→ 组长开场 + m2 跳过 + m3 执行 + 组长收尾 = 4 步
    assert len(out["steps"]) == 4, out["steps"]
    assert [s.get("phase") for s in out["steps"]] == ["plan", "skipped", "work", "summary"]
    assert seen[0]["group_id"] == g.id and seen[0]["is_lead"] is True
    assert seen[0]["group_lead_id"] == "lead"
    # 组长派过活之后，"worker-2"的接力轮被跳过（避免同一件事做两遍）
    assert out["steps"][1].get("skipped"), out["steps"][1]
    assert [c[0] for c in coord.calls] == ["m2"]
    db.close()


def test_群组运行时成员状态翻成running再翻回来(tmp_path, monkeypatch):
    """v0.10.34（owner："你在哪跑呢，我咋没看见"）：群组运行必须**可见**。

    以前群组运行不碰工位状态：控制台工位页一直是 idle，群组页只有"点按钮的人"
    能看到转圈，用 API/脚本触发的运行完全不可见 —— owner 因此以为任务卡住了。
    """
    import dogbuddy.agents.core.loop as loop_mod
    from dogbuddy.agents.core.loop import AgentResult

    db, gm, g = _stack(tmp_path)
    seen_status: list[tuple[str, str]] = []

    class CaptureLoop:
        def __init__(self, *a, **k):
            pass

        async def run(self, prompt, ctx):
            seen_status.append((ctx.window_id, db.get_window(ctx.window_id).status))
            return AgentResult(content="ok", stop_reason="done")

    monkeypatch.setattr(loop_mod, "AgentLoop", CaptureLoop)
    _run(gm.run(g.id, "任务"))
    assert seen_status, "至少要跑一轮"
    assert all(st == "running" for _, st in seen_status), \
        f"运行中必须是 running（控制台才看得见）：{seen_status}"
    # 全部跑完后回到 idle（谁都不许一直挂着"运行中"）
    for wid in ("lead", "m2", "m3"):
        assert db.get_window(wid).status == "idle", wid
    # 审计里能看到"开跑"（进行中的运行以前在审计里查不到）
    rows = [dict(r) for r in db._conn.execute(
        "SELECT action, detail FROM audit_log WHERE action='group:run_start'")]
    assert rows and "members=" in rows[0]["detail"], rows
    db.close()
