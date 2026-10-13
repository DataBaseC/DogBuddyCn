# DogBuddyCn — server_exec tool (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Run a server command through AegisServerPanel's /api/terminal/exec.

The panel is never modified; the call carries its X-Agent-Token header and
respects the panel's 1..600s timeout contract. Danger gating happens in the
L3 PermissionGate (permission="shell")."""
from __future__ import annotations

from typing import Any

from .registry import ToolContext, ToolResult


class ServerExecTool:
    name = "server_exec"
    description = ("Execute a command on the server via AegisServerPanel (POST /api/terminal/exec). "
                   "Use for server-level operations outside the workspace. Timeout 1..600 seconds.")
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "shell command to run on the server"},
            "timeout": {"type": "integer", "description": "seconds, 1..600 (default 60)"},
        },
        "required": ["command"],
    }
    permission = "shell"

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
