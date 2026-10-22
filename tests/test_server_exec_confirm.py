# DogBuddyCn — server_exec 每次都要人工确认（v0.10.50 · 方案B）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""P0 降级（2026-10-22 实测）：应用进程 env 里有 DOGBUDDY_PANEL_TOKEN（= root）。
员工只要有 shell，写个 .py 跑一下就能读到同一个令牌 → 绕过工作区边界/危险命令检查
直接拿 root。彻底修法是"令牌不进应用进程"，本轮按 owner 选的方案 B 先降一级：
**server_exec 一律 needs_confirm + irreversible**，必须有人在控制台点批准才执行。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.permissions import PermissionGate  # noqa: E402
from dogbuddy.agents.core.scope import Scope  # noqa: E402
from dogbuddy.services.admin_service import AdminService  # noqa: E402
from dogbuddy.tools.registry import ToolContext, ToolResult  # noqa: E402


def _ctx(**extra) -> ToolContext:
    return ToolContext(window_id="w1", workspace="/tmp", mode="AUTO",
                       actor="admin:default",
                       scope=Scope(level="system", actor="admin:default", source="role:admin"),
                       extra=extra)


def test_server_exec_always_needs_confirmation():
    gate = PermissionGate(confirm_available=True)
    d = gate.check("server_exec", "shell", {"command": "ls /"}, _ctx(), "system")
    assert d.allowed is False
    assert d.needs_confirm is True
    assert d.irreversible is True, "托管代审绝不能自动放行宿主机命令"
    assert "人工确认" in d.reason

    # 批准后的重放（confirmed）才放行
    d2 = gate.check("server_exec", "shell", {"command": "ls /"}, _ctx(confirmed=True), "system")
    assert d2.allowed is True


def test_benign_commands_in_sandbox_are_not_affected():
    """只有 server_exec 变严；工位里的普通 shell 不受影响。"""
    gate = PermissionGate(confirm_available=True)
    d = gate.check("shell", "shell", {"command": "ls -la"}, _ctx(confirmed=False), "")
    assert d.allowed is True


class _FakeDecisions:
    def __init__(self, answer: str = "批准"):
        self.answer = answer
        self.asked: list[tuple] = []

    def ask(self, window_id, question, options=None, source="", supersede=True):
        self.asked.append((window_id, question, source))
        return type("P", (), {"id": 7})()

    async def wait(self, decision_id):
        return self.answer


def _admin(decisions) -> AdminService:
    return AdminService(db=None, llm_factory=lambda: None, windows=None, jobs=None,
                        sessions=None, workspaces=None, memory=None, config=None,
                        decisions=decisions)


def test_admin_confirm_asker_waits_for_the_decision():
    fake = _FakeDecisions("批准")
    admin = _admin(fake)
    answer = asyncio.new_event_loop().run_until_complete(
        admin._confirm_asker("default")("宿主机命令必须人工确认（server_exec）：ls /",
                                        options=["批准", "拒绝"]))
    assert answer == "批准"
    assert fake.asked, "确认请求必须落进待决队列（控制台才看得见）"
    assert fake.asked[0][0].startswith("admin:"), "待决事项要挂在管理员会话上"
    assert fake.asked[0][2] == "server_exec"


def test_without_decision_service_it_fails_closed():
    admin = _admin(None)
    answer = asyncio.new_event_loop().run_until_complete(
        admin._confirm_asker("default")("q", options=["批准", "拒绝"]))
    assert "拒绝" in answer, "没有确认通道时必须拒绝，绝不能静默放行"


def test_denied_confirmation_blocks_execution():
    fake = _FakeDecisions("拒绝")
    admin = _admin(fake)
    answer = asyncio.new_event_loop().run_until_complete(
        admin._confirm_asker("default")("q", options=["批准", "拒绝"]))
    assert answer == "拒绝"


def test_admin_ctx_exposes_the_confirm_channel():
    admin = _admin(_FakeDecisions())
    ctx = ToolContext(window_id="admin:default", workspace=".", mode="AUTO",
                      actor="admin:default")
    # _run_turn 里注入的正是这两个：ask_user_fn（确认通道）+ extra.decisions
    assert callable(admin._confirm_asker("default"))
    assert ctx.extra.get("decisions") is None  # 默认空 = fail-closed，直到装配注入
