# DogBuddyCn — 运营站点状态（0.15.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""站点状态。站点本体是静态文件（`/news/`），这里只给"站还活着吗"。

刻意**不提供匿名刷新接口**：刷新会花模型钱，必须走运营回合（预算闸门）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["news"])

    @r.get("/news/status", dependencies=auth_deps)
    def status() -> dict[str, Any]:
        news = getattr(svc, "news", None)
        if news is None:
            raise HTTPException(status_code=503, detail="站点服务未启用")
        return news.status()

    return r
