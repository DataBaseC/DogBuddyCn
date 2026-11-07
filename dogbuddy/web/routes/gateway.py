# DogBuddyCn — gateway status route (L1 Interface, v0.10.50)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""`GET /api/gateway/status` —— 控制台「网关状态」卡片的数据源。

owner 2026-10-22 的决定：**QQ 只接管理员**（不做 QQ→工位绑定）。既然如此，
"网关到底通没通"就必须在控制台一眼可见——以前只能翻 `/var/lib/dogbuddy/logs`
或面板的 HTTP 访问日志，而且掉线没有任何提示。

只回状态，**绝不回 access_token**（`token_required` 只表示"设没设"）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["gateway"])

    @r.get("/gateway/status", dependencies=auth_deps)
    def status() -> dict[str, Any]:
        cfg = getattr(svc, "app_config", None)
        gw = getattr(cfg, "gateway", None)
        enabled = bool(getattr(gw, "enabled", False))
        whitelist = list(getattr(gw, "whitelist", []) or [])
        kr = getattr(svc, "router", None)
        qq_routed = bool(kr is not None
                         and getattr(kr, "mode", "off") != "off"
                         and kr.enabled_for("qq:private:0"))
        base = {
            "enabled": enabled,
            "host": getattr(gw, "host", ""),
            "port": getattr(gw, "port", None),
            "whitelist_size": len(whitelist),
            # 员工不直连 QQ：聊天仍进管理员；显式 /task 才进 KernelRouter。
            "routing": "kernel+admin" if qq_routed else "admin_only",
            "kernel_route": (getattr(kr, "mode", "off") if kr is not None else "off"),
            "qq_router": qq_routed,
            "note": ("QQ 聊天走管理员；开启 kernel route 且会话命中 qq:* 时，"
                     "/task 等命令复用 WorkItem/dispatch_run。员工不直连 QQ。"),
        }
        adapter = getattr(svc, "adapter", None)
        if adapter is None or not enabled:
            base.update({"listening": False, "clients": 0,
                         "hint": ("网关未启用：把 config.yaml 的 gateway.enabled 设为 true "
                                  "并配好 access_token，再让 NapCat 反向连 ws://<host>:<port>/")})
            return base
        st = adapter.status() if hasattr(adapter, "status") else {}
        base.update(st)
        base["ok"] = bool(st.get("listening"))
        if not st.get("clients"):
            base["hint"] = "网关在监听，但还没有 OneBot 客户端连上来（NapCat 侧看「网络配置」是否已连接）"
        return base

    return r
