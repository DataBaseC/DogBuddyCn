# DogBuddyCn — 单管理员 + 多对话（v2 口径，2026-10-14 用户拍板）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""用户口径：**只有一个管理员**与用户对接；**用户可以选择新建对话**。

"新建对话"的意义：换一件事就开一个对话，各自独立上下文、互不串味——
而不是把所有历史挤在一条会话里让管理员反复被无关内容干扰。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import SCHEMA_VERSION, Database  # noqa: E402


def _app(tmp_path, monkeypatch, token="tok"):
    from dogbuddy.main import DogBuddyApp
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token=token, port=8140),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = token
    return app, client


# --------------------------------------------------------------------------- schema
def test_迁移_v9_建对话表且幂等(tmp_path):
    assert SCHEMA_VERSION >= 9
    p = tmp_path / "a.db"
    db = Database(p)
    try:
        assert db.schema_version == SCHEMA_VERSION
        cols = {r[1] for r in db._conn.execute("PRAGMA table_info(conversations)")}
        assert {"id", "title", "archived", "created_at", "updated_at",
                "last_message_at"} <= cols
        assert db.list_conversations() == []
    finally:
        db.close()
    db2 = Database(p)
    try:
        assert db2.schema_version == SCHEMA_VERSION and db2.list_conversations() == []
    finally:
        db2.close()


def test_默认对话自愈(tmp_path, monkeypatch):
    """升级后/删光后必须仍有一个可用对话，用户不会面对空白。"""
    app, _client = _app(tmp_path, monkeypatch)
    svc = app.admin
    convs = svc.list_conversations()
    assert len(convs) == 1 and convs[0]["id"] == svc.DEFAULT_CONVERSATION
    # 删掉唯一一个 → 自愈出新的
    assert svc.delete_conversation(svc.DEFAULT_CONVERSATION) is True
    assert len(svc.list_conversations()) == 1


def test_接管老版本会话历史(tmp_path, monkeypatch):
    """升级自愈：v9 之前会话记在 `admin:console`，接管后**历史不能看起来丢了**。

    这是真事——线上生产库升级时正好有 28 条历史挂在 admin:console 下。
    """
    app, _client = _app(tmp_path, monkeypatch)
    # 造出"老版本残留"：对话表清空，历史只挂在 admin:console / admin:system
    app.db._conn.execute("DELETE FROM conversations")
    app.db._conn.commit()
    for i in range(5):
        app.db.add_message("admin:console", "user", f"第 {i} 条老消息")
    app.db.add_message("admin:console", "assistant", "老回复")
    app.db.add_message("admin:system", "user", "[system event] 噪音")

    convs = app.admin.list_conversations()
    assert len(convs) == 1, f"应恰好接管出一个对话，实际 {[c['id'] for c in convs]}"
    conv = convs[0]
    assert conv["id"] == "console", "要接管消息最多的那个会话，而不是新建空的"
    assert conv["title"] == "默认对话"
    assert conv["message_count"] == 6, "历史条数必须完整"
    assert conv["preview"] == "第 4 条老消息"
    assert all(c["id"] != "system" for c in convs), "系统通知会话不是用户的对话"
    assert len(app.admin.history("console", 50)) == 6


