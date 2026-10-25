# DogBuddyCn — 抽检与复核 API（0.12.2）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""抽检策略与复核闭环的对外接口。

`POST /api/qc/plan` 是**纯计算**（不落库）：给批次大小就能算出该抽几件、为什么；
带 `open_batch=true` 才真的开一个批次。这样界面/Agent 可以先试算再决定。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class PlanIn(BaseModel):
    batch_size: int = 100
    risk_prior: float = 0.02
    target_miss: float = 0.05
    cost_per_item: float = 0.02
    budget: float = 0.5
    max_ratio: float = 0.2
    line: str = ""
    open_batch: bool = False
    pipeline_run_id: str = ""


class BatchIn(BaseModel):
    total: int = 0
    sampled: int = 0
    line: str = ""
    pipeline_run_id: str = ""
    note: str = ""


class ReviewIn(BaseModel):
    item_ref: str = ""
    model_label: str = ""
    confidence: float = 0.0
    verdict: str = "agree"          # agree | reject | unknown
    reviewer: str = "owner"


class CloseIn(BaseModel):
    defects_found: int | None = None


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["qc"])

    def _qc():
        qc = getattr(svc, "qc", None)
        if qc is None:
            raise HTTPException(status_code=503, detail="抽检服务未启用")
        return qc

    @r.post("/qc/plan", dependencies=auth_deps)
    def plan(body: PlanIn) -> dict[str, Any]:
        qc = _qc()
        out = qc.plan(body.batch_size, risk_prior=body.risk_prior,
                      target_miss=body.target_miss, cost_per_item=body.cost_per_item,
                      budget=body.budget, max_ratio=body.max_ratio)
        if body.open_batch:
            row = qc.open_batch(total=int(out["batch"]), sampled=int(out["sample"]),
                                line=body.line, pipeline_run_id=body.pipeline_run_id,
                                policy=out["policy"], cost_estimate=float(out["cost"]))
            out["batch_id"] = row.get("id")
        return out

    @r.get("/qc/batches", dependencies=auth_deps)
    def list_batches(limit: int = 50) -> dict[str, Any]:
        return {"batches": _qc().batches(limit=limit)}

    @r.post("/qc/batches", dependencies=auth_deps)
    def open_batch(body: BatchIn) -> dict[str, Any]:
        if int(body.total or 0) <= 0:
            raise HTTPException(status_code=400, detail="total 必须大于 0")
        return _qc().open_batch(total=body.total, sampled=body.sampled,
                                line=body.line, pipeline_run_id=body.pipeline_run_id)

    @r.get("/qc/batches/{batch_id}", dependencies=auth_deps)
    def get_batch(batch_id: str) -> dict[str, Any]:
        try:
            return _qc().detail(batch_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.post("/qc/batches/{batch_id}/review", dependencies=auth_deps)
    def add_review(batch_id: str, body: ReviewIn) -> dict[str, Any]:
        try:
            return _qc().review(batch_id, item_ref=body.item_ref,
                                model_label=body.model_label,
                                confidence=body.confidence,
                                reviewer=body.reviewer, verdict=body.verdict)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.post("/qc/batches/{batch_id}/close", dependencies=auth_deps)
    def close_batch(batch_id: str, body: CloseIn) -> dict[str, Any]:
        try:
            return _qc().close(batch_id, defects_found=body.defects_found)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    return r
