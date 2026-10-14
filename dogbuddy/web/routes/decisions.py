# DogBuddyCn — pending-decision routes (L1 Interface, ROADMAP T2.3)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""The owner's inbox of questions from agents (股东模式: decide, don't watch)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class DecisionAnswerIn(BaseModel):
    answer: str


def _dto(d) -> dict:
    return {"id": d.id, "window_id": d.window_id, "question": d.question,
            "options": d.options, "answer": d.answer, "status": d.status,
            "source": d.source, "created_at": d.created_at,
            "answered_at": d.answered_at, "expires_at": d.expires_at}


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["decisions"])

    @r.get("/decisions", dependencies=auth_deps)
    def list_decisions(status: str | None = "pending", limit: int = 100) -> dict:
        out = svc.decisions.list(status=status, limit=limit)
        return {"decisions": [_dto(d) for d in out],
                "pending_count": len(svc.decisions.list(status="pending", limit=1000))}

    @r.post("/decisions/{decision_id}/answer", dependencies=auth_deps)
    def answer(decision_id: int, body: DecisionAnswerIn) -> dict:
        d = svc.decisions.answer(decision_id, body.answer)
        if d is None:
            raise HTTPException(status_code=404,
                                detail="decision not found or already closed")
        return _dto(d)

    @r.post("/decisions/{decision_id}/cancel", dependencies=auth_deps)
    def cancel(decision_id: int) -> dict:
        if not svc.decisions.cancel(decision_id):
            raise HTTPException(status_code=404,
                                detail="decision not found or already closed")
        return {"cancelled": True, "id": decision_id}

    @r.post("/decisions/sweep", dependencies=auth_deps)
    def sweep() -> dict:
        expired = svc.decisions.sweep()
        return {"expired": [_dto(d) for d in expired]}

    return r
