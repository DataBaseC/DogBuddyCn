# DogBuddyCn — 全局密钥在设置页停用（v0.10.25）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 要求："把设置页里全局 llm key 这个给禁用了，我感觉一点用都没有，只留模型页里的列表"。

口径：**密钥归属"模型条目"**（`activate()` 会把条目的密钥切到运行时），
设置页那个全局密钥框停用；真的往接口塞一把新密钥要**明确拒绝**，
而不是默默写进去、也不是默默忽略。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_models import _client  # noqa: E402


def test_设置页写全局密钥被明确拒绝(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        before = client.get("/api/settings").json()["llm"]["api_key"]
        r = client.put("/api/settings", json={"llm": {"api_key": "sk-brand-new-key-9999"}})
        assert r.status_code == 400, r.text
        msg = r.json()["detail"]
        assert "停用" in msg and "模型管理" in msg, msg
        # 运行时的密钥没有被改动（拒绝就得干干净净）
        assert app.config.llm.api_key == "k"
        assert client.get("/api/settings").json()["llm"]["api_key"] == before


def test_设置页回传掩码或留空仍然放行(tmp_path, monkeypatch):
    """前端可能把掩码原样回传（历史行为）：那属于"不改密钥"，必须照常通过。"""
    app, client = _client(tmp_path, monkeypatch)
    with client:
        masked = client.get("/api/settings").json()["llm"]["api_key"]
        assert client.put("/api/settings", json={
            "llm": {"api_key": masked, "temperature": 0.5}}).status_code == 200
        assert client.put("/api/settings", json={
            "llm": {"temperature": 0.6}}).status_code == 200
        assert app.config.llm.api_key == "k", "掩码/留空不能动真密钥"
        assert app.config.llm.temperature == 0.6


def test_模型条目自带密钥仍能切换运行时密钥(tmp_path, monkeypatch):
    """停用的是"设置页那个框"，不是"全局密钥"本身 —— 条目切换必须照常带密钥。"""
    app, client = _client(tmp_path, monkeypatch)
    with client:
        created = client.post("/api/models", json={
            "model": "kv-model", "base_url": "http://kv/v1",
            "api_key": "sk-entry-key-7777"}).json()
        mid = created["model"]["id"]
        act = client.post(f"/api/models/{mid}/activate").json()
        assert act["result"]["api_key_switched"] is True
        assert app.config.llm.api_key == "sk-entry-key-7777"
        assert app.config.llm.model == "kv-model"


def test_设置页仍回掩码不泄明文(tmp_path, monkeypatch):
    """设置页仍要能看到"当前生效密钥的掩码"（只读展示），但**绝不回明文**。"""
    app, client = _client(tmp_path, monkeypatch)
    with client:
        app.config.llm.api_key = "sk-abcdefghijklmnop"
        masked = client.get("/api/settings").json()["llm"]["api_key"]
        assert masked and masked != "sk-abcdefghijklmnop", f"回传了明文：{masked}"
        assert masked.endswith("mnop"), f"掩码要给末尾几位好辨认：{masked}"
        assert "abcdefghij" not in masked


def test_向导把密钥落成模型条目而不是塞给设置页():
    """v0.10.26：全局密钥停用后，向导**不能**再往 PUT /api/settings 塞密钥（服务端会 400）。

    静态钉住前端：向导保存分支走"建条目（带密钥）→ 设为当前"，不再有 payload.api_key。
    """
    js = (Path(__file__).resolve().parents[1] /
          "dogbuddy/web/static/js/views-settings.js").read_text(encoding="utf-8")
    assert "A.models.create(entry)" in js, "向导要建模型条目"
    assert "if (state.api_key) entry.api_key = state.api_key;" in js, "密钥要落在条目上"
    assert "A.models.activate(mid)" in js, "保存后要「设为当前」（连同密钥切换）"
    assert "payload.api_key = state.api_key" not in js, "向导不能再把密钥塞给设置页"
    # v0.10.27：整张 LLM 兜底卡片（含那个已停用的密钥框）都删了 —— 比"禁用"更彻底
    assert "set-api-key" not in js, "全局密钥框应随卡片一起删除"
    assert 'data-group="llm"' not in js, "LLM 兜底卡片整张都没了"


def test_设置页不再有全局密钥入口():
    """v0.10.27（owner）：设置页的「LLM 兜底配置」整张卡删了，密钥只在模型条目上。"""
    js = (Path(__file__).resolve().parents[1] /
          "dogbuddy/web/static/js/views-settings.js").read_text(encoding="utf-8")
    assert "set-api-key" not in js and "set-provider" not in js and "set-base-url" not in js, \
        "设置页不该再有 LLM 凭证/地址输入"
    assert "cardLLM" not in js, "卡片函数应已删除"
    # 后端拒绝仍然保留（防有人绕过界面直接调接口）
    route = (Path(__file__).resolve().parents[1] /
             "dogbuddy/web/routes/settings.py").read_text(encoding="utf-8")
    assert "全局 API Key 已在设置页停用" in route
