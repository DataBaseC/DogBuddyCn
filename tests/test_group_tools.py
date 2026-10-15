# DogBuddyCn — 群组工具回归（v0.10.20，圆桌测试暴露的缺口）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""圆桌测试（2026-10-15）暴露：管理员**没有任何 group 工具**。

owner 让管理员"建个群组让他们自己聊"，管理员在对话里明说只能退化成
"共享工作区 + 多窗口圆桌"土办法 → DB 里 0 个群组 → 前端群组页永远空白，
工人之间的沟通也散在各窗口会话和工作区文件里，没有群频道。

这些测试钉住四件事：
1. 管理员注册表里**有** group_create/group_list/group_assign/group_channel_post，
   且与 `roles.ADMIN_TOOLS` 一致（不变量测试另有一条全等断言）；
2. 端到端：建组 → 拉 3 个工位 → 发一条频道消息，DB 里 groups/group_members/
   messages.group_id 都如实落账；
3. 窗口 assign 进组后，它的会话消息自动带 group_id 进共享频道（T3.1 机制）；
4. 作用域 fail-closed：主管只能动自己项目里的工位/群组；员工（none）全拒；
   管理员（system）全通。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.permissions import PermissionGate  # noqa: E402
from dogbuddy.agents.core.roles import ADMIN_TOOLS  # noqa: E402
from dogbuddy.agents.core.scope import NONE, SYSTEM, Scope  # noqa: E402
from dogbuddy.agents.management.tools import (  # noqa: E402
    PROJECT_TOOLS, AdminServices, build_management_tools)
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.ports import Project, Window  # noqa: E402
from dogbuddy.services.group_manager import GroupManager  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402

GROUP_TOOLS = ["group_create", "group_list", "group_assign", "group_channel_post"]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _stack(tmp_path):
    """真实 DB + 真实 WindowManager + 真实 GroupManager 的最小栈。"""
    db = Database(str(tmp_path / "t.db"))
    bus = EventBus()
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=bus,
                       checkpoints_dir=tmp_path / "ckpt")
    return db, wm, GroupManager(db, wm, bus=bus)


def _ctx(window_id="admin", scope=None, db=None) -> ToolContext:
    return ToolContext(window_id=window_id, workspace=".", mode="AUTO",
                       actor="admin", db=db, scope=scope)


# ------------------------------------------------------------ 声明与登记
def test_group_tools_registered_and_whitelisted():
    svc = AdminServices()  # 全 None：构造期不许碰服务（调用期才 need()）
    names = {t.name for t in build_management_tools(svc)}
    for n in GROUP_TOOLS:
        assert n in names, f"管理工具包里没有 {n}——模型根本看不到它"
        assert n in ADMIN_TOOLS, f"ADMIN_TOOLS 漏登记 {n}（AgentLoop 不会展示它）"
        assert n in PROJECT_TOOLS, f"{n} 不在 PROJECT_TOOLS——主管也用不了群组"


def test_group_tools_are_project_scoped():
    """project 级：主管可编排自己项目里的群组；运行时 gate 按归属 fail-closed。"""
    svc = AdminServices()
    tools = {t.name: t for t in build_management_tools(svc)}
    for n in GROUP_TOOLS:
        assert getattr(tools[n], "required_scope", "") == "project"


# ------------------------------------------------------------ 端到端
def test_group_tools_end_to_end(tmp_path):
    db, wm, groups = _stack(tmp_path)
    svc = AdminServices(groups=groups)
    tools = {t.name: t for t in build_management_tools(svc)}
    ctx = _ctx(scope=SYSTEM, db=db)
    w1 = wm.create(name="qa-env", workspace_path=str(tmp_path / "ws1"))
    w2 = wm.create(name="qa-tools", workspace_path=str(tmp_path / "ws2"))
    w3 = wm.create(name="qa-ops", workspace_path=str(tmp_path / "ws3"))

    r = _run(tools["group_create"].execute(
        {"name": "圆桌测试", "description": "3 工位圆桌"}, ctx))
    assert r.ok, r.output
    gid = r.data["group_id"]

    for w in (w1, w2, w3):
        r = _run(tools["group_assign"].execute(
            {"group_id": gid, "window": w.id}, ctx))
        assert r.ok, r.output
    # 窗口绑定频道（T3.1）：之后的会话消息自动进群频道
    assert db.get_window(w1.id).group_id == gid

    r = _run(tools["group_channel_post"].execute(
        {"group_id": gid, "text": "R1 结论：调度层是最大短板"}, ctx))
    assert r.ok, r.output

    # DB 如实落账（owner 的验收口径）
    assert db._conn.execute("select count(*) from groups").fetchone()[0] == 1
    assert db._conn.execute("select count(*) from group_members").fetchone()[0] == 3
    chan = db._conn.execute(
        "select count(*) from messages where group_id=?", (gid,)).fetchone()[0]
    assert chan == 1

    r = _run(tools["group_list"].execute({}, ctx))
    assert r.ok and "圆桌测试" in r.output and "qa-env" in r.output

    # 频道内容前端可见（routes/groups.py 的 channel DTO 同源）
    msgs = groups.channel(gid)
    assert len(msgs) == 1 and "调度层" in msgs[0]["content"]
    db.close()


