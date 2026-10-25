# DogBuddyCn — 模型分级路由（0.12.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""便宜的先用、翻车再升。钉住四条不变量：

  ① `p_fail` 走 Beta 平滑（没样本时是 0.15，不是 0）；
  ② **同端点**：跨端点的便宜模型绝不当降档目标（换供应商 = 401/404）；
  ③ **冷启动只降不升**：样本 < MIN_SAMPLES 时不做升级判断，
     但预算 warn 时**可以**提前降档（这是省钱的主动动作）；
  ④ loop 只在第一轮决策一次，切了模型写审计、并按结果记样本。
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

from dogbuddy.agents.core.loop import AgentLoop  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.llm.provider import LLMResponse  # noqa: E402
from dogbuddy.services.cost_service import CostService  # noqa: E402
from dogbuddy.services.routing_service import RoutingService  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402
from dogbuddy.web.routes.costs import router as costs_router  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
EP = "https://api.same.example.com/v1"


@pytest.fixture()
def svc(tmp_path):
    db = Database(str(tmp_path / "r.db"))
    cost = CostService(db)
    # 同端点：strong(贵) / cheap(便宜)；另一个端点上还有个更便宜的，必须被忽略
    for mid, price in (("m-strong", 0.08), ("m-cheap", 0.004), ("m-other", 0.0001)):
        db.upsert_cost_model({"id": f"p:{mid}", "provider": "p", "model": mid,
                              "in_price": price / 4, "out_price": price, "enabled": 1})
    db.upsert_model({"id": "p_m-strong", "provider": "p", "model": "m-strong",
                     "base_url": EP, "enabled": 1})
    db.upsert_model({"id": "p_m-cheap", "provider": "p", "model": "m-cheap",
                     "base_url": EP, "enabled": 1})
    db.upsert_model({"id": "x_m-other", "provider": "x", "model": "m-other",
                     "base_url": "https://api.other.example.com/v1", "enabled": 1})
    routing = RoutingService(db, cost)
    yield routing, cost, db
    db.close()


# ------------------------------------------------------------- p_fail / 样本
def test_p_fail_is_smoothed(svc):
    routing, cost, db = svc
    assert routing.p_fail("t", "m-cheap") == pytest.approx(0.15, abs=1e-6)
    routing.record("t", "m-cheap", ok=False)
    routing.record("t", "m-cheap", ok=False)
    pf = routing.p_fail("t", "m-cheap")
    # (2 + 0.15*8) / (2 + 8) = 3.2/10
    assert pf == pytest.approx(0.32, abs=1e-6)
    assert routing.samples("t", "m-cheap") == 2


def test_stats_expose_p_fail(svc):
    routing, cost, db = svc
    routing.record("admin_chat", "m-cheap", ok=True)
    row = next(r for r in routing.stats() if r["model"] == "m-cheap")
    assert row["attempts"] == 1 and row["p_fail"] > 0


# ---------------------------------------------------------------- 决策
def test_cold_start_stays_when_budget_ok(svc):
    routing, cost, db = svc
    d = routing.decide("m-strong", task_class="t", budget={"level": "ok"})
    assert d["action"] == "stay", d
    assert "样本" in d["reason"] or "维持" in d["reason"]


def test_budget_warn_downroutes_even_in_cold_start(svc):
    routing, cost, db = svc
    d = routing.decide("m-strong", task_class="t",
                       budget={"level": "warn", "ratio": 0.85})
    assert d["action"] == "downroute"
    assert d["model"] == "m-cheap", "必须挑同端点最便宜的那档"
    assert "85%" in d["reason"]


def test_downroute_when_enough_samples_and_low_fail(svc):
    routing, cost, db = svc
    for _ in range(120):
        routing.record("t", "m-cheap", ok=True)
    d = routing.decide("m-strong", task_class="t", budget={"level": "ok"})
    assert d["action"] == "downroute" and d["model"] == "m-cheap", d


def test_cheap_with_high_fail_blocks_downroute(svc):
    routing, cost, db = svc
    for _ in range(20):
        routing.record("t", "m-cheap", ok=False)
    d = routing.decide("m-strong", task_class="t", budget={"level": "warn", "ratio": 0.9})
    assert d["action"] == "stay"
    assert "偏高" in d["reason"], d


def test_never_picks_other_endpoint(svc):
    routing, cost, db = svc
    # 把同端点的便宜档停用 → 只剩跨端点的极便宜档，必须不动
    db.upsert_cost_model({"id": "p:m-cheap", "model": "m-cheap", "enabled": 0,
                          "in_price": 0.001, "out_price": 0.004})
    d = routing.decide("m-strong", task_class="t", budget={"level": "warn", "ratio": 0.9})
    assert d["action"] == "stay", d
    assert d["model"] != "m-other"


