# DogBuddyCn — Batch 9：三层指挥链（T9.1-T9.6）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""ROADMAP Batch 9 的验收标准：
  * 主管能派活给本项目员工并收回报
  * **主管改他人工位必须失败**（越权专项测试）
  * 管理员功能零回归
  * 无主管场景退化直连正常

核心不变量：作用域**由角色推导、不接受自报**，且**fail-closed**
（未声明/判不出来/越界 → 一律拒绝）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.permissions import PermissionGate  # noqa: E402
from dogbuddy.agents.core.roles import (  # noqa: E402
    ADMIN_TOOLS, SUPERVISOR_TOOLS, WORKER_PROGRAMMER_TOOLS, get_role, list_roles)
from dogbuddy.agents.core.scope import NONE, SYSTEM, Scope, ScopeResolver  # noqa: E402
from dogbuddy.db import SCHEMA_VERSION, Database  # noqa: E402
from dogbuddy.events.bus import WORKER_QUESTION, Event, EventBus  # noqa: E402
from dogbuddy.ports import Project, Window  # noqa: E402
from dogbuddy.services.supervision_service import (  # noqa: E402
    SUPERVISOR_COMMAND, SUPERVISOR_DIGEST, WORKER_REPORT, SupervisionService)
from dogbuddy.tools.registry import ToolContext  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _setup(tmp_path, with_supervisor=True):
    """两个项目：p1（有主管 sup1 + 员工 w1）+ p2（员工 w9），用来验证越权。"""
    db = Database(tmp_path / "t.db")
    db.insert_project(Project(id="p1", name="P1", root_path=str(tmp_path / "p1")))
    db.insert_project(Project(id="p2", name="P2", root_path=str(tmp_path / "p2")))
    db.insert_window(Window(id="w1", name="员工一", role="worker_programmer",
                            workspace_path=str(tmp_path / "w1"), model="", project_id="p1"))
    db.insert_window(Window(id="w9", name="别人家的员工", role="worker_programmer",
                            workspace_path=str(tmp_path / "w9"), model="", project_id="p2"))
    if with_supervisor:
        db.insert_window(Window(id="sup1", name="主管一", role="supervisor",
                                workspace_path=str(tmp_path / "sup1"), model="",
                                project_id="p1"))
        db.update_project("p1", owner_supervisor_id="sup1")
    return db


def _ctx(db, window_id, scope) -> ToolContext:
    return ToolContext(window_id=window_id, workspace=".", mode="BUILD", db=db,
                       scope=scope, extra={})


# --------------------------------------------------------------------------- T9.1 底座
def test_迁移_v8_加主管字段与_admins_表(tmp_path):
    assert SCHEMA_VERSION >= 8
    p = tmp_path / "a.db"
    db = Database(p)
    try:
        cols = {r[1] for r in db._conn.execute("PRAGMA table_info(projects)")}
        assert "owner_supervisor_id" in cols
        acols = {r[1] for r in db._conn.execute("PRAGMA table_info(admins)")}
        assert {"id", "name", "role_level", "token_hash", "enabled"} <= acols
    finally:
        db.close()
    db2 = Database(p)
    try:
        assert db2.schema_version == SCHEMA_VERSION   # 幂等
    finally:
        db2.close()


def test_T9_1_scope_由角色推导不被自报(tmp_path):
    db = _setup(tmp_path)
    try:
        r = ScopeResolver(db)
        sup = r.for_window(db.get_window("sup1"))
        assert sup.level == "project" and sup.project_ids == frozenset({"p1"})
        worker = r.for_window(db.get_window("w1"))
        assert worker.level == "none", "员工没有管理作用域"
        assert r.for_admin("bob").is_system
        # 主管换到别的项目（没 owner 字段）也认得出来：按项目里的 supervisor 兜底
        db.update_project("p1", owner_supervisor_id="")
        assert r.for_window(db.get_window("sup1")).project_ids == frozenset({"p1"})
    finally:
        db.close()


