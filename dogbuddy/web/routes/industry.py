# DogBuddyCn — 工业信号与事件接入（0.13.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""边缘 → 管理侧的**单向**入口：推指标、发事件。

两条路径都刻意保持"薄"：
  * `POST /api/metrics/push` 只收数字（名字/值/单位/来源），不收图、不收原始报文；
  * `POST /api/ingress/events` 只做一件事：把一批事件变成一次流水线运行
    （`items` 进 payload，具体怎么处理由流水线定义），立刻返回 run_id。

这样边缘侧只要一个 HTTP 客户端就能接入，不需要理解我们的内部结构。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class MetricIn(BaseModel):
    name: str
    value: float = 0
    unit: str = ""
    source: str = ""
    ts: str = ""
    meta: dict[str, Any] = {}


class IngressIn(BaseModel):
    pipeline_id: str
    line: str = ""
    items: list[dict[str, Any]] = []
    payload: dict[str, Any] = {}
    wait: bool = False          # True = 同步跑完再返回（调试用，别在生产开）


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["industry"])

    def _sensors():
        hub = getattr(svc, "metrics", None)
        if hub is None:
            raise HTTPException(status_code=503, detail="指标服务未启用")
        return hub

    def _pl():
        pl = getattr(svc, "pipelines", None)
        if pl is None:
            raise HTTPException(status_code=503, detail="流水线引擎未启用")
        return pl

    # ------------------------------------------------------------- 指标
    @r.post("/metrics/push", dependencies=auth_deps)
    def push_metric(body: MetricIn) -> dict[str, Any]:
        try:
            return _sensors().push(body.name, body.value, unit=body.unit,
                                   source=body.source, ts=body.ts, meta=body.meta)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.get("/metrics", dependencies=auth_deps)
    def list_metrics(name: str = "") -> dict[str, Any]:
        return {"metrics": _sensors().latest(name)}

    @r.get("/metrics/{name}/history", dependencies=auth_deps)
    def metric_history(name: str, limit: int = 100) -> dict[str, Any]:
        return {"name": name, "points": _sensors().history(name, limit=limit)}

    # ------------------------------------------------------------- 事件
    @r.post("/ingress/events", dependencies=auth_deps)
    async def ingress(body: IngressIn) -> dict[str, Any]:
        pl = _pl()
        payload = {"line": body.line, "items": body.items}
        payload.update(body.payload or {})
        try:
            run = pl.start(body.pipeline_id, payload)
        except Exception as e:  # noqa: BLE001 — PipelineError 与"不存在"都该是 400
            raise HTTPException(status_code=400, detail=str(e)) from e
        if body.wait:
            st = await pl.tick(run["id"])
            return {"run_id": run["id"], "accepted": len(body.items),
                    "status": (st.get("run") or {}).get("status")}
        asyncio.create_task(pl.tick(run["id"]))
        return {"run_id": run["id"], "accepted": len(body.items), "status": "running"}

    return r
