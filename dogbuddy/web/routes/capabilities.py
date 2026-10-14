# DogBuddyCn — capability catalogue routes (L1 Interface, Batch 1 / T1.2)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""GET /api/capabilities — the console's capability page is generated from the
real tool registry + role registry, never hand-maintained.

A new user's complaint was "功能都被封装进管理员工具里了，新用户根本不知道有什么工具".
This endpoint makes every capability self-describing: name, plain-language
description, parameters (from the function-calling JSON schema), permission
level, which roles have it, and a coarse risk label for the UI.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

_RISK = {"read": "low", "write": "mid", "network": "mid", "shell": "high"}


def _args_summary(parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a JSON schema's properties into console-friendly arg rows."""
    props = parameters.get("properties") or {}
    required = set(parameters.get("required") or [])
    rows = []
    for name, spec in props.items():
        spec = spec or {}
        rows.append({
            "name": name,
            "type": spec.get("type", "any"),
            "required": name in required,
            "description": spec.get("description", ""),
            "default": spec.get("default"),
            "enum": spec.get("enum"),
        })
    return rows


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["capabilities"])

    @r.get("/capabilities", dependencies=auth_deps)
    def capabilities() -> dict:
        # Two tool packs exist (worker tools + admin management pack); the
        # catalogue must show ALL of them or new users stay blind — the very
        # complaint that started T1.2.
        from dogbuddy.agents.admin.tools import AdminServices, build_admin_tools
        from dogbuddy.agents.core.roles import list_roles
        from dogbuddy.agents.worker_programmer.tools import build_worker_registry

        registry = build_worker_registry("admin")
        entries: list[Any] = [registry.get(n) for n in sorted(registry.names())]
        entries += build_admin_tools(AdminServices())  # metadata only, ports stay None

        # T5.3: bridged MCP tools on the same shelf, labelled by source
        mcp_tools = []
        bridge = getattr(getattr(svc, "windows", None), "mcp_bridge", None)
        if bridge is not None:
            try:
                mcp_tools = list(bridge.tools)
            except Exception:  # noqa: BLE001 — catalogue degrades, never 500s
                mcp_tools = []
        entries += mcp_tools

        tools = []
        for tool in entries:
            if tool is None:
                continue
            perm = getattr(tool, "permission", "read")
            tools.append({
                "name": getattr(tool, "name", ""),
                "title": getattr(tool, "title", "") or getattr(tool, "name", ""),
                "description": getattr(tool, "description", ""),
                "permission": perm,
                "risk": _RISK.get(perm, "mid"),
                "source": getattr(tool, "source", "builtin"),
                "args": _args_summary(getattr(tool, "parameters", {}) or {}),
            })
        tools.sort(key=lambda t: t["name"])

        roles = []
        for spec in list_roles():
            roles.append({
                "name": spec.name,
                "display_name": spec.display_name,
                "default_mode": spec.default_mode,
                "is_admin": spec.is_admin,
                "tool_names": list(spec.tool_names),
            })

        names = {t["name"] for t in tools}
        for role in roles:
            role["tool_names"] = [n for n in role["tool_names"] if n in names]
            role["tool_count"] = len(role["tool_names"])

        return {
            "tools": tools,
            "roles": roles,
            "tool_count": len(tools),
            "permissions": sorted({t["permission"] for t in tools}),
        }

    return r
