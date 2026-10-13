# DogBuddyCn — logs & audit routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Audit log + job run history."""
from __future__ import annotations

from fastapi import APIRouter


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["logs"])

    @r.get("/logs/audit", dependencies=auth_deps)
    def audit(limit: int = 200) -> list[dict]:
        return [{"id": e.id, "actor": e.actor, "action": e.action,
                 "detail": e.detail, "created_at": e.created_at}
                for e in svc.db.list_audit(limit)]

    @r.get("/logs/runs", dependencies=auth_deps)
    def runs(limit: int = 100, window_id: str | None = None) -> list[dict]:
        rows = svc.jobs.runs(None, limit)
        if window_id:
            rows = [r for r in rows if r.get("window_id") == window_id]
        return rows

    return r
