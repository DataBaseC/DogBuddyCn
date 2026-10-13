# DogBuddyCn — job routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Scheduled task CRUD / pause / resume / run_now / run history — thin DTO shells."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class JobIn(BaseModel):
    window_id: str
    name: str
    schedule: str
    prompt: str
    report_strategy: str = "brief"
    kind: str = "prompt"
    report_channel: str = ""
    llm_profile: str = ""


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
            "enabled": j.enabled, "last_run_at": j.last_run_at,
            "next_run_at": j.next_run_at, "fail_count": j.fail_count}


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["jobs"])

    @r.get("/jobs", dependencies=auth_deps)
    def list_jobs(window_id: str | None = None) -> list[dict]:
        return [_job_dto(j) for j in svc.jobs.list(window_id)]

    @r.post("/jobs", dependencies=auth_deps)
    def create_job(body: JobIn) -> dict:
        try:
            job = svc.jobs.create(window_id=body.window_id, name=body.name,
                                  schedule=body.schedule, prompt=body.prompt,
                                  report_strategy=body.report_strategy, kind=body.kind,
                                  report_channel=body.report_channel,
                                  llm_profile=body.llm_profile)
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
    def run_now(job_id: str) -> dict:
        return {"result": svc.jobs.run_now(job_id)}

    @r.get("/jobs/{job_id}/runs", dependencies=auth_deps)
    def runs(job_id: str) -> list[dict]:
        return svc.jobs.runs(job_id)

    return r
