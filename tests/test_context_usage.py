# DogBuddyCn — context usage: ledger vs occupancy (v11)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""回归测试：**上下文占用**与**累计花费**是两个口径，不能共用 `SUM(tokens)`。

线上症状（owner 亲自报的）：
  1. 控制台「上下文用量」恒 0 —— assistant 消息 append 时根本没传 tokens；
  2. 管理员对话连用量端点都没有（合成窗口 `admin:<id>` 不在 windows 表 → 404）；
  3. 连带后果更严重：`Compactor.maybe_compact` 取 `SUM(tokens)`，恒 0 →
     **自动压缩永不触发**，上下文会一直涨到模型拒绝超长请求。

所以这里既钉"写进去的值"，也钉"压缩器按哪个值判"。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dogbuddy.agents.core.loop import AgentLoop  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.llm.provider import LLMResponse  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402
from dogbuddy.windows.compactor import Compactor  # noqa: E402
from dogbuddy.windows.session import WindowSession  # noqa: E402

from helpers import FakeLLM, run  # noqa: E402

TOKEN = "usage-token"
REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------- 单测
def _session(tmp_path) -> tuple[Database, WindowSession]:
    db = Database(tmp_path / "u.db")
    w = Window(id="w1", name="w1", role="worker_programmer",
               workspace_path=str(tmp_path), model="m")
    db.insert_window(w)
    return db, WindowSession(w, db, db, db)


def test_assistant_message_records_ledger_tokens(tmp_path):
    """① 记账层：assistant 消息必须带上本轮 total_tokens（账本/显示用）。"""
    db, sess = _session(tmp_path)
    llm = FakeLLM([LLMResponse(content="done",
                               usage={"prompt_tokens": 900, "completion_tokens": 100,
                                      "total_tokens": 1000})])
    loop = AgentLoop(llm=llm, registry=build_registry(), session=sess)
    run(loop.run("go", ToolContext(window_id="w1", workspace=str(tmp_path), db=db)))

    msg = [m for m in sess.recent(10) if m.role == "assistant"][-1]
    assert msg.tokens == 1000, "assistant 消息没记账本 token"
    assert sess.token_usage() == 1000
    # ② 占用层：context_tokens 取 prompt_tokens，**不是** total_tokens
    assert sess.context_tokens() == 900
    assert sess.context_tokens_estimated is False
    db.close()


def test_compactor_keys_on_occupancy_not_cumulative_spend(tmp_path):
    """压缩器按"上下文有多满"判，不按"迄今花了多少"判。

    构造一个**累计花费远超阈值、但当前上下文很小**的会话：修复前会用 SUM 判 →
    误压缩（长寿命工位反复被压碎）；修复后看 prompt_tokens → 不压缩。
    """
    db, sess = _session(tmp_path)
    # 累计花费 40000（远超阈值 2000），当前上下文只有 500
    for _ in range(20):
        sess.append("assistant", "x" * 10, tokens=2000)
    assert sess.token_usage() == 40000, "前提：累计花费已远超阈值"
    sess.note_usage({"prompt_tokens": 500, "total_tokens": 2000})

    c = Compactor(FakeLLM(), db, threshold=0.8, max_context_tokens=1000)
    assert run(c.maybe_compact(sess)) is None, "压缩器还在按累计花费判（会误压缩长寿命工位）"

    # 上下文真的满了 → 必须压缩
    sess.note_usage({"prompt_tokens": 900, "total_tokens": 2000})
    rec = run(c.maybe_compact(sess))
    assert rec is not None, "上下文占用超阈值却没触发压缩"
    db.close()


def test_compactor_missing_note_usage_falls_back_to_estimate(tmp_path):
    """本进程还没拿到真实 usage 时，压缩判据退化为按字符粗估，而不是 0。"""
    db, sess = _session(tmp_path)
    for _ in range(40):
        sess.append("user", "y" * 200)
    assert sess.last_prompt_tokens == 0
    est = sess.context_tokens()
    assert est > 0, "没有真实 usage 时必须给出估算值（否则压缩永不触发）"
    assert sess.context_tokens_estimated is True
    db.close()


# ----------------------------------------------------------------- API 层
def _app(tmp_path, monkeypatch) -> DogBuddyApp:
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(
        llm=LLMConfig(base_url="http://invalid.local/v1", api_key="k", model="fake",
                      request_timeout=5.0, max_context_tokens=4000, compact_threshold=0.8),
        web=WebConfig(auth_token=TOKEN, port=8125),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    fake = FakeLLM([LLMResponse(content="好的",
                                usage={"prompt_tokens": 300, "completion_tokens": 20,
                                       "total_tokens": 320})])
    app.llm_factory = lambda: fake
    app.windows.llm_factory = app.llm_factory
    app.admin.llm_factory = app.llm_factory
    return app


def test_admin_chat_usage_endpoint_reports_real_numbers(tmp_path, monkeypatch):
    """③ 后端层：管理员用量必须有自己的端点，不能走 /api/windows/admin:<id>/usage。"""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        # 修复前的现状：管理员会话不是真实工位 → 404
        assert client.get("/api/windows/admin:default/usage").status_code == 404

        r = client.get("/api/admin/chat/usage", params={"conversation": "default"})
        assert r.status_code == 200, "管理员用量端点缺失"
        before = r.json()
        assert before["context_tokens"] == 0
        assert before["max_context_tokens"] == 4000 and before["threshold"] == 0.8

        assert client.post("/api/admin/chat", json={"text": "在吗"}).status_code == 200

        after = client.get("/api/admin/chat/usage", params={"conversation": "default"}).json()
        assert after["context_tokens"] == 300, "用量没取到最近一次调用的 prompt_tokens"
        assert after["tokens"] == 320, "账本口径（累计花费）不对"
        assert after["estimated"] is False

        # 端点不能顺手把合成窗口写进 windows 表（否则会污染工位列表）
        assert app.db.get_window("admin:default") is None
        assert all(w.id != "admin:default" for w in app.windows.list())


def test_admin_chat_usage_rejects_reserved_and_bad_ids(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        assert client.get("/api/admin/chat/usage", params={"conversation": "system"}).status_code == 400
        assert client.get("/api/admin/chat/usage", params={"conversation": "../etc"}).status_code == 400


# ------------------------------------------------------------- 前端接线
def test_chat_usage_bar_is_wired_end_to_end():
    """④ 前端层：容器、API、渲染三处必须都在（只改一层界面上仍然什么都看不到）。"""
    html = (REPO / "dogbuddy/web/static/index.html").read_text(encoding="utf-8")
    chat_js = (REPO / "dogbuddy/web/static/js/views-chat.js").read_text(encoding="utf-8")
    api_js = (REPO / "dogbuddy/web/static/js/api.js").read_text(encoding="utf-8")
    ui_js = (REPO / "dogbuddy/web/static/js/ui.js").read_text(encoding="utf-8")

    assert 'id="chat-usage"' in html, "对话视图没有用量条容器"
    assert "#chat-usage" in chat_js, "对话视图没挂载用量条"
    assert "adminChatUsage" in chat_js and "adminChatUsage" in api_js, "前端没有调用管理员用量接口"
    assert "/api/admin/chat/usage" in api_js, "API 层没有这个端点"
    assert "usageHTML" in ui_js, "共用用量条渲染函数不在 ui.js"
