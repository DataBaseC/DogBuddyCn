# DogBuddyCn — project routes (L1 Interface, ROADMAP T7.6)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""项目管理 API：项目的增删改、工位挂载、以及**参数继承链可视化**。

`GET /api/projects/{id}/params-chain` 是 T7.2 的验收点：把
`项目 → 工位 → profile → 全局` 每一层的值与来源都摊开，管理台才能回答
"这个参数到底是谁定的"。以前只有"继承中/已覆盖"两态，项目层进来后会
出现"被项目覆盖但工位没写"，两态不够用了。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ProjectIn(BaseModel):
    name: str
    root_path: str | None = None
    git_init: bool = False
    params: dict[str, Any] | None = None


class ProjectPatch(BaseModel):
    name: str | None = None
    root_path: str | None = None
    params: dict[str, Any] | None = None
    owner_supervisor_id: str | None = None   # T9.4：指定该项目的主管工位


class MountIn(BaseModel):
    window_id: str
    project_id: str
    subdir: str | None = None


def _dto(p: Any, db: Any) -> dict:
    windows = [w for w in db.list_windows() if (getattr(w, "project_id", "") or "") == p.id]
    groups = [g for g in db.list_groups() if (getattr(g, "project_id", "") or "") == p.id]
    return {
        "id": p.id, "name": p.name, "root_path": p.root_path,
        "params": p.params or {}, "created_at": p.created_at, "updated_at": p.updated_at,
        # T9.4：该项目的主管（指挥链的中间层）；空 = 无主管（事件直连管理员）
        "owner_supervisor_id": getattr(p, "owner_supervisor_id", "") or "",
        "owner_supervisor_name": _supervisor_name(db, p),
        "windows": [{"id": w.id, "name": w.name, "subdir": getattr(w, "subdir", "") or ""}
                    for w in windows],
        "groups": [{"id": g.id, "name": g.name} for g in groups],
        "window_count": len(windows), "group_count": len(groups),
    }


