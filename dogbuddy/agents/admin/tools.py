# DogBuddyCn — admin tool assembly (L3 Domain) — 兼容层
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Batch 9 · T9.2：管理工具已统一到 `agents/management/tools.py`（角色无关）。

本模块只做**向后兼容再导出**：现有代码与测试里的
`from dogbuddy.agents.admin.tools import AdminServices, build_admin_tools`
继续可用，且**管理员行为逐位不变**（管理员 = management 工具集 + system 装配）。
新代码请直接用 `agents.management.tools`。
"""
from __future__ import annotations

from ..management.tools import (  # noqa: F401
    PROJECT_TOOLS,
    SYSTEM_TOOL_NAMES,
    AdminServices,
    FnTool,
    _p,
    _props,
    build_admin_tools,
    build_management_tools,
    build_supervisor_tools,
)