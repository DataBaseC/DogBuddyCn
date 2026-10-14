# DogBuddyCn — MCP bridge: external tools become internal Tools (L5, ROADMAP T5.1)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Wires MCP servers' tools into DogBuddyCn's ToolRegistry.

权限映射默认 read 从紧 (T5.2): an MCP tool carries NO permission metadata, so
it starts at `read` — the safest class (no writes, no shell). Only an explicit
`mcp.permissions` mapping (`{"server.tool": "write|shell|network"}`) upgrades
it. The name is namespaced `mcp_<server>_<tool>` so external tools can never
shadow built-ins.

Config keys (ConfigService / config table):
  mcp.enabled      bool                    — master switch
  mcp.servers      [{"name","command","args":[...],"env":{...}}]
  mcp.permissions  {"<server>.<tool>": "read|write|shell|network"}
"""
from __future__ import annotations

import logging
import re
from typing import Any

from ..tools.registry import ToolContext, ToolResult
from .mcp_client import McpClientError, StdioMcpClient

logger = logging.getLogger("dogbuddy.external.mcp.bridge")

_VALID_PERMS = {"read", "write", "shell", "network"}


def safe_tool_name(server: str, tool: str) -> str:
    """mcp_<server>_<tool>, sanitised — never collides with built-in names."""
    clean = lambda s: re.sub(r"[^a-zA-Z0-9_]", "_", s)[:40]  # noqa: E731
    return f"mcp_{clean(server)}_{clean(tool)}"


class McpToolWrapper:
    """Adapts one MCP tool to the internal Tool protocol (registry.Tool)."""

    def __init__(self, client: StdioMcpClient, server: str, name: str,
                 description: str, schema: dict[str, Any], permission: str):
        self._client = client
        self.server = server
        self.tool = name
        self.name = safe_tool_name(server, name)
        self.title = f"{server}: {name}"
        self.description = f"[MCP {server}] {description}".strip()
        self.parameters = schema or {"type": "object", "properties": {}}
        self.permission = permission if permission in _VALID_PERMS else "read"
        self.source = f"mcp:{server}"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            out = await self._client.call_tool(self.tool, args)
        except McpClientError as e:
            return ToolResult.failure(f"MCP tool error: {e}")
        except Exception as e:  # noqa: BLE001 — external code, always guarded
            logger.exception("mcp tool %s crashed", self.name)
            return ToolResult.failure(f"MCP tool crashed: {e}")
        return ToolResult.success(out)


class McpBridge:
    """Owns MCP connections; registry tools are views over them."""

    def __init__(self, config_get: Any = None):
        self.config_get = config_get
        self._clients: dict[str, StdioMcpClient] = {}
        self._tools: list[McpToolWrapper] = []
        self._started = False

    # ------------------------------------------------------------- config
    def _cfg(self, key: str, default: Any) -> Any:
        if self.config_get is None:
            return default
        try:
            return self.config_get(key, default)
        except Exception:  # noqa: BLE001
            return default

    def _permission_for(self, server: str, tool: str) -> str:
        """T5.2: default read (从紧); explicit mapping may upgrade."""
        mapping = self._cfg("mcp.permissions", {}) or {}
        return str(mapping.get(f"{server}.{tool}", mapping.get(tool, "read")) or "read")

    # ----------------------------------------------------------- lifecycle
    async def start(self) -> int:
        """Connect every configured server; returns the number of tools found."""
        if not self._cfg("mcp.enabled", False):
            return 0
        servers = self._cfg("mcp.servers", []) or []
        for spec in servers:
            if not isinstance(spec, dict) or not spec.get("command"):
                continue
            name = str(spec.get("name") or spec["command"])
            client = StdioMcpClient(command=str(spec["command"]),
                                    args=[str(a) for a in (spec.get("args") or [])],
                                    env={str(k): str(v) for k, v in (spec.get("env") or {}).items()},
                                    name=name)
            try:
                await client.start()
            except Exception as e:  # noqa: BLE001 — one bad server never blocks the rest
                logger.warning("mcp server %s failed to start: %s", name, e)
                continue
            self._clients[name] = client
            try:
                for t in await client.list_tools():
                    self._tools.append(McpToolWrapper(
                        client, name, t.name, t.description, t.input_schema,
                        self._permission_for(name, t.name)))
            except Exception as e:  # noqa: BLE001
                logger.warning("mcp server %s tools/list failed: %s", name, e)
        self._started = True
        logger.info("mcp bridge up: %d server(s), %d tool(s)",
                    len(self._clients), len(self._tools))
        return len(self._tools)

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
        self._tools.clear()
        self._started = False

    # --------------------------------------------------------------- view
    @property
    def tools(self) -> list[McpToolWrapper]:
        return list(self._tools)

    @property
    def started(self) -> bool:
        return self._started

    def register_into(self, registry: Any) -> int:
        """Add every bridged tool to an internal ToolRegistry (per window)."""
        n = 0
        for tool in self._tools:
            try:
                registry.register(tool)
                n += 1
            except Exception:  # noqa: BLE001
                logger.exception("mcp tool %s register failed", tool.name)
        return n
