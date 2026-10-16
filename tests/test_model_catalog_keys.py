# DogBuddyCn — 模型目录自带密钥 + 一键接入（schema v12）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""回归测试：**新增一个 LLM 模型必须是一条表单的事，而且密钥不能被接口回传**。

背景（owner 亲自报的："我怎么新增 llm 模型，太不方便了吧"）：
  * 目录条目只有 provider/base_url，没有密钥 → "设为当前"换到另一家服务商时，
    用的还是全局 `llm.api_key`（401），必须再回设置页手改；
  * 表单要一次填 9 个字段（provider 还得手打），跨服务商时尤其难受。

v12 的修法：条目自带可选密钥（`models.api_key`），"设为当前"连密钥一起切；
表单加服务商预设 + 密钥栏 + 测试连接 + 拉取模型列表；接口只回掩码。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import SCHEMA_VERSION, Database  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402
from dogbuddy.services.model_service import ModelService, mask_key  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TOKEN = "model-token"
RAW_KEY = "sk-supersecret-9f3a"


# ----------------------------------------------------------------- 迁移 / 存储
def test_schema_v12_and_migration_adds_api_key_column(tmp_path):
    db = Database(tmp_path / "new.db")
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(models)").fetchall()}  # noqa: SLF001
    assert "api_key" in cols
    # v13（runs 表）之后这里不再钉死 12：只要求"v12 迁移确实存在且库已跟上"
    assert SCHEMA_VERSION >= 12 and db.schema_version == SCHEMA_VERSION
    db.close()


