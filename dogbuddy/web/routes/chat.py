# DogBuddyCn — admin chat routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""控制台 ↔ 管理员对话。

v2 口径（2026-10-14 用户拍板）：
* **只有一个管理员**，它是用户唯一的对接窗口（员工不直接找用户，逐级上报）
* **用户可以有多个对话**：每个对话是一份独立上下文，互不串味
  （就像"新建对话"：换一件事就开一个，避免把不相关的话题混在一条历史里）

`conversation` 参数是新名字；老客户端传 `sender` 仍然可用（同一个键空间）。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# 对话 id 的形状：只允许字母/数字/下划线/连字符，1..64 位。
# 注意 12 位 hex 的**工位 id 也会通过这个正则**——所以 DAO 层的
# 「裸 id 只在不是真实工位时才删」这道兜底不可省（审查报告 P0-3）。
_CONV_ID_RX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 系统保留频道：`admin:system` 是员工提问 / 危险命令确认 / 逐级上报的落点，
# 不是一个"用户对话"，绝不能被对话 API 读取、重命名、删除或清空。
_RESERVED_CONV_IDS = frozenset({"system"})


class ChatIn(BaseModel):
    text: str
    # 注意：这里**不能给默认值**，否则老客户端只传 sender 时会被默认值吞掉
    conversation: str | None = None
    sender: str | None = None       # 兼容老客户端（同一个键空间）


class ConversationIn(BaseModel):
    title: str = ""


class ConversationPatch(BaseModel):
    title: str


class ResetIn(BaseModel):
    conversation: str | None = None
    sender: str | None = None


def _validated_conv_id(cid: str) -> str:
    """对话 id 白名单校验（审查报告 P0-3）。

    初版 `_conv_id` 只做 `strip()`，于是对话 id 可以是任意字符串——
    包括一个**真实工位 id**，删除时就连带抹掉那个工位的全部历史。
    """
    cid = str(cid or "").strip()
    if not _CONV_ID_RX.match(cid):
        raise HTTPException(
            status_code=400,
            detail=f"非法对话 id：{cid!r}（只允许 1-64 位字母/数字/下划线/连字符）")
    if cid in _RESERVED_CONV_IDS:
        raise HTTPException(
            status_code=400, detail=f"{cid!r} 是系统保留频道，不能作为对话操作")
    return cid


def _conv_id(conversation: str | None, sender: str | None) -> str:
    """对话键：`conversation` 优先，其次兼容老字段 `sender`，都没有则默认对话。"""
    return _validated_conv_id(conversation or sender or "default")



