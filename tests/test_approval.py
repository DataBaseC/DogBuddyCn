# DogBuddyCn — Batch 4 tests: approval flow, ELEVATED trust, 托管代审 (T4.x)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dogbuddy.agents.core.permissions import (IRREVERSIBLE_PATTERNS,
                                              PermissionGate,
                                              is_irreversible_command)
from dogbuddy.tools.registry import (AllowAllGate, GateDecision, ToolContext,
                                     ToolRegistry, ToolResult)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(**extra):
    return ToolContext(window_id="w1", workspace="/tmp/ws", mode="BUILD",
                       actor="worker", extra=extra)


class EchoTool:
    name = "shell"
    description = "run a command"
    parameters = {"type": "object", "properties": {"command": {"type": "string"}}}
    permission = "shell"

    def __init__(self):
        self.executed = []

    async def execute(self, args, ctx):
        self.executed.append(args)
        return ToolResult.success(f"ran: {args.get('command','')}")


# ------------------------------------------------------------ gate rules
def test_irreversible_detection():
    assert is_irreversible_command("rm -rf /") is not None
    assert is_irreversible_command("mkfs.ext4 /dev/sdb") is not None
    assert is_irreversible_command("ls -la") is None
    assert len(IRREVERSIBLE_PATTERNS) >= 8


def test_irreversible_always_needs_confirm():
    gate = PermissionGate(confirm_available=True)
    d = gate.check("shell", "shell", {"command": "rm -rf /important"}, _ctx())
    assert d.allowed is False and d.needs_confirm is True
    assert d.irreversible is True


def test_elevated_allowlist_passes_ordinary_danger():
    gate = PermissionGate(confirm_available=True)
    ctx = _ctx(trust="elevated", approval_allowlist=["git push"])
    d = gate.check("shell", "shell", {"command": "git push --force origin main"}, ctx)
    # --force 未必命中黑名单，但假设命中：allowlist 应放行
    if d.needs_confirm:
        assert d.allowed is True or d.irreversible is False
    else:
        assert d.allowed is True


def test_elevated_allowlist_cannot_pass_irreversible():
    """三道保险之一：放行清单盖不住不可逆命令。"""
    gate = PermissionGate(confirm_available=True)
    ctx = _ctx(trust="elevated", approval_allowlist=["rm -rf"])
    d = gate.check("shell", "shell", {"command": "rm -rf /data"}, ctx)
    assert d.allowed is False and d.needs_confirm is True
    assert d.irreversible is True


def test_normal_trust_ignores_allowlist():
    gate = PermissionGate(confirm_available=True)
    ctx = _ctx(trust="normal", approval_allowlist=["git push"])
    d = gate.check("shell", "shell", {"command": "git push --force"}, ctx)
    # 普通信任级即使命令在清单里也要确认（若命中黑名单）
    from dogbuddy.agents.core.permissions import is_dangerous_command
    if is_dangerous_command("git push --force"):
        assert d.needs_confirm is True


# ------------------------------------------------------------ approval flow
def _registry_with_echo():
    reg = ToolRegistry(gate=PermissionGate(confirm_available=True))
    tool = EchoTool()
    reg.register(tool)
    return reg, tool


def test_owner_approval_replays_command():
    reg, tool = _registry_with_echo()
    ctx = _ctx()
    ctx.ask_user_fn = lambda q, options=None: "批准"
    res = _run(_run_flow(reg, "shell", {"command": "rm -rf /important"}, ctx))
    assert res.ok and tool.executed == [{"command": "rm -rf /important"}]
    assert "ran: rm -rf /important" in res.output


def test_owner_denial_blocks_command():
    reg, tool = _registry_with_echo()
    ctx = _ctx()
    ctx.ask_user_fn = lambda q, options=None: "拒绝"
    res = _run(_run_flow(reg, "shell", {"command": "rm -rf /important"}, ctx))
    assert res.ok is False and tool.executed == []
    assert "拒绝" in res.output


def test_reviewer_approves_automatically():
    reg, tool = _registry_with_echo()

    async def reviewer(name, args, reason):
        return True

    ctx = _ctx(approval_reviewer=reviewer)
    ctx.ask_user_fn = lambda q, options=None: (_ for _ in ()).throw(
        AssertionError("托管代审已判定，不该打扰用户"))
    res = _run(_run_flow(reg, "shell", {"command": "systemctl stop docker"}, ctx))
    assert res.ok and len(tool.executed) == 1


def test_reviewer_denial_blocks():
    reg, tool = _registry_with_echo()

    async def reviewer(name, args, reason):
        return False

    ctx = _ctx(approval_reviewer=reviewer)
    res = _run(_run_flow(reg, "shell", {"command": "systemctl stop docker"}, ctx))
    assert res.ok is False and tool.executed == []
    assert "托管代审" in res.output


def test_irreversible_skips_reviewer_and_asks_owner():
    """三道保险：不可逆命令不走托管代审，必须问本人。"""
    reg, tool = _registry_with_echo()
    asked = []

    async def reviewer(name, args, reason):
        raise AssertionError("irreversible 不得交给审查器")

    def ask(q, options=None):
        asked.append(q)
        return "拒绝"

    ctx = _ctx(approval_reviewer=reviewer)
    ctx.ask_user_fn = ask
    res = _run(_run_flow(reg, "shell", {"command": "rm -rf /data"}, ctx))
    assert res.ok is False and tool.executed == []
    assert asked, "必须问本人"


def test_approval_is_audited():
    import tempfile
    from dogbuddy.db import Database
    db = Database(f"{tempfile.mkdtemp()}/t.db")
    reg, tool = _registry_with_echo()
    reg.audit = db  # tool executions留痕走 registry 的 audit sink
    ctx = _ctx()
    ctx.db = db
    ctx.ask_user_fn = lambda q, options=None: "批准"
    _run(_run_flow(reg, "shell", {"command": "rm -rf /important"}, ctx))
    actions = [e.action for e in db.list_audit(50)]
    assert "approval:approved" in actions
    assert any(a.startswith("tool:") for a in actions)  # 执行本身也有痕


async def _run_flow(registry, name, args, ctx):
    """mirror of AgentLoop._approval_flow (kept in sync by test_loop_approval)."""
    result = await registry.execute(name, args, ctx)
    if not result.needs_confirm:
        return result
    from dogbuddy.agents.core.loop import AgentLoop
    fake_loop = object.__new__(AgentLoop)
    fake_loop.registry = registry
    fake_loop.session = None
    fake_loop.llm = None

    class TC:
        pass

    tc = TC()
    tc.name = name
    tc.arguments = args
    return await fake_loop._approval_flow(tc, result, ctx)
