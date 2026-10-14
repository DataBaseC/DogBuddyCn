# DogBuddyCn — 审批逐级 + 闭环（审查报告 P1-6）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""审查报告 P1-6：「审批逐级没有闭环：配了主管反而更差」。

`SupervisionService.approval_target` 在有主管时返回 `target="supervisor"`；
`loop.py` 收到后只给主管工位投一条文本 + 写审计，然后 `return failure`。
**没有任何机制把这条审批回传**：不落 `pending_decisions`，主管也没有任何工具
能批准这条待批命令。于是：

| 配置 | 用户触发危险命令时 |
|---|---|
| 无主管（v1） | `ask_user` → 用户批准 → 重放执行 |
| **有主管（Batch 9 默认）** | 主管收到一条文本，**永远没有批准通道** → 永久卡死 |

安全上是 fail-closed（没放行），功能上是死路。这些测试锁住闭环。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.loop import AgentLoop  # noqa: E402
from dogbuddy.agents.core.scope import Scope  # noqa: E402
from dogbuddy.agents.management.tools import AdminServices, build_admin_tools  # noqa: E402
from dogbuddy.db import Database, Window  # noqa: E402
from dogbuddy.ports import Project  # noqa: E402
from dogbuddy.services.decisions import DecisionService  # noqa: E402
from dogbuddy.services.supervision_service import SupervisionService  # noqa: E402
from dogbuddy.tools.registry import ToolContext, ToolResult  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _RecordingRegistry:
    """不真的执行工具，只记录"有没有重放这次调用"。"""

    def __init__(self) -> None:
        self.replays: list[tuple[str, dict, bool]] = []

    async def execute(self, name, args, ctx, approved=False):
        self.replays.append((name, dict(args), approved))
        return ToolResult.success("executed")


class _FakeWindows:
    def __init__(self) -> None:
        self.dispatched: list[tuple[str, str]] = []

    def dispatch(self, window_id, text, urgent=False) -> None:
        self.dispatched.append((window_id, text))


def _setup(tmp_path, with_supervisor=True):
    db = Database(tmp_path / "t.db")
    db.insert_project(Project(id="p1", name="P1", root_path=str(tmp_path / "p1")))
    db.insert_window(Window(id="w1", name="员工一", role="worker_programmer",
                            workspace_path=str(tmp_path / "w1"), model="",
                            mode="BUILD", project_id="p1"))
    if with_supervisor:
        db.insert_window(Window(id="sup1", name="主管一", role="supervisor",
                                workspace_path=str(tmp_path / "sup1"), model="",
                                mode="BUILD", project_id="p1"))
        db.update_project("p1", owner_supervisor_id="sup1")
    decisions = DecisionService(db, poll_interval=0.01)
    windows = _FakeWindows()
    sup = SupervisionService(db=db, windows=windows, bus=None, route_reports=True)
    return db, decisions, sup, windows


def _ctx(decisions, sup, window_id="w1"):
    return ToolContext(
        window_id=window_id, workspace=".", mode="BUILD", actor="worker",
        scope=Scope(level="none", read_project_ids=frozenset({"p1"}), actor=window_id),
        extra={"approval_router": sup.approval_target,
               "supervision_dispatch": sup.dispatch_text,
               "decisions": decisions})


def _dangerous(tc_name="shell"):
    tc = SimpleNamespace(name=tc_name, arguments={"command": "rm -rf build/"})
    return tc, ToolResult.confirm("dangerous command (recursive delete)", irreversible=False)


def _loop(registry):
    return AgentLoop(llm=None, registry=registry, session=SimpleNamespace())