def test_uproute_when_current_fails_a_lot(svc):
    routing, cost, db = svc
    for _ in range(120):
        routing.record("t", "m-cheap", ok=False)
    d = routing.decide("m-cheap", task_class="t", budget={"level": "ok"})
    assert d["action"] == "uproute" and d["model"] == "m-strong", d


def test_no_upgrade_in_cold_start(svc):
    routing, cost, db = svc
    routing.record("t", "m-cheap", ok=False)      # 只有 1 个样本
    d = routing.decide("m-cheap", task_class="t", budget={"level": "ok"})
    assert d["action"] == "stay", "样本不足时不许升级（否则一次失败就上最贵档）"


# ------------------------------------------------------------------- loop
class _Session:
    def __init__(self):
        self.window = SimpleNamespace(id="w-route")
        self.appended = []

    def append(self, role, content, meta=None, **kw):
        self.appended.append((role, content))

    def build_context(self, builder=None):
        return [{"role": "user", "content": "hi"}]


class _Registry:
    def schemas(self, only=None):
        return []


class _LLM:
    def __init__(self, model="m-strong"):
        self.model_name = model
        self.calls = 0
        self.switched = []

    def set_model(self, name):
        self.model_name = name
        self.switched.append(name)

    async def chat(self, messages, tools=None, **kw):
        self.calls += 1
        return LLMResponse(content="ok", tool_calls=[],
                           usage={"prompt_tokens": 5, "completion_tokens": 2})


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(db, router, recorder=None):
    return ToolContext(window_id="w-route", workspace=".", mode="AUTO", actor="t",
                       db=db, extra={"router": router, "route_record": recorder})


def test_loop_applies_route_once_and_records(svc):
    routing, cost, db = svc
    llm = _LLM("m-strong")
    loop = AgentLoop(llm=llm, registry=_Registry(), session=_Session(),
                     tool_names=[], max_iterations=3)
    calls = {"n": 0}
    recorded = []

    def router(current):
        calls["n"] += 1
        assert current == "m-strong", "loop 要把当前模型名传进来"
        return {"action": "downroute", "model": "m-cheap",
                "reason": "预算 warn 0.85", "p_fail": 0.15, "samples": 0}

    res = _run(loop.run("hi", _ctx(db, router, lambda ok, m, e: recorded.append((ok, m, e)))))
    assert llm.switched == ["m-cheap"], "只切一次"
    assert llm.calls == 1 and res.stop_reason == "done"
    assert calls["n"] == 1, "只在第一轮决策"
    assert recorded == [(True, "m-cheap", True)], recorded
    acts = [r[0] for r in db._conn.execute(
        "select action from audit_log where action like 'cost:%'")]
    assert "cost:route" in acts


def test_loop_ignores_router_errors(svc):
    routing, cost, db = svc
    llm = _LLM("m-strong")
    loop = AgentLoop(llm=llm, registry=_Registry(), session=_Session(),
                     tool_names=[], max_iterations=2)

    def router(current):
        raise RuntimeError("boom")

    res = _run(loop.run("hi", _ctx(db, router)))
    assert res.stop_reason == "done" and llm.calls >= 1


def test_loop_without_router_still_works(svc):
    routing, cost, db = svc
    llm = _LLM("m-strong")
    loop = AgentLoop(llm=llm, registry=_Registry(), session=_Session(),
                     tool_names=[], max_iterations=2)
    ctx = ToolContext(window_id="w", workspace=".", mode="AUTO", actor="t",
                      db=db, extra={})
    assert _run(loop.run("hi", ctx)).stop_reason == "done"


# ------------------------------------------------------------------ 接口
@pytest.fixture()
def client(svc):
    routing, cost, db = svc
    app = FastAPI()
    app.include_router(costs_router(SimpleNamespace(cost=cost, routing=routing), []),
                       prefix="/api")
    return TestClient(app), routing


def test_routes_endpoint(client):
    c, routing = client
    assert c.get("/api/costs/routes").json()["stats"] == []
    routing.record("admin_chat", "m-cheap", ok=False)
    body = c.get("/api/costs/routes").json()
    assert body["stats"][0]["model"] == "m-cheap"
    assert body["health"]["attempts"] == 1 and body["health"]["failed"] == 1


def test_console_wiring_for_routes():
    idx = (REPO / "dogbuddy/web/static/index.html").read_text(encoding="utf-8")
    js = (REPO / "dogbuddy/web/static/js/views-costs.js").read_text(encoding="utf-8")
    api = (REPO / "dogbuddy/web/static/js/api.js").read_text(encoding="utf-8")
    app = (REPO / "dogbuddy/web/app.py").read_text(encoding="utf-8")
    assert 'id="cost-routes"' in idx
    assert "function renderRoutes()" in js and "p_fail" in js
    assert "/api/costs/routes" in api
    assert '"routing": (svc.routing.health()' in app
