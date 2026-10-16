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
    assert "同名同端点" in msg and "备注" in msg, f"错误信息要可行动：{msg}"
    rows = db.list_models()
    assert len(rows) == 1, f"不该多出一条：{[r['id'] for r in rows]}"
    kept = db.get_model(first["id"])
    assert kept["note"] == "第一条：原始备注", "原条目备注被覆盖了"
    assert kept["context_window"] == 1048576
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
