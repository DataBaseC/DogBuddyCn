# DogBuddyCn — shadow intake (Web only in 0.19.x)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Source → Trigger/Thread, shadow-only.

The old admin path still executes the turn. This class only records what a
future Router would have seen; a failure here must never affect the reply.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


class ShadowIntake:
    def __init__(self, store: Any, enabled: bool = True):
        self.store = store
        self.enabled = bool(enabled)

    def accept_web(self, *, conversation: str, text: str, sender: str = "owner",
                   title: str = "") -> dict[str, Any] | None:
        if not self.enabled or self.store is None:
            return None
        body = str(text or "").strip()
        if not body:
            return None
        thread = self.store.ensure_thread(
            source="web", external_id=str(conversation or "default"),
            kind="web", title=title or f"Web · {conversation or 'default'}")
        # 同一秒内完全相同的文本视为重复投递；跨秒允许用户重复说同一句话。
        dedupe = f"web:{conversation}:{sender}:{_digest(body)}:{int(time.time())}"
        trigger, created = self.store.record_trigger(
            source="web", external_id=str(conversation or "default"),
            thread_id=str(thread.get("id") or ""), kind="message",
            payload={"text": body[:8000], "sender": sender},
            dedupe_key=dedupe, status="candidate")
        trigger["created"] = created
        if created:
            # Shadow 语义：只建对象，不改变旧路径执行。下一阶段 Router 可直接复用。
            wi = self.store.create_work_item(
                thread_id=str(thread.get("id") or ""), title=body[:80],
                description=body, created_by=sender)
            self.store.create_mandate(
                work_item_id=str(wi.get("id") or ""), scope="web",
                allowed_actions=["reply"], actor_id=sender)
            self.store.subscribe(work_item_id=str(wi.get("id") or ""),
                                 thread_id=str(thread.get("id") or ""),
                                 created_by=sender)
            trigger["work_item_id"] = wi.get("id")
        return trigger
