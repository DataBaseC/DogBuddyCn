# DogBuddyCn — 新增模型**不覆盖同名**回归（v0.10.23）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测（第二次报告）："创建新模型还是会覆盖同名模型"。

0.10.21 只修了"同名但**不同**端点"（另存 `...@host`）。**同名同端点**仍然走
`upsert` 的更新语义 → 第二条把第一条的备注/密钥/参数悄悄盖掉。生产复现（修复前）：

    create#1 -> 200 openai_compat___lead_tmp_dup_test  note='第一条：原始备注'
    create#2 -> 200 openai_compat___lead_tmp_dup_test  note='第二条：新备注'
    目录里同名条数: 1        ← 第一条被吃掉了，且无任何提示

现在：同名同端点 → `ModelExistsError`（路由 409 + 人话）；同名不同端点 → 另存；
改配置请走 `PATCH /models/{id}`。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.model_service import (ModelExistsError, ModelService,  # noqa: E402
                                             make_model_id)


def _svc(tmp_path):
    db = Database(str(tmp_path / "m.db"))
    cfg = AppConfig(paths=PathsConfig(), llm=LLMConfig())
    return db, ModelService(db, cfg)


def test_同名同端点新增被拒绝且原条目一字未动(tmp_path):
    db, svc = _svc(tmp_path)
    first = svc.create(model="deepseek-v4.1-flash", provider="openai_compat",
                       base_url="https://token.sensenova.cn/v1",
                       note="第一条：原始备注", context_window=1048576)
    with pytest.raises(ModelExistsError) as ei:
        svc.create(model="deepseek-v4.1-flash", provider="openai_compat",
                   base_url="https://token.sensenova.cn/v1", note="第二条：新备注")
    msg = str(ei.value)
    assert "完全同身份" in msg and "备注" in msg and "另一把 key" in msg, f"错误信息要可行动：{msg}"
    rows = db.list_models()
    assert len(rows) == 1, f"不该多出一条：{[r['id'] for r in rows]}"
    kept = db.get_model(first["id"])
    assert kept["note"] == "第一条：原始备注", "原条目备注被覆盖了"
    assert kept["context_window"] == 1048576
    db.close()


def test_同名同端点不同key各存一条(tmp_path):
    """owner 的真正诉求：**同一个服务商、同一个模型、两把 key**，要能各存一条。"""
    db, svc = _svc(tmp_path)
    a = svc.create(model="deepseek-v4.1-flash", provider="openai_compat",
                   base_url="https://token.sensenova.cn/v1", api_key="sk-account-A-1111",
                   note="账号 A")
    b = svc.create(model="deepseek-v4.1-flash", provider="openai_compat",
                   base_url="https://token.sensenova.cn/v1", api_key="sk-account-B-2222",
                   note="账号 B")
    assert a["id"] != b["id"], "同端点不同 key 必须是两条"
    assert b["id"].endswith("-k2222"), f"新 id 要能一眼区分密钥：{b['id']}"
    assert a["id"] == make_model_id("openai_compat", "deepseek-v4.1-flash")
    rows = db.list_models()
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id[a["id"]]["api_key"] == "sk-account-A-1111", "A 的 key 不能被改"
    assert by_id[a["id"]]["note"] == "账号 A"
    assert by_id[b["id"]]["api_key"] == "sk-account-B-2222"
    # 第三把 key 再存一条 → 三条共存
    c = svc.create(model="deepseek-v4.1-flash", provider="openai_compat",
                   base_url="https://token.sensenova.cn/v1", api_key="sk-account-C-3333")
    assert len(db.list_models()) == 3
    assert len({a["id"], b["id"], c["id"]}) == 3
    db.close()


def test_同名同端点同一把key才拒绝(tmp_path):
    db, svc = _svc(tmp_path)
    svc.create(model="m-key", provider="openai_compat",
               base_url="https://a.example/v1", api_key="sk-same-9999", note="第一次")
    with pytest.raises(ModelExistsError) as ei:
        svc.create(model="m-key", provider="openai_compat",
                   base_url="https://a.example/v1", api_key="sk-same-9999", note="第二次")
    assert "完全同身份" in str(ei.value)
    rows = db.list_models()
    assert len(rows) == 1 and rows[0]["note"] == "第一次", "同身份不许覆盖"
    db.close()


def test_用全局key与自带key也算不同身份(tmp_path):
    db, svc = _svc(tmp_path)
    a = svc.create(model="m-g", provider="openai_compat", base_url="https://a.example/v1",
                   api_key="sk-own-4444")
    b = svc.create(model="m-g", provider="openai_compat", base_url="https://a.example/v1")
    assert a["id"] != b["id"] and b["id"].endswith("-kglobal")
    assert len(db.list_models()) == 2
    db.close()


def test_按身份收编不造重复条目(tmp_path):
    """adopt_current 要按"名+端点+密钥"找，不能因为 id 带了后缀就再造一条。"""
    db, svc = _svc(tmp_path)
    created = svc.create(model="m-adopt", provider="openai_compat",
                         base_url="https://a.example/v1", api_key="sk-adopt-7777")
    assert created["id"].endswith("m-adopt"), created["id"]
    svc.app.llm.model = "m-adopt"
    svc.app.llm.base_url = "https://a.example/v1"
    svc.app.llm.api_key = "sk-adopt-7777"
    again = svc.adopt_current()
    assert again["id"] == created["id"], "同身份收编必须命中已有条目（不能造重复）"
    assert len(db.list_models()) == 1
    db.close()


def test_同名不同端点仍然另存一条(tmp_path):
    db, svc = _svc(tmp_path)
    a = svc.create(model="m1", provider="openai_compat", base_url="https://a.example/v1",
                   note="A 家")
    b = svc.create(model="m1", provider="openai_compat", base_url="https://b.example/v1",
                   note="B 家")
    assert a["id"] != b["id"] and b["endpoint_conflict"] is True
    assert len(db.list_models()) == 2
    assert db.get_model(a["id"])["note"] == "A 家"
    assert db.get_model(b["id"])["note"] == "B 家"
    db.close()


def test_改配置走PATCH不受影响(tmp_path):
    """新增只负责新增；改名/改备注/改端点仍走更新路径。"""
    db, svc = _svc(tmp_path)
    a = svc.create(model="m2", provider="openai_compat", base_url="https://a.example/v1",
                   note="原备注")
    svc.update(a["id"], note="改过的备注")
    assert db.get_model(a["id"])["note"] == "改过的备注"
    # 用 upsert（低层写入者）仍然可以更新 —— import/adopt 走的就是它
    svc.upsert(id=a["id"], model="m2", base_url="https://a.example/v1", note="再改")
    assert db.get_model(a["id"])["note"] == "再改"
    db.close()


def test_先删再增可以重建(tmp_path):
    db, svc = _svc(tmp_path)
    a = svc.create(model="m3", provider="openai_compat", base_url="https://a.example/v1")
    svc.remove(a["id"])
    again = svc.create(model="m3", provider="openai_compat", base_url="https://a.example/v1",
                       note="重建的")
    assert again["id"] == a["id"] and len(db.list_models()) == 1
    assert db.get_model(again["id"])["note"] == "重建的"
    db.close()


def test_缺模型名仍是普通ValueError(tmp_path):
    db, svc = _svc(tmp_path)
    with pytest.raises(ValueError) as ei:
        svc.create(model="   ")
    assert not isinstance(ei.value, ModelExistsError), "空名字不是冲突，路由该回 400"
    db.close()
