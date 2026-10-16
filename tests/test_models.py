# DogBuddyCn — 模型管理页（目录 + 备注 + 切换）+ schema v5
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""用户诉求："得有个模型管理页，不然用户怎么替换模型？每个模型后边可以写备注，
方便管理员了解模型能力和限制。"

这里锁住四件事：
  1) 目录与运行时配置解耦，`activate()` 才是"换模型"的唯一落地动作（且必须落盘）
  2) 备注（note）是**用户手写的资产**——任何自动导入都不得覆盖
  3) 迁移 v5 幂等、老库可平滑升级
  4) HTTP 契约（含 404/400 这类边界）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import SCHEMA_VERSION, Database  # noqa: E402
from dogbuddy.services.model_service import (  # noqa: E402
    CAPABILITY_TAGS, ModelService, make_model_id)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _svc(tmp_path, fetched=None, provider="openai_compat", model="m-old"):
    db = Database(tmp_path / "t.db")
    cfg = AppConfig(llm=LLMConfig(provider=provider, base_url="http://ep/v1",
                                  api_key="k", model=model, max_context_tokens=8192))
    saved: list[str] = []
    svc = ModelService(db, cfg, persist=lambda: (saved.append("1"), "/tmp/config.yaml")[1],
                       fetch_models=fetched)
    return svc, db, cfg, saved


# --------------------------------------------------------------------------- schema v5
def test_迁移_v5_建表且幂等(tmp_path):
    assert SCHEMA_VERSION >= 5
    p = tmp_path / "a.db"
    db = Database(p)
    try:
        assert db.schema_version == SCHEMA_VERSION
        cols = {r[1] for r in db._conn.execute("PRAGMA table_info(models)")}
        assert {"id", "provider", "model", "note", "capability", "context_window",
                "enabled", "is_default", "source"} <= cols
    finally:
        db.close()
    # 再开一次（幂等，不重复建表/迁移）
    db2 = Database(p)
    try:
        assert db2.schema_version == SCHEMA_VERSION
        assert db2.list_models() == []
    finally:
        db2.close()


def test_老库_v4_能升到_v5(tmp_path):
    import sqlite3
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(p))
    # 模拟 v4 的库：有 windows 表 + schema_version=4，但没有 models 表
    conn.execute("CREATE TABLE windows(id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT)")
    conn.execute("INSERT INTO schema_version(version, applied_at) VALUES(4,'x')")
    conn.commit()
    conn.close()

    db = Database(p)
    try:
        assert db.schema_version == SCHEMA_VERSION
        assert db.list_models() == []
    finally:
        db.close()


# --------------------------------------------------------------------------- id 与 CRUD
def test_模型_id_稳定且路径安全():
    mid = make_model_id("openai_compat", "gpt-4o-mini")
    assert mid == "openai_compat_gpt-4o-mini"
    assert "/" not in mid, "id 会作为路径参数，不能含 /（否则路由 404）"
    assert make_model_id("", "m") == "openai_compat_m", "provider 缺省要有合理默认"
    assert make_model_id("openai_compat", "a/b c") == make_model_id("openai_compat", "a/b c")
    long_id = make_model_id("p", "x" * 200)
    assert len(long_id) <= 88 and "/" not in long_id
    with pytest.raises(ValueError):
        make_model_id("p", "   ")


def test_新增_更新_删除_保留未提供的字段(tmp_path):
    svc, db, _cfg, _saved = _svc(tmp_path)
    try:
        m = svc.upsert(model="deepseek-v4-flash", display_name="DeepSeek V4",
                       note="工具调用稳；429 凶", capability="工具调用,中文强",
                       context_window=128000, speed="快", cost="低")
        mid = m["id"] if "id" in m else make_model_id("openai_compat", "deepseek-v4-flash")
        assert m["model"] == "deepseek-v4-flash"
        assert m["note"] == "工具调用稳；429 凶"

        # PATCH 语义：只改 note，其它字段原样保留
        svc.update(mid, note="改过的备注：只读不改")
        again = db.get_model(mid)
        assert again["note"] == "改过的备注：只读不改"
        assert again["context_window"] == 128000
        assert again["capability"] == "工具调用,中文强"

        assert svc.remove(mid) is True
        assert svc.remove(mid) is False
        assert db.list_models() == []
    finally:
        db.close()