# ---------------------------------------------------------------- 落队列
def test_升级给主管会落_pending_decisions(tmp_path):
    """★ 初版只投一条文本——主管手上没有任何东西可以"批准"。"""
    db, decisions, sup, windows = _setup(tmp_path)
    try:
        ctx = _ctx(decisions, sup)
        registry = _RecordingRegistry()
        tc, tres = _dangerous()

        async def scenario():
            async def answer_soon():
                for _ in range(200):
                    pending = decisions.list(status="pending", limit=10)
                    if pending:
                        decisions.answer(pending[0].id, "批准")
                        return
                    await asyncio.sleep(0.01)

            task = asyncio.ensure_future(answer_soon())
            res = await _loop(registry)._approval_flow(tc, tres, ctx)
            await task
            return res

        res = _run(scenario())

        rows = db.list_pending_decisions(status=None, limit=10)
        assert rows, "升级给主管必须落 pending_decisions（否则没有回传通道）"
        row = rows[0]
        assert row.window_id == "sup1", "待决事项的收件人应该是主管工位"
        assert row.source == "escalated_to_supervisor"
        assert "w1" in row.question, "问题里要写清是哪个工位在申请"
        # 主管被通知到了
        assert windows.dispatched and windows.dispatched[0][0] == "sup1"
        # 批准 → 真的重放了
        assert registry.replays == [("shell", {"command": "rm -rf build/"}, True)]
        assert res.ok is True
    finally:
        db.close()


def test_升级通知里带待决事项编号(tmp_path):
    """主管的 Agent 需要拿到 id 才能调用 approve_decision。"""
    db, decisions, sup, windows = _setup(tmp_path)
    try:
        ctx = _ctx(decisions, sup)
        tc, tres = _dangerous()

        async def scenario():
            async def answer_soon():
                for _ in range(200):
                    pending = decisions.list(status="pending", limit=10)
                    if pending:
                        decisions.answer(pending[0].id, "拒绝")
                        return
                    await asyncio.sleep(0.01)

            task = asyncio.ensure_future(answer_soon())
            await _loop(_RecordingRegistry())._approval_flow(tc, tres, ctx)
            await task

        _run(scenario())
        text = windows.dispatched[0][1]
        assert "待决事项 #" in text
        assert "approve_decision" in text
    finally:
        db.close()


# ---------------------------------------------------------------- 裁决生效
def test_主管拒绝则不重放(tmp_path):
    db, decisions, sup, windows = _setup(tmp_path)
    try:
        ctx = _ctx(decisions, sup)
        registry = _RecordingRegistry()
        tc, tres = _dangerous()

        async def scenario():
            async def answer_soon():
                for _ in range(200):
                    pending = decisions.list(status="pending", limit=10)
                    if pending:
                        decisions.answer(pending[0].id, "拒绝")
                        return
                    await asyncio.sleep(0.01)

            task = asyncio.ensure_future(answer_soon())
            res = await _loop(registry)._approval_flow(tc, tres, ctx)
            await task
            return res

        res = _run(scenario())
        assert registry.replays == [], "主管拒绝后绝不能执行"
        assert res.ok is False and "拒绝" in res.output
    finally:
        db.close()


def test_超时未裁决必须_fail_closed(tmp_path):
    """没答上来 ≠ 批准。初版"只提请不执行"在这一点上是对的，不能改坏。"""
    db, decisions, sup, windows = _setup(tmp_path)
    try:
        decisions.default_timeout = 0.05  # 立刻超时
        ctx = _ctx(decisions, sup)
        registry = _RecordingRegistry()
        tc, tres = _dangerous()
        res = _run(_loop(registry)._approval_flow(tc, tres, ctx))
        assert registry.replays == [], "超时绝不能放行"
        assert res.ok is False
    finally:
        db.close()


def test_没有主管时仍走_ask_user_这条老路(tmp_path):
    """零回归：项目没主管 → 路由给管理员，不走逐级。"""
    db, decisions, sup, windows = _setup(tmp_path, with_supervisor=False)
    try:
        ctx = _ctx(decisions, sup)
        called: list[str] = []

        async def ask(question, options=None):
            called.append(question)
            return "批准"

        ctx.ask_user_fn = ask
        registry = _RecordingRegistry()
        tc, tres = _dangerous()
        res = _run(_loop(registry)._approval_flow(tc, tres, ctx))

        assert called, "无主管时应退回 ask_user（v1 行为）"
        assert registry.replays and registry.replays[0][2] is True
        assert res.ok is True
        assert not windows.dispatched
    finally:
        db.close()


