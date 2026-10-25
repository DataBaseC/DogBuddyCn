# DogBuddyCn — 成本账本（0.11.0，schema 16）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""把"钱"变成可查的数字（docs/工业自动化设计.md §4.1）。

为什么先做这个而不是调度算法：4 天 94 次压缩 / 19% 降级 / 缓存命中一度 11.5%，
而系统里只有 token 求和、没有单价、没有逐任务归集 —— 成本不可观测时，
任何"检测成本智能调度"都是拍脑袋。

钉住的口径：
  ① 单价按 `provider:model` → `model` 回退，取不到就是 0（不允许瞎猜）；
  ② 金额 = miss×in + hit×(in-cache) + out×out + images×in，全部**每 1K token**；
  ③ `idem_key` 重复上报**不重复计费**（run_retry / 群组步骤续跑会重放调用）；
  ④ 改价不回算历史（账本行自带 unit_price / amount）；
  ⑤ 改价、生成占位价都要留审计。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import SCHEMA_VERSION, Database  # noqa: E402
from dogbuddy.services.cost_service import CostService  # noqa: E402


@pytest.fixture()
def svc(tmp_path):
    db = Database(str(tmp_path / "cost.db"))
    yield CostService(db), db
    db.close()


# ------------------------------------------------------------------ 建表/迁移
def test_schema_is_16_with_cost_tables(svc):
    _, db = svc
    # 16 = 成本账本（0.11.0）；17 = 路由样本（0.12.0）。加表就要一起挪这个断言。
    assert SCHEMA_VERSION == 18
    for table in ("cost_model", "usage_ledger", "budgets", "model_route_stats",
                  "pipelines", "pipeline_runs", "pipeline_steps"):
        row = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        assert row is not None, f"缺表 {table}"


def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "again.db")
    db1 = Database(path)
    db1.close()
    db2 = Database(path)          # 再开一次不应报错、不应重复建
    assert db2._conn.execute("SELECT count(*) FROM schema_version").fetchone()[0] >= 16
    db2.close()


# ---------------------------------------------------------------------- 单价
def test_price_lookup_falls_back_and_defaults_to_zero(svc):
    c, db = svc
    c.upsert_price({"provider": "sensenova", "model": "nova", "in_price": 0.004,
                    "out_price": 0.012})
    assert c.price_for("sensenova", "nova")["in_price"] == 0.004
    # provider 不一致但 model 同名：仍能按 model 命中（id 就是 model）
    db.upsert_cost_model({"id": "loose", "model": "loose", "in_price": 0.1,
                          "out_price": 0.2, "enabled": 1})
    assert c.price_for("whatever", "loose")["in_price"] == 0.1
    # 完全没配：全 0，且不炸
    zero = c.price_for("nobody", "nothing")
    assert zero["in_price"] == 0 and zero["out_price"] == 0


def test_amount_math_including_cache_and_images(svc):
    c, db = svc
    db.upsert_cost_model({"id": "p:m", "provider": "p", "model": "m",
                          "in_price": 0.01, "out_price": 0.03, "cache_price": 0.005,
                          "enabled": 1})
    # 1000 in（其中 400 命中缓存）+ 200 out = 600*0.01/1000 + 400*0.005/1000 + 200*0.03/1000
    amount, unit = c.amount_for("p", "m", in_tokens=1000, out_tokens=200,
                                cache_hit_tokens=400)
    assert unit == 0.01
    assert amount == pytest.approx(0.006 + 0.002 + 0.006, abs=1e-9)
    # 图片按张计价（unit=image 时 in_price 即每张）
    img_amount, _ = c.amount_for("p", "m", images=3)
    assert img_amount == pytest.approx(0.03, abs=1e-9)


def test_negative_price_is_rejected(svc):
    c, _ = svc
    with pytest.raises(ValueError):
        c.upsert_price({"model": "bad", "in_price": -1})


