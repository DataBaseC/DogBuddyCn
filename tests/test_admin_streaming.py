# DogBuddyCn — 管理员也要流式输出（打字机）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 问："管理员为啥不是流式输出？"

答案：**工位**有 `window.stream`（`window_manager._on_delta` → SSE → 打字机气泡），
而**管理员**那条路径 `AdminService._run_turn` 调 `loop.run(prompt, ctx)` 时
**根本没传 `on_delta`** —— 所以控制台上永远是"发送中… → 整段蹦出来"。

修法：管理员也走流式，但用**自己的事件名** `admin.stream`
（管理员没有工位，复用 `window.stream` 只会让前端按 window_id 找不到东西），
payload 带 `conversation` 让多对话不串屏；服务端 60ms 合并一次增量，
否则每个 token 一帧会把 SSE 队列（500 上限、满了丢最旧）刷爆。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import ADMIN_STREAM, EventBus  # noqa: E402
from dogbuddy.llm.provider import LLMResponse  # noqa: E402
from dogbuddy.memory.service import MemoryService  # noqa: E402
from dogbuddy.services.admin_service import AdminService  # noqa: E402
from dogbuddy.services.config_service import ConfigService  # noqa: E402
from dogbuddy.services.job_manager import JobManager  # noqa: E402
from dogbuddy.services.session_manager import SessionManager  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.services.workspace_manager import WorkspaceManager  # noqa: E402
from dogbuddy.cron.jobs import JobStore  # noqa: E402

from helpers import FakeLLM, run  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _admin(tmp_path, script):
    db = Database(tmp_path / "t.db")
    bus = EventBus()
    seen: list = []
    bus.subscribe("*", lambda e: seen.append(e))
    cfg = AppConfig(llm=LLMConfig(), paths=PathsConfig(
        data_dir=str(tmp_path), workspaces_dir=str(tmp_path / "ws")))
    llm = FakeLLM(script)
    wm = WindowManager(db=db, llm_factory=lambda: llm, bus=bus,
                       memory=MemoryService(tmp_path / "mem"),
                       checkpoints_dir=tmp_path / "ckpt")
    sessions = SessionManager(wm, db)
    admin = AdminService(db=db, llm_factory=lambda: llm, windows=wm,
                         jobs=JobManager(JobStore(db), wm), sessions=sessions,
                         workspaces=WorkspaceManager(tmp_path / "ws", db),
                         memory=MemoryService(tmp_path / "mem"),
                         config=ConfigService(db, cfg), bus=bus)
    return db, bus, seen, admin


def test_admin_turn_emits_admin_stream_and_reconstructs_the_reply(tmp_path):
    """整段回复 = 各 delta 拼起来；每条都带 conversation（多对话不串屏）。"""
    text = "先建工位，再派任务，最后汇报结果。"
    db, bus, seen, admin = _admin(tmp_path, [LLMResponse(content=text)])

    async def main():
        await bus.start()                      # 总线是"入队 + 后台分发"，不 start 就没有分发
        try:
            return await admin.handle_user_message("帮我建个工位", sender="default")
        finally:
            await asyncio.sleep(0.05)           # 让队列里最后几帧分发完
            await bus.stop()

    reply = run(main())
    frames = [e for e in seen if e.type == ADMIN_STREAM]
    assert frames, "管理员这条路径一条 admin.stream 都没发（还是整段蹦出来）"
    assert all(f.payload.get("conversation") == "default" for f in frames)
    assert "".join(f.payload["delta"] for f in frames) == text
    assert reply == text
    db.close()


def test_admin_stream_is_scoped_per_conversation(tmp_path):
    db, bus, seen, admin = _admin(tmp_path, [LLMResponse(content="A 的回复"),
                                             LLMResponse(content="B 的回复")])

    async def main():
        await bus.start()
        try:
            await admin.handle_user_message("甲", sender="conv-a")
            await admin.handle_user_message("乙", sender="conv-b")
        finally:
            await asyncio.sleep(0.05)
            await bus.stop()

    run(main())
    by_conv: dict[str, str] = {}
    for e in seen:
        if e.type == ADMIN_STREAM:
            by_conv[e.payload["conversation"]] = by_conv.get(e.payload["conversation"], "") + e.payload["delta"]
    assert by_conv == {"conv-a": "A 的回复", "conv-b": "B 的回复"}, by_conv
    db.close()


