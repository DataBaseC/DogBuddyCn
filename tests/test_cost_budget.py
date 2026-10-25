# DogBuddyCn — 预算闸门（0.11.1）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""`0.11.0` 只是"看见钱"，这一版才"按钱办事"。

钉住的不变量：
  ① 判定来自**账本**（不另存计数），作用域 global/job/group/window/run 各自过滤；
  ② 取**比值最大**的那条预算（窗口超 120% 不会被宽松的全局预算掩盖）；
  ③ 到 80% 只 warn，超过 100% 才 exceeded；
  ④ `downgrade` 会挑单价表里更便宜的模型；没有更便宜的就按暂停处理（不静默放行）；
  ⑤ loop 在**调模型之前**拦：pause 时 `llm.chat` 一次都不该被调用，且写审计。
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
from dogbuddy.tools.registry import ToolContext  # noqa: E402
from dogbuddy.web.routes.costs import router as costs_router  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def svc(tmp_path):
    db = Database(str(tmp_path / "b.db"))
    cost = CostService(db)
    db.upsert_cost_model({"id": "p:m-high", "provider": "p", "model": "m-high",
                          "in_price": 0.02, "out_price": 0.08, "enabled": 1})
    db.upsert_cost_model({"id": "p:m-low", "provider": "p", "model": "m-low",
                          "in_price": 0.002, "out_price": 0.004, "enabled": 1})
    yield cost, db
    db.close()


def _spend_row(db, amount: float, **scope):
    """直接写账本（金额可控），比走 record() 再算单价更直观。"""
    db.record_usage({"ts": CostService._period_start("day"), "amount": amount,
                     "in_tokens": 1, "out_tokens": 1, **scope})


# --------------------------------------------------------------- 判定口径
def test_no_budget_means_ok(svc):
    cost, db = svc
    st = cost.budget_state(window_id="w1")
    assert st["level"] == "ok" and st["exceeded"] is False


def test_warn_at_80_percent_and_exceeded_over_100(svc):
    cost, db = svc
    cost.upsert_budget({"scope_type": "global", "limit_amount": 10, "strategy": "pause"})
    _spend_row(db, 8.5)
    st = cost.budget_state()
    assert st["level"] == "warn" and 0.8 <= st["ratio"] < 1

    cost.upsert_budget({"scope_type": "global", "limit_amount": 5, "strategy": "pause"})
    st2 = cost.budget_state()
    assert st2["exceeded"] is True and st2["level"] == "exceeded"
    assert st2["action"] == "pause"


def test_worst_ratio_wins_over_loose_global(svc):
    cost, db = svc
    cost.upsert_budget({"scope_type": "global", "limit_amount": 100, "strategy": "pause"})
    cost.upsert_budget({"scope_type": "window", "scope_id": "w1",
                        "limit_amount": 1, "strategy": "pause"})
    now = CostService._period_start("day")
    db.record_usage({"ts": now, "amount": 1.5, "window_id": "w1", "in_tokens": 1})
    st = cost.budget_state(window_id="w1")
    assert st["exceeded"] and st["scope"] == "window", st
    # 别的工位不受这条预算影响
    assert cost.budget_state(window_id="w2")["exceeded"] is False


def test_downgrade_picks_cheaper_model(svc):
    cost, db = svc
    cost.upsert_budget({"scope_type": "global", "limit_amount": 1, "strategy": "downgrade"})
    db.record_usage({"ts": CostService._period_start("day"), "amount": 2, "in_tokens": 1})
    st = cost.budget_state(current_model="m-high")
    assert st["action"] == "downgrade"
    assert st["suggest_model"] == "m-low", st
    # 已经在最便宜的档上 → 没有可降的，理由里要说"按暂停处理"
    st2 = cost.budget_state(current_model="m-low")
    assert st2["suggest_model"] == ""
    assert "暂停" in st2["reason"]


def test_budget_validation(tmp_path):
    db = Database(str(tmp_path / "v.db"))
    cost = CostService(db)
    for bad in ({"scope_type": "planet"}, {"period": "year"},
                {"strategy": "explode"}, {"limit_amount": -1},
                {"scope_type": "window"}):              # window 必须有 scope_id
        body = {"scope_type": "global", "period": "day", "strategy": "pause",
                "limit_amount": 1}
        body.update(bad)
        with pytest.raises(ValueError):
            cost.upsert_budget(body)
    db.close()


# ------------------------------------------------------------------- loop
class _Session:
    def __init__(self):
        self.window = SimpleNamespace(id="w-budget")
        self.appended = []

    def append(self, role, content, meta=None, **kw):
        self.appended.append((role, content))

    def build_context(self, builder=None):
        return [{"role": "user", "content": "hi"}]


class _Registry:
    def schemas(self, only=None):
        return []

    async def execute(self, *a, **kw):        # pragma: no cover — 本用例不调工具
        raise AssertionError("no tools expected")


