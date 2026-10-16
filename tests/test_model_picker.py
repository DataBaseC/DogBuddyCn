# DogBuddyCn — 拉取模型列表必须"能选"（真下拉，不再用 datalist）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""回归测试：模型选择一律走**真下拉**，并且模型编辑只在"模型管理"页。

线上实测（owner 报的）：SiliconFlow 返回 **99** 个模型，但界面上"只能选第一个"。
根因在前端：代码在拉取成功后**自动把第一个模型填进输入框**，而 `<datalist>` 的候选
是**按输入框里已有文字过滤**的 —— 输入框里已经是第一个模型名，下拉里就只剩它自己。
（API 侧是好的：一次返回 99 条。）

0.10.11：拉取结果改放进真正的 `<select>`（全量、不过滤），选中后写进"模型名"。
0.10.12：**把那半套 datalist 全删掉**（它只会让同一件事有两个入口、还会骗人），
并顺手合并重复入口：设置页不再编辑模型，只留"打开模型管理"+"测兜底配置"。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
JS = REPO / "dogbuddy/web/static/js"
TOKEN = "pick-token"
MODELS = ["BAAI/bge-large-en-v1.5", "BAAI/bge-m3", "zai-org/GLM-5.3-Flash", "Qwen/Qwen3-8B"]


def _js(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def _app(tmp_path, monkeypatch) -> DogBuddyApp:
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(
        llm=LLMConfig(base_url="https://api.siliconflow.cn/v1", model="m", api_key="global-key"),
        web=WebConfig(auth_token=TOKEN, port=8165),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    return DogBuddyApp(cfg)


# ----------------------------------------------------------------- API 侧
def test_fetch_by_model_ref_uses_the_entrys_own_base_url_and_key(tmp_path, monkeypatch):
    """条目自带密钥时，拉取也必须用它的地址与密钥（否则 401，列表拉不出来）。"""
    app = _app(tmp_path, monkeypatch)
    seen: dict = {}

    async def fake_fetch(provider=None, base_url=None, api_key=None):
        seen.update(provider=provider, base_url=base_url, api_key=api_key)
        return [{"id": m, "context_window": 0} for m in MODELS]

    monkeypatch.setattr(app.config_svc, "fetch_models_meta", fake_fetch)

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

        client.get("/api/settings/models", params={"model_ref": entry["id"],
                                                   "base_url": "https://override/v1"})
        assert seen["base_url"] == "https://override/v1"

        assert client.get("/api/settings/models", params={"model_ref": "nope"}).status_code == 404


# ----------------------------------------------------------------- 前端：真下拉
def test_model_page_offers_the_whole_list_and_never_auto_fills():
    js = _js("views-models.js")
    assert "mdl-f-select" in js and "mdl-f-picker" in js, "拉取结果没有真正的下拉控件"
    assert "mi.value = list[0]" not in js, "又把第一个模型自动填进输入框了"
    assert "U.fillModelSelect('mdl-f-select', list, 'mdl-f-model')" in js
    # 下拉常驻，用户不用猜它什么时候出现
    assert 'id="mdl-f-picker"' in js and "display:none" not in js.split('id="mdl-f-picker"')[1][:40]


def test_every_model_picker_is_a_select_not_a_datalist():
    cases = [
        ("views-models.js", "mdl-f-select"),        # 模型管理页 新增/编辑
        ("views-settings.js", "wz-model-select"),   # 首次设置向导
        ("views-main.js", "model-select"),          # 工位编辑
        ("views-profiles.js", "pf-model-select"),   # LLM 配置套
    ]
    for name, sel in cases:
        js = _js(name)
        assert 'id="%s"' % sel in js, f"{name} 缺少 {sel}"
        assert "fillModelSelect" in js, f"{name} 的拉取没接上真下拉"
    settings = _js("views-settings.js")
    assert "state.model = state.models[0]" not in settings, "向导仍在自动填第一个模型"


def test_no_datalist_left_anywhere():
    """datalist 那半套已删：同一件事不该有两个入口，而且它会按输入框文字过滤骗人。"""
    for name in ("ui.js", "views-models.js", "views-settings.js",
                 "views-main.js", "views-profiles.js", "views-chat.js"):
        assert "'<datalist" not in _js(name), f"{name} 还在拼 datalist 元素"
    # 解释这件事的注释要留在共用实现里，避免以后有人又加回去
    assert "按输入框里已有的文字过滤" in _js("ui.js")


def test_settings_no_longer_edits_models():
    """模型只有一个来源：模型管理页。

    历史：v0.10.x 先把设置页的模型输入框/拉取按钮合并掉（只留「打开模型管理」+
    「测兜底配置」）；v0.10.27（owner）连整张 **LLM 兜底配置** 卡片也删了
    （连同全局密钥框），所以现在设置页**一个模型相关输入都没有**。
    """
    s = _js("views-settings.js")
    assert 'id="set-model"' not in s, "设置页又有模型输入框了"
    assert "set-fetch-models" not in s, "设置页又有拉取按钮了"
    assert 'id="set-provider"' not in s and 'id="set-base-url"' not in s, \
        "设置页又有服务商/地址输入框了（应归模型管理页）"
    assert 'id="set-test-llm"' not in s, "「测试兜底配置」随卡片一起删了"
    assert "cardLLM" not in s and 'data-group="llm"' not in s, "LLM 兜底卡片必须已删除"
    # "保存该组" 的 payload 不再带 model（模型只在模型页维护）；
    # 向导那一步会把填的模型收进目录，那里的 payload.model 是允许的
    assert "model: document.getElementById('set-model').value" not in s


def test_settings_no_longer_renders_the_profiles_card():
    """owner：设置页的「LLM 配置套」卡片也删了。

    注意：**后端能力与模块保留** —— 工位/任务表单里的"配置套"下拉仍由
    `views-profiles.js` 提供（`ProfilesMod.fillSelect`），只是设置页不再有编辑卡片。
    """
    s = _js("views-settings.js")
    assert "Profiles.cardHTML()" not in s, "设置页不该再渲染配置套卡片"
    assert "Profiles.bind()" not in s
    main = _js("views-main.js")
    assert "ProfilesMod.fillSelect" in main, "工位表单的配置套下拉不能一起被删掉"


def test_api_client_passes_model_ref_through():
    api = _js("api.js")
    assert "model_ref: modelRef" in api
    assert "A.settings.models(a.provider, a.base_url, a.api_key, undefined, a.model_ref)" \
        in _js("views-models.js")
