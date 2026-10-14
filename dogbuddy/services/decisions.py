# DogBuddyCn — pending-decision service (L2 Application, ROADMAP T2.3)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""The durable "questions awaiting the owner" queue.

Before this existed, `ask_user` blocked an agent on a plain callback: if no
admin session happened to be open at that exact moment, the question died with
the session and the agent hung forever. Now every question lands in the
`pending_decisions` table (survives restarts), emits a bus event (so the SSE
stream and notifiers can push it), and the agent polls the row for the answer
with a timeout — the whole point of "股东模式: decisions come to the owner,
the owner does not sit and watch".

Timeout policy: a question unanswered past `default_timeout` flips to
`timeout`; the waiting agent receives a clear "[timeout]" answer and continues
autonomously (runs in AUTO mode) or reports back (PLAN mode) — it never hangs.
A newer `ask_user` from the same window supersedes older open ones (an agent
asking again has moved on).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..events.bus import Event
from ..ports import PendingDecision

logger = logging.getLogger("dogbuddy.services.decisions")

# well-known event types (kept next to the service that owns them)
DECISION_CREATED = "decision.created"
DECISION_ANSWERED = "decision.answered"
DECISION_TIMEOUT = "decision.timeout"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DecisionService:
    def __init__(self, db: Any, bus: Any = None,
                 default_timeout: float = 12 * 3600.0,
                 poll_interval: float = 2.0):
        self.db = db
        self.bus = bus
        self.default_timeout = default_timeout
        self.poll_interval = poll_interval

    # ------------------------------------------------------------- ask
    def ask(self, window_id: str, question: str, options: list | None = None,
            timeout: float | None = None, source: str = "ask_user",
            supersede: bool = True) -> PendingDecision:
        """Record a new question; optionally supersede this window's older open ones.

        `supersede=False`（审批升级用）：升级给**主管**的待决事项不能把主管自己
        正在问的问题顶掉——那是两件不同的事，只是恰好挂在同一个工位上
        （审查报告 P1-6）。
        """
        if supersede:
            cancelled = self.db.cancel_pending_decisions(window_id)
            if cancelled:
                logger.info("window %s: superseded %d open decision(s)", window_id, cancelled)
        ttl = timeout if timeout is not None else self.default_timeout
        expires_at = (_now() + timedelta(seconds=ttl)).isoformat() if ttl > 0 else None
        d = self.db.add_pending_decision(window_id, question, options=options,
                                         source=source, expires_at=expires_at)
        self._emit(DECISION_CREATED, {
            "id": d.id, "window_id": window_id, "question": question,
            "options": options or [], "expires_at": expires_at})
        return d


    async def wait(self, decision_id: int) -> str:
        """Block (async) until the decision is answered, times out or is cancelled.

        Returns the owner's answer, or a bracketed status string so the agent
        can tell "no answer" apart from an actual answer.
        """
        while True:
            d = self.db.get_pending_decision(decision_id)
            if d is None:
                return "[cancelled] the question no longer exists"
            if d.status == "answered":
                return d.answer or "(empty answer)"
            if d.status == "cancelled":
                return "[cancelled] superseded by a newer question from this window"
            if d.status == "timeout":
                return "[timeout] the owner did not answer in time"
            if d.expires_at and d.expires_at < _now().isoformat():
                self.db.answer_pending_decision(decision_id, "", status="timeout")
                self._emit(DECISION_TIMEOUT, {"id": decision_id,
                                              "window_id": d.window_id,
                                              "question": d.question})
                return "[timeout] the owner did not answer in time"
            await asyncio.sleep(self.poll_interval)

    # ------------------------------------------------------------ answer
    def answer(self, decision_id: int, answer: str) -> PendingDecision | None:
        d = self.db.get_pending_decision(decision_id)
        if d is None or d.status != "pending":
            return None
        out = self.db.answer_pending_decision(decision_id, answer, status="answered")
        self._emit(DECISION_ANSWERED, {
            "id": decision_id, "window_id": d.window_id,
            "question": d.question, "answer": answer})
        return out

    def cancel(self, decision_id: int) -> bool:
        d = self.db.get_pending_decision(decision_id)
        if d is None or d.status != "pending":
            return False
        self.db.answer_pending_decision(decision_id, "", status="cancelled")
        return True

    def list(self, status: str | None = "pending", limit: int = 100) -> list[PendingDecision]:
        return self.db.list_pending_decisions(status, limit)

    # ------------------------------------------------------------- sweep
    def sweep(self) -> list[PendingDecision]:
        """Flip overdue questions to timeout and announce each.

        Run periodically (see `start_sweeper`); waiting agents also detect
        expiry on their own — this is the durable backstop for agents that
        crashed mid-wait.
        """
        expired = self.db.expire_pending_decisions()
        for d in expired:
            self._emit(DECISION_TIMEOUT, {"id": d.id, "window_id": d.window_id,
                                          "question": d.question})
        return expired

    def start_sweeper(self, interval: float = 60.0) -> asyncio.Task:
        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval)
                try:
                    self.sweep()
                except Exception:  # noqa: BLE001 — a sweep failure never kills the loop
                    logger.exception("decision sweep failed")
        return asyncio.get_event_loop().create_task(_loop())

    def _emit(self, event_type: str, payload: dict) -> None:
        if self.bus is not None:
            try:
                self.bus.emit(Event(type=event_type, payload=payload))
            except Exception:  # noqa: BLE001 — events never break asking
                logger.warning("decision event emit failed", exc_info=True)