def test_T9_1_作用域值对象语义():
    sup = Scope(level="project", project_ids=frozenset({"p1", "p2"}))
    assert sup.allows_project("p1") and not sup.allows_project("p9")
    assert not sup.allows_project(""), "空 project_id 不能算放行（fail-closed）"
    assert SYSTEM.allows_project("任意项目")
    assert not NONE.allows_project("p1")
    assert sup.covers(Scope(level="project", project_ids=frozenset({"p1"})))
    assert not sup.covers(Scope(level="project", project_ids=frozenset({"p9"})))
    assert "project(" in sup.describe() and "system" in SYSTEM.describe()


# --------------------------------------------------------------------------- T9.2 工具统一化
def test_T9_2_管理工具带_required_scope():
    from dogbuddy.agents.management.tools import (
        PROJECT_TOOLS, SYSTEM_TOOL_NAMES, build_management_tools, build_supervisor_tools)
    from dogbuddy.agents.admin.tools import AdminServices, build_admin_tools
    svc = AdminServices()
    all_tools = build_management_tools(svc)
    names = {t.name for t in all_tools}
    assert names == {t.name for t in build_admin_tools(svc)}, "工具名一个都不能变"

    scope_of = {t.name: t.required_scope for t in all_tools}
    for n in PROJECT_TOOLS:
        assert scope_of[n] == "project", n
    for n in SYSTEM_TOOL_NAMES:
        assert scope_of[n] == "system", n
    assert {"config_set", "self_update"} <= SYSTEM_TOOL_NAMES
    assert {"window_create", "task_create", "window_status"} <= PROJECT_TOOLS

    sup = {t.name for t in build_supervisor_tools(svc)}
    assert not (sup & SYSTEM_TOOL_NAMES), f"主管工具集绝不能含 system 级：{sup & SYSTEM_TOOL_NAMES}"
    assert "window_create" in sup and "self_update" not in sup


def test_T9_2_未归类的新管理工具默认不下放主管():
    """fail-closed：新加的管理工具默认按 system 处理，必须显式列入 PROJECT_TOOLS。"""
    from dogbuddy.agents.management.tools import annotate_scopes

    class Fake:
        def __init__(self, name):
            self.name = name

    tools = annotate_scopes([Fake("brand_new_admin_tool"), Fake("window_list")])
    assert tools[0].required_scope == "system"
    assert tools[1].required_scope == "project"


def test_T9_2_管理员行为零回归():
    """管理员 = 全部管理工具 + system 作用域，工具集合与 v1 完全一致。"""
    assert len(ADMIN_TOOLS) == 32, "管理员工具数不该变（零回归）"
    from dogbuddy.agents.admin.tools import AdminServices, build_admin_tools
    assert set(ADMIN_TOOLS) == {t.name for t in build_admin_tools(AdminServices())}


# --------------------------------------------------------------------------- T9.3 主管装配
def test_T9_3_主管工具集_员工工具加项目级管理():
    spec = get_role("supervisor")
    assert spec.scope_level == "project"
    assert {"read_file", "shell", "write_file"} <= set(spec.tool_names), "主管要能亲自下场"
    assert {"window_create", "task_create", "window_status"} <= set(spec.tool_names)
    assert not (set(spec.tool_names) & {"config_set", "profile_save", "self_update",
                                        "memory_write"}), "主管绝不能有系统级工具"
    assert set(WORKER_PROGRAMMER_TOOLS) <= set(SUPERVISOR_TOOLS)
    assert len(list_roles()) >= 3


# --------------------------------------------------------------------------- 越权专项（核心验收）
def test_主管改他人工位必须失败(tmp_path):
    """**ROADMAP 明确要求的越权专项测试。**"""
    db = _setup(tmp_path)
    try:
        gate = PermissionGate(scope_resolver=ScopeResolver(db))
        sup_scope = ScopeResolver(db).for_window(db.get_window("sup1"))
        ctx = _ctx(db, "sup1", sup_scope)

        # 自己项目的工位 → 允许
        ok = gate.check("window_update", "write", {"window_id": "w1"}, ctx,
                        required_scope="project")
        assert ok.allowed, ok.reason

        # 别人项目的工位 → 必须失败
        denied = gate.check("window_update", "write", {"window_id": "w9"}, ctx,
                            required_scope="project")
        assert not denied.allowed
        assert "越权" in denied.reason and "p2" in denied.reason

        # 直接指定 project_id 也拦
        assert not gate.check("task_create", "write", {"project_id": "p2"}, ctx,
                              required_scope="project").allowed
        # 删工位同样拦
        assert not gate.check("window_delete", "write", {"window_id": "w9"}, ctx,
                              required_scope="project").allowed
    finally:
        db.close()


