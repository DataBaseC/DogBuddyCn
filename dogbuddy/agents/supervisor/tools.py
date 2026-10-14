# DogBuddyCn — supervisor tool assembly (L3 Domain, Batch 9 · T9.3)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""主管工位的工具装配：**员工工具 + 项目级管理工具**（不含任何 system 级）。

为什么主管要有员工工具：ROLES.md 说主管"必要时可亲自下场干一小段"；只给管理工具
的话这句话无法成立（没有 read_file/shell 怎么下场）。文档里"主管工具集只有管理工具"
与"必要时可下场"自相矛盾，这里按 ROADMAP T9.3 的写法收敛：
**员工工具 + scoped 管理工具**。

越权防线与本装配无关（装配只是"少给"），真正的裁决在
`PermissionGate.check_scope`：即使有人绕过装配把管理工具塞给主管，
运行时按作用域校验也会拒绝（fail-closed）。
"""
from __future__ import annotations

from typing import Any

from ..core.permissions import PermissionGate
from ..core.roles import RoleSpec, get_role
from ..management.tools import build_supervisor_tools
from ...tools.registry import ToolRegistry, build_registry


def build_supervisor_registry(svc: Any,
                              role: str | RoleSpec = "supervisor",
                              gate: PermissionGate | None = None,
                              audit: Any = None,
                              jobs: Any = None,
                              run_job_now: Any = None,
                              include_system: bool = False) -> ToolRegistry:
    """装出一个主管工位的注册表。

    `include_system=False`（默认）= 拒绝下放 system 级工具；
    传 True 只用于测试对照（生产装配绝不该用）。
    """
    spec = role if isinstance(role, RoleSpec) else get_role(role)
    registry = build_registry(gate=gate or PermissionGate(), audit=audit,
                              jobs=jobs, run_job_now=run_job_now)
    scopes = None if include_system else {"project"}
    for tool in build_supervisor_tools(svc, scopes=scopes):
        registry.register(tool)
    return registry
