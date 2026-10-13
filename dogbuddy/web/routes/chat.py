# DogBuddyCn — admin chat routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Console ↔ admin agent conversation.

Before this module the ONLY way to talk to the admin agent was the QQ gateway:
`AdminService.handle_user_message` was called from `gateway/router.py` and from
nowhere else, so the web console could create windows and jobs but could not ask
the admin anything. These routes close that gap.

Handlers only validate params → call `svc.admin` → return DTOs (no business).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ChatIn(BaseModel):
    text: str
    sender: str = "default"   # one persistent admin conversation per sender


class SenderIn(BaseModel):
    sender: str = "default"


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["admin-chat"])

    def _llm_ready() -> bool:
        try:
            return bool(svc.config and svc.config.llm_configured())
        except Exception:  # noqa: BLE001
            return False

    @r.get("/admin/chat/admins", dependencies=auth_deps)
    def list_admins() -> dict:
        return {"admins": svc.admin.active_admins(),
                "llm_configured": _llm_ready()}

    @r.get("/admin/chat/messages", dependencies=auth_deps)
    def messages(sender: str = "default", limit: int = 100) -> dict:
        limit = max(1, min(int(limit), 500))
        return {"sender": sender, "messages": svc.admin.history(sender, limit)}

    @r.post("/admin/chat", dependencies=auth_deps)
    async def chat(body: ChatIn) -> dict:
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text 不能为空")
        sender = (body.sender or "default").strip() or "default"
        if not _llm_ready():
            raise HTTPException(
                status_code=409,
                detail="LLM 尚未配置：请到「设置」填写 base_url / model / api_key 后再对话")
        reply = await svc.admin.handle_user_message(text, sender=sender)
        svc.db.audit("web", "admin:chat", f"sender={sender} len={len(text)}")
        return {"sender": sender, "reply": reply,
                "messages": svc.admin.history(sender, 100)}

    @r.post("/admin/chat/reset", dependencies=auth_deps)
    def reset(body: SenderIn) -> dict:
        sender = (body.sender or "default").strip() or "default"
        dropped = svc.admin.reset(sender)
        svc.db.audit("web", "admin:reset", f"sender={sender} dropped={dropped}")
        return {"sender": sender, "dropped": dropped}

    return r