def _supervisor_name(db: Any, p: Any) -> str:
    sid = getattr(p, "owner_supervisor_id", "") or ""
    if not sid:
        return ""
    try:
        w = db.get_window(sid)
    except Exception:  # noqa: BLE001
        return ""
    return getattr(w, "name", "") if w is not None else ""


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["projects"])

    def _ws():
        ws = getattr(svc, "workspaces", None)
        if ws is None or not hasattr(ws, "create_project"):
            raise HTTPException(status_code=503, detail="项目服务未启用")
        return ws

    @r.get("/projects", dependencies=auth_deps)
    def list_projects() -> dict:
        db = svc.db
        return {"projects": [_dto(p, db) for p in db.list_projects()],
                "summary": db.project_summary(),
                "workspaces": _safe_roots(svc)}

    @r.post("/projects", dependencies=auth_deps)
    def create_project(body: ProjectIn) -> dict:
        ws = _ws()
        try:
            p = ws.create_project(body.name, body.root_path or "", git_init=body.git_init,
                                  params=body.params)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"project": _dto(p, svc.db), "projects": [_dto(x, svc.db) for x in svc.db.list_projects()]}

    @r.get("/projects/{project_ref}", dependencies=auth_deps)
    def get_project(project_ref: str) -> dict:
        p = svc.db.get_project(project_ref)
        if p is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_ref}")
        return {"project": _dto(p, svc.db)}

    @r.patch("/projects/{project_ref}", dependencies=auth_deps)
    def patch_project(project_ref: str, body: ProjectPatch) -> dict:
        p = svc.db.get_project(project_ref)
        if p is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_ref}")
        fields = body.model_dump(exclude_none=True)
        if "root_path" in fields:
            from pathlib import Path
            fields["root_path"] = str(Path(fields["root_path"]).expanduser().resolve())
            Path(fields["root_path"]).mkdir(parents=True, exist_ok=True)
        updated = svc.db.update_project(p.id, **fields)
        svc.db.audit("web", "project:update",
                     f"{p.id} fields={sorted(fields)}")
        return {"project": _dto(updated or p, svc.db)}

    @r.delete("/projects/{project_ref}", dependencies=auth_deps)
    def delete_project(project_ref: str) -> dict:
        p = svc.db.get_project(project_ref)
        if p is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_ref}")
        # 只解挂，不删工作目录、不删工位（目录里有用户的代码）
        svc.db.delete_project(p.id)
        svc.db.audit("web", "project:delete", f"{p.id} {p.name}（工位已解挂，目录保留）")
        return {"deleted": p.id, "projects": [_dto(x, svc.db) for x in svc.db.list_projects()]}

    @r.post("/projects/mount", dependencies=auth_deps)
    def mount(body: MountIn) -> dict:
        ws = _ws()
        try:
            ws.mount(body.window_id, body.project_id, body.subdir or "")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"projects": [_dto(x, svc.db) for x in svc.db.list_projects()]}

    @r.post("/projects/unmount/{window_id}", dependencies=auth_deps)
    def unmount(window_id: str) -> dict:
        w = svc.db.get_window(window_id)
        if w is None:
            raise HTTPException(status_code=404, detail=f"工位不存在：{window_id}")
        svc.db.update_window(window_id, project_id="", subdir="")
        svc.db.audit("web", "project:unmount", window_id)
        return {"unmounted": window_id,
                "projects": [_dto(x, svc.db) for x in svc.db.list_projects()]}

    @r.get("/projects/{project_ref}/chain", dependencies=auth_deps)
    def chain(project_ref: str) -> dict:
        """指挥链一览：管理员 → 主管 → 员工（T9.4 的可视化依据）。"""
        p = svc.db.get_project(project_ref)
        if p is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_ref}")
        sup_id = getattr(p, "owner_supervisor_id", "") or ""
        sup = svc.db.get_window(sup_id) if sup_id else None
        workers = []
        for w in svc.db.list_windows():
            if (getattr(w, "project_id", "") or "") != p.id:
                continue
            if w.id == sup_id:
                continue
            workers.append({"id": w.id, "name": w.name, "role": w.role,
                            "status": getattr(w, "status", "")})
        health = "ok"
        if sup_id and sup is None:
            health = "主管工位不存在 → 事件退化直连管理员"
        elif not sup_id:
            health = "无主管 → 事件直连管理员（兼容单管理员部署）"
        return {
            "project": {"id": p.id, "name": p.name, "root_path": p.root_path},
            "admin": {"scope": "system", "note": "管理员工位：全部资源与配置"},
            "supervisor": ({"id": sup.id, "name": sup.name, "scope": "project",
                            "scope_projects": [p.id]}
                           if sup is not None else None),
            "workers": workers,
            "health": health,
            "addressing": "project_id → projects.owner_supervisor_id → 主管工位",
        }

    @r.get("/projects/{project_ref}/params-chain", dependencies=auth_deps)
    def params_chain(project_ref: str, window_id: str | None = None) -> dict:
        """把参数继承链摊开：每层写了什么、最后生效值、来源是哪层。"""
        cfg = getattr(svc, "app_config", None)
        if cfg is None:
            raise HTTPException(status_code=503, detail="配置不可用")
        p = svc.db.get_project(project_ref)
        if p is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：{project_ref}")
        w = svc.db.get_window(window_id) if window_id else None
        effective = cfg.effective_params(w, getattr(w, "llm_profile", "") or None, p)
        sources = cfg.param_sources(w, getattr(w, "llm_profile", "") or None, p)
        return {
            "project": {"id": p.id, "name": p.name, "params": p.params or {}},
            "window": ({"id": w.id, "name": w.name, "params": w.params or {},
                        "project_id": getattr(w, "project_id", "") or "",
                        "subdir": getattr(w, "subdir", "") or ""} if w else None),
            "effective": effective,
            "sources": sources,
            "order": ["window", "project", "profile", "global"],
        }

    return r


def _safe_roots(svc) -> list[str]:
    try:
        return list(svc.workspaces.list())
    except Exception:  # noqa: BLE001 — 列目录失败不影响项目管理
        return []
