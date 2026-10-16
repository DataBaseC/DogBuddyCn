# DogBuddyCn — 模型上下文来自服务商 + 建员工时指定压缩阈值（0.10.12）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 的口径（2026-10-15）：

> 模型拉取应该采用服务商默认的上下文，然后管理员建立员工时会指定压缩阈值

两件事：
  ① **上下文窗口**：服务商给了就用它（导入时写进条目；条目的 `context_window` 会由
     `activate()` 写进运行时配置）；服务商**没给**就留空（0 = 继承），**不许替它编一个数**。
     实测 SiliconFlow 的 `/v1/models` 只回 `id/object/created/owned_by`，所以"未知"是常态。
  ② **压缩阈值**：管理员建工位时明确指定（0.3~0.95），落成工位参数覆盖；
     越界必须报错——一个 `compact_threshold: 5` 会让压缩永不触发，而界面上看不出来。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dogbuddy.agents.management.tools import AdminServices, build_admin_tools  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.llm.models import _context_of  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402
from dogbuddy.memory.service import MemoryService  # noqa: E402
from dogbuddy.services.model_service import ModelService  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TOKEN = "ctx-token"


# ------------------------------------------------------------------ ① 上下文来源
def test_context_fields_are_read_from_provider_payloads():
    """各家网关的字段名不一样，能读到哪个用哪个；读不到 = 0（未知）。"""
    assert _context_of({"id": "a", "context_length": 65536}) == 65536
    assert _context_of({"id": "a", "context_window": 32768}) == 32768
    assert _context_of({"id": "a", "max_model_len": 8192}) == 8192
    assert _context_of({"id": "a", "top_provider": {"context_length": 200000}}) == 200000
    assert _context_of({"id": "a", "meta": {"max_input_tokens": 128000}}) == 128000
    assert _context_of({"id": "a", "object": "model", "created": 0, "owned_by": ""}) == 0


def _model_service(tmp_path, meta):
    db = Database(tmp_path / "m.db")
    cfg = AppConfig(llm=LLMConfig(base_url="https://x/v1", model="m", api_key="k"),
                    paths=PathsConfig(data_dir=str(tmp_path), workspaces_dir=str(tmp_path / "ws")))

    async def fake_meta(provider=None, base_url=None, api_key=None):
        return meta

    svc = ModelService(db, cfg, persist=lambda: "cfg.yaml", fetch_meta=fake_meta)
    return db, svc


def test_import_writes_provider_context_and_never_invents_one(tmp_path):
    db, svc = _model_service(tmp_path, [
        {"id": "vendor/known-128k", "context_window": 128000},
        {"id": "vendor/unknown", "context_window": 0},
    ])
    import asyncio
    res = asyncio.get_event_loop().run_until_complete(svc.import_fetched("openai_compat", "https://x/v1"))
    assert res["fetched"] == 2 and res["context_filled"] == 1
    known = db.get_model("openai_compat_vendor_known-128k")
    unknown = db.get_model("openai_compat_vendor_unknown")
    assert known["context_window"] == 128000, "服务商给了上下文却没写进条目"
    assert int(unknown["context_window"] or 0) == 0, "服务商没给却编了一个数"
    db.close()


def test_import_never_overwrites_a_hand_written_context(tmp_path):
    db, svc = _model_service(tmp_path, [{"id": "m1", "context_window": 32768}])
    db.upsert_model({"id": "openai_compat_m1", "provider": "openai_compat", "model": "m1",
                     "base_url": "https://x/v1", "context_window": 4096, "enabled": 1})
    import asyncio
    asyncio.get_event_loop().run_until_complete(svc.import_fetched("openai_compat", "https://x/v1"))
    assert db.get_model("openai_compat_m1")["context_window"] == 4096, "覆盖了用户手填的值"
    db.close()


def test_activate_with_unknown_context_keeps_the_global(tmp_path):
    """服务商没给上下文 → 条目留空 → 切模型时**不许**改动全局 max_context_tokens。"""
    db, svc = _model_service(tmp_path, [{"id": "m1", "context_window": 0}])
    import asyncio
    asyncio.get_event_loop().run_until_complete(svc.import_fetched("openai_compat", "https://x/v1"))
    cfg = svc.app
    cfg.llm.max_context_tokens = 64000
    svc.activate("openai_compat_m1")
    assert cfg.llm.max_context_tokens == 64000
    db.close()


# ------------------------------------------------------------------ ② 建工位时给压缩阈值
def test_api_create_window_with_compact_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", model="", api_key=""),
                    web=WebConfig(auth_token=TOKEN, port=8167),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        r = client.post("/api/windows", json={
            "name": "带阈值的员工", "workspace_path": str(tmp_path / "ws" / "w1"),
            "params": {"compact_threshold": 0.6}})
        assert r.status_code == 200, r.text
        wid = r.json()["id"]
        assert r.json()["params"]["compact_threshold"] == 0.6
        # effective_params 走同一条链 → 压缩器拿到的就是 0.6
        w = app.windows.get(wid)
        assert app.config.effective_params(w)["compact_threshold"] == 0.6

        # 越界必须拒绝（否则"永不压缩"却看不出来）
        bad = client.post("/api/windows", json={
            "name": "越界", "workspace_path": str(tmp_path / "ws" / "w2"),
            "params": {"compact_threshold": 5}})
        assert bad.status_code == 400 and "超出范围" in bad.json()["detail"]


