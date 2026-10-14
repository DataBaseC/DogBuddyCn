# DogBuddyCn — minimal MCP stdio client (L5 Infrastructure, ROADMAP T5.1)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Model Context Protocol client over stdio — zero dependencies on purpose.

The sandbox may have no pip access at runtime, so this speaks raw JSON-RPC 2.0
over a subprocess's stdin/stdout (newline-delimited), which is exactly what MCP
stdio servers use. Supported methods: initialize, tools/list, tools/call.

Deliberately NOT a full MCP SDK: no resources, no prompts, no SSE transport.
Tools are all DogBuddyCn needs to widen its capability surface (T5.1).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("dogbuddy.external.mcp")

PROTOCOL_VERSION = "2024-11-05"


@dataclass
class McpTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


class McpClientError(RuntimeError):
    pass


class StdioMcpClient:
    """One MCP server connection (subprocess + JSON-RPC)."""

    def __init__(self, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None, name: str = ""):
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.name = name or command
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self.server_info: dict[str, Any] = {}

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> dict[str, Any]:
        self._proc = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**self.env} if self.env else None)
        self._reader_task = asyncio.get_event_loop().create_task(self._read_loop())
        self.server_info = await self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "DogBuddyCn", "version": "2"},
        })
        # servers expect the initialized notification before other calls
        self._notify("notifications/initialized", {})
        return self.server_info

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._proc:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            self._proc = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # ------------------------------------------------------------ requests
    async def list_tools(self) -> list[McpTool]:
        data = await self._request("tools/list", {})
        tools = []
        for t in (data.get("tools") or []):
            tools.append(McpTool(
                name=str(t.get("name", "")),
                description=str(t.get("description", "")),
                input_schema=dict(t.get("inputSchema") or {})))
        return [t for t in tools if t.name]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Run one tool; MCP results are content blocks — flatten to text."""
        data = await self._request("tools/call",
                                  {"name": name, "arguments": arguments})
        parts = []
        for block in (data.get("content") or []):
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, dict):
                parts.append(json.dumps(block, ensure_ascii=False)[:2000])
        if data.get("isError"):
            raise McpClientError("".join(parts)[:500] or f"tool {name} failed")
        return "\n".join(parts)[:20000]

    # ----------------------------------------------------------- transport
    def _send(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise McpClientError(f"mcp server {self.name} not running")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(self, method: str, params: dict[str, Any],
                       timeout: float = 30.0) -> Any:
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as e:
            raise McpClientError(f"mcp {self.name}: {method} timed out") from e
        finally:
            self._pending.pop(rid, None)

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except (json.JSONDecodeError, ValueError):
                    continue  # tolerate stray output
                rid = msg.get("id")
                fut = self._pending.get(rid) if rid is not None else None
                if fut is None or fut.done():
                    continue
                if "error" in msg:
                    err = msg["error"] or {}
                    fut.set_exception(McpClientError(
                        f"mcp {self.name}: {err.get('message', err)}"))
                else:
                    fut.set_result(msg.get("result") or {})
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — reader death must not crash the app
            logger.exception("mcp reader died for %s", self.name)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(McpClientError(f"mcp {self.name} disconnected"))
            self._pending.clear()