# ---------------------------------------------------------------- 主管的工具
def test_主管的_approve_decision_工具能裁决(tmp_path):
    """初版主管**没有任何工具**能批准另一工位的待批命令。"""
    db, decisions, sup, windows = _setup(tmp_path)
    try:
        d = decisions.ask("sup1", "【待你裁决 · 来自工位 w1】工具 shell",
                          options=["批准", "拒绝"], source="escalated_to_supervisor",
                          supersede=False)
        tools = {t.name: t for t in build_admin_tools(AdminServices())}
        tool = tools["approve_decision"]
        ctx = ToolContext(window_id="sup1", workspace=".", mode="BUILD", db=db,
                          scope=Scope(level="project", project_ids=frozenset({"p1"}),
                                      actor="sup1"),
                          extra={"decisions": decisions})

        async def run_tool():
            return await tool.execute({"decision_id": d.id, "approve": True}, ctx)

        res = _run(run_tool())
        assert res.ok, res.output
        row = db.get_pending_decision(d.id)
        assert row.status == "answered" and row.answer == "批准"
    finally:
        db.close()


def test_主管不能裁决别人的待决事项(tmp_path):
    db, decisions, sup, windows = _setup(tmp_path)
    try:
        db.insert_window(Window(id="sup2", name="主管二", role="supervisor",
                                workspace_path=str(tmp_path / "sup2"), model="",
                                mode="BUILD", project_id="p2"))
        d = decisions.ask("sup2", "别人的问题", supersede=False)
        tools = {t.name: t for t in build_admin_tools(AdminServices())}
        ctx = ToolContext(window_id="sup1", workspace=".", mode="BUILD", db=db,
                          scope=Scope(level="project", project_ids=frozenset({"p1"}),
                                      actor="sup1"),
                          extra={"decisions": decisions})

        async def run_tool():
            return await tools["approve_decision"].execute(
                {"decision_id": d.id, "approve": True}, ctx)

        res = _run(run_tool())
        assert res.ok is False and "无权裁决" in res.output
        assert db.get_pending_decision(d.id).status == "pending", "不能被别人裁决掉"
    finally:
        db.close()


def test_管理员可以裁决任意待决事项(tmp_path):
    """system 作用域是升级链的终点，不受"只批自己的"限制。"""
    db, decisions, sup, windows = _setup(tmp_path)
    try:
        d = decisions.ask("w1", "员工的问题", supersede=False)
        tools = {t.name: t for t in build_admin_tools(AdminServices())}
        ctx = ToolContext(window_id="admin:default", workspace=".", mode="BUILD", db=db,
                          scope=Scope(level="system", actor="admin:default"),
                          extra={"decisions": decisions})

        async def run_tool():
            return await tools["approve_decision"].execute(
                {"decision_id": d.id, "approve": True}, ctx)

        assert _run(run_tool()).ok is True
        assert db.get_pending_decision(d.id).status == "answered"
    finally:
        db.close()


def test_升级不取消主管自己正在问的问题(tmp_path):
    """`supersede=False`：两件事只是同一个收件人，不是"又问了一遍"。"""
    db, decisions, sup, windows = _setup(tmp_path)
    try:
        own = decisions.ask("sup1", "主管自己的问题")
        esc = decisions.ask("sup1", "员工升级的审批", source="escalated_to_supervisor",
                            supersede=False)
        assert db.get_pending_decision(own.id).status == "pending", \
            "升级不该把主管自己的提问顶掉"
        assert db.get_pending_decision(esc.id).status == "pending"
    finally:
        db.close()
