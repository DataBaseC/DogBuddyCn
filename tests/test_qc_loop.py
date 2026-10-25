# DogBuddyCn — 数据闭环（0.13.1）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""0.12.2 只把"复核结论"记下来，这一版才是**闭环**：

    抽样 ─> 复核 ─驳回─> 难例池 ─┐
                  │              ├─> 漂移告警（产线×模型）
                  └─> 路由 p_fail ┘
    ↑                              │
    └── 下一批的 risk_prior ←── 真实漏检 ┘

钉住四件事：
  ① 驳回**自动**进难例池（不用人手再点一次）；agree 不进；
  ② 复核带 `model` 时把结论回灌路由（`p_fail` 会升）；
  ③ `plan()` 默认用**最近几批真实漏检**当先验，没有历史才退回 2%；
  ④ 漂移按「产线 × 模型」分组、超阈值告警，且**一小时内只报警一次**（别刷屏）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.qc_service import QcService  # noqa: E402
from dogbuddy.services.routing_service import RoutingService  # noqa: E402
from dogbuddy.web.routes.qc import router as qc_router  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    import dogbuddy.services.qc_service as mod
    mod._ALERTED.clear()          # 去重状态是进程级的，测试之间要清
    db = Database(str(tmp_path / "loop.db"))
    routing = RoutingService(db)
    qc = QcService(db, routing=routing)
    yield qc, routing, db, tmp_path
    db.close()


# ------------------------------------------------------------------ 难例池
def test_reject_goes_to_hard_samples(env):
    qc, routing, db, _ = env
    b = qc.open_batch(total=100, sampled=10, line="L1", task_class="detect")
    qc.review(b["id"], item_ref="img-1", model_label="ng", model="cv-a",
              confidence=0.3, verdict="reject")
    qc.review(b["id"], item_ref="img-2", model_label="ok", model="cv-a",
              confidence=0.95, verdict="agree")
    rows = qc.hard_samples()
    assert [r["item_ref"] for r in rows] == ["img-1"], "只有 reject 进难例池"
    sample = rows[0]
    assert sample["line"] == "L1" and sample["task_class"] == "detect"
    assert sample["model"] == "cv-a" and sample["verdict"] == "reject"
    assert db.count_hard_samples() == 1


def test_hard_samples_can_filter(env):
    qc, routing, db, _ = env
    a = qc.open_batch(total=10, sampled=2, line="L1")
    b = qc.open_batch(total=10, sampled=2, line="L2")
    qc.review(a["id"], item_ref="a1", verdict="reject")
    qc.review(b["id"], item_ref="b1", verdict="reject")
    assert {r["item_ref"] for r in qc.hard_samples(line="L1")} == {"a1"}
    assert {r["item_ref"] for r in qc.hard_samples(batch_id=b["id"])} == {"b1"}


# -------------------------------------------------------------- 路由回灌
def test_review_feeds_routing_p_fail(env):
    qc, routing, db, _ = env
    b = qc.open_batch(total=100, sampled=10, line="L1", task_class="detect")
    before = routing.p_fail("detect", "cv-a")
    for i in range(4):
        qc.review(b["id"], item_ref=f"x{i}", model="cv-a", verdict="reject")
    after = routing.p_fail("detect", "cv-a")
    assert routing.samples("detect", "cv-a") == 4
    assert after > before, f"驳回应该抬高 p_fail：{before} -> {after}"
    # 另一个模型不受影响
    assert routing.samples("detect", "cv-b") == 0


def test_review_without_model_does_not_touch_routing(env):
    qc, routing, db, _ = env
    b = qc.open_batch(total=10, sampled=2)
    qc.review(b["id"], item_ref="x", verdict="reject")     # 没给 model
    assert routing.samples("qc", "") == 0
    assert db.count_hard_samples() == 1, "难例照样进池"


# ---------------------------------------------------------- 抽检自适应
def test_plan_uses_recent_miss_as_prior(env):
    qc, routing, db, _ = env
    b = qc.open_batch(total=100, sampled=20, line="L1")
    qc.close(b["id"], defects_found=4)      # rate 0.2 → miss = 0.2*80/100 = 0.16
    out = qc.plan(1000, cost_per_item=0.02, budget=1000, max_ratio=1.0)
    assert out["risk_used"] == pytest.approx(0.16, abs=1e-6)
    assert "历史漏检" in out["risk_source"]
    # 显式关掉自适应时用传入的先验
    out2 = qc.plan(1000, risk_prior=0.02, auto_risk=False,
                   cost_per_item=0.02, budget=1000, max_ratio=1.0)
    assert out2["risk_used"] == pytest.approx(0.02)
    assert "默认先验" in out2["risk_source"]


