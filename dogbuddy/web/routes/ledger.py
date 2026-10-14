# DogBuddyCn — usage ledger routes (L1 Interface, ROADMAP T2.4)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""账本页数据：token 用量按工位/按天聚合，可选单价换算成本。

数据只收集不汇报（PROPOSALS #3）：用户自己打开账本页看；通知永不推账单。
单价存 config key `ledger.price_per_1m`（每百万 token 价格，货币单位自便），
默认 0 = 只显示 tokens。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel


class LedgerPriceIn(BaseModel):
    price_per_1m: float


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["ledger"])

    def _price() -> float:
        try:
            return float(svc.config.get("ledger.price_per_1m", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _cost(tokens: int, price: float) -> float:
        return round(tokens / 1_000_000 * price, 4) if price else 0.0

    @r.get("/ledger", dependencies=auth_deps)
    def ledger(days: int = 30) -> dict:
        data = svc.db.ledger_summary(days=days)
        price = _price()
        names = {w.id: w.name for w in svc.windows.list()}
        for row in data["by_window"]:
            row["window_name"] = names.get(row["window_id"], row["window_id"])
            row["cost"] = _cost(row["tokens"], price)
        for row in data["by_day"]:
            row["cost"] = _cost(row["tokens"], price)
        data["price_per_1m"] = price
        data["total_cost"] = _cost(data["total_tokens"], price)
        return data

    @r.get("/ledger/price", dependencies=auth_deps)
    def get_price() -> dict:
        return {"price_per_1m": _price()}

    @r.post("/ledger/price", dependencies=auth_deps)
    def set_price(body: LedgerPriceIn) -> dict:
        value = max(0.0, float(body.price_per_1m))
        svc.config.set("ledger.price_per_1m", value)
        return {"price_per_1m": value, "persisted": bool(svc.persist())}

    return r
