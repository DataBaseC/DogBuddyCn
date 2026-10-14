# DogBuddyCn — model management routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""模型管理页 API（v0.5.2）。

`GET /api/models` 是页面数据源；`POST /api/models/{id}/activate` 是"换模型"的
唯一入口——它写运行时配置并落盘，所以刷新页面/重启进程后依然是新模型。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ModelIn(BaseModel):
    id: str | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    display_name: str | None = None
    note: str | None = None            # 能力与限制备注（核心）
    capability: str | None = None
    context_window: int | None = None
    speed: str | None = None
    cost: str | None = None
    enabled: bool | None = None
    source: str | None = None


class ModelPatch(BaseModel):
    display_name: str | None = None
    note: str | None = None
    capability: str | None = None
    context_window: int | None = None
    speed: str | None = None
    cost: str | None = None
    enabled: bool | None = None
    base_url: str | None = None
    provider: str | None = None
    model: str | None = None


class ImportIn(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["models"])

    def _m():
        models = getattr(svc, "models", None)
        if models is None:
            raise HTTPException(status_code=503, detail="模型目录服务未启用")
        return models

    @r.get("/models", dependencies=auth_deps)
    def list_models(enabled_only: bool = False) -> dict:
        m = _m()
        return {"models": m.catalog(enabled_only=enabled_only),
                "current": m.current(),
                "summary": m.summary()}

    @r.get("/models/options", dependencies=auth_deps)
    def options() -> dict:
        """给前端渲染下拉/标签用的枚举。"""
        from ...services.model_service import CAPABILITY_TAGS, COST_LEVELS, SPEED_LEVELS
        return {"capabilities": list(CAPABILITY_TAGS),
                "speeds": list(SPEED_LEVELS), "costs": list(COST_LEVELS)}

    @r.post("/models", dependencies=auth_deps)
    def create_model(body: ModelIn) -> dict:
        m = _m()
        try:
            saved = m.upsert(**body.model_dump(exclude_none=True))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"model": saved, "models": m.catalog()}

    @r.patch("/models/{model_ref}", dependencies=auth_deps)
    def patch_model(model_ref: str, body: ModelPatch) -> dict:
        m = _m()
        try:
            saved = m.update(model_ref, **body.model_dump(exclude_none=True))
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"model": saved, "models": m.catalog()}

    @r.delete("/models/{model_ref}", dependencies=auth_deps)
    def delete_model(model_ref: str) -> dict:
        m = _m()
        if not m.remove(model_ref):
            raise HTTPException(status_code=404, detail=f"模型不存在：{model_ref}")
        return {"deleted": model_ref, "models": m.catalog()}

    @r.post("/models/{model_ref}/activate", dependencies=auth_deps)
    def activate(model_ref: str) -> dict:
        """换模型：写运行时配置并落盘（这是页面"设为当前"按钮的落地动作）。"""
        m = _m()
        try:
            result = m.activate(model_ref)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except (ValueError, RuntimeError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"result": result, "current": m.current()}

    @r.post("/models/import", dependencies=auth_deps)
    async def import_models(body: ImportIn) -> dict:
        """从 provider 拉取模型列表批量入库（已存在的保留用户备注）。"""
        m = _m()
        try:
            result = await m.import_fetched(body.provider, body.base_url, body.api_key)
        except Exception as e:  # noqa: BLE001 — 拉取失败要给出人话
            raise HTTPException(status_code=502, detail=f"拉取模型列表失败：{e}") from e
        return {"result": result, "models": m.catalog(), "summary": m.summary()}

    return r