def test_price_change_keeps_old_rows(svc):
    c, db = svc
    c.upsert_price({"provider": "p", "model": "m", "in_price": 0.01, "out_price": 0.02})
    c.record(provider="p", model="m", in_tokens=1000, out_tokens=0, idem_key="k1")
    c.upsert_price({"provider": "p", "model": "m", "in_price": 99, "out_price": 99})
    c.record(provider="p", model="m", in_tokens=1000, out_tokens=0, idem_key="k2")
    rows = {r["idem_key"]: r["amount"] for r in db.usage_rows(limit=10)}
    assert rows["k1"] == pytest.approx(0.01, abs=1e-9), "改价把历史行改掉了"
    assert rows["k2"] == pytest.approx(99.0, abs=1e-6)


# ---------------------------------------------------------------------- 记账
def test_record_is_idempotent(svc):
    c, db = svc
    db.upsert_cost_model({"id": "p:m", "provider": "p", "model": "m",
                          "in_price": 0.01, "out_price": 0.02, "enabled": 1})
    first = c.record(provider="p", model="m", in_tokens=1000, idem_key="run1:0:1")
    again = c.record(provider="p", model="m", in_tokens=1000, idem_key="run1:0:1")
    assert first["stored"] is True and again["stored"] is False
    assert len(db.usage_rows(limit=10)) == 1, "重复上报被记了两笔"


def test_record_without_idem_key_is_allowed(svc):
    c, db = svc
    assert c.record(provider="p", model="m", in_tokens=10)["stored"] is True
    assert c.record(provider="p", model="m", in_tokens=10)["stored"] is True
    assert len(db.usage_rows(limit=10)) == 2


# ---------------------------------------------------------------------- 汇总
def test_summary_groups_and_totals(svc):
    c, db = svc
    db.upsert_cost_model({"id": "p:m", "provider": "p", "model": "m",
                          "in_price": 0.01, "out_price": 0.02, "enabled": 1})
    c.record(provider="p", model="m", window_id="w1", in_tokens=1000, idem_key="a")
    c.record(provider="p", model="m", window_id="w2", in_tokens=2000, idem_key="b")
    out = c.summary(days=7)
    assert out["total"]["calls"] == 2
    assert out["total"]["amount"] == pytest.approx(0.03, abs=1e-9)
    # 按工位分组：两个桶
    assert {b["bucket"] for b in out["by_window"]} == {"w1", "w2"}
    # 非法 group_by 不能拼进 SQL，退化成按天
    assert c.summary(days=7, group_by="ts; DROP TABLE usage_ledger")["buckets"] is not None


def test_health_reports_zero_priced(svc):
    c, db = svc
    assert c.health()["hint"]                       # 空表时给提示
    db.upsert_cost_model({"id": "free", "model": "free", "in_price": 0, "out_price": 0})
    h = c.health()
    assert h["priced_models"] == 1 and h["zero_priced"] == 1


# ------------------------------------------------------------------ 占位单价
def test_seed_from_models_uses_tiers_and_keeps_existing(svc):
    c, db = svc
    models = [
        {"provider": "p", "model": "cheap", "cost": "low"},
        {"provider": "p", "model": "pricey", "cost": "high"},
        {"provider": "p", "model": "plain"},                 # 无档位 → medium
        {"provider": "p", "model": "pricey", "cost": "low"},  # 已存在 → 跳过
    ]
    res = c.seed_from_models(models)
    assert res["created"] == 3 and res["skipped"] == 1
    cheap = c.price_for("p", "cheap")
    pricey = c.price_for("p", "pricey")
    assert cheap["in_price"] < pricey["in_price"], "档位没有形成贵/便宜排序"
    assert "待校准" in (pricey.get("note") or "")


def test_audit_written_for_price_and_seed(svc):
    c, db = svc
    c.upsert_price({"provider": "p", "model": "m", "in_price": 0.01})
    c.seed_from_models([{"provider": "p", "model": "m2", "cost": "low"}])
    actions = {r[0] for r in db._conn.execute(
        "SELECT action FROM audit_log WHERE action LIKE 'cost:%'")}
    assert {"cost:price", "cost:seed"} <= actions
