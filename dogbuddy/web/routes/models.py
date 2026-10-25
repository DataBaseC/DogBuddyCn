# DogBuddyCn — model management routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""模型管理页 API（v0.5.2）。

`GET /api/models` 是页面数据源；`POST /api/models/{id}/activate` 是"换模型"的
唯一入口——它写运行时配置并落盘，所以刷新页面/重启进程后依然是新模型。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ModelJsonImportIn(BaseModel):
    """粘贴导入的入参（v0.10.57）。

    `text` 是给人用的：整段 JSON 原样贴进来即可（可以是导出文件本身，也可以只是
    一个数组）；`models` 是给程序用的。两者都给时以 `models` 为准。
    """
    mode: str = "merge"                     # merge（默认，安全）| replace（删清单外条目）
    text: str | None = None
    models: list[dict[str, Any]] | None = None


class ModelIn(BaseModel):
    id: str | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None           # 条目自带密钥（留空=沿用全局）；响应只回掩码
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
    api_key: str | None = None           # 留空或回传掩码 = 不改密钥


class ImportIn(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    # v0.10.21（owner 实测）：拉取**过多**是真的（SiliconFlow 一次 99 条）。
    # 允许先按关键词过滤、或只导入前端勾选的那些，别把整家目录一股脑灌进来。
    q: str = ""
    only: list[str] | None = None


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

    @r.get("/models/export", dependencies=auth_deps)
    def export_models() -> dict:
        """导出模型目录为可粘贴的 JSON。**不含任何明文密钥**（服务层白名单保证）。"""
        return _m().export_models()

    @r.post("/models/import-json", dependencies=auth_deps)
    def import_models_json(body: ModelJsonImportIn) -> dict:
        """粘贴导入（支持 `text` 原样粘 JSON，或直接给 `models` 数组）。

        与已有的 `POST /models/import`（从服务商**拉取**列表）是两件事，路径分开。
        """
        models = body.models
        if not models and body.text:
            try:
                parsed = json.loads(body.text)
            except Exception as e:  # noqa: BLE001 — 粘错格式要给人看得懂的报错
                raise HTTPException(status_code=400, detail=f"JSON 解析失败：{e}") from e
            if isinstance(parsed, dict):
                models = parsed.get("models") or []
            elif isinstance(parsed, list):
                models = parsed
            else:
                raise HTTPException(status_code=400, detail="JSON 里没有 models 数组")
        if not models:
            raise HTTPException(status_code=400, detail="没有可导入的模型（models 为空）")
        try:
            return _m().import_models(list(models), mode=body.mode)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @r.get("/models/options", dependencies=auth_deps)
    def options() -> dict:
        """给前端渲染下拉/标签用的枚举。"""
        from ...services.model_service import (CAPABILITY_TAGS, COST_LEVELS,
                                               PROVIDER_PRESETS, SPEED_LEVELS)
        return {"capabilities": list(CAPABILITY_TAGS),
                "speeds": list(SPEED_LEVELS), "costs": list(COST_LEVELS),
                "providers": [dict(p) for p in PROVIDER_PRESETS]}

    @r.post("/models", dependencies=auth_deps)
    def create_model(body: ModelIn) -> dict:
        """新增模型条目。**同名同端点已存在 → 409，不覆盖**（要改走 PATCH）。

        owner 实测："创建新模型还是会覆盖同名模型" —— 旧实现走 upsert 的更新语义，
        第二条把第一条的备注/密钥悄悄盖掉，返回 200 且没有任何提示。
        """
        m = _m()
        from ...services.model_service import ModelExistsError
        try:
            saved = m.create(**body.model_dump(exclude_none=True))
        except ModelExistsError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
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

    @r.post("/models/adopt-current", dependencies=auth_deps)
    def adopt_current() -> dict:
        """把正在运行的模型收进目录（页面上看不到它时的一键补救）。"""
        m = _m()
        try:
            saved = m.adopt_current()
        except (ValueError, RuntimeError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"model": saved, "models": m.catalog(), "summary": m.summary()}

    @r.get("/models/verify-current", dependencies=auth_deps)
    async def verify_current(probe: bool = False) -> dict:
        """当前模型真的在服务商列表里吗（拼错/下线会静默 400）。

        v0.10.21：`probe=1` 再发一个 1-token 真调用 —— "在列表里"不等于"能用"
        （owner 实测 ds4.1：列表里有，套餐不含，一调 403 permission_denied）。
        """
        return await _m().verify_current(probe=probe)

    @r.post("/models/import", dependencies=auth_deps)
    async def import_models(body: ImportIn) -> dict:
        """从 provider 拉取模型列表批量入库（已存在的保留用户备注与端点）。"""
        m = _m()
        try:
            result = await m.import_fetched(body.provider, body.base_url, body.api_key,
                                            only=body.only, q=body.q)
        except Exception as e:  # noqa: BLE001 — 拉取失败要给出人话
            from ...services.model_service import fetch_error_hint
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：{e}{fetch_error_hint(e)}") from e
        return {"result": result, "models": m.catalog(), "summary": m.summary()}

    return r