def test_删除不存在的模型返回_False(tmp_path):
    svc, db, _cfg, _saved = _svc(tmp_path)
    try:
        assert svc.remove("nope") is False
    finally:
        db.close()


# --------------------------------------------------------------------------- 切换当前模型
def test_activate_写入运行时配置并落盘(tmp_path):
    svc, db, cfg, saved = _svc(tmp_path)
    try:
        svc.upsert(model="cheap-model", provider="openai_compat",
                   base_url="http://cheap/v1", context_window=32000, note="便宜、慢")
        mid = make_model_id("openai_compat", "cheap-model")
        res = svc.activate(mid)

        assert cfg.llm.model == "cheap-model", "运行时配置必须真的变"
        assert cfg.llm.base_url == "http://cheap/v1"
        assert cfg.llm.max_context_tokens == 32000, "上下文窗口要跟着带过来"
        assert saved, "必须落盘（否则重启就回退）"
        assert res["saved_to"]
        # 目录里标记当前
        assert [r["is_default"] for r in db.list_models() if r["id"] == mid] == [1]
        assert svc.summary()["current_in_catalog"] is True
        assert svc.summary()["current_model"] == "cheap-model"
    finally:
        db.close()


def test_activate_拒绝已停用模型(tmp_path):
    svc, db, _cfg, _saved = _svc(tmp_path)
    try:
        svc.upsert(model="disabled-m", enabled=False)
        mid = make_model_id("openai_compat", "disabled-m")
        with pytest.raises(ValueError, match="停用"):
            svc.activate(mid)
    finally:
        db.close()


def test_activate_未知模型抛_KeyError(tmp_path):
    svc, db, _cfg, _saved = _svc(tmp_path)
    try:
        with pytest.raises(KeyError):
            svc.activate("nope/nope")
    finally:
        db.close()


def test_catalog_标记当前与未纳入目录(tmp_path):
    svc, db, _cfg, _saved = _svc(tmp_path, model="在用的模型")
    try:
        svc.upsert(model="在用的模型")
        svc.upsert(model="别的模型")
        rows = {r["model"]: r for r in svc.catalog()}
        assert rows["在用的模型"]["is_current"] is True
        assert rows["别的模型"]["is_current"] is False

        # 配置里换成一个不在目录里的模型 → 页面要能提示"未纳入目录"
        svc.app.llm.model = "野模型"
        assert svc.summary()["current_in_catalog"] is False
    finally:
        db.close()


# --------------------------------------------------------------------------- 拉取导入
def test_import_新增且不覆盖手写备注(tmp_path):
    async def fake_fetch(provider=None, base_url=None, api_key=None):
        return ["m-a", "m-b", "m-c"]

    svc, db, _cfg, _saved = _svc(tmp_path, fetched=fake_fetch)
    try:
        # 先手写一条 m-b 的备注
        svc.upsert(model="m-b", note="这条备注是我的心血，不能被导入覆盖", source="manual")

        res = _run(svc.import_fetched())
        assert res["fetched"] == 3
        assert sorted(res["added"]) == ["m-a", "m-c"]
        assert res["refreshed"] == ["m-b"]
        assert res["kept_notes"] == 1

        row = db.get_model(make_model_id("openai_compat", "m-b"))
        assert row["note"] == "这条备注是我的心血，不能被导入覆盖", "导入绝不能覆盖用户备注"
        assert db.get_model(make_model_id("openai_compat", "m-a"))["source"] == "fetched"

        # 再导一次：不重复新增
        res2 = _run(svc.import_fetched())
        assert res2["added"] == [] and len(res2["refreshed"]) == 3
    finally:
        db.close()


def test_import_没有拉取能力时报错(tmp_path):
    svc, db, _cfg, _saved = _svc(tmp_path, fetched=None)
    try:
        with pytest.raises(RuntimeError, match="拉取"):
            _run(svc.import_fetched())
    finally:
        db.close()


