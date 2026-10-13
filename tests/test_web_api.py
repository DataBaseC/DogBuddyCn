# DogBuddyCn — web API integration tests (real DogBuddyApp assembly + TestClient)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig
from dogbuddy.llm.provider import LLMResponse
from dogbuddy.main import DogBuddyApp

from helpers import FakeLLM, tc

TOKEN = "test-token"


def _app(tmp_path) -> DogBuddyApp:
    cfg = AppConfig(
        llm=LLMConfig(base_url="http://invalid.local/v1", api_key="k", model="fake",
                      request_timeout=5.0),
        web=WebConfig(auth_token=TOKEN),
        paths=PathsConfig(data_dir=str(tmp_path / "data"),
                          workspaces_dir=str(tmp_path / "workspaces")),
    )
    cfg.gateway.enabled = False  # no WS server in tests
    app = DogBuddyApp(cfg)
    # swap in a scripted LLM so dispatches finish instantly
    fake = FakeLLM([LLMResponse(content="done")])
    app.llm_factory = lambda: fake
    app.windows.llm_factory = app.llm_factory
    app.admin.llm_factory = app.llm_factory
    return app


def _client(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app.web_app)
    client.__enter__()  # trigger startup lifespan (boots window workers, scheduler)
    client.headers["X-Auth-Token"] = TOKEN
    return app, client


def test_auth_required(tmp_path):
    _, client = _client(tmp_path)
    r = client.get("/api/status", headers={"X-Auth-Token": ""})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"token": TOKEN})
    assert r.status_code == 200 and r.json()["token"] == TOKEN
    r = client.post("/api/auth/login", json={"password": "wrong"})
    assert r.status_code == 401


def test_status_and_overview(tmp_path):
    _, client = _client(tmp_path)
    assert client.get("/api/status").json()["running"] is True
    ov = client.get("/api/overview").json()
    assert "windows" in ov and "jobs" in ov and "recent_runs" in ov


def test_windows_crud_flow(tmp_path):
    app, client = _client(tmp_path)
    ws = tmp_path / "proj"
    r = client.post("/api/windows", json={"name": "dev", "role": "worker_programmer",
                                          "workspace_path": str(ws), "model": "fake"})
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    assert client.get("/api/windows").json()[0]["name"] == "dev"
    assert client.patch(f"/api/windows/{wid}", json={"mode": "PLAN"}).json()["mode"] == "PLAN"
    assert client.post(f"/api/windows/{wid}/pause").json()["paused"] is True
    assert client.post(f"/api/windows/{wid}/resume").json()["resumed"] is True

    r = client.post(f"/api/windows/{wid}/dispatch", json={"prompt": "say hi"})
    assert r.json()["queued"] is True
    import time
    time.sleep(0.3)
    msgs = client.get(f"/api/windows/{wid}/messages").json()["messages"]
    assert any(m["role"] == "assistant" for m in msgs)

    assert client.post(f"/api/windows/{wid}/context/inject",
                       json={"text": "note"}).json()["injected"] is True
    assert "dropped" in client.post(f"/api/windows/{wid}/context/reset").json()
    assert "replaced" in client.post(f"/api/windows/{wid}/context/compress").json()
    assert client.delete(f"/api/windows/{wid}").json()["deleted"] is True
    app.db.close()


def test_jobs_flow(tmp_path):
    app, client = _client(tmp_path)
    ws = tmp_path / "proj"
    wid = client.post("/api/windows", json={"name": "dev", "workspace_path": str(ws)}).json()["id"]
    r = client.post("/api/jobs", json={"window_id": wid, "name": "morning",
                                       "schedule": "every day at 9am",
                                       "prompt": "continue todos"})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    assert r.json()["schedule_type"] == "cron"
    jobs = client.get("/api/jobs").json()
    assert jobs[0]["name"] == "morning"
    assert client.post(f"/api/jobs/{jid}/pause").json()["result"] == "paused"
    assert client.post(f"/api/jobs/{jid}/resume").json()["result"] == "resumed"
    r = client.post("/api/jobs", json={"window_id": wid, "name": "bad", "schedule": "nope",
                                       "prompt": "x"})
    assert r.status_code == 400  # invalid schedule surfaced
    assert client.post(f"/api/jobs/{jid}/run_now").json()["result"].startswith("dispatched")
    assert isinstance(client.get(f"/api/jobs/{jid}/runs").json(), list)
    assert client.delete(f"/api/jobs/{jid}").json()["result"] == "removed"
    app.db.close()


