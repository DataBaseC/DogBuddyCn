# DogBuddyCn — 运营回合（0.14.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""运营回合钉住四件事：

  ① **不猜指标**：读不到指标就 `unknown` 并停下判断，不拿 0 当数据；
  ② **判定要防抖**：变化小于 1% 不算成功（否则噪声会被当成"有效动作"）；
  ③ **变体隔离**：keep 继续放大同一实验，adjust/stop 判负并换新变体；
  ④ **不偷花钱**：预算用尽时回合直接 `blocked`，不启动流水线；没有执行流水线
     时只记计划，不假装跑过。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.cost_service import CostService  # noqa: E402
from dogbuddy.services.operations_service import OperationsService  # noqa: E402
from dogbuddy.services.pipeline_service import PipelineService  # noqa: E402
from dogbuddy.services.sensor_hub import SensorHub  # noqa: E402
from dogbuddy.web.routes.ops import router as ops_router  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture()
def env(tmp_path):
    db = Database(str(tmp_path / "ops.db"))
    cost = CostService(db)
    metrics = SensorHub(db)
    pipelines = PipelineService(db, workspace_root=str(tmp_path / "ws"))
    pipelines.upsert({"id": "do", "steps": [{"id": "s", "action": "noop"}]})
    ops = OperationsService(db, pipelines=pipelines, cost=cost, metrics=metrics)
    yield ops, pipelines, metrics, cost, db, tmp_path
    db.close()


def _goal(ops, **kw):
    body = {"id": "g1", "name": "站点增长", "metric_name": "site.visits",
            "direction": "up", "target": 130, "pipeline_id": "do"}
    body.update(kw)
    return ops.upsert_goal(body)


def _round(ops, pipelines, metrics=None, after=None):
    """开一轮 → 跑流水线 → **等指标变化** → 收尾。

    真实世界是"动作生效之后指标才动"，所以 after 必须在流水线跑完后才推，
    否则 before 与 after 取到同一个值（测试一开始就踩了这个坑）。
    """
    res = ops.start_round("g1")
    run_id = (res["journal"] or {}).get("pipeline_run_id") or ""
    if run_id:
        _run(pipelines.tick(run_id))
        if metrics is not None and after is not None:
            metrics.push("site.visits", after)
        ops.tick()
    return res


# ------------------------------------------------------------------ 目标
def test_goal_validation(env):
    ops, pipelines, metrics, cost, db, _ = env
    for bad in ({"name": ""}, {"name": "x", "direction": "sideways"},
                {"name": "x", "cadence": "hourly"},
                {"name": "x", "pipeline_id": "nope"}):
        with pytest.raises(ValueError):
            ops.upsert_goal({**{"id": "b"}, **bad})
    assert ops.goals() == []


def test_goal_crud(env):
    ops, pipelines, metrics, cost, db, _ = env
    g = _goal(ops)
    assert g["metric_name"] == "site.visits" and float(g["target"]) == 130
    assert [x["id"] for x in ops.goals()] == ["g1"]
    assert ops.delete_goal("g1") is True
    with pytest.raises(ValueError):
        ops.goal("g1")


# ------------------------------------------------------------------ 计划
def test_plan_without_metric_is_honest(env):
    ops, pipelines, metrics, cost, db, _ = env
    _goal(ops, metric_name="")
    plan = ops.plan_round("g1")
    assert "还没有定义可度量指标" in plan["hypothesis"]
    assert "指标名" in plan["action"]
    assert plan["metric_before"] is None


def test_plan_marks_missing_metric_data(env):
    ops, pipelines, metrics, cost, db, _ = env
    _goal(ops)
    plan = ops.plan_round("g1")
    assert plan["metric_before"] is None
    assert "目前没有上报" in plan["hypothesis"]
    assert "先让边缘" in plan["action"]


