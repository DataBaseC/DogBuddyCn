# DogBuddyCn — permission gate & dangerous-command blacklist (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Permission decision logic — business rules live here (ARCHITECTURE §3).

Mode matrix (hard constraints, DEV_PROMPT §安全红线):
  BUILD — writes allowed inside workspace; writes outside workspace and
          dangerous commands are blocked (dangerous ones may need IM confirm)
  PLAN  — every write/shell operation blocked (read-only planning)
  AUTO  — everything allowed, EXCEPT unattended scheduled tasks (no AUTO on cron)

The gate is injected into tools/registry (L4) which only calls it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ...tools.registry import GateDecision, ToolContext

# Dangerous command blacklist. Matched against shell/server_exec command strings.
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*-\w*[rR]\w*f\w*\s+([^\s]*\s+)*(--no-preserve-root\s+)?['\"]?/(\s|['\"]|$)", "recursive delete of filesystem root"),
    (r"\brm\s+(-[a-zA-Z]*\s+)*-\w*[rR]\w*f\w*\s+([^\s]*\s+)*~?/?(\s|$)", "recursive delete of a top-level path"),
    (r"\bmkfs(\.\w+)?\b", "filesystem format"),
    (r"\b(shutdown|reboot|halt|poweroff|init\s+0)\b", "system power operation"),
    (r"\bsystemctl\s+(stop|disable|mask|restart|kill)\s+", "systemd unit control"),
    (r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;", "fork bomb"),
    (r"\bdd\b[^\n]*\bof=/dev/", "raw device write"),
    (r"\bmkswap\b|\bswipe\b", "swap/erase device"),
    (r"\bchmod\s+(-[a-zA-Z]+\s+)*(-R\s+)?777\s+['\"]?/(\s|['\"]|$)", "chmod 777 on root"),
    (r"\bchown\s+(-[a-zA-Z]+\s+)*-R\s+[^\s]+\s+['\"]?/(\s|['\"]|$)", "recursive chown of root"),
    (r"\bkill\s+(-9\s+)?-1\b|pkill\s+-9\s+-1", "kill all processes"),
    (r"\bcrontab\s+(-[a-zA-Z]+\s+)*-r\b", "wipe crontab"),
    (r">\s*/dev/sd[a-z]", "raw disk overwrite"),
    (r"\bmktable\b|\bparted\b[^\n]*\brm\b", "partition table rewrite"),
    # ---- Windows dangerous commands (same red line on Windows hosts) ----
    (r"\bformat\s+[a-z]:", "disk format"),
    (r"\bdiskpart\b", "disk partitioning"),
    (r"\bbcdedit\b", "boot configuration edit"),
    (r"\b(del|erase)\b[^&|\n]*/s\b[^&|\n]*/q\b[^&|\n]*[a-z]:[\\/]{0,2}\s*$", "recursive quiet delete of a drive root"),
    (r"\b(rd|rmdir)\b[^&|\n]*/s\b[^&|\n]*[a-z]:[\\/]{0,2}\s*$", "recursive delete of a drive root"),
    (r"\breg(\.exe)?\s+(add|delete|restore)\s+HK(LM|CU)?(\\SYSTEM|\\SOFTWARE|\\SAM|\\SECURITY)", "registry critical-hive edit"),
    (r"\bnet(\.exe)?\s+user\b", "windows account manipulation"),
    (r"\bnet(\.exe)?\s+localgroup\s+administrators\b", "admin group manipulation"),
    (r"\bsc(\.exe)?\s+(delete|stop|disabledemandstart)\s+", "windows service control"),
    (r"\bcipher(\.exe)?\s+/w", "disk wipe (cipher /w)"),
    (r"\btaskkill(\.exe)?\b[^&|\n]*/IM\b[^&|\n]*(csrss|smss|wininit|services|lsass)\.exe", "kill of a critical system process"),
]

# Writes to the AegisServerPanel installation/config are forbidden (DEV_PROMPT 禁止事项).
PANEL_PATH_PATTERN = re.compile(
    r"(dogbuddy[-_]?server[-_]?panel|/etc/dogbuddy-panel|panel/config\.|panel_data)", re.IGNORECASE)

_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in DANGEROUS_PATTERNS]


def is_dangerous_command(command: str) -> str | None:
    """Return the reason when *command* matches the blacklist, else None."""
    for rx, why in _COMPILED:
        if rx.search(command):
            return why
    return None


def _outside_workspace(path_str: str, workspace: str) -> bool:
    if not path_str:
        return False
    try:
        target = (Path(workspace) / path_str).resolve()
        root = Path(workspace).resolve()
        return not target.is_relative_to(root)
    except (OSError, ValueError):
        return True


class PermissionGate:
    """The real gate (L3). Optional confirm_fn marks that user IM confirmation is
    available; when absent, dangerous commands are denied outright."""

    def __init__(self, confirm_available: bool = False):
        self.confirm_available = confirm_available

    def check(self, tool_name: str, permission: str, args: dict[str, Any], ctx: ToolContext) -> GateDecision:
        mode = (ctx.mode or "BUILD").upper()
        scheduled = bool(ctx.extra.get("scheduled"))
        confirmed = bool(ctx.extra.get("confirmed"))

        # --- hard rule: unattended scheduled tasks never run in AUTO ---
        if mode == "AUTO" and scheduled:
            return GateDecision(False, "AUTO mode is forbidden for unattended scheduled tasks; use BUILD")

        if mode == "AUTO":
            return self._check_dangerous(args, ctx, confirmed, allow_all=True)

        if mode == "PLAN":
            if permission in ("write", "shell"):
                return GateDecision(False, f"PLAN mode blocks all {permission} operations (read-only planning)")
            return GateDecision(True)

        # --- BUILD ---
        if permission == "write":
            path = str(args.get("path", ""))
            if path and _outside_workspace(path, ctx.workspace):
                return GateDecision(False, f"BUILD mode: write outside workspace denied ({path})")
            if path and PANEL_PATH_PATTERN.search(path):
                return GateDecision(False, "modifying AegisServerPanel files is forbidden")
            return GateDecision(True)

        if permission == "shell":
            return self._check_dangerous(args, ctx, confirmed, allow_all=False)

        return GateDecision(True)

    def _check_dangerous(self, args: dict[str, Any], ctx: ToolContext, confirmed: bool,
                         allow_all: bool) -> GateDecision:
        command = str(args.get("command", ""))
        if not command:
            return GateDecision(True)
        reason = is_dangerous_command(command)
        if reason is None:
            return GateDecision(True)
        if confirmed:
            return GateDecision(True)
        if allow_all and ctx.extra.get("manual"):
            # AUTO on a manual session: still surface the danger, needs explicit confirm
            return GateDecision(False, f"dangerous command ({reason}) requires user confirmation", needs_confirm=True)
        if self.confirm_available:
            return GateDecision(False, f"dangerous command ({reason}); user IM confirmation required", needs_confirm=True)
        return GateDecision(False, f"dangerous command blocked: {reason}")