def test_能力标签枚举非空():
    assert "工具调用" in CAPABILITY_TAGS and len(CAPABILITY_TAGS) >= 4


# --------------------------------------------------------------------------- HTTP
def _client(tmp_path, monkeypatch):
    from dogbuddy.main import DogBuddyApp
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="http://ep/v1", api_key="k", model="m0"),
                    web=WebConfig(auth_token="tok", port=8127),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = "tok"
    return app, client


def test_路由需要鉴权(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        assert client.get("/api/models", headers={"X-Auth-Token": ""}).status_code == 401


def test_路由_CRUD_切换与备注(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        opts = client.get("/api/models/options").json()
        assert "工具调用" in opts["capabilities"]

        created = client.post("/api/models", json={
            "model": "cheap-1", "display_name": "便宜一号",
            "note": "限速凶，别并发", "capability": "便宜", "context_window": 16000}).json()
        mid = created["model"]["id"]

        listed = client.get("/api/models").json()
        assert any(m["id"] == mid for m in listed["models"])
        assert listed["summary"]["noted"] == 1

        # 备注单独改（这是页面最常见操作）
        patched = client.patch(f"/api/models/{mid}", json={"note": "更新后的备注"}).json()
        assert patched["model"]["note"] == "更新后的备注"
        assert patched["model"]["context_window"] == 16000, "只改 note 不应抹掉其它字段"

        # 切换当前模型 → 运行时配置 + 落盘
        act = client.post(f"/api/models/{mid}/activate").json()
        assert act["result"]["model"] == "cheap-1"
        assert act["current"]["model"] == "cheap-1"
        assert act["result"]["saved_to"].endswith("config.yaml")

        assert client.get("/api/models").json()["current"]["model"] == "cheap-1"

        assert client.delete(f"/api/models/{mid}").status_code == 200
        assert client.patch(f"/api/models/{mid}", json={"note": "x"}).status_code == 404


def test_路由_新增缺模型名返回400(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        r = client.post("/api/models", json={"model": "   "})
        assert r.status_code == 400


def test_路由_切换不存在返回404(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        assert client.post("/api/models/nope/activate").status_code == 404


def test_切换后_config_落盘可回读(tmp_path, monkeypatch):
    """端到端：切模型 → 写盘 → 重新加载配置仍是新模型（这就是"能替换模型"）。"""
    from dogbuddy.config import load_config
    app, client = _client(tmp_path, monkeypatch)
    cfg_file = tmp_path / "config.yaml"
    with client:
        client.post("/api/models", json={"model": "persisted-model", "base_url": "http://new/v1"})
        client.post(f"/api/models/{make_model_id('openai_compat', 'persisted-model')}/activate")
    assert cfg_file.exists()
    reloaded = load_config(cfg_file)
    assert reloaded.llm.model == "persisted-model"
    assert reloaded.llm.base_url == "http://new/v1"


def test_路由_新增同名同端点返回409且不覆盖(tmp_path, monkeypatch):
    """owner 实测："创建新模型还是会覆盖同名模型"（修复前是静默 200 + 覆盖）。"""
    app, client = _client(tmp_path, monkeypatch)
    with client:
        first = client.post("/api/models", json={
            "model": "dup-1", "base_url": "http://ep/v1", "note": "原始备注"}).json()
        assert client.post("/api/models", json={
            "model": "dup-1", "base_url": "http://ep/v1",
            "note": "新备注"}).status_code == 409
        rows = client.get("/api/models").json()["models"]
        same = [m for m in rows if m["model"] == "dup-1"]
        assert len(same) == 1, "同名同端点不该多出一条，也不该被覆盖"
        assert same[0]["note"] == "原始备注"
        # 同名但换端点 → 另存一条（两条共存）
        second = client.post("/api/models", json={
            "model": "dup-1", "base_url": "http://other-host/v1", "note": "另一家"})
        assert second.status_code == 200
        rows = client.get("/api/models").json()["models"]
        dup = [m for m in rows if m["model"] == "dup-1"]
        assert len(dup) == 2, [m["id"] for m in dup]
        assert {m["note"] for m in dup} == {"原始备注", "另一家"}