def test_plan_switches_variant_after_failure(env):
    ops, pipelines, metrics, cost, db, _ = env
    _goal(ops)
    metrics.push("site.visits", 100)
    _round(ops, pipelines, metrics, after=100)   # 100 → 100：噪声范围 → adjust
    plan = ops.plan_round("g1")
    assert plan["round_no"] == 2
    assert "换一个变体" in plan["action"]


# ------------------------------------------------------------------ 回合
def test_round_adjust_when_no_change(env):
    ops, pipelines, metrics, cost, db, _ = env
    _goal(ops)
    metrics.push("site.visits", 100)
    res = _round(ops, pipelines, metrics, after=100)
    j = ops.journal(limit=1)[0]
    assert j["status"] == "done" and j["verdict"] == "adjust"
    assert j["delta"] == pytest.approx(0)
    exp = ops.experiments()[0]
    assert exp["status"] == "lost"
    assert res["started"] is True


def test_round_keep_and_target_reached(env):
    ops, pipelines, metrics, cost, db, _ = env
    _goal(ops)
    metrics.push("site.visits", 100)
    _round(ops, pipelines, metrics, after=100)   # 100 → 100 adjust
    _round(ops, pipelines, metrics, after=110)   # +10 → keep，未达标
    exps = ops.experiments()
    assert exps[0]["status"] == "running", exps    # keep 但没达标：继续放大同一变体
    keep = ops.journal(limit=1)[0]
    assert keep["verdict"] == "keep" and keep["delta"] == pytest.approx(10)
    _round(ops, pipelines, metrics, after=135)   # 达标 → 实验 won
    last = ops.journal(limit=1)[0]
    assert last["verdict"] == "keep"
    won = [e for e in ops.experiments() if e["status"] == "won"]
    assert won, ops.experiments()


def test_round_unknown_when_metric_missing(env):
    ops, pipelines, metrics, cost, db, _ = env
    _goal(ops, metric_name="never.reported")
    res = ops.start_round("g1")
    assert res["started"] is True
    _run(pipelines.tick(res["journal"]["pipeline_run_id"]))
    ops.tick()
    j = ops.journal(limit=1)[0]
    assert j["verdict"] == "unknown"
    assert "无法判定" in (j["notes"] or "")


def test_round_blocked_by_budget(env):
    ops, pipelines, metrics, cost, db, _ = env
    _goal(ops)
    cost.upsert_budget({"scope_type": "global", "limit_amount": 1,
                        "strategy": "pause"})
    db.record_usage({"ts": CostService._period_start("day"), "amount": 2.0,
                     "in_tokens": 1, "out_tokens": 1})
    res = ops.start_round("g1")
    j = res["journal"]
    assert res["started"] is False and j["status"] == "blocked"
    assert "预算拦下" in (j["notes"] or "")
    assert ops.health()["blocked"] == 1


def test_round_without_pipeline_is_planned_only(env):
    ops, pipelines, metrics, cost, db, _ = env
    _goal(ops, pipeline_id="")
    res = ops.start_round("g1")
    assert res["started"] is False
    assert res["journal"]["status"] == "planned"
    assert "没有绑定执行流水线" in (res["journal"]["notes"] or "")


def test_round_payload_rendered_into_pipeline(env):
    """运营回合的动作要能塞进共享流水线 —— 否则每个目标都得建一条线。"""
    ops, pipelines, metrics, cost, db, tmp_path = env
    pipelines.upsert({"id": "write", "steps": [
        {"id": "w", "action": "write_file",
         "params": {"path": "rounds/{round_no}.txt",
                    "content": "{goal_name}：{action}"}}]})
    _goal(ops, pipeline_id="write")
    metrics.push("site.visits", 50)
    res = ops.start_round("g1")
    _run(pipelines.tick(res["journal"]["pipeline_run_id"]))
    ops.tick()
    out = (tmp_path / "ws" / "rounds" / "1.txt")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.startswith("站点增长：")
    assert "针对 site.visits" in text


