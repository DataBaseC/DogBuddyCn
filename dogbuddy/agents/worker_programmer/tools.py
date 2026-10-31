# DogBuddyCn — worker_programmer tool assembly (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Assemble the programmer toolbox from L4 tools, wired with the L3 permission gate."""
from __future__ import annotations

from ..core.permissions import PermissionGate
from ..core.roles import RoleSpec, get_role
from ...tools.registry import ToolRegistry, build_registry


def build_worker_registry(role: str | RoleSpec = "worker_programmer",
                          gate: PermissionGate | None = None,
                          audit: any = None,
                          jobs: any = None, run_job_now: any = None,
                          coordinator: any = None,
                          skills_root: str = "") -> ToolRegistry:
    spec = role if isinstance(role, RoleSpec) else get_role(role)
    if spec is None:
        spec = get_role("worker_programmer")
    # `jobs`/`run_job_now` (T8.1) 让 `schedule` 工具能真的建/触发定时任务；
    # 不注入时工具仍在架子上但执行会返回"排期服务不可用"
    registry = build_registry(gate=gate or PermissionGate(), audit=audit,
                              jobs=jobs, run_job_now=run_job_now,
                              coordinator=coordinator)
    # v0.18.0：临时 skill（只对 ctx.window_id 生效）。注册在这里而不是
    # build_registry，是为了让管理员/能力页也能看到工具，但只有员工角色
    # 的 tool_names 会真正把它发给模型。
    from ...tools import skill_ops
    for t in skill_ops.all_tools(skills_root):
        registry.register(t)
    # tool set is enforced at schema level (AgentLoop filters by spec.tool_names);
    # the registry itself stays complete so explore_subagent can rebuild subsets.
    return registry
