# DogBuddyCn — 流水线 API（0.12.1）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""编排接口：定义 / 起跑 / 查状态 / 续跑。

设计取舍：`POST /pipelines/{id}/run` **立刻返回 run_id**，真正执行丢到后台任务——
一次 tick 里可能有 `window_run` 步骤要跑几分钟，不能让 HTTP 挂着（也不会被
面板/浏览器的超时砍断）。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...services.pipeline_service import PipelineError


class PipelineIn(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    steps: list[dict[str, Any]] = []
    enabled: bool = True


class RunIn(BaseModel):
    payload: dict[str, Any] = {}


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["pipelines"])

    def _pl():
        pl = getattr(svc, "pipelines", None)
        if pl is None:
            raise HTTPException(status_code=503, detail="流水线引擎未启用")
        return pl

    # 注意顺序：`/pipelines/runs...` 必须排在 `/pipelines/{pid}...` 之前，
    # 否则 "runs" 会被当成一个 pipeline id。
    @r.get("/pipelines/runs", dependencies=auth_deps)
    def list_runs(limit: int = 30, pipeline_id: str = "") -> dict:
        return {"runs": _pl().runs(limit=limit, pipeline_id=pipeline_id)}

    @r.get("/pipelines/runs/{run_id}", dependencies=auth_deps)
    def run_status(run_id: str) -> dict:
        try:
            return _pl().status(run_id)
        except PipelineError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.post("/pipelines/runs/{run_id}/resume", dependencies=auth_deps)
    async def resume_run(run_id: str) -> dict:
        pl = _pl()
        if pl.db.get_pipeline_run(run_id) is None:
            raise HTTPException(status_code=404, detail=f"run 不存在：{run_id}")
        asyncio.create_task(pl.resume(run_id))
        return {"ok": True, "run_id": run_id, "status": "running"}

    @r.get("/pipelines", dependencies=auth_deps)
    def list_pipelines() -> dict:
        return {"pipelines": _pl().list()}

    @r.get("/pipelines/{pid}", dependencies=auth_deps)
    def get_pipeline(pid: str) -> dict:
        pipe = _pl().get(pid)
        if pipe is None:
            raise HTTPException(status_code=404, detail=f"流水线不存在：{pid}")
        return pipe

    @r.put("/pipelines/{pid}", dependencies=auth_deps)
    def put_pipeline(pid: str, body: PipelineIn) -> dict:
        try:
            return _pl().upsert({**body.model_dump(), "id": pid})
        except PipelineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.delete("/pipelines/{pid}", dependencies=auth_deps)
    def delete_pipeline(pid: str) -> dict:
        return {"deleted": _pl().delete(pid)}

    @r.post("/pipelines/{pid}/run", dependencies=auth_deps)
    async def run_pipeline(pid: str, body: RunIn) -> dict:
        pl = _pl()
        try:
            run = pl.start(pid, body.payload)
        except PipelineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        asyncio.create_task(pl.tick(run["id"]))
        return {"run_id": run["id"], "status": "running"}

    return r
