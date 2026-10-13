# DogBuddyCn — auth routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Login: password or static token → X-Auth-Token for subsequent calls."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class LoginIn(BaseModel):
    password: str | None = None
    token: str | None = None


def router(svc) -> APIRouter:
    r = APIRouter(tags=["auth"])

    @r.post("/auth/login")
    def login(body: LoginIn) -> dict:
        web = svc.app_config.web
        expected_token = web.auth_token or web.password
        if body.token and expected_token and body.token == expected_token:
            return {"token": body.token}
        if body.password and web.password and body.password == web.password:
            return {"token": expected_token}
        if not expected_token:  # open mode (no auth configured) — local use
            return {"token": ""}
        raise HTTPException(status_code=401, detail="bad credentials")

    return r
