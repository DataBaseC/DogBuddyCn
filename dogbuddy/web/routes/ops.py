# DogBuddyCn — 运营回合 API（0.14.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""目标 / 回合 / 日志 / 实验的对外接口。

刻意的取舍：`POST /ops/goals/{id}/rounds` 只在**明确调用**时才开跑；
没有"到点自动花钱"的接口——节奏到了该跑，也由人排的定时任务来触发。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class GoalIn(BaseModel):
    name: str = ""
    description: str = ""
    metric_name: str = ""
    metric_unit: str = ""
    direction: str = "up"            # up | down
    baseline: float | None = None
    target: float | None = None
    cadence: str = "manual"         # manual | daily | weekly
    pipeline_id: str = ""
    enabled: bool = True


class RoundIn(BaseModel):
    execute: bool = True


class CloseIn(BaseModel):
    observed: str = ""
    verdict: str = ""               # keep | adjust | stop | unknown（空 = 自动判）
    metric_after: float | None = None


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["ops"])

    def _ops():
        ops = getattr(svc, "ops", None)
        if ops is None:
            raise HTTPException(status_code=503, detail="运营回合服务未启用")
        return ops

    # 注意顺序：`/ops/journal...` 不能落在 `/ops/goals/{gid}` 之类的通配里；
    # 两者前缀不同，这里显式分组，避免以后加通配路由时踩坑。
    @r.get("/ops/journal", dependencies=auth_deps)
    def list_journal(goal_id: str = "", limit: int = 30) -> dict[str, Any]:
        return {"journal": _ops().journal(goal_id=goal_id, limit=limit)}

    @r.post("/ops/journal/{jid}/close", dependencies=auth_deps)
    def close_round(jid: str, body: CloseIn) -> dict[str, Any]:
        try:
            return _ops().close_round(jid, observed=body.observed,
                                      verdict=body.verdict,
                                      metric_after=body.metric_after)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.get("/ops/experiments", dependencies=auth_deps)
    def list_experiments(goal_id: str = "", limit: int = 30) -> dict[str, Any]:
        return {"experiments": _ops().experiments(goal_id=goal_id, limit=limit)}

    @r.post("/ops/tick", dependencies=auth_deps)
    def tick() -> dict[str, Any]:
        updated = _ops().tick()
        return {"updated": len(updated), "journal": updated}

    @r.get("/ops/goals", dependencies=auth_deps)
    def list_goals(enabled_only: bool = False) -> dict[str, Any]:
        return {"goals": _ops().goals(enabled_only=enabled_only)}

    @r.put("/ops/goals/{gid}", dependencies=auth_deps)
    def put_goal(gid: str, body: GoalIn) -> dict[str, Any]:
        try:
            return _ops().upsert_goal({**body.model_dump(), "id": gid})
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.get("/ops/goals/{gid}", dependencies=auth_deps)
    def get_goal(gid: str) -> dict[str, Any]:
        try:
            return _ops().goal(gid)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.delete("/ops/goals/{gid}", dependencies=auth_deps)
    def delete_goal(gid: str) -> dict[str, Any]:
        return {"deleted": _ops().delete_goal(gid)}

    @r.post("/ops/goals/{gid}/plan", dependencies=auth_deps)
    def plan_round(gid: str) -> dict[str, Any]:
        try:
            return _ops().plan_round(gid)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.post("/ops/goals/{gid}/rounds", dependencies=auth_deps)
    async def start_round(gid: str, body: RoundIn) -> dict[str, Any]:
        try:
            return _ops().start_round(gid, execute=body.execute)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.get("/ops/goals/{gid}/journal", dependencies=auth_deps)
    def goal_journal(gid: str, limit: int = 30) -> dict[str, Any]:
        return {"journal": _ops().journal(goal_id=gid, limit=limit)}

    return r