def test_manual_close_and_validation(env):
    ops, pipelines, metrics, cost, db, _ = env
    _goal(ops, pipeline_id="")
    res = ops.start_round("g1", execute=False)
    jid = res["journal"]["id"]
    closed = ops.close_round(jid, observed="手测：改了标题", verdict="keep",
                             metric_after=101)
    assert closed["status"] == "done" and closed["verdict"] == "keep"
    assert "人工观察" in (closed["notes"] or "")
    with pytest.raises(ValueError):
        ops.close_round(jid, verdict="maybe")
    with pytest.raises(ValueError):
        ops.close_round("or-nope")


def test_tick_marks_failed_pipeline(env):
    ops, pipelines, metrics, cost, db, _ = env
    pipelines.upsert({"id": "boom", "steps": [
        {"id": "s", "action": "qc_plan", "params": {"batch_size": 10}}]})
    # 没有 qc 服务 → 步骤失败 → 回合失败、实验判负
    _goal(ops, pipeline_id="boom")
    res = ops.start_round("g1")
    _run(pipelines.tick(res["journal"]["pipeline_run_id"]))
    updated = ops.tick()
    j = updated[0] if updated else ops.journal(limit=1)[0]
    assert j["status"] == "failed"
    assert ops.experiments()[0]["status"] == "lost"


def test_health_counts(env):
    ops, pipelines, metrics, cost, db, _ = env
    _goal(ops)
    metrics.push("site.visits", 100)
    _round(ops, pipelines, metrics, after=100)
    h = ops.health()
    assert h["goals"] == 1 and h["enabled"] == 1
    assert h["rounds"] == 1 and h["verdicts"]["adjust"] == 1


# ------------------------------------------------------------------ 接口
@pytest.fixture()
def client(env):
    ops, pipelines, metrics, cost, db, _ = env
    app = FastAPI()
    app.include_router(ops_router(SimpleNamespace(ops=ops), []), prefix="/api")
    return TestClient(app), ops


def test_console_operations_wired():
    root = Path(__file__).resolve().parents[1] / "dogbuddy/web/static"
    idx = (root / "index.html").read_text(encoding="utf-8")
    app = (root / "js/app.js").read_text(encoding="utf-8")
    api = (root / "js/api.js").read_text(encoding="utf-8")
    view = (root / "js/views-operations.js").read_text(encoding="utf-8")
    order = (Path(__file__).resolve().parents[1] / "tools/bundle-order.json"
             ).read_text(encoding="utf-8")
    assert 'data-view="operations"' in idx and 'id="view-operations"' in idx
    assert "case 'operations'" in app and "operations: '运营'" in app
    assert "/api/ops/goals" in api and "/api/ops/journal" in api
    assert "startRound" in api and "startRound" in view
    assert "计划" in view and "跑一轮" in view
    assert "views-operations.js" in order


def test_ops_routes(client):
    c, ops = client
    assert c.get("/api/ops/goals").json()["goals"] == []
    body = {"name": "站点增长", "metric_name": "site.visits",
            "direction": "up", "target": 130, "pipeline_id": "do"}
    assert c.put("/api/ops/goals/g1", json=body).status_code == 200
    assert c.get("/api/ops/goals/g1").json()["name"] == "站点增长"
    plan = c.post("/api/ops/goals/g1/plan").json()
    assert plan["round_no"] == 1
    r = c.post("/api/ops/goals/g1/rounds", json={"execute": False})
    assert r.status_code == 200
    assert r.json()["journal"]["status"] == "planned"
    assert len(c.get("/api/ops/journal").json()["journal"]) == 1
    assert c.get("/api/ops/experiments").json()["experiments"]
    assert c.post("/api/ops/goals/nope/plan").status_code == 400
    assert c.get("/api/ops/goals/nope").status_code == 404
    assert c.post("/api/ops/journal/or-nope/close", json={}).status_code == 404