class _LLM:
    def __init__(self, model="m-high"):
        self.model_name = model
        self.calls = 0
        self.switched = []

    def set_model(self, name):
        self.model_name = name
        self.switched.append(name)

    async def chat(self, messages, tools=None, **kw):
        self.calls += 1
        return LLMResponse(content="ok", tool_calls=[],
                           usage={"prompt_tokens": 10, "completion_tokens": 2})


def _ctx(db, check):
    return ToolContext(window_id="w-budget", workspace=".", mode="AUTO",
                       actor="test", db=db, extra={"budget_check": check})


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_loop_pauses_before_calling_model(svc):
    cost, db = svc
    llm = _LLM()
    loop = AgentLoop(llm=llm, registry=_Registry(), session=_Session(),
                     tool_names=[], max_iterations=3)

    def check():
        return {"exceeded": True, "action": "pause", "level": "exceeded",
                "scope": "global", "scope_id": "", "period": "day",
                "spent": 2.0, "limit": 1.0, "reason": "global 预算用尽：已用 2.0000 / 1.0000"}

    res = _run(loop.run("hello", _ctx(db, check)))
    assert llm.calls == 0, "预算用尽时必须**一次都不调模型**"
    assert res.stop_reason == "budget"
    assert res.error_code == "budget_exhausted"
    assert "预算用尽" in res.content
    actions = [r[0] for r in db._conn.execute(
        "select action from audit_log where action like 'cost:%'")]
    assert "cost:paused" in actions, "暂停必须留审计"


def test_loop_downgrades_once_then_continues(svc):
    cost, db = svc
    llm = _LLM("m-high")
    loop = AgentLoop(llm=llm, registry=_Registry(), session=_Session(),
                     tool_names=[], max_iterations=3)

    def check():
        return {"exceeded": True, "action": "downgrade", "level": "exceeded",
                "scope": "global", "scope_id": "", "period": "day",
                "spent": 2.0, "limit": 1.0, "suggest_model": "m-low",
                "reason": "global 预算用尽（降档）"}

    res = _run(loop.run("hello", _ctx(db, check)))
    assert llm.calls == 1, "降档之后应该继续跑（用便宜模型）"
    assert llm.switched == ["m-low"], "只降一次档"
    assert llm.model_name == "m-low"
    assert res.stop_reason == "done"
    actions = [r[0] for r in db._conn.execute(
        "select action from audit_log where action like 'cost:%'")]
    assert "cost:downgrade" in actions


def test_loop_without_budget_hook_still_works(svc):
    cost, db = svc
    llm = _LLM()
    loop = AgentLoop(llm=llm, registry=_Registry(), session=_Session(),
                     tool_names=[], max_iterations=2)
    ctx = ToolContext(window_id="w", workspace=".", mode="AUTO", actor="t", db=db, extra={})
    res = _run(loop.run("hi", ctx))
    assert llm.calls >= 1 and res.stop_reason == "done"


# ------------------------------------------------------------------ 接口
@pytest.fixture()
def client(svc):
    cost, db = svc
    app = FastAPI()
    app.include_router(costs_router(SimpleNamespace(cost=cost), []), prefix="/api")
    return TestClient(app), cost, db


def test_budget_routes(client):
    c, cost, db = client
    assert c.get("/api/costs/budgets").json()["budgets"] == []
    r = c.put("/api/costs/budgets/global:*",
              json={"scope_type": "global", "period": "day",
                    "limit_amount": 3, "strategy": "downgrade"})
    assert r.status_code == 200 and float(r.json()["limit_amount"]) == 3
    listed = c.get("/api/costs/budgets").json()["budgets"]
    assert len(listed) == 1 and listed[0]["spent"] == 0 and listed[0]["level"] == "ok"
    assert c.delete("/api/costs/budgets/global:*").json()["deleted"] is True
    # 非法 → 400（校验在服务层）
    bad = c.put("/api/costs/budgets/x", json={"scope_type": "planet", "limit_amount": 1})
    assert bad.status_code == 400
    # summary / health 都要能看到预算
    assert "budgets" in c.get("/api/costs/summary").json()
    assert "budget" in cost.health()


def test_console_wiring_for_budgets():
    idx = (REPO / "dogbuddy/web/static/index.html").read_text(encoding="utf-8")
    js = (REPO / "dogbuddy/web/static/js/views-costs.js").read_text(encoding="utf-8")
    api = (REPO / "dogbuddy/web/static/js/api.js").read_text(encoding="utf-8")
    assert 'id="cost-budgets"' in idx
    assert "function renderBudgets()" in js and "function editBudget(" in js
    assert "deleteBudget" in api and "/api/costs/budgets/" in api
    adm = (REPO / "dogbuddy/services/admin_service.py").read_text(encoding="utf-8")
    wm = (REPO / "dogbuddy/services/window_manager.py").read_text(encoding="utf-8")
    assert '"budget_check": self._budget_check(' in adm
    assert '"budget_check": self._budget_check(' in wm
