# DogBuddyCn — restricted test runner for the reviewer (0.25.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Run only explicitly allow-listed commands, never arbitrary shell."""
from __future__ import annotations

import asyncio
import shlex
from typing import Any

from .registry import ToolContext, ToolResult
from .shell_ops import _child_env

MAX_OUTPUT = 12000


class RunTestsTool:
    name = "run_tests"
    description = (
        "Run one of the configured, allow-listed test commands in this workstation's "
        "workspace. No arbitrary shell: the command must exactly match `review.test_commands`. "
        "Returns the exit code and tail of the output.")
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string",
                        "description": "One configured test command; empty = the first one."},
            "timeout": {"type": "number",
                        "description": "Seconds, default 300, max 600."},
        },
    }
    permission = "read"
    required_scope = ""

    def __init__(self, allowed: list[str] | None = None):
        self.allowed = [str(x).strip() for x in (allowed or []) if str(x).strip()]

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not self.allowed:
            return ToolResult.failure("未配置受控测试命令（review.test_commands）")
        cmd = str(args.get("command") or "").strip() or self.allowed[0]
        if cmd not in self.allowed:
            return ToolResult.failure(
                f"测试命令不在白名单：{cmd}；允许：{self.allowed}")
        try:
            argv = shlex.split(cmd, posix=True)
        except ValueError as e:
            return ToolResult.failure(f"测试命令无法解析：{e}")
        if not argv:
            return ToolResult.failure("测试命令为空")
        cwd = str(getattr(ctx, "workspace", "") or ".") or "."
        try:
            timeout = max(5.0, min(float(args.get("timeout") or 300), 600.0))
        except (TypeError, ValueError):
            timeout = 300.0
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, env=_child_env())
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult.failure(f"测试超时（{timeout:.0f}s）：{cmd}")
        except FileNotFoundError:
            return ToolResult.failure(f"测试命令不存在：{argv[0]}")
        except Exception as e:  # noqa: BLE001 — never kill the review turn
            return ToolResult.failure(f"测试启动失败：{e}")
        text = (out or b"").decode("utf-8", "replace")
        tail = text[-MAX_OUTPUT:]
        data = {"command": cmd, "exit_code": int(proc.returncode or 0),
                "truncated": len(text) > len(tail)}
        if proc.returncode == 0:
            return ToolResult.success(tail or "(no output)", **data)
        return ToolResult.failure(f"测试失败（exit={proc.returncode}）\n{tail}", **data)


def all_tools(allowed: list[str] | None = None) -> list[Any]:
    return [RunTestsTool(allowed)]
