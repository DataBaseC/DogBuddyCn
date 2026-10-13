# DogBuddyCn — console notifier channel (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Console channel: writes the report to the application log. Never fails."""
from __future__ import annotations

import logging

logger = logging.getLogger("dogbuddy.notify")


class ConsoleNotifier:
    """Log-only channel (always available as the fallback output)."""

    def __init__(self, name: str = "console") -> None:
        self.name = name

    async def send(self, text: str) -> None:
        try:
            logger.info("report: %s", text[:300])
        except Exception:  # noqa: BLE001 — logging must never raise
            pass
