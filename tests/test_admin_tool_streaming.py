# DogBuddyCn — 管理员工具调用实时显示 + 折叠（v0.10.51）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""owner："管理员调用工具不是实时显示，且没有折叠选项。"

以前工具卡片只在整轮结束后随 `/messages` 一起返回：一轮 10~90 秒里界面像卡死。
现在每次工具返回就发一帧 SSE `admin.tool`（`AgentLoop._run_tool` 里的 `on_tool` 钩子，
不是各个工具自己发——那样要改几十处，也会漏掉子代理），前端边跑边折成一张卡片。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.events.bus import ADMIN_TOOL, Event, EventBus  # noqa: E402
from dogbuddy.services.admin_service import AdminService  # noqa: E402
from dogbuddy.tools.registry import ToolResult  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


class _Bus:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def test_admin_service_emits_tool_events():
    bus = _Bus()
    admin = AdminService(db=None, llm_factory=lambda: None, windows=None, jobs=None,
                         sessions=None, workspaces=None, memory=None, config=None,
                         bus=bus)
    hook = admin._tool_emitter("default")
    assert callable(hook)
    hook("read_file", {"path": "a.txt"}, ToolResult.success("hello"))
    hook("shell", {"command": "rm -rf /"}, ToolResult.failure("blocked"))

    assert len(bus.events) == 2
    first, second = bus.events
    assert first.type == ADMIN_TOOL
    assert first.payload["conversation"] == "default"
    assert first.payload["name"] == "read_file" and first.payload["ok"] is True
    assert second.payload["ok"] is False
    assert "blocked" in second.payload["output"]
    # 参数摘要必须是可读 JSON（前端直接展示，不做二次解析）
    assert '"path"' in first.payload["args"]


def test_no_bus_means_no_hook():
    admin = AdminService(db=None, llm_factory=lambda: None, windows=None, jobs=None,
                         sessions=None, workspaces=None, memory=None, config=None,
                         bus=None)
    assert admin._tool_emitter("default") is None


def test_loop_calls_the_on_tool_hook():
    """钩子必须挂在 loop 的统一执行点，否则子代理/新工具都会漏。"""
    from dogbuddy.agents.core.loop import AgentLoop

    seen = []

    class _Reg:
        async def execute(self, name, args, ctx, approved=False):
            return ToolResult.success("ok")

    class _Session:
        def __init__(self):
            self.rows = []

        def append(self, *a, **k):
            self.rows.append((a, k))

    class _TC:
        name = "read_file"
        arguments = {"path": "x"}

    loop = AgentLoop(llm=None, registry=_Reg(), session=_Session())
    from dogbuddy.tools.registry import ToolContext
    ctx = ToolContext(window_id="admin:default", workspace=".")
    ctx.extra["on_tool"] = lambda n, a, r: seen.append((n, a, r.ok))
    asyncio.new_event_loop().run_until_complete(loop._run_tool(_TC(), ctx, type("R", (), {})()))
    assert seen == [("read_file", {"path": "x"}, True)]


def test_hook_failure_does_not_break_the_tool():
    from dogbuddy.agents.core.loop import AgentLoop
    from dogbuddy.tools.registry import ToolContext

    class _Reg:
        async def execute(self, name, args, ctx, approved=False):
            return ToolResult.success("ok")

    class _Session:
        def append(self, *a, **k):
            pass

    class _TC:
        name = "read_file"
        arguments = {}

    loop = AgentLoop(llm=None, registry=_Reg(), session=_Session())
    ctx = ToolContext(window_id="w", workspace=".")
    ctx.extra["on_tool"] = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    res = asyncio.new_event_loop().run_until_complete(loop._run_tool(_TC(), ctx, type("R", (), {})()))
    assert res.ok is True and res.output == "ok"


# ------------------------------------------------------------------ 前端
def test_frontend_renders_collapsible_tool_cards():
    js = (REPO / "dogbuddy/web/static/js/views-chat.js").read_text(encoding="utf-8")
    assert "global.SSE.on('admin.tool', onTool)" in js, "对话页没订阅 admin.tool"
    assert "function toolDetailsHtml(" in js
    assert '<details class="chat-tools"' in js, "工具卡片必须可折叠（原生 details）"
    assert "工具调用 ×" in js and "正在调用工具 ×" in js
    # 收尾时清掉实时卡片，交给持久化消息（否则一轮跑完会重复显示两份）
    assert "state.liveTools = [];" in js
    assert "#chat-tool-live" in js


def test_history_dto_exposes_tool_name():
    """折叠卡片要显示真实工具名；DTO 少一列，前端只能写死 "tool"（实测到了）。"""
    src = (REPO / "dogbuddy/services/admin_service.py").read_text(encoding="utf-8")
    assert '"tool_name":' in src, "管理员 history DTO 必须带 tool_name"
    js = (REPO / "dogbuddy/web/static/js/views-chat.js").read_text(encoding="utf-8")
    assert "m.tool_name || meta.tool_name" in js


def test_group_member_meta_uses_its_own_class():
    js = (REPO / "dogbuddy/web/static/js/views-groups.js").read_text(encoding="utf-8")
    css = (REPO / "dogbuddy/web/static/css/views.css").read_text(encoding="utf-8")
    assert 'grp-member-meta' in js, "组员副标题要用专用类（不要复用 muted small）"
    assert ".grp-member-meta" in css
    assert ".grp-member-info { flex: 1; min-width: 0; display: flex;" in css, \
        "名字与副标题要分成两行，行高才不会被压"
    assert ".chat-tools" in css and ".chat-tools > summary" in css
