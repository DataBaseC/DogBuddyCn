# DogBuddyCn — worker_programmer tool assembly (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Assemble the programmer toolbox from L4 tools, wired with the L3 permission gate."""
from __future__ import annotations

from ..core.permissions import PermissionGate
from ..core.roles import RoleSpec, get_role
from ...tools.registry import ToolRegistry, build_registry


def build_worker_registry(role: str | RoleSpec = "worker_programmer",
                          gate: PermissionGate | None = None,
                          audit: any = None) -> ToolRegistry:
    spec = role if isinstance(role, RoleSpec) else get_role(role)
    if spec is None:
        spec = get_role("worker_programmer")
    registry = build_registry(gate=gate or PermissionGate(), audit=audit)
    # tool set is enforced at schema level (AgentLoop filters by spec.tool_names);
    # the registry itself stays complete so explore_subagent can rebuild subsets.
    return registry
