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
# 2026-10（审查报告 P0-1）：模式原来只认 `dogbuddy[-_]?server[-_]?panel`，
# 而产品实际叫 **AegisServerPanel**——`/opt/aegis-server-panel/...` 根本匹配不到。
# 两种命名都收进来。
PANEL_PATH_PATTERN = re.compile(
    r"(dogbuddy|aegis)[-_]?(server)?[-_]?panel|/etc/dogbuddy-panel|panel/config\.|panel_data",
    re.IGNORECASE)


_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in DANGEROUS_PATTERNS]


# T4.3 三道保险之第一道 — 不可逆命令：无论信任级/放行清单/托管代审，永远需要
# 用户本人批准。subset of DANGEROUS_PATTERNS that destroys data or the machine.
IRREVERSIBLE_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*-?\w*[rR]\w*f\w*", "recursive delete"),
    (r"\bmkfs(\.\w+)?\b", "filesystem format"),
    (r"\bdd\b[^\\n]*\bof=/dev/", "raw device write"),
    (r">\s*/dev/sd[a-z]", "raw disk overwrite"),
    (r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "system power operation"),
    (r"\bformat\s+[a-z]:", "disk format"),
    (r"\bdiskpart\b", "disk partitioning"),
    (r"\bbcdedit\b", "boot configuration edit"),
    (r"\bmktable\b|\\bparted\b", "partition table rewrite"),
]


