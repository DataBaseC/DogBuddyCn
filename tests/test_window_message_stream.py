# DogBuddyCn — 控制台实时推送"新消息"（含工具调用）（v0.10.36）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测："员工调用工具的时候，并没显示，得刷新一下才行。"

根因（已核实）：后端 `WindowSession.append()` **只写库、不广播**；唯一实时通道是
`window.stream`（助手**文本增量**）。于是 `tool`（工具调用）与 `system`
（`[限速暂停]`/`[越权拦截]` 等）消息从来不推 —— 界面只能刷新后重拉 `/messages`。

修法：会话新增 `on_message` 回调，`WindowManager._boot` / `AdminService.session_for`
把它接到事件总线（`window.message`，载荷与 `/messages` 的 DTO 同形状），
前端 `views-main.js` 订阅后直接插进列表。

注意：`EventBus.emit()` 是**入队**，真正分发要事件循环里的 `_run()` 任务 ——
所以这些测试都在一个跑起来的循环里做，并在断言前让出一次调度。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402


async def _drain(times: int = 6) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


def _fixture(tmp_path, bus):
    db = Database(str(tmp_path / "m.db"))
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=bus,
                       checkpoints_dir=tmp_path / "ckpt")
    ws = tmp_path / "ws"
    ws.mkdir()
    w = db.insert_window(Window(id="w1", name="worker-1", role="worker_programmer",
                                mode="BUILD", workspace_path=str(ws), model=""))
    return db, wm, w


def test_工具消息会实时推送(tmp_path):
    """这是 owner 抱怨的那一条：工具调用必须立刻推出去。"""
    async def main():
        bus = EventBus()
        got: list = []
        bus.subscribe("*", lambda e: got.append(e))
        await bus.start()
        try:
            db, wm, w = _fixture(tmp_path, bus)
            rt = wm._rt(w.id)                 # 首次访问即 _boot，装上 on_message
            rt.session.append("tool", "Wrote 5191 bytes to styles/editorial.css",
                              meta={"tool_name": "write_file", "tool_call_id": "call_1"})
            await _drain()
            assert got, "工具消息必须广播（否则界面只能靠刷新）"
            p = got[-1].payload
            assert got[-1].type == "window.message"
            assert p["window_id"] == w.id
            m = p["message"]
            assert m["role"] == "tool" and m["tool_name"] == "write_file"
            assert m["tool_call_id"] == "call_1"
            assert "editorial.css" in m["content"]
            db.close()
        finally:
            await bus.stop()

    asyncio.new_event_loop().run_until_complete(main())


def test_系统消息也推_限速暂停这类提示不再要刷新(tmp_path):
    async def main():
        bus = EventBus()
        got: list = []
        bus.subscribe("*", lambda e: got.append(e))
        await bus.start()
        try:
            db, wm, w = _fixture(tmp_path, bus)
            wm._rt(w.id).session.append("system", "[限速暂停] 服务端要求等待 600 秒")
            await _drain()
            assert any("限速暂停" in e.payload["message"]["content"] for e in got), got
            db.close()
        finally:
            await bus.stop()

    asyncio.new_event_loop().run_until_complete(main())


def test_没有总线也不能崩(tmp_path):
    """装配可缺省：没有 bus 时会话照常工作（只是不推）。"""
    db, wm, w = _fixture(tmp_path, None)
    m = wm._rt(w.id).session.append("tool", "x", meta={"tool_name": "shell"})
    assert m is not None
    db.close()


def test_推送失败不影响写库(tmp_path):
    """回调抛异常时必须被吞掉（推送坏掉不能把会话写坏）。"""
    async def main():
        bus = EventBus()

        def boom(_e):
            raise RuntimeError("sse down")

        bus.subscribe("*", boom)
        await bus.start()
        try:
            db, wm, w = _fixture(tmp_path, bus)
            m = wm._rt(w.id).session.append("assistant", "照常落库")
            await _drain()
            assert m is not None and m.content == "照常落库"
            db.close()
        finally:
            await bus.stop()

    asyncio.new_event_loop().run_until_complete(main())


def test_载荷形状与_messages_接口一致(tmp_path):
    """前端是"直接塞进列表"的，形状必须和 `/messages` 的 DTO 对齐。"""
    async def main():
        bus = EventBus()
        got: list = []
        bus.subscribe("*", lambda e: got.append(e))
        await bus.start()
        try:
            db, wm, w = _fixture(tmp_path, bus)
            wm._rt(w.id).session.append("tool", "ok", meta={"tool_name": "shell"})
            await _drain()
            dto = wm.recent_messages(w.id, limit=5)[-1]
            pushed = [e for e in got if e.type == "window.message"][-1].payload["message"]
            for k in ("id", "role", "content", "created_at", "tokens", "tool_name",
                      "tool_call_id", "meta"):
                assert k in pushed, f"推送缺字段 {k}"
                assert pushed[k] == dto[k], \
                    f"字段 {k} 与 /messages 不一致：{pushed[k]!r} != {dto[k]!r}"
            db.close()
        finally:
            await bus.stop()

    asyncio.new_event_loop().run_until_complete(main())