def test_files_explorer(tmp_path):
    app, client = _client(tmp_path)
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "a.txt").write_text("hello", encoding="utf-8")
    # point file service at this workspace
    app.files.roots.append(ws.resolve())

    listing = client.get("/api/files/list", params={"workspace": str(ws)}).json()
    assert any(e["name"] == "a.txt" for e in listing["entries"])
    assert listing["breadcrumbs"]

    read = client.get("/api/files/read", params={"path": "a.txt", "workspace": str(ws)}).json()
    assert read["content"] == "hello"

    assert client.post("/api/files/write", json={"path": "b.txt", "content": "x",
                                                 "workspace": str(ws)}).status_code == 200
    r = client.post("/api/files/mkdir", json={"path": "", "name": "sub", "workspace": str(ws)})
    assert r.status_code == 200
    r = client.post("/api/files/rename", json={"path": "a.txt", "new_name": "c.txt",
                                               "workspace": str(ws)})
    assert r.status_code == 200
    tree = client.get("/api/files/tree", params={"workspace": str(ws)}).json()
    assert any(e["name"] == "c.txt" for e in tree)
    r = client.post("/api/files/delete", json={"path": "c.txt", "workspace": str(ws)})
    assert r.json()["deleted"] is True
    # path escape must be rejected
    r = client.get("/api/files/read", params={"path": "../../etc/passwd", "workspace": str(ws)})
    assert r.status_code == 403
    app.db.close()


def test_settings_and_whitelist(tmp_path):
    app, client = _client(tmp_path)
    s = client.get("/api/settings").json()
    assert "retry" in s["llm"] and "backoff" in s["llm"]["retry"]
    r = client.put("/api/settings", json={"llm": {"retry": {"base_interval": 15, "backoff": "fixed"}},
                                          "report": {"default_strategy": "detailed"}})
    assert r.json()["llm"]["retry"]["base_interval"] == 15
    assert r.json()["report"]["default_strategy"] == "detailed"
    assert client.post("/api/whitelist", json={"qq_id": "123", "name": "me"}).json()["added"] == "123"
    assert client.get("/api/whitelist").json()[0]["qq_id"] == "123"
    assert client.request("DELETE", "/api/whitelist", json={"qq_id": "123"}).json()["removed"] is True
    app.db.close()


def test_logs(tmp_path):
    app, client = _client(tmp_path)
    client.post("/api/windows", json={"name": "dev", "workspace_path": str(tmp_path / "p")})
    audit = client.get("/api/logs/audit").json()
    assert any("window:create" in e["action"] for e in audit)
    assert isinstance(client.get("/api/logs/runs").json(), list)
    app.db.close()


def test_settings_models_and_test_llm(tmp_path):
    app, client = _client(tmp_path)

    async def fake_models(provider=None, base_url=None, api_key=None):
        return ["gpt-4o-mini", "gpt-4o"]

    async def fake_test(provider=None, base_url=None, api_key=None, model=None):
        return {"ok": True, "latency_ms": 42, "model_count": 2, "message": "connected"}

    app.config_svc.fetch_models = fake_models
    app.config_svc.test_llm = fake_test

    r = client.get("/api/settings/models", params={"base_url": "https://x/v1"})
    assert r.status_code == 200
    assert r.json()["models"] == ["gpt-4o-mini", "gpt-4o"] and r.json()["error"] == ""

    r = client.post("/api/settings/test_llm", json={"model": "gpt-4o"})
    assert r.json()["ok"] is True and r.json()["latency_ms"] == 42

    # the test app ships api_key+model → configured (wizard would not show)
    st = client.get("/api/status").json()
    assert st["llm_configured"] is True

    # empty key/model → not configured (setup wizard banner appears)
    app.config.llm.api_key = ""
    app.config.llm.model = ""
    assert client.get("/api/status").json()["llm_configured"] is False
    app.db.close()


def test_settings_profiles_crud(tmp_path):
    app, client = _client(tmp_path)

    # create a named profile and optionally activate it
    r = client.post("/api/settings/profiles/cheap",
                    json={"llm": {"base_url": "https://cheap.example/v1",
                                  "api_key": "ck", "model": "cheap-model"},
                          "set_active": True})
    assert r.status_code == 200
    body = r.json()
    assert "cheap" in body["profiles"] and body["active_llm_profile"] == "cheap"

    listing = client.get("/api/settings/profiles").json()
    assert "cheap" in listing["names"] and listing["active"] == "cheap"
    assert listing["profiles"]["cheap"]["model"] == "cheap-model"

    # partial update keeps other fields
    r = client.patch("/api/settings/profiles/cheap", json={"llm": {"model": "cheap-v2"}})
    assert r.json()["profiles"]["cheap"]["model"] == "cheap-v2"
    assert r.json()["profiles"]["cheap"]["base_url"] == "https://cheap.example/v1"

    # window binds a profile and it round-trips
    w = client.post("/api/windows", json={"name": "dev",
                                          "workspace_path": str(tmp_path / "p"),
                                          "llm_profile": "cheap"}).json()
    assert w["llm_profile"] == "cheap"
    w2 = client.patch(f"/api/windows/{w['id']}", json={"llm_profile": ""}).json()
    assert "llm_profile" in w2

    # jobs carry profile + channel
    j = client.post("/api/jobs", json={"window_id": w["id"], "name": "j",
                                       "schedule": "30m", "prompt": "go",
                                       "llm_profile": "cheap",
                                       "report_channel": "webhook-ops"}).json()
    assert j["llm_profile"] == "cheap" and j["report_channel"] == "webhook-ops"

    # delete deactivates when active
    r = client.request("DELETE", "/api/settings/profiles/cheap")
    assert r.json()["deleted"] is True and r.json()["active"] == ""
    assert client.get("/api/settings/profiles").json()["names"] == [""]
    app.db.close()
