# DogBuddyCn — 角色工具白名单硬约束（审查报告 P1-7 / 变异 v4）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""审查报告第六节的变异测试证明：**把迭代执行器的工具白名单换掉，375 条测试全绿**。

根因是 `registry.schemas(only=...)` 写成 `if only and name not in only`——
`None` 和 `[]` **都等于"不过滤"**。于是：

* `iteration_executor.py:204` 的 `tool_names=None` 把**注册表全量**交给模型
  （员工因此拿到白名单外的 `project_memory_write`；MCP 工具接入后更是无门通道）
* `group_manager._strict_tools` 在"交集为空"时返回 `[]`，同样全量放行
* `window_manager` 里未知角色（老库残留）也退化成 `None` → 全量放行

这些测试锁住"白名单必须是硬约束"。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.loop import AgentLoop  # noqa: E402
from dogbuddy.agents.core.permissions import PermissionGate  # noqa: E402
from dogbuddy.agents.core.roles import get_role  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.tools.registry import AllowAllGate, GateDecision, ToolContext  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _app(tmp_path, monkeypatch):
    from dogbuddy.main import DogBuddyApp
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="tok", port=8141),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = "tok"
    return app, client


# ------------------------------------------------------------------ schemas 语义
def test_schemas_空名单不给任何工具():
    """`only=[]` 必须是"一个都不给"——初版它等于"不过滤"。"""
    from dogbuddy.tools.registry import build_registry
    reg = build_registry(gate=PermissionGate())
    assert reg.names(), "注册表不该是空的"
    assert reg.schemas(only=[]) == [], "空白名单必须给 0 个工具"
    assert len(reg.schemas(only=None)) == len(reg.names()), "None = 不过滤"
    one = reg.names()[:1]
    assert len(reg.schemas(only=one)) == 1


def test_角色白名单严格等于给出的工具集():
    from dogbuddy.agents.worker_programmer.tools import build_worker_registry
    reg = build_worker_registry("worker_programmer", gate=PermissionGate())
    names = {s["function"]["name"] for s in reg.schemas(only=get_role("worker_programmer").tool_names)}
    assert names == set(get_role("worker_programmer").tool_names)


# ------------------------------------------------------- 注册表把作用域转发给 gate
class _RecordingGate:
    """记录 gate 收到了什么——用来证明 `required_scope` 的转发没被删掉。"""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def check(self, tool_name, permission, args, ctx, required_scope=""):
        self.seen.append((tool_name, required_scope))
        return GateDecision(False, "stop here (test)")


def test_注册表把_required_scope_转发给_gate(tmp_path):
    """★ 变异 v1 的直接回归：删掉这行转发，作用域维度整体失效而测试全绿。"""
    from dogbuddy.tools.project_memory_ops import ProjectMemoryReadTool
    from dogbuddy.tools.registry import ToolRegistry

    gate = _RecordingGate()
    reg = ToolRegistry(gate=gate)
    tool = ProjectMemoryReadTool()
    reg.register(tool)
    ctx = ToolContext(window_id="w", workspace=str(tmp_path), mode="BUILD", actor="worker")

    _run(reg.execute(tool.name, {"project_id": "p1"}, ctx))

    assert gate.seen, "gate 根本没被调用"
    assert gate.seen[0][1] == tool.required_scope, (
        f"注册表没有把 required_scope 转给 gate（收到 {gate.seen[0][1]!r}，"
        f"工具声明的是 {tool.required_scope!r}）——作用域裁决会整体失效")


def test_AllowAllGate_对声明作用域的工具拒绝(tmp_path):
    """P2-9：`ToolRegistry(gate=None)` 退化为 AllowAllGate，装配漏传即失守。"""
    gate = AllowAllGate()
    ctx = ToolContext(window_id="w", workspace=str(tmp_path), mode="BUILD", actor="worker")
    assert gate.check("read_file", "read", {}, ctx).allowed is True
    d = gate.check("config_set", "write", {}, ctx, required_scope="system")
    assert d.allowed is False, "没能力做作用域裁决时不能默认放行（fail-closed）"


