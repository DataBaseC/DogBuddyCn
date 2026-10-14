# DogBuddyCn — shell execution tool (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""shell tool: run commands in the workspace, foreground or background.

  - foreground: merged stdout/stderr capture, hard timeout kills the process
    group (started in its own session), output truncated to 20000 chars
  - background: returns immediately with the pid and registers the subprocess
    on ctx.extra["bg_processes"] for later inspection/reaping
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any

from .registry import ToolContext, ToolResult

logger = logging.getLogger("dogbuddy.tools.shell")

MAX_OUTPUT_CHARS = 20000
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 3600


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off", ""):
        return False
    return default


def _spawn_kwargs() -> dict[str, Any]:
    """POSIX: own session so the whole tree can be killed; Windows: process group."""
    if os.name == "nt":
        import subprocess
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the whole process tree; POSIX uses killpg, Windows uses taskkill /T."""
    if os.name == "nt":
        try:
            import subprocess
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
            return
        except Exception:  # noqa: BLE001 — fall through to plain kill
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except Exception:  # noqa: BLE001 — process may already be gone
            pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


async def _drain(proc: asyncio.subprocess.Process) -> None:
    """Discard background output so the pipe never blocks the child process."""
    try:
        if proc.stdout is not None:
            while await proc.stdout.read(65536):
                pass
    except Exception:  # noqa: BLE001
        pass
    try:
        await proc.wait()
    except Exception:  # noqa: BLE001
        pass


class ShellTool:
    """shell — execute a shell command inside the workspace."""

    name = "shell"
    description = (
        "Run a shell command with the workspace as working directory. stdout and "
        "stderr are merged and truncated to 20000 characters. The command is "
        "killed after `timeout` seconds (default 60). Set background=true to "
        "start the command without waiting; the pid is returned and the process "
        "is tracked in ctx.extra['bg_processes']."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command line to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Seconds to wait before killing the process (default 60).",
            },
            "background": {
                "type": "boolean",
                "description": "Start the command and return immediately with its pid (default false).",
            },
        },
        "required": ["command"],
    }
    permission = "shell"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            command = args.get("command")
            if command is None or not str(command).strip():
                return ToolResult.failure("command must be a non-empty string")
            command = str(command)
            timeout = _to_int(args.get("timeout"), DEFAULT_TIMEOUT)
            timeout = max(1, min(timeout, MAX_TIMEOUT))
            background = _to_bool(args.get("background"), False)
            cwd = str(ctx.workspace)

            # T7.4：shell 是绕过文件边界的缝——越界必须走 ELEVATED + 留痕
            from .shell_guard import analyze_command, is_elevated, refusal_message
            risks = analyze_command(command, cwd)
            if risks:
                audit = getattr(ctx, "db", None)
                elevated = is_elevated(ctx)
                actor = getattr(ctx, "actor", "worker")
                detail = (f"elevated={elevated} trust={((ctx.extra or {}).get('trust') or '')} "
                          f"cmd={command[:200]} risks="
                          f"{[r['reason'] for r in risks]}")
                if audit is not None:
                    try:
                        audit.audit(actor, "shell:boundary", detail)
                    except Exception:  # noqa: BLE001 — 留痕失败不能反过来挡住判断
                        logger.warning("boundary audit failed", exc_info=True)
                if not elevated:
                    logger.warning("shell boundary refused: %s", [r["reason"] for r in risks])
                    return ToolResult.failure(refusal_message(risks, cwd),
                                              boundary=True, risks=risks)
                logger.info("shell boundary allowed (elevated): %s",
                            [r["reason"] for r in risks])

            if background:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    **_spawn_kwargs(),
                )
                ctx.extra.setdefault("bg_processes", []).append(proc)
                asyncio.ensure_future(_drain(proc))
                return ToolResult.success(
                    f"background process started (pid={proc.pid})",
                    pid=proc.pid,
                    background=True,
                )

            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **_spawn_kwargs(),
            )
            try:
                out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                _kill_process_group(proc)
                try:
                    out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                except Exception:  # noqa: BLE001
                    out_bytes = b""
                output = (out_bytes or b"").decode("utf-8", errors="replace")
                if len(output) > MAX_OUTPUT_CHARS:
                    output = output[:MAX_OUTPUT_CHARS] + "\n...[output truncated]"
                suffix = f"\n{output}" if output.strip() else ""
                return ToolResult.failure(
                    f"timeout: command killed after {timeout}s: {command}{suffix}",
                    timeout=True,
                )

            output = (out_bytes or b"").decode("utf-8", errors="replace")
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n...[output truncated]"
            return ToolResult.success(output, returncode=proc.returncode)
        except Exception as e:  # noqa: BLE001
            logger.warning("shell failed", exc_info=True)
            return ToolResult.failure(f"shell failed: {e}")


def all_tools() -> list:
    return [ShellTool()]
