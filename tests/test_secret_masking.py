# DogBuddyCn — 密钥出接口前必须掩码（面板 agent token 曾经明文泄露）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""回归测试：`GET /api/settings` 绝不能回传任何明文密钥。

2026-10-15 实测发现（部署当天，为了部署而读 `/api/settings` 时撞见的）：
`_dump()` 里 `"panel": cfg.panel.model_dump()` → 面板的 `agent_token` **明文**返回，
前端还直接把它渲染进 `<input value=...>`。而那个 token 通过面板的
`/api/terminal/exec` 能**在服务器上以 root 执行任意命令**
（实测：`X-Agent-Token: <token>` + `{"command":"id"}` → `uid=0(root)`）。

所以：任何拿到控制台 web token 的人 = 拿到 root。这条测试钉死"只回掩码"。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dogbuddy.config import (  # noqa: E402
    AppConfig, LLMConfig, PathsConfig, WebConfig, is_masked_secret, mask_secret,
)
from dogbuddy.main import DogBuddyApp  # noqa: E402

TOKEN = "settings-token"
LLM_KEY = "sk-llm-supersecret-0001"
# ⚠️ 必须是**假的**：这里绝不能用线上真令牌。
# 2026-10-15 事故：这一行最初贴的是生产面板 Agent Token，随 0.10.8 提交推到了
# **公开仓库**——等于把服务器 root 令牌公开了（发现后立刻轮换了面板令牌）。
# 教训：任何"示例凭据"都要写成一眼假的形状，并在提交前 grep 一遍真值。
PANEL_TOKEN = "panel-agent-token-EXAMPLE-not-real-0000"


def _app(tmp_path, monkeypatch) -> tuple[DogBuddyApp, AppConfig]:
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.delenv("DOGBUDDY_PANEL_TOKEN", raising=False)
    cfg = AppConfig(
        llm=LLMConfig(base_url="https://x/v1", model="m", api_key=LLM_KEY),
        web=WebConfig(auth_token=TOKEN, port=8161),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    cfg.panel.enabled = True
    cfg.panel.base_url = "http://127.0.0.1:8787"
    cfg.panel.agent_token = PANEL_TOKEN
    app = DogBuddyApp(cfg)
    return app, cfg


def test_mask_secret_semantics():
    assert mask_secret("") == "" and mask_secret(None) == ""
    assert mask_secret(LLM_KEY) == "********0001"
    assert mask_secret("short") == "********", "短密钥不能露尾巴"
    assert is_masked_secret("********0001") and not is_masked_secret("real-token-value")


def test_settings_never_returns_plaintext_secrets(tmp_path, monkeypatch):
    app, _cfg = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        r = client.get("/api/settings")
        assert r.status_code == 200
        # 本次事故的核心断言：整个响应体里不许出现明文
        assert PANEL_TOKEN not in r.text, "面板 agent token 明文泄露（= 任何人拿到 root）"
        assert LLM_KEY not in r.text, "LLM 密钥明文泄露"
        data = r.json()
        assert data["panel"]["agent_token"].startswith("********")
        assert data["llm"]["api_key"] == "********0001"
        assert data["llm"]["keys"] == []
        # 非密钥字段照常可读（不能为了掩码把整段藏起来）
        assert data["panel"]["base_url"] == "http://127.0.0.1:8787"
        assert data["panel"]["enabled"] is True
        assert data["llm"]["model"] == "m"


def test_saving_the_mask_back_does_not_destroy_the_token(tmp_path, monkeypatch):
    """前端把掩码原样回传时，服务端必须当成"没改"——否则真 token 会被掩码覆盖。"""
    app, cfg = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        r = client.put("/api/settings", json={"panel": {
            "enabled": True, "base_url": "http://127.0.0.1:8787",
            "agent_token": mask_secret(PANEL_TOKEN)}})
        assert r.status_code == 200
        assert r.json()["panel"]["agent_token"] == mask_secret(PANEL_TOKEN)
        assert cfg.panel.agent_token == PANEL_TOKEN, "掩码被存成了真 token"

        client.put("/api/settings", json={"panel": {"agent_token": ""}})
        assert cfg.panel.agent_token == PANEL_TOKEN, "空值不该清掉 token"

        client.put("/api/settings", json={"panel": {"agent_token": "brand-new-token-123456"}})
        assert cfg.panel.agent_token == "brand-new-token-123456", "真给新值时要生效"
        # 更新后又只能读到掩码
        assert client.get("/api/settings").json()["panel"]["agent_token"] == mask_secret("brand-new-token-123456")


def test_llm_key_keeps_the_old_masking_contract(tmp_path, monkeypatch):
    """回归：LLM 密钥的既有口径（掩码回传=不改）不能因为统一口径而退化。"""
    app, cfg = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        client.put("/api/settings", json={"llm": {"api_key": "********0001"}})
        assert cfg.llm.api_key == LLM_KEY
        client.put("/api/settings", json={"llm": {"model": "m2"}})
        assert cfg.llm.api_key == LLM_KEY and cfg.llm.model == "m2"
        client.put("/api/settings", json={"llm": {"api_key": "sk-brand-new-9999"}})
        assert cfg.llm.api_key == "sk-brand-new-9999"


def test_frontend_panel_token_field_is_write_only():
    js = (Path(__file__).resolve().parents[1] /
          "dogbuddy/web/static/js/views-settings.js").read_text(encoding="utf-8")
    assert 'id="set-panel-token"' in js
    assert "留空保持不变" in js, "输入框要提示留空不变"
    assert "masked(ptoken)" in js, "前端也要拦住回传的掩码"
    # 不能再把掩码/真值预填进 value
    assert "'set-panel-token\" value=\"' +" not in js
