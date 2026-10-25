# DogBuddyCn — 成本页接口与控制台接线（0.11.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""`/api/costs/*` 的行为 + 控制台接线（导航/视图/bundle/api 客户端）。

路由层只做参数校验与转发，算术在 CostService（已由 test_cost_ledger 覆盖）；
这里钉的是**契约**：非法输入不落库、改价留审计、汇总口径来自服务层。
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
from dogbuddy.services.cost_service import CostService  # noqa: E402
from dogbuddy.web.routes.costs import router as costs_router  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path):
    db = Database(str(tmp_path / "c.db"))
    cost = CostService(db)
    catalog = [{"provider": "p", "model": "m1", "cost": "low"},
               {"provider": "p", "model": "m2", "cost": "high"}]
    svc = SimpleNamespace(cost=cost, models=SimpleNamespace(catalog=lambda: catalog))
    app = FastAPI()
    app.include_router(costs_router(svc, []), prefix="/api")
    yield TestClient(app), cost, db
    db.close()


def test_summary_and_ledger(client):
    c, cost, db = client
    db.upsert_cost_model({"id": "p:m1", "provider": "p", "model": "m1",
                          "in_price": 0.01, "out_price": 0.02, "enabled": 1})
    cost.record(provider="p", model="m1", window_id="w1", in_tokens=1000, idem_key="x")
    r = c.get("/api/costs/summary?days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["total"]["calls"] == 1
    assert body["total"]["amount"] == pytest.approx(0.01, abs=1e-9)
    ledger = c.get("/api/costs/ledger?limit=10").json()["rows"]
    assert len(ledger) == 1 and "meta_json" not in ledger[0]


def test_price_put_and_validation(client):
    c, cost, db = client
    r = c.put("/api/costs/models/p:m1", json={"provider": "p", "model": "m1",
                                              "in_price": 0.02, "out_price": 0.05})
    assert r.status_code == 200 and r.json()["in_price"] == 0.02
    audit = [row[0] for row in db._conn.execute(
        "SELECT action FROM audit_log WHERE action LIKE 'cost:%'")]
    assert "cost:price" in audit
    bad = c.put("/api/costs/models/p:m1", json={"model": "m1", "in_price": -1})
    assert bad.status_code == 400


def test_seed_endpoint_uses_catalog_tiers(client):
    c, cost, db = client
    r = c.post("/api/costs/models/seed")
    assert r.status_code == 200
    assert r.json()["created"] == 2
    prices = {p["id"]: p for p in c.get("/api/costs/models").json()["prices"]}
    assert prices["p:m1"]["in_price"] < prices["p:m2"]["in_price"]


def test_budget_validation(client):
    c, cost, db = client
    ok = c.put("/api/costs/budgets/global:*", json={"scope_type": "global",
                                                   "limit_amount": 5, "period": "day",
                                                   "strategy": "downgrade"})
    assert ok.status_code == 200 and ok.json()["limit_amount"] == 5
    for bad in ({"scope_type": "planet"}, {"period": "year"},
                {"strategy": "explode"}, {"limit_amount": -1}):
        body = {"scope_type": "global", "period": "day", "strategy": "downgrade",
                "limit_amount": 1}
        body.update(bad)
        assert c.put("/api/costs/budgets/b1", json=body).status_code == 400


def test_cost_service_missing_is_503(tmp_path):
    app = FastAPI()
    app.include_router(costs_router(SimpleNamespace(cost=None), []), prefix="/api")
    assert TestClient(app).get("/api/costs/summary").status_code == 503


# --------------------------------------------------------------- 控制台接线
def test_console_wiring():
    idx = (REPO / "dogbuddy/web/static/index.html").read_text(encoding="utf-8")
    app_js = (REPO / "dogbuddy/web/static/js/app.js").read_text(encoding="utf-8")
    api_js = (REPO / "dogbuddy/web/static/js/api.js").read_text(encoding="utf-8")
    order = (REPO / "tools/bundle-order.json").read_text(encoding="utf-8")
    assert 'data-view="costs"' in idx and 'id="view-costs"' in idx
    assert "case 'costs':" in app_js and "V.Costs.load()" in app_js
    assert "costs: {" in api_js and "/api/costs/summary" in api_js
    assert "js/views-costs.js" in order, "新页面必须登记进 bundle-order.json"


def test_loop_and_compactor_have_usage_hooks():
    """记账必须挂在 loop 的统一响应点与压缩摘要上，否则会漏掉一半花费。"""
    loop = (REPO / "dogbuddy/agents/core/loop.py").read_text(encoding="utf-8")
    comp = (REPO / "dogbuddy/windows/compactor.py").read_text(encoding="utf-8")
    adm = (REPO / "dogbuddy/services/admin_service.py").read_text(encoding="utf-8")
    wm = (REPO / "dogbuddy/services/window_manager.py").read_text(encoding="utf-8")
    assert 'ctx.extra.get("on_usage")' in loop
    # 钩子必须拿到 llm（只有客户端知道 model_name；0.11.0 首跑就是这里漏了，
    # tokens 记上了但金额是 0）
    assert "usage_hook(resp, self.llm)" in loop
    assert 'getattr(llm, "model_name", "")' in adm
    assert 'getattr(llm, "model_name", "")' in wm
    assert "self.cost.record(" in comp
    assert '"on_usage": self._usage_hook(sender)' in adm
    assert '"on_usage": self._usage_hook(w, run_id, model)' in wm