def test_uid_create_and_update_carry_the_threshold(tmp_path):
    """管理员通过对话建员工时，也能指定/修改压缩阈值。"""
    import asyncio

    db = Database(tmp_path / "t.db")
    bus = EventBus()
    mem = MemoryService(tmp_path / "memory")
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=bus, memory=mem,
                       checkpoints_dir=tmp_path / "ckpt")
    # v0.10.17：工位模型必须来自模型目录 → 测试也要先建目录条目
    cfg = AppConfig(llm=LLMConfig(), paths=PathsConfig(
        data_dir=str(tmp_path), workspaces_dir=str(tmp_path / "ws")))
    models = ModelService(db, cfg, persist=lambda: "cfg.yaml")
    db.upsert_model({"id": "m-x", "provider": "openai_compat", "model": "m-x",
                     "base_url": "https://x/v1", "enabled": 1})
    svc = AdminServices(windows=wm, models=models)
    tools = {t.name: t for t in build_admin_tools(svc)}
    ctx = ToolContext(window_id="admin:default", workspace=str(tmp_path), db=db)

    async def main():
        r = await tools["window_create"].execute(
            {"name": "阈值员工", "workspace": str(tmp_path / "ws" / "a"),
             "compact_threshold": 0.5}, ctx)
        assert r.ok, r.output
        wid = r.data.get("window_id")
        assert wm.get(wid).params["compact_threshold"] == 0.5

        r2 = await tools["window_update"].execute(
            {"window_id": wid, "compact_threshold": 0.9, "model": "m-x"}, ctx)
        assert r2.ok, r2.output
        w = wm.get(wid)
        assert w.params["compact_threshold"] == 0.9 and w.model == "m-x"

        r3 = await tools["window_create"].execute(
            {"name": "坏阈值", "workspace": str(tmp_path / "ws" / "b"),
             "compact_threshold": 12}, ctx)
        assert not r3.ok and "超出范围" in r3.output

    asyncio.get_event_loop().run_until_complete(main())
    db.close()


# ------------------------------------------------------------------ 前端
def test_frontend_exposes_the_threshold_when_creating_an_employee():
    js = (REPO / "dogbuddy/web/static/js/views-main.js").read_text(encoding="utf-8")
    assert 'id="win-threshold"' in js, "建工位弹窗没有压缩阈值字段"
    assert 'id="win-maxctx"' in js, "建工位弹窗没有上下文上限字段"
    # 阈值要落到工位参数覆盖里（越界前端先拦，后端再拦一道）
    assert "params.compact_threshold = thr" in js
    assert "payload.params = params" in js
    assert "0.3 ~ 0.95" in js


def test_model_form_does_not_impose_128k_on_a_new_entry():
    js = (REPO / "dogbuddy/web/static/js/views-models.js").read_text(encoding="utf-8")
    assert "context_window: state.current.max_context_tokens" not in js, \
        "新模型又默认为全局 128k（应该留空 = 继承服务商/全局）"
    assert "继承" in js or "服务商" in js


# ------------------------------------------------------------------ ③ 管理员的模型
def test_admin_model_is_visible_and_switchable(tmp_path, monkeypatch):
    """管理员没有工位：它的模型就是全局运行时模型。要能看见、能换。"""
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="https://api.siliconflow.cn/v1",
                                  model="zai-org/GLM-5.3-Flash", api_key="k"),
                    web=WebConfig(auth_token=TOKEN, port=8168),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        u = client.get("/api/admin/chat/usage", params={"conversation": "default"}).json()
        assert u["model"] == "zai-org/GLM-5.3-Flash"
        assert u["base_url"] == "https://api.siliconflow.cn/v1"
        # 「设为当前」就是给管理员换模型：目录条目 → 全局运行时配置
        entry = client.post("/api/models", json={
            "provider": "openai_compat", "model": "another-model",
            "base_url": "https://other.example/v1", "context_window": 64000}).json()["model"]
        client.post(f"/api/models/{entry['id']}/activate")
        after = client.get("/api/admin/chat/usage", params={"conversation": "default"}).json()
        assert after["model"] == "another-model"
        assert after["base_url"] == "https://other.example/v1"
        # 条目自带的上下文随之生效（这才是"用服务商默认上下文"的落点）
        assert after["max_context_tokens"] == 64000


def test_frontend_shows_the_admin_model_in_chat_and_models_page():
    chat = (REPO / "dogbuddy/web/static/js/views-chat.js").read_text(encoding="utf-8")
    html = (REPO / "dogbuddy/web/static/index.html").read_text(encoding="utf-8")
    models = (REPO / "dogbuddy/web/static/js/views-models.js").read_text(encoding="utf-8")
    settings = (REPO / "dogbuddy/web/static/js/views-settings.js").read_text(encoding="utf-8")

    assert 'id="chat-model"' in html and "模型：" in chat
    assert "u.model" in chat, "对话页没显示管理员模型"
    assert 'id="chat-model-go"' in html and "App.go('models')" in chat
    assert "管理员当前模型" in models and "设为管理员模型" in models
    # v0.10.27：设置页的 LLM 卡片已删，"当前模型"只在对话页/模型页显示
    assert "管理员当前模型" not in settings
