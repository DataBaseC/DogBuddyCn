# DogBuddyCn — 0.10.15：流式不闪 / 工位从目录选模型 / 掉线有终态与错误码
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 报的三件事（2026-10-15）：

1. "流式输出时，输出窗口会闪动" —— 每个 delta 都 `outerHTML` 换整块气泡 → 重排重绘。
2. "工位点击修改模型，应该从模型列表里直接选配置好的模型" —— 以前下拉要点了
   "拉取模型"（问服务商要**全量** 99 个）才出现；而且选的只是一个名字，
   地址/密钥仍来自 profile（跨服务商时 401/404，且很难看出原因）。
3. "工位模型掉线了，显示的还是正在输入（上方提示限速等待中），也没有错误代码" ——
   LLM 失败落库的是 **system** 消息，而前端只认新的 assistant 消息 → 死等 180s；
   限速徽标只有"亮"没有"灭"。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.loop import AgentLoop  # noqa: E402
from dogbuddy.tools.registry import ToolRegistry  # noqa: E402
from dogbuddy.windows.session import WindowSession  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import Event, EventBus  # noqa: E402
from dogbuddy.llm.profile_registry import LLMProfileRegistry  # noqa: E402
from dogbuddy.memory.service import MemoryService  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402

from helpers import run  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
JS = REPO / "dogbuddy/web/static/js"
CSS = REPO / "dogbuddy/web/static/css"


class DeadLLM:
    """模拟"掉线"：chat / stream_chat 直接抛错。

    默认用**永久错误**（ValueError）：瞬态错误会触发 loop 的 C1 续跑
    （3 次 × 10s 退避），测试没必要真等 30 秒。
    """

    def __init__(self, exc: BaseException | None = None):
        self.exc = exc or ValueError("connection reset by peer")

    async def chat(self, messages, tools=None, **kw):
        raise self.exc

    def stream_chat(self, messages, tools=None, **kw):
        async def gen():
            raise self.exc
            yield  # pragma: no cover
        return gen()


# ------------------------------------------------------------------ ③ 错误码
def test_loop_carries_the_error_code(tmp_path):
    db = Database(tmp_path / "t.db")
    from dogbuddy.ports import Window
    w = Window(id="w1", name="w", role="worker_programmer",
               workspace_path=str(tmp_path / "ws"), model="", mode="BUILD", status="idle")
    session = WindowSession(w, db, db, db)
    loop = AgentLoop(llm=DeadLLM(ValueError("model not found")), registry=ToolRegistry(),
                     session=session, builder=None, tool_names=[], max_iterations=1)
    result = run(loop.run("hi", ToolContext(window_id="w1", workspace=str(tmp_path))))
    assert result.stop_reason == "error"
    assert result.error_code, "AgentResult 没带错误码 —— 控制台只能显示'一直转圈'"
    assert result.error_detail
    assert any(m.role == "system" and "LLM error" in m.content for m in session.recent(10))
    db.close()


def test_run_finished_event_has_status_and_error_code(tmp_path):
    """一次运行结束必须发终态（带错误码）—— 前端据此立刻收起'正在输入…'。"""
    db = Database(tmp_path / "t.db")
    bus = EventBus()
    seen: list = []
    bus.subscribe("*", lambda e: seen.append(e))

    async def main():
        await bus.start()
        wm = WindowManager(db=db, llm_factory=lambda *a, **k: DeadLLM(), bus=bus,
                           memory=MemoryService(tmp_path / "mem"),
                           checkpoints_dir=tmp_path / "ckpt")
        await wm.start()
        w = wm.create(name="掉线工位", role="worker_programmer",
                      workspace_path=str(tmp_path / "ws" / "d"),
                      params={"retry": {"max_retries": 0}})   # 测试不等退避
        try:
            await wm.dispatch_and_wait(w.id, "干活")
            await asyncio.sleep(0.05)
        finally:
            await wm.stop()
            await bus.stop()

    run(main())
    fin = [e for e in seen if e.type == "window.run_finished"]
    assert fin, "没有终态事件 → 前端只能死等 180s 超时"
    assert fin[0].payload["status"] == "failed"
    assert fin[0].payload["error_code"], "终态里没有错误码"
    assert fin[0].payload["window_id"]
    db.close()


def test_successful_run_also_emits_a_terminal_event(tmp_path):
    from helpers import FakeLLM

    db = Database(tmp_path / "t.db")
    bus = EventBus()
    seen: list = []
    bus.subscribe("*", lambda e: seen.append(e))

    async def main():
        await bus.start()
        llm = FakeLLM([])
        wm = WindowManager(db=db, llm_factory=lambda *a, **k: llm, bus=bus,
                           memory=MemoryService(tmp_path / "mem"),
                           checkpoints_dir=tmp_path / "ckpt")
        await wm.start()
        w = wm.create(name="正常工位", role="worker_programmer",
                      workspace_path=str(tmp_path / "ws" / "ok"),
                      params={"retry": {"max_retries": 0}})
        try:
            await wm.dispatch_and_wait(w.id, "干活")
            await asyncio.sleep(0.05)
        finally:
            await wm.stop()
            await bus.stop()

    run(main())
    fin = [e for e in seen if e.type == "window.run_finished"]
    assert fin and fin[0].payload["status"] == "ok"
    assert fin[0].payload["error_code"] == ""
    db.close()


