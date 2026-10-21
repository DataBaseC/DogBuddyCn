# DogBuddyCn — job routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Scheduled task CRUD / pause / resume / run_now / run history — thin DTO shells."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class JobIn(BaseModel):
    window_id: str = ""
    name: str
    schedule: str
    prompt: str
    report_strategy: str = "brief"
    kind: str = "prompt"
    report_channel: str = ""
    llm_profile: str = ""
    #: v0.10.44：非空 = 到点跑一条**群组流水线**（window_id 可省，默认用组长）
    group_id: str = ""


class JobPatch(BaseModel):
    name: str | None = None
    schedule: str | None = None
    prompt: str | None = None
    report_strategy: str | None = None
    kind: str | None = None
    report_channel: str | None = None
    llm_profile: str | None = None


def _job_dto(j: Any) -> dict:
    return {"id": j.id, "window_id": j.window_id, "name": j.name,
            "schedule_type": j.schedule_type, "schedule": j.schedule_raw,
            "prompt": j.prompt, "report_strategy": j.report_strategy,
            "kind": j.kind, "report_channel": j.report_channel,
            "llm_profile": j.llm_profile,
            # v0.10.44：群组任务（到点跑整条流水线）
            "group_id": getattr(j, "group_id", "") or "",
            "enabled": j.enabled, "last_run_at": j.last_run_at,
            "next_run_at": j.next_run_at, "fail_count": j.fail_count}


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["jobs"])

    @r.get("/jobs", dependencies=auth_deps)
    def list_jobs(window_id: str | None = None) -> list[dict]:
        return [_job_dto(j) for j in svc.jobs.list(window_id)]

    @r.post("/jobs", dependencies=auth_deps)
    def create_job(body: JobIn) -> dict:
        wid = body.window_id
        # v0.10.44：群组任务可以只给 group_id，window_id 默认取组长工位
        if body.group_id and not wid:
            g = svc.groups.get(body.group_id) if getattr(svc, "groups", None) else None
            if g is None:
                raise HTTPException(status_code=404, detail=f"没有这个群组：{body.group_id}")
            wid = getattr(g, "lead_window_id", "") or ""
            if not wid:
                raise HTTPException(status_code=400, detail=f"群组「{g.name}」还没有组长")
        if not wid:
            raise HTTPException(status_code=400, detail="window_id 或 group_id 必须给一个")
        try:
            job = svc.jobs.create(window_id=wid, name=body.name,
                                  schedule=body.schedule, prompt=body.prompt,
                                  report_strategy=body.report_strategy, kind=body.kind,
                                  report_channel=body.report_channel,
                                  llm_profile=body.llm_profile,
                                  group_id=body.group_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _job_dto(job)

    @r.patch("/jobs/{job_id}", dependencies=auth_deps)
    def patch_job(job_id: str, body: JobPatch) -> dict:
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            job = svc.jobs.update(job_id, **fields)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _job_dto(job)

    @r.delete("/jobs/{job_id}", dependencies=auth_deps)
    def delete_job(job_id: str) -> dict:
        msg = svc.jobs.remove(job_id)
        return {"result": msg}

    @r.post("/jobs/{job_id}/pause", dependencies=auth_deps)
    def pause(job_id: str) -> dict:
        return {"result": svc.jobs.pause(job_id)}

    @r.post("/jobs/{job_id}/resume", dependencies=auth_deps)
    def resume(job_id: str) -> dict:
        return {"result": svc.jobs.resume(job_id)}

    @r.post("/jobs/{job_id}/run_now", dependencies=auth_deps)
    async def run_now(job_id: str) -> dict:
        """立即执行（v0.10.46）：群组任务在后台起整条流水线，普通任务派给工位。

        以前这里是同步 `svc.jobs.run_now` → 群组任务被拒 / 误派给组长，
        前端却一律弹"已触发执行"（owner 实测："点了没反应"）。
        """
        return await svc.jobs.run_now_async(job_id)

    @r.get("/jobs/{job_id}/runs", dependencies=auth_deps)
    def runs(job_id: str) -> list[dict]:
        return svc.jobs.runs(job_id)

    return r
