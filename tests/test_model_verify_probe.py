# DogBuddyCn — 「验证」按钮真调用探测回归（v0.10.21）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测（ds4.1）：模型**在**服务商列表里，但这把 key 的套餐不含它 ——
一调就是 `403 permission_denied_error: model is not available in the current token plan`。

旧的 `/models/verify-current` 只查"在不在列表"，于是显示绿灯、真跑任务才炸。
现在 `probe=True` 会再发一个 1-token 真调用，并在"在列表里但调用被拒"时给 warning。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.model_service import ModelService  # noqa: E402


def _svc(tmp_path, model="deepseek-v4.1-flash", probe=None, ids=("sensenova-6.8-flash-lite",
                                                                 "deepseek-v4.1-flash")):
    db = Database(str(tmp_path / "m.db"))
    cfg = AppConfig(paths=PathsConfig(), llm=LLMConfig(
        provider="openai_compat", base_url="https://token.sensenova.cn/v1",
        model=model, api_key="sk-x"))

    async def fetch_meta(provider=None, base_url=None, api_key=None):
        return [{"id": i, "context_window": 1000} for i in ids]

    svc = ModelService(db, cfg, fetch_meta=fetch_meta, probe=probe)
    return db, svc


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_只在列表里不算通过_真调用被拒要报警(tmp_path):
    async def probe(provider, base_url, api_key, model):
        return {"ok": True, "model_count": 7, "latency_ms": 12,
                "chat_error": "LLM error 403: model is not available in the current token plan"}

    db, svc = _svc(tmp_path, probe=probe)
    r = _run(svc.verify_current(probe=True))
    assert r["in_provider_list"] is True, "列表校验本身是通过的"
    assert r["usable"] is False
    assert "真调用被拒" in r["warning"] and "token plan" in r["warning"]
    assert r["probe"]["error"].startswith("LLM error 403")
    db.close()


def test_真调用通过时给出可用与延迟(tmp_path):
    async def probe(provider, base_url, api_key, model):
        return {"ok": True, "model_count": 7, "latency_ms": 321, "chat_sample": "ok"}

    db, svc = _svc(tmp_path, probe=probe)
    r = _run(svc.verify_current(probe=True))
    assert r["usable"] is True and r["ok"] is True
    assert r["probe"]["latency_ms"] == 321
    assert "warning" not in r
    db.close()


def test_不传probe时保持老行为_不花那一次调用(tmp_path):
    calls = []

    async def probe(*a, **k):
        calls.append(a)
        return {"ok": True}

    db, svc = _svc(tmp_path, probe=probe)
    r = _run(svc.verify_current())
    assert r["in_provider_list"] is True
    assert "usable" not in r and calls == [], "默认不该发真调用（省额度）"
    db.close()


def test_探测抛异常也不能500(tmp_path):
    async def probe(*a, **k):
        raise RuntimeError("network down")

    db, svc = _svc(tmp_path, probe=probe)
    r = _run(svc.verify_current(probe=True))
    assert r["usable"] is False and "network down" in r["probe"]["error"]
    db.close()