def is_irreversible_command(command: str) -> str | None:
    """Reason string if this command is irreversible, else None."""
    for pattern, reason in IRREVERSIBLE_PATTERNS:
        if re.search(pattern, command):
            return reason
    return None


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
    available; when absent, dangerous commands are denied outright.

    Batch 9 · T9.1：新增**作用域维度**（fail-closed）。管理类工具声明
    `required_scope`（project|system）；调用前校验「目标资源 ∈ 调用者 Scope」。
    判定不出来 = 拒绝——宁可错杀不可放行（越权是安全红线）。
    """

    def __init__(self, confirm_available: bool = False, scope_resolver: Any = None):
        self.confirm_available = confirm_available
        self.scope_resolver = scope_resolver

    def check(self, tool_name: str, permission: str, args: dict[str, Any],
              ctx: ToolContext, required_scope: str = "") -> GateDecision:
        # --- T9.1 作用域裁决（在模式矩阵之前：作用域是更硬的约束）---
        scope_denied = self.check_scope(tool_name, args, ctx, required_scope)
        if scope_denied is not None:
            return scope_denied

        mode = (ctx.mode or "BUILD").upper()
        scheduled = bool(ctx.extra.get("scheduled"))
        confirmed = bool(ctx.extra.get("confirmed"))

        # --- v0.10.50 · 方案B：宿主机命令**每次都要人确认** ---
        # 背景（P0，2026-10-22 实测）：应用进程的 env 里有 DOGBUDDY_PANEL_TOKEN
        # （= 面板 root）。它本来是给 server_exec 用的，但员工只要有 shell，写个 .py
        # 跑一下就能读到同一个令牌，绕过工作区边界与危险命令检查直接拿 root。
        # 彻底修法是"令牌不进应用进程"（独立受限代理），本轮先按 owner 选的方案 B
        # 把敞口降一级：**server_exec 一律 needs_confirm**，人点了才执行。
        # 注意 `irreversible=True`：托管代审（approval_reviewer）绝不能自动放行它。
        if tool_name == "server_exec" and not confirmed:
            cmd = str(args.get("command", ""))[:150]
            return GateDecision(
                False, f"宿主机命令必须人工确认（server_exec）：{cmd}",
                needs_confirm=True, irreversible=True)

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

    # ------------------------------------------------------- T9.1 作用域
    def check_scope(self, tool_name: str, args: dict[str, Any], ctx: ToolContext,
                    required_scope: str = "") -> GateDecision | None:
        """返回拒绝决定，或 None（放行）。

        **fail-closed**：工具声明了 `required_scope` 却无法判定目标归属 → 拒绝。
        未声明 `required_scope` 的工具（读写文件/shell 等）不走这条路。
        """
        required = str(required_scope or "").strip().lower()
        if not required:
            return None  # 普通工具：作用域管不着

        scope = getattr(ctx, "scope", None)
        if scope is None:
            return GateDecision(False, f"{tool_name} 需要 {required} 作用域，但调用者未声明作用域（拒绝）")
        if required == "system" and not scope.is_system:
            return GateDecision(
                False, f"越权：{tool_name} 需要 system 作用域（仅管理员），"
                       f"当前 {scope.describe()}")
        if required == "project_read":
            # 读级：项目成员即可（员工只读自己的项目）。比 "project" 弱，但**不是**空检查
            # —— `project_id` 是模型自报的，必须校验目标 ∈ 你的可读项目集
            # （审查报告 P1-4：降成 "" 会让任何工位读任何项目的记忆）。
            if scope.is_system:
                return None
            target_pid, how = self.resolve_target_project(args, ctx)
            if not target_pid:
                return GateDecision(
                    False, f"越权：无法判定 {tool_name} 的目标项目（{how}）；"
                           f"作用域校验失败即拒绝（fail-closed）。")
            if not scope.allows_read(target_pid):
                return GateDecision(
                    False, f"越权：目标项目 {target_pid} 不在你的可读范围 "
                           f"{scope.describe()} 内（只能读自己所属项目的共享记忆）")
            return None
        if required == "project":
            if scope.is_system:
                return None
            if scope.rank == 0:
                return GateDecision(False, f"越权：{tool_name} 需要 project 作用域，"
                                           f"当前 {scope.describe()}")
            target_pid, how = self.resolve_target_project(args, ctx)
            if not target_pid:
                # fail-closed：说得出需要 project，却定位不到目标 → 拒绝
                return GateDecision(
                    False, f"越权：无法判定 {tool_name} 的目标项目（{how}）；"
                           f"作用域校验失败即拒绝（fail-closed）。"
                           f"请显式传入 project_id/window_id 之一。")
            if not scope.allows_project(target_pid):
                return GateDecision(
                    False, f"越权：目标属于项目 {target_pid}，不在你的作用域 "
                           f"{scope.describe()} 内（主管只能管自己负责的项目）")
        return None

    def resolve_target_project(self, args: dict[str, Any], ctx: ToolContext) -> tuple[str, str]:
        """从参数里推出"这次调用想动哪个项目"。返回 (project_id, 依据说明)。"""
        resolver = self.scope_resolver
        if resolver is None:
            from .scope import ScopeResolver
            resolver = ScopeResolver(getattr(ctx, "db", None))

        # 1) 直接给了 project_id
        pid = str(args.get("project_id") or args.get("project") or "").strip()
        if pid:
            return pid, "args.project_id"
        # 2) 给了工位 → 查它挂在哪个项目
        for key in ("window_id", "window", "target_window"):
            wid = str(args.get(key) or "").strip()
            if wid:
                resolved = resolver.project_of_window(wid)
                return resolved, f"window:{wid}→project"
        # 3) 群组 / 任务 / 迭代
        gref = str(args.get("group_id") or args.get("group") or "").strip()
        if gref:
            return resolver.project_of_group(gref), f"group:{gref}→project"
        jid = str(args.get("job_id") or args.get("task_id") or "").strip()
        if jid:
            return resolver.project_of_job(jid), f"job:{jid}→project"
        iid = str(args.get("iteration_id") or "").strip()
        if iid:
            return resolver.project_of_iteration(iid), f"iteration:{iid}→project"
        # 4) 退路：调用者自己就是某个项目的（主管给自己项目建任务时常见）
        own = getattr(ctx, "extra", {}).get("project_id")
        if own:
            return str(own), "ctx.project_id"
        return "", "无可判定的目标"

    def _check_dangerous(self, args: dict[str, Any], ctx: ToolContext, confirmed: bool,
                         allow_all: bool) -> GateDecision:
        command = str(args.get("command", ""))
        if not command:
            return GateDecision(True)
        # AegisServerPanel 的安装/配置**任何权限下都不得触碰**（DEV_PROMPT 禁止事项）。
        # 初版只在 permission=="write" 时查 path 参数，于是 `shell`/`server_exec`
        # 以 shell 权限执行 `echo x > /etc/dogbuddy-panel/config.json` 直接放行
        # （审查报告 P0-1 附带发现）。这里补在 shell 通道上，且优先于危险命令判定。
        if PANEL_PATH_PATTERN.search(command):
            return GateDecision(False, "modifying AegisServerPanel files is forbidden")
        reason = is_dangerous_command(command)
        irreversible = is_irreversible_command(command)
        # T4.3: the irreversible check is WIDER than the dangerous list —
        # e.g. `rm -rf /important` kills a whole project dir without matching
        # the "top-level path" pattern, yet is never auto-allowable.
        if reason is None and irreversible is None:
            return GateDecision(True)
        if confirmed:  # an approved replay (T4.1) — the human already said yes
            return GateDecision(True)
        reason = reason or f"irreversible operation ({irreversible})"
        # T4.2: ELEVATED workbench + allowlisted command → auto-allow,
        # UNLESS the command is irreversible (first of the three safeguards)
        if not irreversible and str(ctx.extra.get("trust", "")) == "elevated":
            for pat in (ctx.extra.get("approval_allowlist") or []):
                pat = str(pat or "").strip()
                if not pat:
                    continue
                try:
                    matched = bool(re.search(pat, command)) or command.startswith(pat)
                except re.error:
                    matched = command.startswith(pat)
                if matched:
                    return GateDecision(True)
        flag = bool(irreversible)
        if allow_all and ctx.extra.get("manual"):
            # AUTO on a manual session: still surface the danger, needs explicit confirm
            return GateDecision(
                False, f"dangerous command ({reason}) requires user confirmation",
                needs_confirm=True, irreversible=flag)
        if self.confirm_available:
            return GateDecision(
                False, f"dangerous command ({reason}); user IM confirmation required",
                needs_confirm=True, irreversible=flag)
        return GateDecision(False, f"dangerous command blocked: {reason}")
