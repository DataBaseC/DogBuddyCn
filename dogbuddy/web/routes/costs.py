# DogBuddyCn — 成本账本路由（0.11.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""成本查询与单价表/预算维护（docs/工业自动化设计.md §5）。

设计约束（都在测试里）：
  * 金额**只由服务层换算**（`CostService.amount_for`），路由不做算术；
  * 改价必须留审计（单价配错会让整个调度决策偏掉）；
  * 汇总的分组维度是白名单（列名会进 SQL，绝不能让外部决定）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class PriceIn(BaseModel):
    provider: str = ""
    model: str = ""
    unit: str = "token"                 # token | image | minute
    in_price: float = 0.0               # 每 1K token（unit=image 时=每张）
    out_price: float = 0.0
    cache_price: float = 0.0
    currency: str = "CNY"
    note: str = ""
    enabled: bool = True


class BudgetIn(BaseModel):
    scope_type: str = "global"          # global | group | job | window | run
    scope_id: str = ""
    period: str = "day"                 # day | week
    limit_amount: float = 0.0
    strategy: str = "downgrade"         # downgrade | queue | pause
    enabled: bool = True


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["costs"])

    def _cost():
        cost = getattr(svc, "cost", None)
        if cost is None:
            raise HTTPException(status_code=503, detail="成本账本未启用")
        return cost

    @r.get("/costs/summary", dependencies=auth_deps)
    def summary(days: int = 7, group_by: str = "day") -> dict:
        return _cost().summary(days=days, group_by=group_by)

    @r.get("/costs/ledger", dependencies=auth_deps)
    def ledger(limit: int = 100, since: str = "") -> dict:
        return {"rows": _cost().ledger(since=since, limit=limit)}

    @r.get("/costs/models", dependencies=auth_deps)
    def list_prices() -> dict:
        cost = _cost()
        return {"prices": cost.db.list_cost_models(), "count": len(cost.db.list_cost_models())}

    @r.put("/costs/models/{price_id}", dependencies=auth_deps)
    def put_price(price_id: str, body: PriceIn) -> dict:
        cost = _cost()
        payload: dict[str, Any] = body.model_dump()
        payload["id"] = price_id
        try:
            return cost.upsert_price(payload)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.delete("/costs/models/{price_id}", dependencies=auth_deps)
    def delete_price(price_id: str) -> dict:
        return {"deleted": _cost().db.delete_cost_model(price_id)}

    @r.post("/costs/models/seed", dependencies=auth_deps)
    def seed_prices() -> dict:
        """按模型目录的 `cost` 档位生成占位价（已存在的不覆盖）。"""
        models = getattr(svc, "models", None)
        catalog = models.catalog() if models is not None else []
        return _cost().seed_from_models(catalog)

    @r.get("/costs/routes", dependencies=auth_deps)
    def list_routes() -> dict:
        """模型分级路由的样本与健康度（v0.12.0）。

        路由服务没装配（老库/测试）时返回空表而不是 500 —— 成本页要能整体打开。
        """
        routing = getattr(svc, "routing", None)
        if routing is None:
            return {"stats": [], "health": None}
        return {"stats": routing.stats(), "health": routing.health()}

    @r.get("/costs/budgets", dependencies=auth_deps)
    def list_budgets() -> dict:
        """预算列表 + 实时用量（spent/ratio/level 由账本现算，不另存计数）。"""
        cost = _cost()
        return {"budgets": cost.budget_states()}

    @r.put("/costs/budgets/{budget_id}", dependencies=auth_deps)
    def put_budget(budget_id: str, body: BudgetIn) -> dict:
        payload = body.model_dump()
        payload["id"] = budget_id
        try:
            return _cost().upsert_budget(payload)      # 校验与审计都在服务层
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.delete("/costs/budgets/{budget_id}", dependencies=auth_deps)
    def delete_budget(budget_id: str) -> dict:
        return {"deleted": _cost().delete_budget(budget_id)}

    return r