def test_主管拿不到系统级工具(tmp_path):
    db = _setup(tmp_path)
    try:
        gate = PermissionGate(scope_resolver=ScopeResolver(db))
        ctx = _ctx(db, "sup1", ScopeResolver(db).for_window(db.get_window("sup1")))
        for tool in ("config_set", "self_update", "profile_save", "memory_write"):
            d = gate.check(tool, "write", {}, ctx, required_scope="system")
            assert not d.allowed, tool
            assert "system 作用域" in d.reason
    finally:
        db.close()


def test_作用域校验_fail_closed(tmp_path):
    """判不出目标项目 / 没声明作用域 → 一律拒绝（宁可错杀）。"""
    db = _setup(tmp_path)
    try:
        gate = PermissionGate(scope_resolver=ScopeResolver(db))
        sup_scope = ScopeResolver(db).for_window(db.get_window("sup1"))
        # 判不出目标
        d = gate.check("window_list", "read", {}, _ctx(db, "sup1", sup_scope),
                       required_scope="project")
        assert not d.allowed and "fail-closed" in d.reason
        # 没声明作用域
        d2 = gate.check("window_list", "read", {"project_id": "p1"},
                        ToolContext(window_id="sup1", workspace="."),
                        required_scope="project")
        assert not d2.allowed and "未声明作用域" in d2.reason
        # 员工（scope=none）连自己项目的管理工具都不能用
        d3 = gate.check("window_list", "read", {"project_id": "p1"},
                        _ctx(db, "w1", NONE), required_scope="project")
        assert not d3.allowed
    finally:
        db.close()


def test_管理员能管所有项目(tmp_path):
    db = _setup(tmp_path)
    try:
        gate = PermissionGate(scope_resolver=ScopeResolver(db))
        admin_ctx = _ctx(db, "admin:x", SYSTEM)
        for pid in ("p1", "p2"):
            assert gate.check("window_update", "write", {"project_id": pid}, admin_ctx,
                              required_scope="project").allowed
            assert gate.check("config_set", "write", {"project_id": pid}, admin_ctx,
                              required_scope="system").allowed
    finally:
        db.close()


def test_普通工具不受作用域影响(tmp_path):
    """读写文件/shell 没有 required_scope，不能因为 scope=None 就被误杀。"""
    db = _setup(tmp_path)
    try:
        gate = PermissionGate(scope_resolver=ScopeResolver(db))
        ctx = ToolContext(window_id="w1", workspace=str(tmp_path), mode="BUILD", scope=NONE)
        assert gate.check("read_file", "read", {"path": "a.txt"}, ctx).allowed
        assert gate.check("shell", "shell", {"command": "ls"}, ctx).allowed
    finally:
        db.close()


def test_可解析目标项目的五种入参(tmp_path):
    db = _setup(tmp_path)
    try:
        gate = PermissionGate(scope_resolver=ScopeResolver(db))
        ctx = _ctx(db, "sup1", ScopeResolver(db).for_window(db.get_window("sup1")))
        assert gate.resolve_target_project({"project_id": "p1"}, ctx)[0] == "p1"
        assert gate.resolve_target_project({"window_id": "w1"}, ctx)[0] == "p1"
        assert gate.resolve_target_project({"group_id": "g?"}, ctx)[0] == ""
        assert gate.resolve_target_project({}, ctx)[0] == ""     # 没有兜底 project_id
        ctx.extra["project_id"] = "p1"
        assert gate.resolve_target_project({}, ctx)[0] == "p1"   # 兜底：调用者自己的项目
    finally:
        db.close()