# --------------------------------------------------------------- 迭代路径（变异 v4）
def test_迭代路径按角色白名单给工具(tmp_path, monkeypatch):
    """★ 变异 v4 的直接回归：断言**真正交给模型的工具集**。"""
    app, client = _app(tmp_path, monkeypatch)
    with client:
        proj = client.post("/api/projects", json={
            "name": "P1", "root_path": str(tmp_path / "p1")}).json()["project"]
        w = app.windows.create("工人", "worker_programmer", str(tmp_path / "w1"))
        client.post("/api/projects/mount",
                    json={"window_id": w.id, "project_id": proj["id"]})
        iid = client.post("/api/iterations", json={
            "project_id": proj["id"], "goal": "把活干完"}).json()["iteration"]["id"]

        seen: dict = {}

        async def fake_run(self, user_input, ctx, on_delta=None):
            seen["tool_names"] = self.tool_names
            seen["schemas"] = [s["function"]["name"] for s in self.registry.schemas(only=self.tool_names)]
            seen["scope"] = ctx.scope
            return SimpleNamespace(stop_reason="done", content="做完了")

        monkeypatch.setattr(AgentLoop, "run", fake_run)
        entry = app.db.get_iteration(iid)
        _run(app.iteration_exec._default_round(entry, {}, 1))

        assert seen, "迭代轮次没有跑到 AgentLoop"
        assert seen["tool_names"] is not None, (
            "迭代路径必须传角色白名单；tool_names=None 等于把注册表全量交给模型")
        expected = set(get_role("worker_programmer").tool_names)
        assert set(seen["tool_names"]) == expected
        assert set(seen["schemas"]) == expected, "交给模型的工具集必须等于角色白名单"
        assert "project_memory_write" not in seen["schemas"], "员工不得拿到写项目记忆的工具"


def test_迭代路径注入了作用域(tmp_path, monkeypatch):
    """审查报告 P1-5：作用域原来只在 window_manager 一条路径注入。"""
    app, client = _app(tmp_path, monkeypatch)
    with client:
        proj = client.post("/api/projects", json={
            "name": "P2", "root_path": str(tmp_path / "p2")}).json()["project"]
        w = app.windows.create("工人2", "worker_programmer", str(tmp_path / "w2"))
        client.post("/api/projects/mount",
                    json={"window_id": w.id, "project_id": proj["id"]})
        iid = client.post("/api/iterations", json={
            "project_id": proj["id"], "goal": "g"}).json()["iteration"]["id"]
        app.db.update_window(w.id, project_id=proj["id"])

        seen: dict = {}

        async def fake_run(self, user_input, ctx, on_delta=None):
            seen["scope"] = ctx.scope
            return SimpleNamespace(stop_reason="done", content="ok")

        monkeypatch.setattr(AgentLoop, "run", fake_run)
        _run(app.iteration_exec._default_round(app.db.get_iteration(iid), {}, 1))

        assert seen.get("scope") is not None, "迭代路径的 ctx.scope 不能是 None"
        assert seen["scope"].actor == w.id, "作用域应由该工位自己的角色推导出来"


# ------------------------------------------------------- 群组交集为空 = 不给工具
def test_群组角色未知时不给任何工具(tmp_path):
    from dogbuddy.agents.core.roles import register_builtin_roles
    from dogbuddy.db import Database, Window
    from dogbuddy.services.group_manager import GroupManager

    register_builtin_roles()
    db = Database(tmp_path / "g.db")
    db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                            workspace_path="", model="", mode="BUILD"))
    db.insert_window(Window(id="w2", name="w2", role="根本不存在的角色",
                            workspace_path="", model="", mode="BUILD"))
    g = GroupManager(db, windows=None)
    assert g._strict_tools(["w1", "w2"]) == [], "有成员角色未知 → 交集为空 → 一个工具都不给"
    assert g._strict_tools([]) is None, "拿不到成员才返回 None（调用方退回各自白名单）"
    # 同角色 → 全集
    db.insert_window(Window(id="w3", name="w3", role="worker_programmer",
                            workspace_path="", model="", mode="BUILD"))
    both = g._strict_tools(["w1", "w3"])
    assert "write_file" in both and "shell" in both
    db.close()
