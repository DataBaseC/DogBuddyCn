# DogBuddyCn — iteration routes (L1 Interface, ROADMAP T8.5/T8.6)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""项目迭代计划 API：建计划（三档预设/手动覆盖）、看节奏是怎么推出来的、
手动推一轮、暂停/恢复/停止。"""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class IterationIn(BaseModel):
    project_id: str
    goal: str
    mode: str = "standard"
    window_ids: Optional[List[str]] = None
    speed_minutes: int = 0      # 0 = 按档位+画像自动推
    max_rounds: int = 0
    no_progress_limit: int = 0
    budget_tokens: int = 0      # 可选预算上限，0 = 不限


class IterationPatch(BaseModel):
    goal: Optional[str] = None
    mode: Optional[str] = None
    window_ids: Optional[List[str]] = None
    speed_minutes: Optional[int] = None
    max_rounds: Optional[int] = None
    no_progress_limit: Optional[int] = None
    budget_tokens: Optional[int] = None
    status: Optional[str] = None


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["iterations"])

    def _it():
        it = getattr(svc, "iterations", None)
        if it is None:
            raise HTTPException(status_code=503, detail="迭代服务未启用")
        return it

    @r.get("/iterations", dependencies=auth_deps)
    def list_iterations(project_id: str | None = None, status: str | None = None) -> dict:
        it = _it()
        rows = it.list(project_id=project_id, status=status)
        return {"iterations": [{**e, "plan": it.effective_plan(e)} for e in rows],
                "presets": _presets()}

    @r.get("/iterations/presets", dependencies=auth_deps)
    def presets() -> dict:
        return {"presets": _presets()}

    @r.post("/iterations", dependencies=auth_deps)
    def create(body: IterationIn) -> dict:
        it = _it()
        try:
            entry = it.create(body.project_id, body.goal, mode=body.mode,
                              window_ids=body.window_ids,
                              speed_minutes=body.speed_minutes,
                              max_rounds=body.max_rounds,
                              no_progress_limit=body.no_progress_limit,
                              budget_tokens=body.budget_tokens)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"iteration": entry, "plan": it.effective_plan(entry)}

    @r.get("/iterations/{iteration_id}", dependencies=auth_deps)
    def detail(iteration_id: str) -> dict:
        it = _it()
        try:
            return it.detail(iteration_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.patch("/iterations/{iteration_id}", dependencies=auth_deps)
    def patch(iteration_id: str, body: IterationPatch) -> dict:
        it = _it()
        try:
            entry = it.update(iteration_id, **body.model_dump(exclude_none=True))
        except ValueError as e:
            code = 404 if "不存在" in str(e) else 400
            raise HTTPException(status_code=code, detail=str(e)) from e
        return {"iteration": entry, "plan": it.effective_plan(entry)}

    @r.post("/iterations/{iteration_id}/stop", dependencies=auth_deps)
    def stop(iteration_id: str, reason: str = "手动停止") -> dict:
        it = _it()
        try:
            return {"iteration": it.stop(iteration_id, reason)}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.post("/iterations/{iteration_id}/resume", dependencies=auth_deps)
    def resume(iteration_id: str) -> dict:
        it = _it()
        try:
            entry = it.resume(iteration_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"iteration": entry, "plan": it.effective_plan(entry)}

    @r.post("/iterations/{iteration_id}/run_now", dependencies=auth_deps)
    async def run_now(iteration_id: str) -> dict:
        """手动推一轮（不等下一个间隔）。"""
        it = _it()
        if it.get(iteration_id) is None:
            raise HTTPException(status_code=404, detail=f"迭代不存在：{iteration_id}")
        ex = getattr(svc, "iteration_exec", None)
        if ex is None:
            raise HTTPException(status_code=503, detail="迭代执行器未启用")
        try:
            return {"result": await ex.run_one(iteration_id)}
        except Exception as e:  # noqa: BLE001 — 一轮失败要给人话
            raise HTTPException(status_code=500, detail=f"本轮执行失败：{e}") from e

    @r.delete("/iterations/{iteration_id}", dependencies=auth_deps)
    def remove(iteration_id: str) -> dict:
        it = _it()
        if not it.remove(iteration_id):
            raise HTTPException(status_code=404, detail=f"迭代不存在：{iteration_id}")
        return {"deleted": iteration_id}

    return r


def _presets() -> list[dict[str, Any]]:
    from ...services.iteration_service import MODES, PRESETS
    labels = {"conservative": "保守", "standard": "标准", "aggressive": "激进"}
    hints = {"conservative": "改动风险高或模型慢时用：间隔长、轮次少、2 轮无进展就停",
             "standard": "默认：每小时一轮，最多 50 轮，3 轮无进展就停",
             "aggressive": "目标明确且模型快时用：15 分钟一轮，允许更多轮次"}
    return [{"mode": m, "label": labels.get(m, m), "hint": hints.get(m, ""), **PRESETS[m]}
            for m in MODES]
