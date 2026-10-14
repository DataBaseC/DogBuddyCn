# DogBuddyCn — server_exec tool (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Run a server command through AegisServerPanel's /api/terminal/exec.

The panel is never modified; the call carries its X-Agent-Token header and
respects the panel's 1..600s timeout contract. Danger gating happens in the
L3 PermissionGate (permission="shell").

**这是系统级能力（2026-10 收紧，见审查报告 P0-1）**：命令在**面板宿主机**上执行，
不受工作区边界约束（这正是它与 `shell` 的分工——`shell` 锁在工作区内）。
因此它声明 `required_scope="system"`，**只有管理员作用域可用**；员工/主管工位在
`PermissionGate.check_scope` 处被 fail-closed 拒绝。装配层（`WORKER_PROGRAMMER_TOOLS`）
仍保留它，是因为"给不给工具"与"能不能用"是两层：越权由 L3 裁决（与
《DECISIONS-指挥链路口径》决策 1 的两层保证一致）。
"""
from __future__ import annotations

from typing import Any

from .registry import ToolContext, ToolResult


class ServerExecTool:
    name = "server_exec"
    description = ("Execute a command on the **server host** via AegisServerPanel "
                   "(POST /api/terminal/exec). System-level capability, not bound by the "
                   "workspace boundary; admin only. Timeout 1..600 seconds.")
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "shell command to run on the server"},
            "timeout": {"type": "integer", "description": "seconds, 1..600 (default 60)"},
        },
        "required": ["command"],
    }
    permission = "shell"
    #: 系统级能力：员工/主管的工作区作用域拿不到（fail-closed）
    required_scope = "system"


    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = str(args.get("command", "")).strip()
        if not command:
            return ToolResult.failure("command must not be empty")
        if ctx.panel is None:
            return ToolResult.failure("server_exec unavailable: panel client not configured")
        try:
            timeout = int(args.get("timeout", 60))
        except (TypeError, ValueError):
            timeout = 60
        timeout = min(max(timeout, 1), 600)
        try:
            result = await ctx.panel.exec(command, timeout)
        except Exception as e:  # noqa: BLE001 — panel/network errors become tool output
            return ToolResult.failure(f"panel exec failed: {e}")
        stdout = str(result.get("stdout", ""))[:20000]
        stderr = str(result.get("stderr", ""))[:8000]
        code = result.get("returncode", 0)
        output = f"returncode={code}\n--- stdout ---\n{stdout}"
        if stderr:
            output += f"\n--- stderr ---\n{stderr}"
        ok = code == 0
        return ToolResult(ok, output, data={"returncode": code})