def test_group_post_empty_content_rejected(tmp_path):
    db, wm, groups = _stack(tmp_path)
    svc = AdminServices(groups=groups)
    tools = {t.name: t for t in build_management_tools(svc)}
    ctx = _ctx(scope=SYSTEM, db=db)
    g = groups.create("g-empty")
    r = _run(tools["group_channel_post"].execute(
        {"group_id": g.id, "text": "   "}, ctx))
    assert r.ok is False and "空" in r.output
    db.close()


def test_group_assign_unknown_window_fails_clearly(tmp_path):
    db, wm, groups = _stack(tmp_path)
    svc = AdminServices(groups=groups)
    tools = {t.name: t for t in build_management_tools(svc)}
    ctx = _ctx(scope=SYSTEM, db=db)
    g = groups.create("g-nowin")
    r = _run(tools["group_assign"].execute(
        {"group_id": g.id, "window": "no-such-window"}, ctx))
    assert r.ok is False and "window not found" in r.output
    db.close()


def test_group_tools_need_service_wired():
    """服务没注入时给清晰失败，而不是 AttributeError 炸掉整轮。"""
    svc = AdminServices()  # groups=None
    tools = {t.name: t for t in build_management_tools(svc)}
    ctx = _ctx(scope=SYSTEM)
    r = _run(tools["group_create"].execute({"name": "x"}, ctx))
    assert r.ok is False and "groups" in r.output


# ------------------------------------------------------------ 作用域
def _two_project_db(tmp_path):
    db = Database(str(tmp_path / "scope.db"))
    p1 = Project(id="p1", name="P1", root_path=str(tmp_path / "p1"))
    p2 = Project(id="p2", name="P2", root_path=str(tmp_path / "p2"))
    db.insert_project(p1)
    db.insert_project(p2)
    for wid, pid in (("w1", "p1"), ("w2", "p2")):
        db.insert_window(Window(id=wid, name=wid, role="worker_programmer",
                                workspace_path="/tmp", model="", mode="BUILD",
                                project_id=pid))
    g = db.insert_group(__import__("dogbuddy.ports", fromlist=["Group"]).Group(
        id="g1", name="g1", project_id="p1"))
    return db


def test_supervisor_scope_group_assign_fail_closed(tmp_path):
    """主管（project p1）把 p2 的工位拉进群 → 拒；p1 的工位 → 放。"""
    db = _two_project_db(tmp_path)
    gate = PermissionGate(scope_resolver=None)
    sup = Scope(level="project", project_ids=frozenset({"p1"}), actor="sup1")
    ctx = _ctx(window_id="sup1", scope=sup, db=db)

    denied = gate.check_scope("group_assign", {"group_id": "g1", "window": "w2"},
                              ctx, "project")
    assert denied is not None and denied.allowed is False, "跨项目工位必须拒"

    allowed = gate.check_scope("group_assign", {"group_id": "g1", "window": "w1"},
                               ctx, "project")
    assert allowed is None, f"本项目工位应放行：{allowed}"

    # 无归属群组（project_id 为空）对主管同样 fail-closed
    denied2 = gate.check_scope("group_channel_post", {"group_id": "gx", "text": "hi"},
                               ctx, "project")
    assert denied2 is not None and denied2.allowed is False
    db.close()


def test_none_scope_and_admin_scope(tmp_path):
    db = _two_project_db(tmp_path)
    gate = PermissionGate()
    ctx_none = _ctx(scope=NONE, db=db)
    ctx_sys = _ctx(scope=SYSTEM, db=db)
    d = gate.check_scope("group_create", {"name": "x"}, ctx_none, "project")
    assert d is not None and d.allowed is False, "员工（none）不能建群"
    assert gate.check_scope("group_create", {"name": "x"}, ctx_sys, "system") is None
    assert gate.check_scope("group_assign", {"group_id": "g1", "window": "w2"},
                            ctx_sys, "system") is None, "管理员跨项目也放行"
    db.close()


def test_group_tools_through_registry_full_chain(tmp_path):
    """★ 走 ToolRegistry.execute() 全链路（审查报告 v1 变异那条路的群组版）：
    required_scope 转发一旦断了，员工作用域下必须还能拒。"""
    db, wm, groups = _stack(tmp_path)
    from dogbuddy.tools.registry import ToolRegistry
    reg = ToolRegistry(gate=PermissionGate(), audit=db)
    for t in build_management_tools(AdminServices(groups=groups)):
        reg.register(t)

    res = _run(reg.execute("group_create", {"name": "nope"}, _ctx(scope=NONE, db=db)))
    assert res.ok is False and res.data.get("policy_denied") is True

    res = _run(reg.execute("group_create", {"name": "ok"}, _ctx(scope=SYSTEM, db=db)))
    assert res.ok, f"管理员应放行：{res.output}"
    db.close()