def test_plan_without_history_falls_back(env):
    qc, routing, db, _ = env
    out = qc.plan(100)
    assert "默认先验" in out["risk_source"] and out["risk_used"] == pytest.approx(0.02)


# -------------------------------------------------------------- 漂移告警
def _fill(qc, line, model, total, rejects):
    b = qc.open_batch(total=total * 10, sampled=total, line=line)
    for i in range(total):
        qc.review(b["id"], item_ref=f"{line}-{i}", model=model,
                  verdict="reject" if i < rejects else "agree")
    return b


def test_drift_alerts_on_high_reject_rate(env):
    qc, routing, db, _ = env
    _fill(qc, "L1", "cv-a", 12, 7)          # 58% 驳回
    _fill(qc, "L2", "cv-a", 12, 1)          # 8% 驳回
    res = qc.drift(min_samples=10, threshold=0.3)
    keys = {(a["line"], a["model"]) for a in res["alerts"]}
    assert ("L1", "cv-a") in keys
    assert ("L2", "cv-a") not in keys
    alert = next(a for a in res["alerts"] if a["line"] == "L1")
    assert alert["samples"] == 12 and alert["reject_rate"] > 0.5
    acts = [r[0] for r in db._conn.execute(
        "select action from audit_log where action='qc:drift'")]
    assert len(acts) == 1, "只给超阈值的那个写审计"


def test_drift_is_deduped_within_cooldown(env):
    qc, routing, db, _ = env
    _fill(qc, "L1", "cv-a", 12, 7)
    first = qc.drift(min_samples=10)
    second = qc.drift(min_samples=10)
    assert first["new_alerts"] and not second["new_alerts"], "一小时内别重复告警"
    assert second["alerts"], "但当前状态仍然要能查到"
    acts = [r[0] for r in db._conn.execute(
        "select action from audit_log where action='qc:drift'")]
    assert len(acts) == 1


def test_drift_ignores_small_samples(env):
    qc, routing, db, _ = env
    _fill(qc, "L1", "cv-a", 5, 5)           # 5 条全驳回，但样本不足
    assert qc.drift(min_samples=10)["alerts"] == []


def test_close_triggers_drift_check(env):
    qc, routing, db, _ = env
    b = qc.open_batch(total=100, sampled=12, line="L1")
    for i in range(12):
        qc.review(b["id"], item_ref=f"i{i}", model="cv-a",
                  verdict="reject" if i < 7 else "agree")
    qc.close(b["id"])
    acts = [r[0] for r in db._conn.execute(
        "select action from audit_log where action='qc:drift'")]
    assert acts, "收批时就应该把漂移冒出来，而不是等看板被打开"


def test_health_reports_loop_outputs(env):
    qc, routing, db, _ = env
    _fill(qc, "L1", "cv-a", 12, 7)
    qc.drift(min_samples=10)                 # 第一次：写一条告警审计
    h = qc.health()
    assert h["hard_samples"] == 7
    assert h["drift_alerts"] == 1
    # health 只看不写：不该再多出审计（否则每刷一次 healthz 就刷一条告警）
    acts = [r[0] for r in db._conn.execute(
        "select action from audit_log where action='qc:drift'")]
    assert len(acts) == 1


# ------------------------------------------------------------------ 接口
@pytest.fixture()
def client(env):
    qc, routing, db, _ = env
    app = FastAPI()
    app.include_router(qc_router(SimpleNamespace(qc=qc), []), prefix="/api")
    return TestClient(app), qc


def test_qc_loop_routes(client):
    c, qc = client
    b = qc.open_batch(total=100, sampled=12, line="L1")
    for i in range(12):
        c.post(f"/api/qc/batches/{b['id']}/review",
               json={"item_ref": f"i{i}", "model": "cv-a",
                     "verdict": "reject" if i < 7 else "agree"})
    hs = c.get("/api/qc/hard-samples?line=L1").json()
    assert hs["count"] == 7 and hs["samples"][0]["model"] == "cv-a"
    drift = c.get("/api/qc/drift?min_samples=10&threshold=0.3").json()
    assert drift["alerts"] and drift["alerts"][0]["line"] == "L1"
    assert c.get("/api/qc/hard-samples?limit=1").json()["count"] == 1