def test_no_bus_means_no_crash(tmp_path):
    """没有总线（测试/嵌入式用法）也不能因为打字机挂掉。"""
    db = Database(tmp_path / "t.db")
    cfg = AppConfig(llm=LLMConfig(), paths=PathsConfig(
        data_dir=str(tmp_path), workspaces_dir=str(tmp_path / "ws")))
    llm = FakeLLM([LLMResponse(content="ok")])
    wm = WindowManager(db=db, llm_factory=lambda: llm, bus=None,
                       memory=MemoryService(tmp_path / "mem"), checkpoints_dir=tmp_path / "ckpt")
    admin = AdminService(db=db, llm_factory=lambda: llm, windows=wm,
                         jobs=JobManager(JobStore(db), wm),
                         sessions=SessionManager(wm, db),
                         workspaces=WorkspaceManager(tmp_path / "ws", db),
                         memory=MemoryService(tmp_path / "mem"),
                         config=ConfigService(db, cfg), bus=None)
    assert run(admin.handle_user_message("hi", sender="default")) == "ok"
    db.close()


def test_frontend_renders_the_streaming_bubble():
    js = (REPO / "dogbuddy/web/static/js/views-chat.js").read_text(encoding="utf-8")
    assert "global.SSE.on('admin.stream', onStream)" in js, "对话页没订阅 admin.stream"
    assert 'id="chat-stream-bubble"' in js and "stream-caret" in js
    assert "payload.conversation" in js, "没按对话过滤，多对话会串屏"
    assert "正在输入…" in js
    # 结束后必须收起流式气泡（否则会跟正式消息重叠）
    assert "stream.active = false" in js


def test_late_delta_cannot_recreate_the_bubble():
    """0.10.47（owner："管理员会发出莫名的气泡"）。

    实测复现：POST 已经返回、正式消息渲染完之后，服务端 finally 里补发的尾巴帧才到，
    老代码 `onStream` 自己把 stream.active 置 true → 又建了一个气泡，内容只有最后
    几个字、标题"正在输入…"，再也没人收走（截图里就是底部那条莫名气泡）。
    所以 onStream 必须**只认发送中的状态**，收尾后到达的增量一律丢弃。
    """
    js = (REPO / "dogbuddy/web/static/js/views-chat.js").read_text(encoding="utf-8")
    marker = "function onStream(payload) {"
    body = js[js.index(marker):js.index("function setBusy(")]
    assert "if (!stream.active) return;" in body, "收尾后到达的尾巴帧还会重建气泡"
    assert "stream.active = true" not in body, "onStream 不能再自己置 active（那正是残留气泡的来源）"
    # 收尾必须是幂等的单一出口，两条路径（成功/失败）都要走它
    assert "function finishStreamTurn()" in js
    assert js.count("finishStreamTurn();") >= 2
    assert "clearInterval" in js, "等待计时器必须被清掉，否则一直 setInterval"


def test_blank_assistant_messages_are_not_rendered():
    """工具调用轮的 assistant 消息 content 只有空白 —— 以前照样渲染成空气泡。

    工位视图 0.10.43 已修，对话页漏了；这里用 DB 里真实出现的那些 "  " 消息做断言。
    """
    js = (REPO / "dogbuddy/web/static/js/views-chat.js").read_text(encoding="utf-8")
    assert "function visibleMessages()" in js
    assert "String(m.content || '').trim() !== ''" in js
    assert "state.messages.map(bubble)" not in js, "渲染仍在用未过滤的 state.messages"


def test_first_token_wait_is_visible():
    """首帧经常要等 20–90s（实测），期间页面必须能证明自己没卡死，否则人就手动刷新。"""
    js = (REPO / "dogbuddy/web/static/js/views-chat.js").read_text(encoding="utf-8")
    assert "function startTick()" in js and "setInterval" in js
    assert "等待模型响应… " in js
    assert "elapsedSec()" in js
