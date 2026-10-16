# DogBuddyCn — 模型目录身份/导入回归（v0.10.21）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测的两个模型配置问题（2026-10-16）：

1. "我好像新增的会覆盖掉原来的" —— 目录条目 id 只由 `provider + model` 决定
   （`make_model_id`），**不含端点**。于是"同名模型、不同地址"的两条必然撞 id，
   第二条把第一条的 `base_url` 静默改掉；而 key 没跟着换 → 之后请求带着上一家的
   密钥打到新端点 → 401（圆桌实测的 worker-1/worker-2 就是这么失败的）。
2. "拉取模型有时候拉取过多，把默认 key 的模型也拉取了" —— 导入接口留空即静默
   回落到"当前生效"的端点与密钥，SiliconFlow 一次 99 条全灌进目录。

修法：`_resolve_id()` 同名不同端点 → 另存 `...@host`；导入不再改写已存在条目的
端点/服务商；新增 `q`/`only` 过滤；响应回报 `used_base_url`/`key_source`。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.model_service import (ModelService, endpoint_of,  # noqa: E402
                                             fetch_error_hint, make_model_id)


def _stack(tmp_path, base_url="https://token.sensenova.cn/v1", model="sensenova-x",
           key="sk-test-key-123456"):
    db = Database(str(tmp_path / "m.db"))
    cfg = AppConfig(paths=PathsConfig(), llm=LLMConfig(
        provider="openai_compat", base_url=base_url, model=model, api_key=key))
    svc = ModelService(db, cfg)
    return db, cfg, svc


def _fake_fetch(names):
    async def _fetch(provider=None, base_url=None, api_key=None):
        return list(names)

    async def _meta(provider=None, base_url=None, api_key=None):
        return [{"id": n, "context_window": 128000} for n in names]

    return _fetch, _meta


# ------------------------------------------------------------------ 身份
def test_endpoint_of_normalizes():
    assert endpoint_of("https://API.DeepSeek.com/v1/") == "api.deepseek.com/v1"
    assert endpoint_of("http://192.168.3.78:1234/v1") == "192.168.3.78:1234/v1"
    assert endpoint_of("") == ""


def test_同名模型不同端点各存一条而不是覆盖(tmp_path):
    db, cfg, svc = _stack(tmp_path)
    first = svc.upsert(model="deepseek-v4.1-flash", provider="openai_compat",
                       base_url="https://token.sensenova.cn/v1", note="nova 家的")
    assert first["id"] == make_model_id("openai_compat", "deepseek-v4.1-flash")
    second = svc.upsert(model="deepseek-v4.1-flash", provider="openai_compat",
                        base_url="http://192.168.3.78:1234/v1", note="本地那份")
    assert second["endpoint_conflict"] is True, "同名不同端点必须另存，不能改写原来那条"
    assert second["id"] != first["id"]
    rows = db.list_models()
    assert len(rows) == 2, f"两条应该共存，实际 {[r['id'] for r in rows]}"
    by_id = {r["id"]: r for r in rows}
    assert by_id[first["id"]]["base_url"] == "https://token.sensenova.cn/v1"
    assert by_id[first["id"]]["note"] == "nova 家的", "原来那条的备注不能被改"
    assert by_id[second["id"]]["base_url"] == "http://192.168.3.78:1234/v1"
    # 前端用来区分的字段（catalog() 走 _public，列表接口就是它）
    pub = {r["id"]: r for r in svc.catalog()}
    assert pub[second["id"]]["endpoint_host"] == "192.168.3.78:1234"
    assert pub[first["id"]]["endpoint_host"] == "token.sensenova.cn"
    assert pub[second["id"]]["key_source"] == "global", "条目没带 key → 用全局，要如实显示"
    db.close()


def test_同端点重复新增仍然幂等(tmp_path):
    db, cfg, svc = _stack(tmp_path)
    a = svc.upsert(model="m1", base_url="https://a.example/v1", note="n1")
    b = svc.upsert(model="m1", base_url="https://a.example/v1", note="n1-改")
    assert a["id"] == b["id"] and b["endpoint_conflict"] is False
    assert len(db.list_models()) == 1
    assert b["note"] == "n1-改"
    db.close()


