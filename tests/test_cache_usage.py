# DogBuddyCn — cache-hit usage exposure (v0.10.20)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 要在控制台看"缓存命中"之类的成本数据（交接书 §2.2）。

查明：**代码从来没读过这些字段**（全仓库 grep `cache_hit|cached_tokens|
prompt_cache` = 0 处命中）。provider 侧 `_usage_dict()` 一直把 `usage` 里的
嵌套对象**原样保留**（DeepSeek 的 `prompt_cache_hit_tokens`/
`prompt_cache_miss_tokens`、OpenAI 风格的 `prompt_tokens_details.cached_tokens`
都在里面），只是没人解析；API 与前端也只渲染 context/tokens 两个数。

这里钉住：两种 provider 形状 → 统一规范化 → 会话记录 → 用量 API → 前端渲染。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dogbuddy.agents.core.loop import AgentLoop  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.llm.openai_compat import _usage_dict  # noqa: E402
from dogbuddy.llm.provider import LLMResponse  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402
from dogbuddy.windows.session import WindowSession  # noqa: E402

TOKEN = "cache-token"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ----------------------------------------------------------- provider 规范化
def test_usage_dict_deepseek_cache_shape():
    """DeepSeek：顶层 prompt_cache_hit_tokens / prompt_cache_miss_tokens。"""
    out = _usage_dict({"prompt_tokens": 1000, "completion_tokens": 50,
                       "total_tokens": 1050,
                       "prompt_cache_hit_tokens": 800,
                       "prompt_cache_miss_tokens": 200})
    assert out["cache_hit_tokens"] == 800
    assert out["cache_miss_tokens"] == 200
    # 原始字段仍保留（别的消费者可能在校验它们）
    assert out["prompt_cache_hit_tokens"] == 800


def test_usage_dict_openai_nested_cache_shape():
    """OpenAI 风格：prompt_tokens_details.cached_tokens，miss 由 prompt 推。"""
    out = _usage_dict({"prompt_tokens": 1000, "total_tokens": 1050,
                       "prompt_tokens_details": {"cached_tokens": 700}})
    assert out["cache_hit_tokens"] == 700
    assert out["cache_miss_tokens"] == 300
    assert out["prompt_tokens_details"] == {"cached_tokens": 700}


def test_usage_dict_without_cache_fields_adds_nothing():
    """服务商不给缓存字段（SiliconFlow/Sensenova 不一定给）→ 不加键，
    前端据此不显示那一行，而不是显示假的 0%。"""
    out = _usage_dict({"prompt_tokens": 10, "total_tokens": 20})
    assert "cache_hit_tokens" not in out and "cache_miss_tokens" not in out


# ----------------------------------------------------------- 会话记录
def test_session_records_cache_stats(tmp_path):
    db = Database(tmp_path / "c.db")
    w = Window(id="w1", name="w1", role="worker_programmer",
               workspace_path=str(tmp_path), model="m")
    db.insert_window(w)
    sess = WindowSession(w, db, db, db)
    assert sess.cache_stats() == {"cache_hit_tokens": 0, "cache_miss_tokens": 0,
                                  "cache_hit_ratio": 0.0}
    sess.note_usage({"prompt_tokens": 1000, "total_tokens": 1050,
                     "cache_hit_tokens": 800, "cache_miss_tokens": 200})
    stats = sess.cache_stats()
    assert stats["cache_hit_tokens"] == 800 and stats["cache_miss_tokens"] == 200
    assert stats["cache_hit_ratio"] == 0.8
    db.close()


def test_loop_end_to_end_cache_stats(tmp_path):
    """一次真实 loop：provider 回报缓存字段 → 会话 cache_stats 看得到。"""
    from tests.helpers import FakeLLM  # noqa: E402
    db = Database(tmp_path / "c2.db")
    w = Window(id="w1", name="w1", role="worker_programmer",
               workspace_path=str(tmp_path), model="m")
    db.insert_window(w)
    sess = WindowSession(w, db, db, db)
    llm = FakeLLM([LLMResponse(content="done",
                               usage={"prompt_tokens": 1000, "completion_tokens": 50,
                                      "total_tokens": 1050,
                                      "prompt_cache_hit_tokens": 900,
                                      "prompt_cache_miss_tokens": 100})])
    loop = AgentLoop(llm=llm, registry=build_registry(), session=sess)
    _run(loop.run("go", ToolContext(window_id="w1", workspace=str(tmp_path), db=db)))
    assert sess.cache_stats()["cache_hit_ratio"] == 0.9
    db.close()