# --------------------------------------------------------------------------- T9.4 指挥链
class _FakeWindows:
    def __init__(self):
        self.dispatched: list[tuple[str, str]] = []

    def dispatch(self, window_id, text, urgent=True, **kw):
        self.dispatched.append((window_id, text))


def test_T9_4_主管能派活并收回报(tmp_path):
    db = _setup(tmp_path)
    try:
        bus = EventBus()
        fake = _FakeWindows()
        notified: list = []
        svc = SupervisionService(db, windows=fake, bus=bus,
                                 admin_notify=lambda t, l="info": notified.append((t, l)))
        svc.start()

        async def scenario():
            # 管理员 → 主管
            await bus.publish(Event(type=SUPERVISOR_COMMAND,
                                    payload={"project_id": "p1", "text": "把登录做完"}))
            # 员工 → 主管
            await bus.publish(Event(type=WORKER_REPORT,
                                    payload={"window_id": "w1", "text": "登录接口写完了"}))
            # 主管 → 管理员
            await bus.publish(Event(type=SUPERVISOR_DIGEST,
                                    payload={"window_id": "sup1", "text": "本周进展：登录完成"}))
        _run(scenario())

        targets = [w for w, _ in fake.dispatched]
        assert "sup1" in targets, f"管理员指令该投给主管，实际 {targets}"
        assert targets.count("sup1") >= 2, "员工上报也该由主管收（逐级）"
        assert any("登录接口写完了" in t for _, t in fake.dispatched)
        assert notified, "主管的上报要送达管理员"
        assert "本周进展" in notified[-1][0]
    finally:
        db.close()


def test_T9_4_无主管时退化直连管理员(tmp_path):
    db = _setup(tmp_path, with_supervisor=False)
    try:
        bus = EventBus()
        fake = _FakeWindows()
        notified: list = []
        svc = SupervisionService(db, windows=fake, bus=bus,
                                 admin_notify=lambda t, l="info": notified.append((t, l)))
        svc.start()
        assert svc.supervisor_for("p1") == "", "没有主管就该寻址为空"

        _run(bus.publish(Event(type=WORKER_REPORT,
                               payload={"window_id": "w1", "text": "遇到问题"})))
        assert not fake.dispatched, "无主管时不该投给任何工位"
        assert notified and "遇到问题" in notified[0][0], "必须直连管理员（v1 行为）"
    finally:
        db.close()


def test_T9_4_主管工位不存在也退化(tmp_path):
    """owner 指向一个已删除的工位 → 退化直连，而不是把上报丢掉。"""
    db = _setup(tmp_path, with_supervisor=False)
    try:
        db.update_project("p1", owner_supervisor_id="ghost")
        notified: list = []
        bus = EventBus()
        svc = SupervisionService(db, windows=_FakeWindows(), bus=bus,
                                 admin_notify=lambda t, l="info": notified.append(t))
        svc.start()
        assert svc.supervisor_for("p1") == ""
        _run(bus.publish(Event(type=WORKER_REPORT,
                               payload={"window_id": "w1", "text": "救命"})))
        assert notified, "退化路径必须把消息送到管理员"
        assert "救命" in notified[0]
    finally:
        db.close()


def test_T9_4_汇报按角色分流(tmp_path):
    """`report_up` 必须按**角色**分流：员工走 worker.report，主管走 supervisor.digest。"""
    db = _setup(tmp_path)
    try:
        bus = EventBus()
        emitted: list = []
        bus.emit = lambda ev: emitted.append(ev)  # type: ignore[assignment]
        svc = SupervisionService(db, windows=_FakeWindows(), bus=bus)
        svc.report_up("w1", "员工的进展")     # 员工 → WORKER_REPORT
        svc.report_up("sup1", "主管的汇总")   # 主管 → SUPERVISOR_DIGEST
        kinds = [e.type for e in emitted]
        assert WORKER_REPORT in kinds, f"员工上报应走 WORKER_REPORT，实际 {kinds}"
        assert SUPERVISOR_DIGEST in kinds, f"主管汇报应走 SUPERVISOR_DIGEST，实际 {kinds}"
    finally:
        db.close()