def test_端点不同时同址再存仍幂等(tmp_path):
    db, cfg, svc = _stack(tmp_path)
    svc.upsert(model="m1", base_url="https://a.example/v1")
    svc.upsert(model="m1", base_url="https://b.example/v1")
    again = svc.upsert(model="m1", base_url="https://b.example/v1", note="b")
    assert len(db.list_models()) == 2, "同端点重复保存不能又开一条"
    assert again["note"] == "b"
    db.close()


# ------------------------------------------------------------------ 导入
def test_导入不改写已存在条目的端点与密钥(tmp_path):
    """核心回归：以前重拉会把同名条目的 base_url 改掉（key 还是旧家的 → 401）。"""
    db, cfg, svc = _stack(tmp_path)
    svc.upsert(model="QwQ-32B", provider="openai_compat",
               base_url="https://api.siliconflow.cn/v1", api_key="sk-sf-original",
               note="我手写的备注")
    svc._fetch, svc._fetch_meta = _fake_fetch(["QwQ-32B"])
    res = asyncio.new_event_loop().run_until_complete(
        svc.import_fetched(base_url="https://token.sensenova.cn/v1",
                           api_key="sk-nova-key"))
    entry = db.get_model(make_model_id("openai_compat", "QwQ-32B"))
    assert entry["base_url"] == "https://api.siliconflow.cn/v1", "端点被改写了"
    assert entry["api_key"] == "sk-sf-original", "密钥被改写了"
    assert entry["note"] == "我手写的备注"
    assert res["conflicts"], "同名不同端点应回报为另存的新条目"
    assert "QwQ-32B" in res["added"]
    kept = db.get_model(res["conflicts"][0]["id"])
    assert kept["base_url"] == "https://token.sensenova.cn/v1"
    assert kept["api_key"] == "sk-nova-key", "新条目要带上本次导入用的密钥"
    db.close()


def test_导入只导勾选的并回报实际端点与密钥来源(tmp_path):
    db, cfg, svc = _stack(tmp_path)
    svc._fetch, svc._fetch_meta = _fake_fetch(
        ["deepseek-v4.1-flash", "deepseek-v4-flash", "glm-5.2", "kimi-k3"])
    loop = asyncio.new_event_loop()
    # only：只导入勾选的两条（拉回 99 条时不再一股脑入库）
    res = loop.run_until_complete(svc.import_fetched(only=["glm-5.2", "kimi-k3"]))
    assert sorted(res["added"]) == ["glm-5.2", "kimi-k3"]
    assert res["filtered_from"] == 4 and res["fetched"] == 2 and res["filtered"] == 2
    # q：按关键词过滤
    res2 = loop.run_until_complete(svc.import_fetched(q="deepseek"))
    assert sorted(res2["added"]) == ["deepseek-v4-flash", "deepseek-v4.1-flash"]
    # 端点与密钥来源必须回报（否则"默认 key 的模型也被拉进来了"永远是黑盒）
    assert res2["used_base_url"] == "https://token.sensenova.cn/v1"
    assert res2["key_source"] == "global", "没传 key → 用的是全局密钥，要如实回报"
    assert len(db.list_models()) == 4
    db.close()


def test_导入重复条目保留备注且不新增(tmp_path):
    db, cfg, svc = _stack(tmp_path, base_url="https://a.example/v1")
    svc.upsert(model="m1", base_url="https://a.example/v1", note="用户备注")
    svc._fetch, svc._fetch_meta = _fake_fetch(["m1"])
    res = asyncio.new_event_loop().run_until_complete(svc.import_fetched())
    assert res["added"] == [] and res["refreshed"] == ["m1"]
    assert res["kept_notes"] == 1
    assert db.get_model(make_model_id("openai_compat", "m1"))["note"] == "用户备注"
    db.close()


# ------------------------------------------------------------------ 端点不可达
def test_连不上时给的是网络提示而不是模型不存在():
    class ConnectError(Exception):
        pass

    msg = fetch_error_hint(ConnectError("All connection attempts failed"))
    assert "端点连不上" in msg and "同一网段" in msg
    assert fetch_error_hint(ValueError("bad json")) == ""
