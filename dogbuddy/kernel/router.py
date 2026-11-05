# DogBuddyCn — Kernel Router (Fast Path shadow, v0.23.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Classify Web messages into chat/control/task candidates without executing.

0.23.0 is deliberately **shadow-only**: it writes router_decisions and (for
explicit `/task`) a WorkItem, but the old AdminService still answers the turn.
No LLM call, no dispatch, no user-visible behaviour change.
"""
from __future__ import annotations

import logging
from typing import Any

from .domain import RouterDecision

logger = logging.getLogger("dogbuddy.kernel.router")


class KernelRouter:
    MODES = ("off", "shadow", "explicit", "auto")

    def __init__(self, store: Any, intake: Any = None, mode: str = "off"):
        self.store = store
        self.intake = intake
        self.mode = str(mode or "off").lower()
        if self.mode not in self.MODES:
            self.mode = "off"

    def route_web(self, *, conversation: str, text: str,
                  sender: str = "owner") -> dict[str, Any] | None:
        # 0.23.x 只做 shadow；explicit/auto 尚未接管，先按 shadow 记录。
        if self.mode == "off" or self.store is None or self.intake is None:
            return None
        trigger = self.intake.accept_web(
            conversation=conversation, text=text, sender=sender)
        if not trigger:
            return None
        return self.route_trigger(trigger, text=text, sender=sender)

    def route_trigger(self, trigger: dict[str, Any], *, text: str,
                      sender: str = "owner") -> dict[str, Any]:
        trigger_id = str(trigger.get("id") or "")
        thread_id = str(trigger.get("thread_id") or "")
        existing = self.store.get_router_decision(trigger_id)
        if existing is not None:
            return existing

        decision = self.decide(text=text, thread_id=thread_id, sender=sender)
        if decision.action == "create":
            wi = self.store.create_work_item(
                thread_id=thread_id, title=decision.title or decision.prompt[:80],
                description=decision.prompt, created_by=sender, origin="router")
            decision.work_item_id = str(wi.get("id") or "")
            self.store.subscribe(work_item_id=decision.work_item_id,
                                 thread_id=thread_id, created_by=sender)
        rec, _created = self.store.record_router_decision(
            trigger_id=trigger_id, thread_id=thread_id, action=decision.action,
            decision=decision.__dict__, reason=decision.reason,
            work_item_id=decision.work_item_id, window_id=decision.window_id)
        return rec

    def decide(self, *, text: str, thread_id: str, sender: str = "owner") -> RouterDecision:
        body = str(text or "").strip()
        if not body:
            return RouterDecision(action="ignore", reason="empty message")
        low = body.lower()
        if low in ("/cancel", "/stop") or low.startswith(("/cancel ", "/stop ")):
            return RouterDecision(action="cancel", reason="explicit cancel command")
        if low == "/status" or low.startswith(("/status ", "/runs")):
            return RouterDecision(action="status", reason="explicit status command")
        if body.startswith("/task"):
            prompt = body[len("/task"):].strip()
            if not prompt:
                return RouterDecision(action="ask", reason="/task without a goal",
                                      prompt="请补充任务目标。")
            return RouterDecision(action="create", reason="explicit /task command",
                                  prompt=prompt, title=prompt[:80],
                                  mandate_scope="project", confidence=1.0)
        if body.startswith("/continue"):
            target = body[len("/continue"):].strip()
            wi = (self.store.get_work_item(target) if target
                  else self.store.find_open_work_item(thread_id, include_shadow=False))
            if wi is None or str(wi.get("origin") or "") == "shadow":
                return RouterDecision(action="ask", reason="no open non-shadow WorkItem")
            return RouterDecision(action="resume", reason="explicit /continue command",
                                  work_item_id=str(wi.get("id") or ""),
                                  title=str(wi.get("title") or ""), confidence=1.0)
        # 自然语言暂不建任务；explicit 阶段也只认命令。
        return RouterDecision(action="chat", reason="default chat path", confidence=0.5)

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode,
                "decisions": (self.store.count_router_decisions()
                              if self.store is not None else 0),
                "shadow_only": True}