def test_T9_4_workers_of_排除主管自己(tmp_path):
    db = _setup(tmp_path)
    try:
        svc = SupervisionService(db)
        ids = [w["id"] for w in svc.workers_of("p1")]
        assert ids == ["w1"], f"项目里的员工只有 w1，实际 {ids}"
        assert [w["id"] for w in svc.workers_of("p2")] == ["w9"]
    finally:
        db.close()


# --------------------------------------------------------------------------- T9.5 项目记忆
def test_T9_5_主管写员工读_员工不能写(tmp_path):
    from dogbuddy.tools.project_memory_ops import (
        ProjectMemoryReadTool, ProjectMemoryWriteTool)
    db = _setup(tmp_path)
    try:
        sup_ctx = ToolContext(window_id="sup1", workspace=".", mode="BUILD", db=db,
                              extra={"project_id": "p1"})
        sup_ctx.memory_service = None
        # 指定记忆目录（测试隔离）
        sup_ctx.extra["_mem_root"] = str(tmp_path)
        from dogbuddy.tools import project_memory_ops as pm
        original = pm._memory_dir
        # `_memory_dir` 的契约是"返回 projects 目录本身"
        pm._memory_dir = lambda ctx: Path(ctx.extra.get("_mem_root", str(tmp_path))) / "memory" / "projects"
        try:
            w = _run(ProjectMemoryWriteTool().execute(
                {"content": "# 项目约定\n- 只用 UTF-8\n- 提交前跑测试"}, sup_ctx))
            assert w.ok, w.output
            assert (tmp_path / "memory" / "projects" / "p1.md").exists()

            read = _run(ProjectMemoryReadTool().execute({}, sup_ctx))
            assert read.ok and "只跑测试" not in read.output and "提交前跑测试" in read.output

            # 员工读得到
            worker_ctx = ToolContext(window_id="w1", workspace=".", mode="BUILD", db=db,
                                     extra={"project_id": "p1", "_mem_root": str(tmp_path)})
            assert _run(ProjectMemoryReadTool().execute({}, worker_ctx)).ok
            # 但员工**装配层就不给写工具**
            assert "project_memory_write" not in WORKER_PROGRAMMER_TOOLS
            assert "project_memory_read" in WORKER_PROGRAMMER_TOOLS
            # append 模式
            _run(ProjectMemoryWriteTool().execute({"content": "- 新增：禁止 force push",
                                                   "mode": "append"}, sup_ctx))
            body = (tmp_path / "memory" / "projects" / "p1.md").read_text(encoding="utf-8")
            assert "只用 UTF-8" in body and "禁止 force push" in body
        finally:
            pm._memory_dir = original
    finally:
        db.close()


def test_T9_5_项目记忆工具带_project_作用域():
    from dogbuddy.tools.registry import build_registry
    reg = build_registry()
    for name in ("project_memory_read", "project_memory_write"):
        t = reg.get(name)
        assert t is not None and t.required_scope == "project", name


# --------------------------------------------------------------------------- T9.6 审批逐级
def test_T9_6_审批路由逐级与升级(tmp_path):
    db = _setup(tmp_path)
    try:
        svc = SupervisionService(db)
        # 普通高危 + 有主管 → 先找主管
        r = svc.approval_target("w1", irreversible=False)
        assert r["target"] == "supervisor" and r["window_id"] == "sup1"
        # 不可逆 → 直接升级管理员（第一道保险）
        r2 = svc.approval_target("w1", irreversible=True)
        assert r2["target"] == "admin" and "不可逆" in r2["reason"]
        # 无主管项目 → 管理员
        assert svc.approval_target("w9", irreversible=False)["target"] == "admin"
    finally:
        db.close()


def test_T9_6_无主管时审批直连管理员(tmp_path):
    db = _setup(tmp_path, with_supervisor=False)
    try:
        r = SupervisionService(db).approval_target("w1", irreversible=False)
        assert r["target"] == "admin" and "无主管" in r["reason"]
    finally:
        db.close()