# ----------------------------------------------------------- API 层
def _app(tmp_path, monkeypatch, usage: dict) -> DogBuddyApp:
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(
        llm=LLMConfig(base_url="http://invalid.local/v1", api_key="k", model="fake",
                      request_timeout=5.0, max_context_tokens=4000, compact_threshold=0.8),
        web=WebConfig(auth_token=TOKEN, port=8126),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    from tests.helpers import FakeLLM  # noqa: E402
    fake = FakeLLM([LLMResponse(content="好的", usage=usage)])
    app.llm_factory = lambda: fake
    app.windows.llm_factory = app.llm_factory
    app.admin.llm_factory = app.llm_factory
    return app


def test_admin_chat_usage_exposes_cache_hit(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch,
               {"prompt_tokens": 300, "completion_tokens": 20, "total_tokens": 320,
                "prompt_cache_hit_tokens": 240, "prompt_cache_miss_tokens": 60})
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        assert client.post("/api/admin/chat", json={"text": "在吗"}).status_code == 200
        r = client.get("/api/admin/chat/usage", params={"conversation": "default"}).json()
        assert r["cache_hit_tokens"] == 240
        assert r["cache_miss_tokens"] == 60
        assert r["cache_hit_ratio"] == 0.8


def test_window_usage_exposes_cache_hit(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch,
               {"prompt_tokens": 500, "completion_tokens": 20, "total_tokens": 520,
                "prompt_tokens_details": {"cached_tokens": 400}})
    w = app.windows.create(name="dev", workspace_path=str(tmp_path / "proj"))
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        # 先记一轮带缓存字段的 usage（provider 规范化后的形状），让端点有东西可报
        rt = app.windows._rt(w.id)
        rt.session.note_usage(
            {"prompt_tokens": 500, "cache_hit_tokens": 400, "cache_miss_tokens": 100})
        r = client.get(f"/api/windows/{w.id}/usage").json()
        assert r["cache_hit_tokens"] == 400 and r["cache_miss_tokens"] == 100
        assert r["cache_hit_ratio"] == 0.8


def test_note_usage_accepts_raw_provider_shapes(tmp_path):
    """兜底：没过 provider 规范化的原始形状（DeepSeek 顶层 / OpenAI 嵌套）也要认。"""
    db = Database(tmp_path / "raw.db")
    w = Window(id="w1", name="w1", role="worker_programmer",
               workspace_path=str(tmp_path), model="m")
    db.insert_window(w)
    sess = WindowSession(w, db, db, db)
    sess.note_usage({"prompt_tokens": 1000,
                     "prompt_cache_hit_tokens": 900,
                     "prompt_cache_miss_tokens": 100})
    assert sess.cache_stats()["cache_hit_tokens"] == 900
    sess.note_usage({"prompt_tokens": 600,
                     "prompt_tokens_details": {"cached_tokens": 300}})
    stats = sess.cache_stats()
    assert stats["cache_hit_tokens"] == 300 and stats["cache_miss_tokens"] == 300
    db.close()


# ----------------------------------------------------------- 前端接线
def test_usage_bar_renders_cache_hit():
    """前端：用量条有缓存命中一行；全 0 时不显示（不造假数字）。"""
    ui = (Path(__file__).resolve().parents[1]
          / "dogbuddy/web/static/js/ui.js").read_text(encoding="utf-8")
    assert "缓存命中" in ui and "cache_hit_tokens" in ui, "用量条没有缓存命中渲染"
    assert "cacheTotal > 0" in ui, "全 0 时必须不显示（而不是显示假的 0%）"