def test_没有老会话时用默认对话(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    app.db._conn.execute("DELETE FROM conversations")
    app.db._conn.commit()
    convs = app.admin.list_conversations()
    assert len(convs) == 1 and convs[0]["id"] == "default"


# --------------------------------------------------------------------------- 新建/命名
def test_新建对话_自动编号(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        svc.ensure_default_conversation()
        a = svc.create_conversation()
        b = svc.create_conversation()
        assert a["id"] != b["id"]
        titles = {c["title"] for c in svc.list_conversations()}
        assert a["title"] == "对话 1" and b["title"] == "对话 2", titles
        # 指定标题
        c = svc.create_conversation("登录功能")
        assert c["title"] == "登录功能"


def test_首条消息自动成为标题_不覆盖手改(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        conv = svc.create_conversation()          # 自动名"对话 N"
        svc._touch_conversation(conv["id"], "帮我给模型管理页加批量导入")
        assert svc.db.get_conversation(conv["id"])["title"].startswith("帮我给模型管理页")

        # 用户手动改名后，后续消息不能把它覆盖回去
        svc.rename_conversation(conv["id"], "批量导入")
        svc._touch_conversation(conv["id"], "再改一下别的")
        assert svc.db.get_conversation(conv["id"])["title"] == "批量导入"


def test_重命名与删除(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        svc.ensure_default_conversation()
        conv = svc.create_conversation("旧名")
        assert svc.rename_conversation(conv["id"], "新名")["title"] == "新名"
        with pytest.raises(KeyError):
            svc.rename_conversation("nope", "x")
        assert svc.delete_conversation(conv["id"]) is True
        assert svc.delete_conversation(conv["id"]) is False


# --------------------------------------------------------------------------- 上下文隔离
def test_对话之间的上下文互不串味(tmp_path, monkeypatch):
    """这是"新建对话"的核心价值：A 对话的消息不能出现在 B 对话里。"""
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        db = app.db
        svc.ensure_default_conversation()
        a = svc.create_conversation("A 对话")
        b = svc.create_conversation("B 对话")
        db.add_message(f"admin:{a['id']}", "user", "只有 A 能看到")
        db.add_message(f"admin:{b['id']}", "user", "只有 B 能看到")

        ha = [m["content"] for m in svc.history(a["id"], 50)]
        hb = [m["content"] for m in svc.history(b["id"], 50)]
        assert "只有 A 能看到" in ha and "只有 B 能看到" not in ha
        assert "只有 B 能看到" in hb and "只有 A 能看到" not in hb


def test_删对话连消息一起删(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        db = app.db
        svc.ensure_default_conversation()
        conv = svc.create_conversation("要删的")
        db.add_message(f"admin:{conv['id']}", "user", "这条也该被删")
        assert svc.history(conv["id"], 10)

        svc.delete_conversation(conv["id"])
        assert svc.history(conv["id"], 10) == [], "对话删了消息也要清掉"


def test_列表带消息数与最近用户消息(tmp_path, monkeypatch):
    app, _client = _app(tmp_path, monkeypatch)
    if True:
        svc = app.admin
        db = app.db
        svc.ensure_default_conversation()
        conv = svc.create_conversation("有内容的")
        db.add_message(f"admin:{conv['id']}", "user", "第一条")
        db.add_message(f"admin:{conv['id']}", "assistant", "回复")
        row = [c for c in svc.list_conversations() if c["id"] == conv["id"]][0]
        assert row["message_count"] == 2
        assert row["preview"] == "第一条"


# --------------------------------------------------------------------------- HTTP
def test_对话接口_需要鉴权(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch)
    with client:
        assert client.get("/api/admin/chat/conversations",
                          headers={"X-Auth-Token": ""}).status_code == 401


def test_对话接口_增改删查(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch)
    with client:
        listed = client.get("/api/admin/chat/conversations").json()
        assert len(listed["conversations"]) >= 1, "至少有一个默认对话"
        assert "只有一个管理员" in listed["note"]

        created = client.post("/api/admin/chat/conversations",
                              json={"title": "登录任务"}).json()
        cid = created["conversation"]["id"]
        assert created["conversation"]["title"] == "登录任务"
        assert len(created["conversations"]) == 2

        renamed = client.patch(f"/api/admin/chat/conversations/{cid}",
                               json={"title": "登录任务（新）"}).json()
        assert renamed["conversation"]["title"] == "登录任务（新）"
        assert client.patch("/api/admin/chat/conversations/nope",
                            json={"title": "x"}).status_code == 404

        assert client.delete(f"/api/admin/chat/conversations/{cid}").status_code == 200
        assert client.delete(f"/api/admin/chat/conversations/{cid}").status_code == 404


def test_消息接口_新老参数都认(tmp_path, monkeypatch):
    """老客户端传 sender、新客户端传 conversation，指向同一个对话。"""
    app, client = _app(tmp_path, monkeypatch)
    with client:
        app.db.add_message("admin:console", "user", "老参数可见")
        by_sender = client.get("/api/admin/chat/messages", params={"sender": "console"}).json()
        by_conv = client.get("/api/admin/chat/messages",
                             params={"conversation": "console"}).json()
        assert by_sender["conversation"] == "console"
        assert by_conv["conversation"] == "console"
        assert any("老参数可见" in m["content"] for m in by_sender["messages"])
        assert any("老参数可见" in m["content"] for m in by_conv["messages"])
        # 不传参数 → 默认对话
        assert client.get("/api/admin/chat/messages").json()["conversation"] == "default"


def test_单管理员声明(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch)
    with client:
        info = client.get("/api/admin/chat/admins").json()
        assert info["single_admin"] is True
        assert client.get("/api/admins").status_code == 404, "v2 没有多管理员接口"


def test_清空某个对话_不影响别的(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch)
    with client:
        keep = client.post("/api/admin/chat/conversations", json={"title": "保留"}).json()
        drop = client.post("/api/admin/chat/conversations", json={"title": "清空"}).json()
        kid, did = keep["conversation"]["id"], drop["conversation"]["id"]
        app.db.add_message(f"admin:{kid}", "user", "保留内容")
        app.db.add_message(f"admin:{did}", "user", "待清空内容")

        r = client.post("/api/admin/chat/reset", json={"conversation": did}).json()
        assert r["conversation"] == did and r["dropped"] >= 1
        assert client.get("/api/admin/chat/messages",
                          params={"conversation": did}).json()["messages"] == []
        left = client.get("/api/admin/chat/messages",
                          params={"conversation": kid}).json()["messages"]
        assert any("保留内容" in m["content"] for m in left), "别的对话不能被连带清空"