def test_run_finished_clears_the_rate_limit_badge(tmp_path, monkeypatch):
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    from dogbuddy.main import DogBuddyApp

    cfg = AppConfig(llm=LLMConfig(base_url="", model="", api_key=""),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    app._rate_limit = {"waiting": True, "attempt": 3, "window_id": ""}
    app._on_run_finished(Event(type="window.run_finished",
                               payload={"window_id": "w1", "status": "failed",
                                        "error_code": "network"}))
    assert app._rate_limit["waiting"] is False, "限速徽标只会亮不会灭"


# ------------------------------------------------------------------ ② 目录条目驱动 provider
def test_model_catalog_entry_supplies_endpoint_and_key():
    cfg = AppConfig(llm=LLMConfig(provider="openai_compat", base_url="https://global/v1",
                                 api_key="global-key", model="global-model"),
                    paths=PathsConfig(data_dir="/tmp", workspaces_dir="/tmp"))
    reg = LLMProfileRegistry(cfg)
    reg.model_lookup = lambda name: ({"provider": "openai_compat",
                                      "base_url": "https://vendor-x/v1",
                                      "api_key": "sk-vendor-x"} if name == "vendor/model"
                                     else None)
    p_entry = reg.provider_for(None, "vendor/model")
    assert p_entry.base_url == "https://vendor-x/v1"
    assert p_entry.provider_config["api_key"] == "sk-vendor-x"
    assert p_entry.get_model() == "vendor/model"
    # 目录里没这个名字 → 完整退回 profile（绝不拿空串覆盖已配好的值）
    p_plain = reg.provider_for(None, "其他模型")
    assert p_plain.base_url == "https://global/v1"
    assert p_plain.provider_config["api_key"] == "global-key"
    reg.model_lookup = lambda name: {"provider": "", "base_url": "", "api_key": ""}
    reg.invalidate()
    p_blank = reg.provider_for(None, "vendor/model")
    assert p_blank.base_url == "https://global/v1"
    assert p_blank.provider_config["api_key"] == "global-key"


def test_db_lookup_by_model_name_prefers_enabled(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_model({"id": "a", "provider": "openai_compat", "model": "m1",
                     "base_url": "https://a/v1", "enabled": 0})
    db.upsert_model({"id": "b", "provider": "openai_compat", "model": "m1",
                     "base_url": "https://b/v1", "enabled": 1})
    row = db.get_model_by_name("m1")
    assert row and row["id"] == "b" and row["base_url"] == "https://b/v1"
    assert db.get_model_by_name("") is None and db.get_model_by_name("nope") is None
    db.close()


# ------------------------------------------------------------------ ① 前端不闪 + 接线
def test_streaming_updates_text_node_instead_of_rebuilding_dom():
    main = (JS / "views-main.js").read_text(encoding="utf-8")
    chat = (JS / "views-chat.js").read_text(encoding="utf-8")
    for name, js in (("views-main.js", main), ("views-chat.js", chat)):
        assert "stream-text" in js, f"{name} 没有增量文本节点"
        assert "body.textContent = st.text" in js or "body.textContent = stream.text" in js, \
            f"{name} 还在整块重建流式气泡（会闪）"
        seg = js.split("stream-text")[0][-900:]
        assert "outerHTML = html" not in seg, f"{name} 流式仍用 outerHTML 重建"
    assert "stickToBottom" in main and "gap <= 80" in chat, "流式时不该抢用户的滚动条"
    assert "white-space: pre-wrap" in (CSS / "views.css").read_text(encoding="utf-8")


def test_run_finished_is_subscribed_and_clears_the_bubble():
    main = (JS / "views-main.js").read_text(encoding="utf-8")
    assert "global.SSE.on('window.run_finished'" in main
    assert "onRunFinished" in main
    assert "finishStream(wid)" in main, "终态到了却没收起'正在输入…'"
    # LLM 掉线落库的是 system 消息 → 判定结束要认它；但瞬态重试的 system 不是终态
    assert "c.indexOf('[LLM error/') >= 0" in main
    assert "[LLM 瞬态失败" in main, "瞬态重试也被当成终态就会误收气泡"


def test_window_model_modal_offers_the_configured_catalog():
    main = (JS / "views-main.js").read_text(encoding="utf-8")
    assert "A.models.list()" in main, "工位弹窗没有从模型目录拉选项"
    assert "跟随管理员模型" in main
    assert "从服务商拉取更多" in main
    assert "管理员当前" in main
    assert 'id="model-select"' in main and 'id="model-input"' in main
