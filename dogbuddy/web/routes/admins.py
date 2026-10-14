# DogBuddyCn — admin accounts API (L1 Interface, Batch 9 · T9.5)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""多管理员实体化（ROADMAP T9.5「admins（多管理员实体化）」）。

两个层级：
  * `owner` —— 第一个管理员，拥有全部权限（配置文件里的 token 视作 owner）
  * `admin` —— 普通管理员

**当前口径（已在 ROADMAP 记录）**：暂不做"按域分管"，`role_level` 只区分
owner/admin；token 一律不回显（只存哈希）。配置文件 token 永远有效，
所以即使这里把记录删光也不会把自己锁在门外。
"""
from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

ROLE_LEVELS = ("owner", "admin")


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _dto(row: dict[str, Any]) -> dict[str, Any]:
    """**绝不回显 token**（只给"是否设过"的布尔）。"""
    return {
        "id": row.get("id", ""), "name": row.get("name", ""),
        "role_level": row.get("role_level", "admin"),
        "note": row.get("note", ""),
        "enabled": bool(row.get("enabled", 1)),
        "has_token": bool(row.get("token_hash")),
        "created_at": row.get("created_at", ""), "updated_at": row.get("updated_at", ""),
    }


class AdminIn(BaseModel):
    name: str
    role_level: str = "admin"
    token: str = ""      # 明文只在创建/重置时传入，服务端只存哈希
    note: str = ""


class AdminPatch(BaseModel):
    name: str | None = None
    role_level: str | None = None
    token: str | None = None
    note: str | None = None
    enabled: bool | None = None


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["admins"])

    @r.get("/admins", dependencies=auth_deps)
    def list_admins() -> dict:
        return {"admins": [_dto(x) for x in svc.db.list_admins()],
                "role_levels": list(ROLE_LEVELS),
                "note": "配置文件里的 token 始终有效，等同 owner；这里的管理员是额外账号。"}

    @r.post("/admins", dependencies=auth_deps)
    def create_admin(body: AdminIn) -> dict:
        if body.role_level not in ROLE_LEVELS:
            raise HTTPException(status_code=400,
                                detail=f"未知 role_level：{body.role_level}（可选 {list(ROLE_LEVELS)}）")
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="name 不能为空")
        if svc.db.get_admin(body.name) is not None:
            raise HTTPException(status_code=400, detail=f"管理员已存在：{body.name}")
        row = svc.db.upsert_admin({"name": body.name.strip(), "role_level": body.role_level,
                                   "note": body.note, "enabled": True,
                                   "token_hash": _hash(body.token) if body.token else ""})
        svc.db.audit("web", "admin:create", f"{row.get('id')} {body.name} {body.role_level}")
        return {"admin": _dto(row), "admins": [_dto(x) for x in svc.db.list_admins()]}

    @r.patch("/admins/{admin_ref}", dependencies=auth_deps)
    def patch_admin(admin_ref: str, body: AdminPatch) -> dict:
        existing = svc.db.get_admin(admin_ref)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"管理员不存在：{admin_ref}")
        fields: dict[str, Any] = {}
        data = body.model_dump(exclude_none=True)
        if "role_level" in data and data["role_level"] not in ROLE_LEVELS:
            raise HTTPException(status_code=400, detail=f"未知 role_level：{data['role_level']}")
        if "token" in data:
            fields["token_hash"] = _hash(data.pop("token"))
        fields.update({k: v for k, v in data.items() if k in ("name", "role_level", "note", "enabled")})
        row = svc.db.upsert_admin({"id": existing["id"], **fields})
        svc.db.audit("web", "admin:update", f"{existing['id']} fields={sorted(fields)}")
        return {"admin": _dto(row), "admins": [_dto(x) for x in svc.db.list_admins()]}

    @r.delete("/admins/{admin_ref}", dependencies=auth_deps)
    def delete_admin(admin_ref: str) -> dict:
        existing = svc.db.get_admin(admin_ref)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"管理员不存在：{admin_ref}")
        svc.db.delete_admin(existing["id"])
        svc.db.audit("web", "admin:delete", f"{existing['id']} {existing.get('name')}")
        return {"deleted": existing["id"],
                "admins": [_dto(x) for x in svc.db.list_admins()]}

    return r
