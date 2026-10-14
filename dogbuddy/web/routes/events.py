# DogBuddyCn — SSE stream routes (L1 Interface, ROADMAP T2.1)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""GET /api/events — the console's live feed (Server-Sent Events).

Browser `EventSource` cannot set request headers, so this one endpoint also
accepts `?token=` (same value as the X-Auth-Token header used elsewhere).
Frames: `data: {"type":..., "payload":..., "created_at":...}\n\n`,
a `retry: 5000` hint on connect and `: ping` comments every ~15s so proxies
keep the connection warm.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

_PING_SECONDS = 15.0


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["events"])

    @r.get("/events")
    async def events(token: str = "") -> StreamingResponse:
        expected = (getattr(svc.app_config.web, "auth_token", "")
                    or getattr(svc.app_config.web, "password", ""))
        if expected and token != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

        broker = svc.sse

        async def stream():
            q = broker.open()
            try:
                yield "retry: 5000\n\n"
                hello = {"type": "hello", "payload": {"clients": broker.client_count}}
                yield f"data: {json.dumps(hello, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        frame = await asyncio.wait_for(q.get(), timeout=_PING_SECONDS)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"
            finally:
                broker.close(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    return r
