# DogBuddyCn — window routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Window CRUD, pause/resume, dispatch, messages, context ops — thin DTO shells."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class WindowIn(BaseModel):
    name: str
    role: str = "worker_programmer"
    workspace_path: str
    model: str = ""
    mode: str = "BUILD"
    llm_profile: str = ""


class WindowPatch(BaseModel):
    name: str | None = None
    model: str | None = None
    mode: str | None = None
    llm_profile: str | None = None


class DispatchIn(BaseModel):
    prompt: str


class InjectIn(BaseModel):
    text: str


def _win_dto(w: Any, todos: list | None = None) -> dict:
    d = {"id": w.id, "name": w.name, "role": w.role, "workspace_path": w.workspace_path,
         "model": w.model, "mode": w.mode, "status": w.status,
         "llm_profile": w.llm_profile,
         "created_at": w.created_at, "updated_at": w.updated_at}
    if todos is not None:
        d["todos"] = [{"id": t.id, "content": t.content, "done": t.done} for t in todos]
    return d


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["windows"])

    @r.get("/windows", dependencies=auth_deps)
    def list_windows() -> list[dict]:
        out = []
        for w in svc.windows.list():
            st = svc.windows.status(w.id)
            d = _win_dto(w)
            d.update(current_task=st.get("current_task", ""), tokens=st.get("tokens", 0))
            out.append(d)
        return out

    @r.post("/windows", dependencies=auth_deps)
    def create_window(body: WindowIn) -> dict:
        try:
            w = svc.windows.create(name=body.name, role=body.role,
                                   workspace_path=body.workspace_path,
                                   model=body.model, mode=body.mode,
                                   llm_profile=body.llm_profile)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _win_dto(w)

    @r.get("/windows/{window_id}", dependencies=auth_deps)
    def get_window(window_id: str) -> dict:
        w = svc.windows.get(window_id)
        if w is None:
            raise HTTPException(status_code=404, detail="window not found")
        rt_todos = svc.sessions._rt(w.id).session.todo_list()
        return _win_dto(w, todos=rt_todos)

    @r.patch("/windows/{window_id}", dependencies=auth_deps)
    def patch_window(window_id: str, body: WindowPatch) -> dict:
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            w = svc.windows.update(window_id, **fields)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _win_dto(w)

    @r.delete("/windows/{window_id}", dependencies=auth_deps)
    def delete_window(window_id: str) -> dict:
        ok = svc.windows.delete(window_id)
        if not ok:
            raise HTTPException(status_code=404, detail="window not found")
        return {"deleted": True}

    @r.post("/windows/{window_id}/pause", dependencies=auth_deps)
    def pause(window_id: str) -> dict:
        return {"paused": svc.windows.pause(window_id)}

    @r.post("/windows/{window_id}/resume", dependencies=auth_deps)
    def resume(window_id: str) -> dict:
        return {"resumed": svc.windows.resume(window_id)}

    @r.post("/windows/{window_id}/dispatch", dependencies=auth_deps)
    def dispatch(window_id: str, body: DispatchIn) -> dict:
        try:
            svc.windows.dispatch_manual(window_id, body.prompt)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"queued": True}

    @r.get("/windows/{window_id}/messages", dependencies=auth_deps)
    def messages(window_id: str, limit: int = 100) -> dict:
        try:
            return {"messages": svc.windows.recent_messages(window_id, limit)}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @r.post("/windows/{window_id}/context/compress", dependencies=auth_deps)
    async def compress(window_id: str) -> dict:
        rec = await svc.sessions.compress(window_id)
        return {"replaced": getattr(rec, "replaced_count", 0)}

    @r.post("/windows/{window_id}/context/reset", dependencies=auth_deps)
    def reset(window_id: str) -> dict:
        return {"dropped": svc.sessions.reset(window_id)}

    @r.post("/windows/{window_id}/context/inject", dependencies=auth_deps)
    def inject(window_id: str, body: InjectIn) -> dict:
        svc.sessions.inject(window_id, body.text)
        return {"injected": True}

    return r
