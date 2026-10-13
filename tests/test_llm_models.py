# DogBuddyCn — LLM model discovery & connection test tests
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

import dogbuddy.llm.models as models_mod
from dogbuddy.llm.models import ModelFetchError, fetch_model_list
from dogbuddy.llm.models import test_connection as probe_connection


class FakeResp:
    def __init__(self, status=200, json_data=None, text=""):
        self.status_code = status
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class FakeClient:
    """Stands in for httpx.AsyncClient; routes by URL substring."""

    routes: dict[str, FakeResp] = {}
    calls: list[str] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        FakeClient.calls.append(url)
        for key, resp in FakeClient.routes.items():
            if key in url:
                return resp
        return FakeResp(status=404, text="not found")


def _install(monkeypatch, routes: dict[str, FakeResp]):
    FakeClient.routes = routes
    FakeClient.calls = []
    monkeypatch.setattr(models_mod.httpx, "AsyncClient", FakeClient)


def test_fetch_models_openai(monkeypatch):
    _install(monkeypatch, {"/models": FakeResp(200, {"data": [{"id": "gpt-4o-mini"},
                                                             {"id": "gpt-4o"}]})})
    out = asyncio.get_event_loop().run_until_complete(
        fetch_model_list("openai_compat", "https://api.example.com/v1", "key"))
    assert out == ["gpt-4o", "gpt-4o-mini"]


def test_fetch_models_adds_v1_suffix(monkeypatch):
    # base_url without /v1: first probe fails, /v1/models wins
    _install(monkeypatch, {"/v1/models": FakeResp(200, {"data": [{"id": "m1"}]})})
    out = asyncio.get_event_loop().run_until_complete(
        fetch_model_list("openai_compat", "https://api.example.com", "key"))
    assert out == ["m1"]
    assert any(u.endswith("/v1/models") for u in FakeClient.calls)


def test_fetch_models_auth_error(monkeypatch):
    _install(monkeypatch, {"/models": FakeResp(401)})
    try:
        asyncio.get_event_loop().run_until_complete(
            fetch_model_list("openai_compat", "https://api.example.com/v1", "bad"))
        assert False, "should raise"
    except ModelFetchError as e:
        assert "api_key" in str(e)


def test_fetch_models_ollama(monkeypatch):
    _install(monkeypatch, {"/api/tags": FakeResp(200, {"models": [{"name": "qwen2.5:7b"},
                                                                 {"name": "llama3"}]})})
    out = asyncio.get_event_loop().run_until_complete(
        fetch_model_list("ollama", "http://127.0.0.1:11434", ""))
    assert out == ["llama3", "qwen2.5:7b"]


def test_test_connection_ok(monkeypatch):
    _install(monkeypatch, {"/models": FakeResp(200, {"data": [{"id": "m1"}]})})
    r = asyncio.get_event_loop().run_until_complete(
        probe_connection("openai_compat", "https://api.example.com/v1", "key"))
    assert r["ok"] is True and r["model_count"] == 1 and "latency_ms" in r


def test_test_connection_down(monkeypatch):
    _install(monkeypatch, {})  # everything 404s
    r = asyncio.get_event_loop().run_until_complete(
        probe_connection("openai_compat", "https://down.example.com/v1", "key"))
    assert r["ok"] is False and r["message"]


def test_config_service_llm_configured(tmp_path):
    from dogbuddy.config import AppConfig, LLMConfig, PathsConfig
    from dogbuddy.db import Database
    from dogbuddy.services.config_service import ConfigService

    db = Database(tmp_path / "t.db")
    cfg = AppConfig(llm=LLMConfig(api_key="", base_url="https://x/v1", model=""),
                    paths=PathsConfig(data_dir=str(tmp_path)))
    svc = ConfigService(db, cfg)
    assert svc.llm_configured() is False
    cfg.llm.api_key = "k"
    cfg.llm.model = "m"
    assert svc.llm_configured() is True
    db.close()
