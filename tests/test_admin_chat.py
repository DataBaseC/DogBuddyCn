# DogBuddyCn — console ↔ admin agent chat API
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Regression tests for `/api/admin/chat*`.

The v1.3 baseline had no way to reach the admin agent from the web console —
`AdminService.handle_user_message` was only wired to the QQ gateway. These tests
pin the new contract: auth required, a clean 409 before the LLM is configured,
history persisted across turns, and per-sender isolation.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.llm.provider import LLMResponse  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402

from helpers import FakeLLM  # noqa: E402

TOKEN = "chat-token"


def _app(tmp_path, monkeypatch, configured: bool = True) -> DogBuddyApp:
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    if configured:
        llm = LLMConfig(base_url="http://invalid.local/v1", api_key="k", model="fake",
                        request_timeout=5.0)
    else:
        llm = LLMConfig(base_url="", api_key="", model="")
    cfg = AppConfig(
        llm=llm,
        web=WebConfig(auth_token=TOKEN, port=8124),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    fake = FakeLLM([LLMResponse(content="好的，已建好工位。")])
    app.llm_factory = lambda: fake
    app.windows.llm_factory = app.llm_factory
    app.admin.llm_factory = app.llm_factory
    return app


def test_chat_requires_auth(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        assert client.post("/api/admin/chat", json={"text": "hi"}).status_code == 401
        assert client.get("/api/admin/chat/messages").status_code == 401


def test_chat_refuses_without_llm_config(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, configured=False)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        r = client.post("/api/admin/chat", json={"text": "在吗"})
        assert r.status_code == 409
        assert "LLM" in r.json()["detail"]
        # the admins endpoint still works, and reports the missing config
        info = client.get("/api/admin/chat/admins").json()
        assert info["llm_configured"] is False


def test_chat_persists_history_across_turns(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN

        r = client.post("/api/admin/chat", json={"text": "每天早8点推进待办", "sender": "console"})
        assert r.status_code == 200
        body = r.json()
        assert body["reply"]
        assert body["sender"] == "console"

        hist = client.get("/api/admin/chat/messages", params={"sender": "console"}).json()
        roles = [m["role"] for m in hist["messages"]]
        assert "user" in roles and "assistant" in roles
        assert any("每天早8点推进待办" in m["content"] for m in hist["messages"])

        # a second turn keeps appending to the same conversation
        client.post("/api/admin/chat", json={"text": "再建一个测试工位", "sender": "console"})
        hist2 = client.get("/api/admin/chat/messages", params={"sender": "console"}).json()
        assert len(hist2["messages"]) > len(hist["messages"])


def test_chat_senders_are_isolated(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        client.post("/api/admin/chat", json={"text": "只有 A 能看到", "sender": "alice"})
        client.post("/api/admin/chat", json={"text": "只有 B 能看到", "sender": "bob"})

        a = client.get("/api/admin/chat/messages", params={"sender": "alice"}).json()["messages"]
        b = client.get("/api/admin/chat/messages", params={"sender": "bob"}).json()["messages"]
        assert any("只有 A 能看到" in m["content"] for m in a)
        assert not any("只有 B 能看到" in m["content"] for m in a)
        assert any("只有 B 能看到" in m["content"] for m in b)

        admins = client.get("/api/admin/chat/admins").json()["admins"]
        assert "alice" in admins and "bob" in admins


def test_chat_reset_drops_history(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        client.post("/api/admin/chat", json={"text": "你好", "sender": "console"})
        assert client.get("/api/admin/chat/messages", params={"sender": "console"}).json()["messages"]

        r = client.post("/api/admin/chat/reset", json={"sender": "console"})
        assert r.status_code == 200 and r.json()["dropped"] >= 1
        left = client.get("/api/admin/chat/messages", params={"sender": "console"}).json()["messages"]
        assert left == []


def test_chat_rejects_empty_text(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        assert client.post("/api/admin/chat", json={"text": "   "}).status_code == 400


def test_admin_turns_are_serialized_per_sender(tmp_path, monkeypatch):
    """Two concurrent turns on one sender must not interleave in the session."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        # both requests share one FakeLLM; serialization means each gets a reply
        r1 = client.post("/api/admin/chat", json={"text": "一号指令", "sender": "s"})
        r2 = client.post("/api/admin/chat", json={"text": "二号指令", "sender": "s"})
        assert r1.status_code == 200 and r2.status_code == 200
        msgs = client.get("/api/admin/chat/messages", params={"sender": "s"}).json()["messages"]
        contents = [m["content"] for m in msgs]
        assert any("一号指令" in c for c in contents)
        assert any("二号指令" in c for c in contents)
        # user/assistant alternate: no two "user" turns in a row
        roles = [m["role"] for m in msgs if m["role"] in ("user", "assistant")]
        assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), roles