def test_T9_6_主管自己触发的高危操作直接找管理员(tmp_path):
    db = _setup(tmp_path)
    try:
        r = SupervisionService(db).approval_target("sup1", irreversible=False)
        assert r["target"] == "admin", "主管不能审批自己（避免自己批自己）"
    finally:
        db.close()


def test_T9_6_审批升级会投递给主管并保持暂停(tmp_path):
    """端到端：员工的高危操作应被投给主管，且**本轮不执行**。"""
    from dogbuddy.agents.core.loop import AgentLoop
    from dogbuddy.llm.provider import LLMResponse, ToolCall
    from dogbuddy.tools.registry import ToolResult, build_registry
    from dogbuddy.windows.session import WindowSession

    db = _setup(tmp_path)
    try:
        dispatched: list = []
        ctx = ToolContext(
            window_id="w1", workspace=str(tmp_path), mode="BUILD", db=db,
            scope=NONE, extra={
                "manual": True,
                "approval_router": lambda wid, irr: {"target": "supervisor",
                                                     "window_id": "sup1"},
                "supervision_dispatch": lambda wid, text: dispatched.append((wid, text)),
            })
        w = db.get_window("w1")
        session = WindowSession(w, db, db, db)
        loop = AgentLoop(llm=None, registry=build_registry(), session=session)

        class TC:
            name = "shell"
            id = "c1"
            arguments = {"command": "shutdown -h now"}

        pending = ToolResult.confirm("dangerous command (system power operation)", irreversible=True)
        out = _run(loop._approval_flow(TC(), pending, ctx))
        assert dispatched, "应把待审批请求投给主管"
        assert dispatched[0][0] == "sup1"
        assert not out.ok, "在主管批准前绝不能执行"
        assert "主管" in out.output
    finally:
        db.close()


# --------------------------------------------------------------------------- T9.4 HTTP
def test_T9_4_指挥链接口(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig
    from dogbuddy.main import DogBuddyApp
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="tok", port=8131),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = "tok"
    with client:
        proj = client.post("/api/projects", json={"name": "P", "root_path": str(tmp_path / "p")}
                           ).json()["project"]
        sup = app.windows.create("主管", "supervisor", str(tmp_path / "sup"))
        client.post("/api/projects/mount", json={"window_id": sup.id, "project_id": proj["id"]})
        worker = app.windows.create("员工", "worker_programmer", str(tmp_path / "w"))
        client.post("/api/projects/mount", json={"window_id": worker.id,
                                                "project_id": proj["id"]})
        # 指定主管
        patched = client.patch(f"/api/projects/{proj['id']}",
                               json={"owner_supervisor_id": sup.id}).json()["project"]
        assert patched["owner_supervisor_id"] == sup.id
        assert patched["owner_supervisor_name"] == "主管"

        chain = client.get(f"/api/projects/{proj['id']}/chain").json()
        assert chain["supervisor"]["id"] == sup.id
        assert [w["id"] for w in chain["workers"]] == [worker.id], "主管不算员工"
        assert chain["health"] == "ok"
        assert "owner_supervisor_id" in chain["addressing"]

        # 主管工位确实装上了项目级管理工具、且没有系统级
        rt = app.windows._rt(sup.id)
        names = set(rt.registry.names())
        assert "window_create" in names and "read_file" in names
        assert "self_update" not in names and "config_set" not in names


def test_v2_只有一个管理员_且没有多管理员接口(tmp_path, monkeypatch):
    """v2 口径（2026-10-14 用户拍板）：**只有一个管理员**与用户对接。

    所以多管理员 CRUD 已被移除——这条测试就是防止它被"好心"加回来。
    """
    from fastapi.testclient import TestClient
    from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig
    from dogbuddy.main import DogBuddyApp
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="tok", port=8132),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = "tok"
    with client:
        # 多管理员接口不该存在
        assert client.get("/api/admins").status_code == 404
        assert client.post("/api/admins", json={"name": "x"}).status_code == 404
        # 管理员会话接口明确声明"单管理员"
        assert client.get("/api/admin/chat/admins").json()["single_admin"] is True


