# DogBuddyCn — review records API (0.25.1)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class OverrideIn(BaseModel):
    reason: str = ""
    actor: str = "owner"


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["reviews"])

    def _svc() -> Any:
        review = getattr(svc, "review", None)
        if review is None:
            raise HTTPException(status_code=503, detail="Review 未启用")
        return review

    @r.get("/reviews", dependencies=auth_deps)
    def list_reviews(status: str = "", limit: int = 50) -> dict:
        review = _svc()
        rows = review.store.list_review_requests(limit=limit, status=status)
        return {"reviews": rows,
                "summary": {"total": review.store.count_reviews(),
                            "waiting": review.store.count_reviews("waiting_human")}}

    @r.get("/reviews/{review_id}", dependencies=auth_deps)
    def get_review(review_id: str) -> dict:
        review = _svc()
        row = review.store.get_review_request(review_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"没有这个审核请求：{review_id}")
        return {"review": row,
                "findings": review.store.list_review_findings(review_id),
                "work_item": review.store.get_work_item(
                    str(row.get("work_item_id") or ""))}

    @r.post("/reviews/{review_id}/override", dependencies=auth_deps)
    def override(review_id: str, body: OverrideIn) -> dict:
        try:
            row = _svc().override(review_id, actor=body.actor or "owner",
                                  reason=body.reason)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"ok": True, "review": row}

    return r
