# DogBuddyCn — 拉取模型列表必须"能选"（前端下拉 + 按条目密钥拉取）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""回归测试：模型页"拉取模型列表"之后，用户要能选到**任意一个**模型。

线上实测（owner 报的）：SiliconFlow 返回 **99** 个模型，但界面上"只能选第一个"。
根因在前端：代码在拉取成功后**自动把第一个模型填进了输入框**，而 `<datalist>` 的候选
是**按输入框里已有文字过滤**的 —— 输入框里已经是 `BAAI/bge-large-en-v1.5`，下拉里就只剩它自己。
（API 侧是好的：一次返回 99 条。）

修法：拉取结果放进真正的 `<select>`（全量、不过滤），下拉不预填输入框；选中后再写进"模型名"。
顺带修一个同源问题：条目**自带密钥**时，"拉取模型列表"会拿全局密钥去请求（401），
现在拉取也支持 `model_ref`（服务端取该条目的地址与密钥）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TOKEN = "pick-token"
MODELS = ["BAAI/bge-large-en-v1.5", "BAAI/bge-m3", "zai-org/GLM-5.3-Flash", "Qwen/Qwen3-8B"]


def _app(tmp_path, monkeypatch) -> DogBuddyApp:
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(
        llm=LLMConfig(base_url="https://api.siliconflow.cn/v1", model="m", api_key="global-key"),
        web=WebConfig(auth_token=TOKEN, port=8165),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    return DogBuddyApp(cfg)


def test_fetch_by_model_ref_uses_the_entrys_own_base_url_and_key(tmp_path, monkeypatch):
    """条目自带密钥时，拉取也必须用它的地址与密钥（否则 401，列表拉不出来）。"""
    app = _app(tmp_path, monkeypatch)
    seen: dict = {}

    async def fake_fetch(provider=None, base_url=None, api_key=None):
        seen.update(provider=provider, base_url=base_url, api_key=api_key)
        return list(MODELS)

    monkeypatch.setattr(app.config_svc, "fetch_models", fake_fetch)

    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        entry = app.db.upsert_model({
            "id": "openai_compat_vendor-x", "provider": "openai_compat", "model": "vendor-x",
            "base_url": "https://vendor-x.example/v1", "api_key": "sk-vendor-x-999999",
            "enabled": 1})
        r = client.get("/api/settings/models", params={"model_ref": entry["id"]})
        assert r.status_code == 200
        assert r.json()["models"] == MODELS
        assert seen == {"provider": "openai_compat",
                        "base_url": "https://vendor-x.example/v1",
                        "api_key": "sk-vendor-x-999999"}, seen

        # 显式给的值优先于条目里的值
        client.get("/api/settings/models", params={"model_ref": entry["id"],
                                                   "base_url": "https://override/v1"})
        assert seen["base_url"] == "https://override/v1"

        # 未知条目 → 404，不触网
        assert client.get("/api/settings/models", params={"model_ref": "nope"}).status_code == 404


def test_all_fetched_models_are_offered_not_just_the_first():
    """前端不得"自动填第一个"：datalist 会按输入框内容过滤，于是只剩一个。"""
    js = (REPO / "dogbuddy/web/static/js/views-models.js").read_text(encoding="utf-8")
    ui = (REPO / "dogbuddy/web/static/js/ui.js").read_text(encoding="utf-8")

    assert "mdl-f-select" in js and "mdl-f-picker" in js, "拉取结果没有真正的下拉控件"
    # 事故那行必须消失
    assert "mi.value = list[0]" not in js, "又把第一个模型自动填进输入框了（下拉会被过滤成一项）"
    assert "U.fillModelSelect('mdl-f-select', list, 'mdl-f-model')" in js
    # 共用实现：datalist 按输入框过滤这件事只在一个地方解释与处理
    assert "function fillModelSelect" in ui
    assert "按输入框里已有的文字过滤" in ui


def test_every_model_fetch_dialog_gets_a_real_select():
    """四个能"拉取模型"的弹窗都要有真下拉，并且都不许自动填第一个。"""
    cases = [
        ("dogbuddy/web/static/js/views-models.js", "mdl-f-select"),
        ("dogbuddy/web/static/js/views-settings.js", "set-model-select"),
        ("dogbuddy/web/static/js/views-settings.js", "wz-model-select"),
        ("dogbuddy/web/static/js/views-main.js", "model-select"),
        ("dogbuddy/web/static/js/views-profiles.js", "pf-model-select"),
    ]
    for path, sel in cases:
        js = (REPO / path).read_text(encoding="utf-8")
        assert 'id="%s"' % sel in js, f"{path} 缺少 {sel}"

    settings = (REPO / "dogbuddy/web/static/js/views-settings.js").read_text(encoding="utf-8")
    assert "state.model = state.models[0]" not in settings, "向导仍在自动填第一个模型"
    assert settings.count("fillModelSelect") >= 2, "设置页的两处拉取都没接上真下拉"
    for path in ("dogbuddy/web/static/js/views-main.js",
                 "dogbuddy/web/static/js/views-profiles.js"):
        assert "fillModelSelect" in (REPO / path).read_text(encoding="utf-8"), f"{path} 没接上"


def test_api_client_passes_model_ref_through():
    api = (REPO / "dogbuddy/web/static/js/api.js").read_text(encoding="utf-8")
    assert "model_ref: modelRef" in api
    js = (REPO / "dogbuddy/web/static/js/views-models.js").read_text(encoding="utf-8")
    assert "A.settings.models(a.provider, a.base_url, a.api_key, undefined, a.model_ref)" in js
