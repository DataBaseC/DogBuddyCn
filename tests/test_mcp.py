# DogBuddyCn — Batch 5 tests: MCP bridge (T5.x)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dogbuddy.external.mcp_bridge import McpBridge, McpToolWrapper, safe_tool_name
from dogbuddy.tools.registry import ToolContext, ToolRegistry


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


FAKE_SERVER = textwrap.dedent("""
    import sys, json
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue
        m = msg.get("method")
        if m == "initialize":
            result = {"serverInfo": {"name": "fake-server"}}
        elif m == "tools/list":
            result = {"tools": [{"name": "echo", "description": "echo text back",
                                 "inputSchema": {"type": "object",
                                                 "properties": {"text": {"type": "string"}}}}]}
        elif m == "tools/call":
            args = msg["params"].get("arguments") or {}
            result = {"content": [{"type": "text", "text": "echo:" + str(args.get("text", ""))}]}
        else:
            result = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\\n")
        sys.stdout.flush()
    """)


def _server_spec(tmp_path):
    p = tmp_path / "fake_mcp_server.py"
    p.write_text(FAKE_SERVER, encoding="utf-8")
    return {"name": "fake", "command": sys.executable, "args": [str(p)]}


# ---------------------------------------------------------------- naming
def test_tool_names_are_namespaced():
    name = safe_tool_name("my server", "do/thing")
    assert name == "mcp_my_server_do_thing"
    assert not name.startswith("shell")  # 永不冒充内置


# ---------------------------------------------------------------- client
def test_stdio_client_full_roundtrip(tmp_path):
    from dogbuddy.external.mcp_client import StdioMcpClient
    spec = _server_spec(tmp_path)

    async def scenario():
        client = StdioMcpClient(spec["command"], spec["args"], name="fake")
        await client.start()
        try:
            tools = await client.list_tools()
            assert [t.name for t in tools] == ["echo"]
            out = await client.call_tool("echo", {"text": "hi"})
            assert out == "echo:hi"
            return True
        finally:
            await client.close()

    assert _run(scenario()) is True


# ---------------------------------------------------------------- bridge
def test_bridge_defaults_to_read_and_registers(tmp_path):
    spec = _server_spec(tmp_path)
    cfg = {"mcp.enabled": True, "mcp.servers": [spec]}

    async def scenario():
        bridge = McpBridge(config_get=lambda k, d=None: cfg.get(k, d))
        n = await bridge.start()
        try:
            assert n == 1
            tool = bridge.tools[0]
            assert tool.permission == "read"      # T5.2 默认从紧
            assert tool.source == "mcp:fake"
            assert tool.name == "mcp_fake_echo"

            reg = ToolRegistry()
            assert bridge.register_into(reg) == 1
            ctx = ToolContext(window_id="w1", workspace="/tmp", mode="BUILD")
            res = await reg.execute("mcp_fake_echo", {"text": "yo"}, ctx)
            assert res.ok and res.output == "echo:yo"
        finally:
            await bridge.close()

    _run(scenario())


def test_bridge_permission_upgrade_is_explicit(tmp_path):
    spec = _server_spec(tmp_path)
    cfg = {"mcp.enabled": True, "mcp.servers": [spec],
           "mcp.permissions": {"fake.echo": "network"}}

    async def scenario():
        bridge = McpBridge(config_get=lambda k, d=None: cfg.get(k, d))
        await bridge.start()
        try:
            assert bridge.tools[0].permission == "network"  # 显式提升才生效
        finally:
            await bridge.close()

    _run(scenario())


def test_bridge_disabled_by_default(tmp_path):
    spec = _server_spec(tmp_path)

    async def scenario():
        bridge = McpBridge(config_get=lambda k, d=None: {} if k != "mcp.enabled" else False)
        return await bridge.start()

    assert _run(scenario()) == 0  # 未启用=零工具（从紧）


def test_invalid_permission_falls_back_to_read(tmp_path):
    tool = McpToolWrapper(client=None, server="s", name="t", description="",
                          schema={}, permission="root")
    assert tool.permission == "read"  # 非法映射一律回 read