def test_migration_12_upgrades_a_v11_database(tmp_path):
    """老库（v11，models 没有 api_key）启动时要被自动补列。"""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT);
    INSERT INTO schema_version(version, applied_at) VALUES(11, '2026-10-14T00:00:00+00:00');
    CREATE TABLE models(
      id TEXT PRIMARY KEY, provider TEXT DEFAULT '', model TEXT NOT NULL,
      base_url TEXT DEFAULT '', display_name TEXT DEFAULT '',
      note TEXT DEFAULT '', capability TEXT DEFAULT '',
      context_window INTEGER DEFAULT 0, speed TEXT DEFAULT '', cost TEXT DEFAULT '',
      enabled INTEGER DEFAULT 1, is_default INTEGER DEFAULT 0,
      source TEXT DEFAULT 'manual', created_at TEXT, updated_at TEXT);
    INSERT INTO models(id, provider, model, base_url, enabled, is_default)
      VALUES('openai_compat_old-model', 'openai_compat', 'old-model', 'https://old/v1', 1, 1);
    """)
    conn.commit()
    conn.close()

    db = Database(path)
    assert db.schema_version == SCHEMA_VERSION
    row = db.get_model("openai_compat_old-model")
    assert row is not None and "api_key" in row and row["api_key"] in ("", None)
    db.close()


def test_mask_key_and_masked_detection():
    assert mask_key("") == "" and mask_key(None) == ""
    assert mask_key("sk-abcdef1234") == "********1234"
    assert mask_key("short") == "********", "短 key 不能露出尾巴（等于泄露大半）"


# ----------------------------------------------------------------- 服务层
def _service(tmp_path) -> tuple[Database, AppConfig, ModelService]:
    db = Database(tmp_path / "s.db")
    cfg = AppConfig(
        llm=LLMConfig(base_url="https://global/v1", model="global-model", api_key="global-key"),
        web=WebConfig(auth_token=TOKEN, port=8131),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    persisted = {"n": 0}
    svc = ModelService(db, cfg, persist=lambda: persisted.__setitem__("n", persisted["n"] + 1) or "cfg.yaml")
    return db, cfg, svc


def test_entry_key_is_stored_but_never_returned_in_plaintext(tmp_path):
    db, _cfg, svc = _service(tmp_path)
    saved = svc.upsert(provider="openai_compat", model="m1", base_url="https://a/v1",
                       api_key=RAW_KEY)
    assert saved["has_api_key"] is True and saved["api_key"] == "********" + RAW_KEY[-4:]
    # 本地库存明文（配置/库都在 0600 的 data 目录），但**接口形状**只给掩码
    assert db.get_model(saved["id"])["api_key"] == RAW_KEY
    body = json.dumps(svc.catalog(), ensure_ascii=False)
    assert RAW_KEY not in body, "目录接口把明文密钥漏出去了"
    assert "********" + RAW_KEY[-4:] in body
    db.close()


def test_patch_with_blank_or_masked_key_keeps_existing(tmp_path):
    db, _cfg, svc = _service(tmp_path)
    mid = svc.upsert(provider="openai_compat", model="m1", base_url="https://a/v1",
                     api_key=RAW_KEY)["id"]
    svc.update(mid, note="改备注", api_key="")                       # 留空 = 不变
    svc.update(mid, api_key="********" + RAW_KEY[-4:])                # 回传掩码 = 不变
    assert db.get_model(mid)["api_key"] == RAW_KEY
    svc.update(mid, api_key="sk-new-key-xyz")                         # 真给新值 = 换掉
    assert db.get_model(mid)["api_key"] == "sk-new-key-xyz"
    db.close()


def test_activate_switches_the_entry_key_too(tmp_path):
    """核心诉求：换到另一家服务商的模型时，密钥要跟着换。"""
    db, cfg, svc = _service(tmp_path)
    mid = svc.upsert(provider="openai_compat", model="m1", base_url="https://other/v1",
                     api_key=RAW_KEY)["id"]
    assert cfg.llm.api_key == "global-key"
    res = svc.activate(mid)
    assert cfg.llm.base_url == "https://other/v1" and cfg.llm.model == "m1"
    assert cfg.llm.api_key == RAW_KEY, "切模型没把该条目自带的密钥切过去（生产会 401）"
    assert res["api_key_switched"] is True
    assert res["saved_to"] == "cfg.yaml", "切换必须落盘，否则重启就回去了"
    db.close()


def test_activate_without_entry_key_keeps_global(tmp_path):
    db, cfg, svc = _service(tmp_path)
    mid = svc.upsert(provider="openai_compat", model="m2", base_url="https://b/v1")["id"]
    res = svc.activate(mid)
    assert cfg.llm.api_key == "global-key" and res["api_key_switched"] is False
    db.close()


# ----------------------------------------------------------------- HTTP 层
def _app(tmp_path, monkeypatch) -> DogBuddyApp:
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(
        llm=LLMConfig(base_url="https://global/v1", model="gm", api_key="global-key"),
        web=WebConfig(auth_token=TOKEN, port=8132),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    return DogBuddyApp(cfg)


def test_api_never_echoes_the_key_and_options_carry_presets(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        opts = client.get("/api/models/options").json()
        assert opts["providers"] and any("SenseNova" in p["label"] for p in opts["providers"])
        assert all(set(p) == {"label", "provider", "url"} for p in opts["providers"])

        created = client.post("/api/models", json={
            "provider": "openai_compat", "model": "m-http", "base_url": "https://a/v1",
            "api_key": RAW_KEY, "note": "测试条目"}).json()
        assert RAW_KEY not in json.dumps(created, ensure_ascii=False)
        assert created["model"]["has_api_key"] is True

        listing = client.get("/api/models")
        assert RAW_KEY not in listing.text, "GET /api/models 回传了明文密钥"
        row = [m for m in listing.json()["models"] if m["model"] == "m-http"][0]
        assert row["has_api_key"] is True and row["api_key"].endswith(RAW_KEY[-4:])

        # 编辑（不动密钥）后依然保留在库里
        client.patch(f"/api/models/{row['id']}", json={"note": "改了备注"})
        assert app.db.get_model(row["id"])["api_key"] == RAW_KEY

        # 目录条目自带的密钥可以用来测连接：未知 id → 404（不触网）
        r = client.post("/api/settings/test_llm", json={"model_ref": "nope/none"})
        assert r.status_code == 404


def test_frontend_has_the_one_form_onboarding_path():
    js = (REPO / "dogbuddy/web/static/js/views-models.js").read_text(encoding="utf-8")
    for hook in ("mdl-f-presets", "mdl-f-key", "mdl-f-test", "mdl-f-fetch",
                 "mdl-f-select", "has_api_key", "api_key_switched"):
        assert hook in js, f"模型页缺少接入要素：{hook}"
    html = (REPO / "dogbuddy/web/static/index.html").read_text(encoding="utf-8")
    assert 'id="mdl-add"' in html and 'id="mdl-import"' in html


# ------------------------------------------------- 当前模型与目录/服务商的一致性
def test_adopt_current_registers_the_running_model(tmp_path, monkeypatch):
    """owner 看到"管理员在用一个列表里没有的模型"：一键把它收进目录。"""
    app = _app(tmp_path, monkeypatch)
    app.config.llm.model = "zai-org/GLM-5.3-Flash"
    app.config.llm.base_url = "https://api.siliconflow.cn/v1"
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        before = client.get("/api/models").json()
        assert before["summary"]["current_in_catalog"] is False
        r = client.post("/api/models/adopt-current")
        assert r.status_code == 200, r.text
        after = client.get("/api/models").json()
        assert after["summary"]["current_in_catalog"] is True
        row = [m for m in after["models"] if m["model"] == "zai-org/GLM-5.3-Flash"][0]
        assert row["base_url"] == "https://api.siliconflow.cn/v1"
        assert row["has_api_key"] is False          # 不复制密钥，继续用全局那把
        assert row["api_key"] == ""
        # 幂等：再点一次不会又加一条
        again = client.post("/api/models/adopt-current").json()
        assert len(again["models"]) == len(after["models"])


def test_verify_current_reports_membership(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.config.llm.model = "vendor/typo-model"

    async def fake_meta(provider=None, base_url=None, api_key=None):
        return [{"id": "vendor/typo-model-2", "context_window": 0},
                {"id": "other/model", "context_window": 0}]

    # ModelService 在构造时就抓住了 fetch_meta 引用 → 要替换它自己那个属性
    monkeypatch.setattr(app.models, "_fetch_meta", fake_meta)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        r = client.get("/api/models/verify-current").json()
        assert r["in_provider_list"] is False and r["model_count"] == 2
        assert r["similar"] == ["vendor/typo-model-2"], "没给出相近的候选（拼错时很有用）"

        app.config.llm.model = "other/model"
        assert client.get("/api/models/verify-current").json()["in_provider_list"] is True


def test_frontend_wires_adopt_and_verify():
    js = (REPO / "dogbuddy/web/static/js/views-models.js").read_text(encoding="utf-8")
    api = (REPO / "dogbuddy/web/static/js/api.js").read_text(encoding="utf-8")
    assert "把当前模型纳入目录" in js and "adopt-current" in js
    assert "校验它在服务商列表里" in js and "verify-current" in js
    assert "adoptCurrent" in api and "verifyCurrent" in api
