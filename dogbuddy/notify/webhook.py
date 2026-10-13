# DogBuddyCn — webhook notifier channel (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Webhook channel: POST {"text": ..., "source": "dogbuddy"} to a URL.

Failures (network errors, HTTP errors, timeouts) are logged as warnings and
never propagate — one dead webhook must not break report delivery.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("dogbuddy.notify")

TIMEOUT_SECONDS = 10.0


class WebhookNotifier:
    def __init__(self, name: str, url: str, timeout: float = TIMEOUT_SECONDS) -> None:
        self.name = name
        self.url = url
        self.timeout = timeout

    async def send(self, text: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    self.url, json={"text": text, "source": "dogbuddy"})
            if getattr(resp, "status_code", 200) >= 400:
                logger.warning("webhook %s returned HTTP %s", self.name, resp.status_code)
        except Exception:  # noqa: BLE001 — delivery failure must not propagate
            logger.warning("webhook %s send failed", self.name, exc_info=True)