def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["admin-chat"])

    def _llm_ready() -> bool:
        try:
            return bool(svc.config and svc.config.llm_configured())
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------ 对话管理
    @r.get("/admin/chat/conversations", dependencies=auth_deps)
    def list_conversations(include_archived: bool = False) -> dict:
        return {"conversations": svc.admin.list_conversations(include_archived),
                "llm_configured": _llm_ready(),
                "note": "只有一个管理员与用户对接；这里管理的是**对话**（各自独立上下文）。"}

    @r.post("/admin/chat/conversations", dependencies=auth_deps)
    def create_conversation(body: ConversationIn) -> dict:
        conv = svc.admin.create_conversation(body.title)
        return {"conversation": conv, "conversations": svc.admin.list_conversations()}

    @r.patch("/admin/chat/conversations/{conv_id}", dependencies=auth_deps)
    def rename_conversation(conv_id: str, body: ConversationPatch) -> dict:
        cid = _validated_conv_id(conv_id)
        try:
            conv = svc.admin.rename_conversation(cid, body.title)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"conversation": conv, "conversations": svc.admin.list_conversations()}

    @r.delete("/admin/chat/conversations/{conv_id}", dependencies=auth_deps)
    def delete_conversation(conv_id: str) -> dict:
        cid = _validated_conv_id(conv_id)
        if not svc.admin.delete_conversation(cid):
            raise HTTPException(status_code=404, detail=f"对话不存在：{cid}")
        svc.db.audit("web", "conversation:delete", cid)
        return {"deleted": cid, "conversations": svc.admin.list_conversations()}


    # ------------------------------------------------------------ 消息
    @r.get("/admin/chat/admins", dependencies=auth_deps)
    def list_admins() -> dict:
        """兼容旧接口：v2 只有一个管理员，这里返回已打开的对话键。"""
        return {"admins": svc.admin.active_admins(),
                "llm_configured": _llm_ready(),
                "single_admin": True}

    @r.get("/admin/chat/messages", dependencies=auth_deps)
    def messages(conversation: str | None = None, sender: str | None = None,
                 limit: int = 100) -> dict:
        limit = max(1, min(int(limit), 500))
        cid = _conv_id(conversation, sender)
        return {"conversation": cid, "sender": cid,
                "messages": svc.admin.history(cid, limit)}

    @r.get("/admin/chat/usage", dependencies=auth_deps)
    def chat_usage(conversation: str | None = None, sender: str | None = None) -> dict:
        """管理员的上下文用量（用户亲自报的"上下文一直显示 0"）。

        **不依赖 `windows` 表**：管理员会话挂在合成窗口 `admin:<cid>`，只存在于内存
        （`AdminService.session_for()`），走 `GET /api/windows/admin:<cid>/usage`
        必然 404 —— 这正是修复前的现状。
        """
        cid = _conv_id(conversation, sender)
        cfg = getattr(svc, "app_config", None)
        llm = getattr(cfg, "llm", None)
        try:
            max_tokens = int(getattr(llm, "max_context_tokens", 0) or 0)
        except (TypeError, ValueError):
            max_tokens = 0
        try:
            threshold = float(getattr(llm, "compact_threshold", 0.0) or 0.0)
        except (TypeError, ValueError):
            threshold = 0.0

        session = svc.admin.session_for(cid)
        used = 0
        try:
            used = int(session.context_tokens())
        except Exception:  # noqa: BLE001 — 用量条降级为 0，绝不 500
            pass
        spent = 0
        try:
            spent = int(svc.db.sum_tokens(f"admin:{cid}"))
        except Exception:  # noqa: BLE001
            pass
        ratio = (used / max_tokens) if max_tokens else 0.0
        # 管理员的模型 = 运行时全局配置（管理员没有工位，所以在"模型管理"页点「设为当前」
        # 就是给管理员换模型；工位不单独指定模型时继承它）
        cur_model = str(getattr(llm, "model", "") or "")
        cur_base = str(getattr(llm, "base_url", "") or "")
        cur_provider = str(getattr(llm, "provider", "") or "")
        return {"conversation": cid,
                "context_tokens": used,
                "max_context_tokens": max_tokens,
                "ratio": round(min(ratio, 1.0), 4),
                "threshold": threshold,
                "tokens": spent,
                "model": cur_model,
                "base_url": cur_base,
                "provider": cur_provider,
                "estimated": bool(getattr(session, "context_tokens_estimated", True))}

    @r.post("/admin/chat", dependencies=auth_deps)
    async def chat(body: ChatIn) -> dict:
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text 不能为空")
        cid = _conv_id(body.conversation, body.sender)
        if not _llm_ready():
            raise HTTPException(
                status_code=409,
                detail="LLM 尚未配置：请到「设置」填写 base_url / model / api_key 后再对话")
        reply = await svc.admin.handle_user_message(text, sender=cid)
        svc.db.audit("web", "admin:chat", f"conversation={cid} len={len(text)}")
        return {"conversation": cid, "sender": cid, "reply": reply,
                "messages": svc.admin.history(cid, 100),
                "conversations": svc.admin.list_conversations()}

    @r.post("/admin/chat/reset", dependencies=auth_deps)
    def reset(body: ResetIn | None = None, conversation: str | None = None) -> dict:
        sender = body.sender if body else None
        cid = _conv_id(conversation or (body.conversation if body else None), sender)
        dropped = svc.admin.reset(cid)
        svc.db.audit("web", "admin:reset", f"conversation={cid} dropped={dropped}")
        return {"conversation": cid, "dropped": dropped}

    return r
