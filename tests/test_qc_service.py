# DogBuddyCn — 抽检策略与复核闭环（0.12.2）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""抽检的三个数字都要能"算给人看"：

  ① `plan()` 的抽样数由 **风险 / 上限 / 预算** 三者取最小，且理由要写清是哪一条卡的；
  ② `close()` 用抽样结果外推缺陷率与**漏检比例**（漏检 = 缺陷率 × 未抽比例）；
  ③ 闭环可用：复核结论回流、批次可查、`qc_plan` 能作为流水线步骤直接跑。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.pipeline_service import PipelineService  # noqa: E402
from dogbuddy.services.qc_service import QcService  # noqa: E402
from dogbuddy.web.routes.qc import router as qc_router  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    db = Database(str(tmp_path / "qc.db"))
    qc = QcService(db)
    yield qc, db, tmp_path
    db.close()


def test_plan_math_matches_formula(env):
    qc, db, _ = env
    out = qc.plan(1000, risk_prior=0.02, target_miss=0.05,
                  cost_per_item=0.02, budget=0.5, max_ratio=0.2)
    assert out["by_risk"] == math.ceil(math.log(0.05) / math.log(0.98)) == 149
    assert out["by_ratio"] == 200
    assert out["by_budget"] == 25
    assert out["sample"] == 25, "预算最紧 → 抽 25 件"
    assert "预算只够 25 件" in out["reason"], out["reason"]
    assert out["cost"] == pytest.approx(0.5)


def test_plan_capped_by_ratio(env):
    qc, db, _ = env
    out = qc.plan(100, risk_prior=0.02, cost_per_item=0.02, budget=1000, max_ratio=0.1)
    assert out["sample"] == 10 and out["by_ratio"] == 10
    assert "抽样上限 10%" in out["reason"], out["reason"]


def test_plan_zero_risk_samples_one(env):
    qc, db, _ = env
    out = qc.plan(500, risk_prior=0.0)
    assert out["sample"] == 1
    assert "先验缺陷率为 0" in out["reason"]


def test_plan_budget_below_one_item_still_samples_one(env):
    qc, db, _ = env
    out = qc.plan(100, budget=0.001, cost_per_item=0.02)
    assert out["sample"] == 1, "再穷也要抽 1 件（0 抽样等于放任）"


def test_needs_review_threshold():
    assert QcService.needs_review(0.5, 0.8) is True
    assert QcService.needs_review(0.9, 0.8) is False
    assert QcService.needs_review(0.0) is True


# ---------------------------------------------------------------- 批次闭环
def test_batch_close_estimates_defect_and_miss(env):
    qc, db, _ = env
    row = qc.open_batch(total=100, sampled=20, line="L1")
    assert row["status"] == "open"
    qc.review(row["id"], item_ref="img-1", model_label="ng", confidence=0.4,
              verdict="reject")
    qc.review(row["id"], item_ref="img-2", model_label="ok", confidence=0.95,
              verdict="agree")
    qc.review(row["id"], item_ref="img-3", model_label="ng", confidence=0.3,
              verdict="reject")
    closed = qc.close(row["id"])
    assert closed["status"] == "closed"
    assert closed["defects_found"] == 2
    assert closed["defect_rate"] == pytest.approx(0.1)
    # 漏检 = 缺陷率 × 未抽比例 = 0.1 × (100-20)/100
    assert closed["miss_estimate"] == pytest.approx(0.08)
    acts = [r[0] for r in db._conn.execute(
        "select action from audit_log where action like 'qc:%'")]
    assert "qc:open" in acts and "qc:close" in acts


def test_close_with_explicit_defects(env):
    qc, db, _ = env
    row = qc.open_batch(total=50, sampled=10)
    closed = qc.close(row["id"], defects_found=5)
    assert closed["defect_rate"] == pytest.approx(0.5)
    assert closed["miss_estimate"] == pytest.approx(0.5 * 40 / 50)


def test_close_unknown_batch_raises(env):
    qc, db, _ = env
    with pytest.raises(ValueError):
        qc.close("qc-nope")


def test_health_reports_avg_rate(env):
    qc, db, _ = env
    a = qc.open_batch(total=100, sampled=20)
    qc.close(a["id"], defects_found=2)
    b = qc.open_batch(total=100, sampled=20)
    qc.close(b["id"], defects_found=4)
    h = qc.health()
    assert h["batches"] == 2 and h["closed"] == 2
    assert h["avg_defect_rate"] == pytest.approx(0.15, abs=1e-6)


# ------------------------------------------------------------ 流水线步骤
def test_qc_plan_step_in_pipeline(env, tmp_path):
    qc, db, _ = env
    svc = PipelineService(db, qc=qc, workspace_root=str(tmp_path / "ws"))
    svc.upsert({"id": "pl-qc", "steps": [
        {"id": "plan", "action": "qc_plan",
         "params": {"batch_size": 1000, "budget": 0.5, "cost_per_item": 0.02,
                    "line": "L1", "open_batch": True}},
        {"id": "report", "action": "write_file",
         "params": {"path": "qc/report.md", "content": "ok"}, "needs": ["plan"]},
    ]})
    run = svc.start("pl-qc")
    import asyncio
    st = asyncio.new_event_loop().run_until_complete(svc.tick(run["id"]))
    assert st["run"]["status"] == "done"
    plan = next(s for s in st["steps"] if s["step_id"] == "plan")["output"]
    assert plan["sample"] == 25
    assert plan.get("batch_id", "").startswith("qc-")
    assert len(qc.batches()) == 1, "open_batch=true 才真的落批次"


def test_qc_plan_step_without_service_fails(tmp_path):
    db = Database(str(tmp_path / "x.db"))
    svc = PipelineService(db, workspace_root=str(tmp_path / "ws"))
    svc.upsert({"id": "p", "steps": [{"id": "s", "action": "qc_plan",
                                      "params": {"batch_size": 10}}]})
    run = svc.start("p")
    import asyncio
    st = asyncio.new_event_loop().run_until_complete(svc.tick(run["id"]))
    assert st["run"]["status"] == "failed"
    assert "未装配抽检服务" in next(s for s in st["steps"])["error"]
    db.close()


# -------------------------------------------------------------------- 接口
@pytest.fixture()
def client(env):
    qc, db, _ = env
    app = FastAPI()
    app.include_router(qc_router(SimpleNamespace(qc=qc), []), prefix="/api")
    return TestClient(app), qc


def test_qc_routes(client):
    c, qc = client
    r = c.post("/api/qc/plan", json={"batch_size": 1000, "budget": 0.5,
                                     "cost_per_item": 0.02})
    assert r.status_code == 200 and r.json()["sample"] == 25
    assert "batch_id" not in r.json(), "默认不落库"
    r2 = c.post("/api/qc/plan", json={"batch_size": 200, "open_batch": True, "line": "L2"})
    bid = r2.json()["batch_id"]
    assert c.get("/api/qc/batches").json()["batches"][0]["id"] == bid
    assert c.post(f"/api/qc/batches/{bid}/review",
                  json={"item_ref": "i-1", "verdict": "reject", "confidence": 0.2}
                  ).status_code == 200
    closed = c.post(f"/api/qc/batches/{bid}/close", json={}).json()
    assert closed["status"] == "closed" and closed["defects_found"] == 1
    assert c.get(f"/api/qc/batches/{bid}").json()["reviews"][0]["item_ref"] == "i-1"
    assert c.get("/api/qc/batches/qc-nope").status_code == 404
    assert c.post("/api/qc/batches", json={"total": 0}).status_code == 400
