# DogBuddyCn — shared work-channel group routes (L1 Interface, ROADMAP T3)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""群组 CRUD、成员管理、共享频道查看、群组任务派发。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class GroupIn(BaseModel):
    name: str
    description: str = ""
    lead_window_id: str = ""
    project_id: str = ""   # T7.3: 挂项目 → 成员共享该项目根目录


class GroupPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    lead_window_id: str | None = None
    project_id: str | None = None


class MemberIn(BaseModel):
    window: str
    role: str = "member"


class GroupRunIn(BaseModel):
    prompt: str


def _group_dto(g, groups) -> dict:
    d = {"id": g.id, "name": g.name, "description": g.description,
         "lead_window_id": g.lead_window_id,
         "project_id": getattr(g, "project_id", "") or "",
         "created_at": g.created_at, "updated_at": g.updated_at}
    # 群组挂的项目名（UI 要显示"共享工作区=哪个项目根"）
    try:
        db = getattr(groups, "db", None)
        proj = db.get_project(d["project_id"]) if (db is not None and d["project_id"]) else None
        d["project_name"] = getattr(proj, "name", "") if proj else ""
        d["shared_workspace"] = getattr(proj, "root_path", "") if proj else ""
    except Exception:  # noqa: BLE001 — DTO never breaks a listing
        d["project_name"] = ""
        d["shared_workspace"] = ""
    try:
        d["members"] = groups.members(g.id)
    except Exception:  # noqa: BLE001 — DTO never breaks a listing
        d["members"] = []
    return d


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["groups"])

    @r.get("/groups", dependencies=auth_deps)
    def list_groups() -> dict:
        return {"groups": [_group_dto(g, svc.groups) for g in svc.groups.list()]}

    @r.post("/groups", dependencies=auth_deps)
    def create_group(body: GroupIn) -> dict:
        try:
            g = svc.groups.create(body.name, description=body.description,
                                  lead_window_id=body.lead_window_id,
                                  project_id=body.project_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _group_dto(g, svc.groups)

    @r.get("/groups/{group_ref}", dependencies=auth_deps)
    def get_group(group_ref: str) -> dict:
        g = svc.groups.get(group_ref)
        if g is None:
            raise HTTPException(status_code=404, detail="group not found")
        return _group_dto(g, svc.groups)

    @r.patch("/groups/{group_ref}", dependencies=auth_deps)
    def patch_group(group_ref: str, body: GroupPatch) -> dict:
        g = svc.groups.get(group_ref)
        if g is None:
            raise HTTPException(status_code=404, detail="group not found")
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if "lead_window_id" in fields:  # lead is a member role, not just a column
            svc.groups.assign(g.id, fields["lead_window_id"], role="lead")
            fields.pop("lead_window_id")
        g = svc.db.update_group(g.id, **fields) if fields else g
        return _group_dto(g, svc.groups)

    @r.delete("/groups/{group_ref}", dependencies=auth_deps)
    def delete_group(group_ref: str) -> dict:
        if not svc.groups.delete(group_ref):
            raise HTTPException(status_code=404, detail="group not found")
        return {"deleted": True}

    @r.post("/groups/{group_ref}/members", dependencies=auth_deps)
    def add_member(group_ref: str, body: MemberIn) -> dict:
        try:
            g = svc.groups.assign(group_ref, body.window, role=body.role)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _group_dto(g, svc.groups)

    @r.delete("/groups/{group_ref}/members/{window_ref}", dependencies=auth_deps)
    def remove_member(group_ref: str, window_ref: str) -> dict:
        if not svc.groups.unassign(group_ref, window_ref):
            raise HTTPException(status_code=404, detail="group or window not found")
        return {"removed": True}

    @r.get("/groups/{group_ref}/channel", dependencies=auth_deps)
    def channel(group_ref: str, limit: int = 100) -> dict:
        try:
            return {"messages": svc.groups.channel(group_ref, limit)}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.post("/groups/{group_ref}/run", dependencies=auth_deps)
    async def run_group(group_ref: str, body: GroupRunIn) -> dict:
        try:
            return await svc.groups.run(group_ref, body.prompt)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return r
