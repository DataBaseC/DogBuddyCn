# DogBuddyCn — progress watchdog (v0.20.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Detect "alive but not progressing" runs without touching their mandate.

This is deliberately read-only: it audits + emits `run.stalled`; it never kills,
retries or expands permissions. The owner/admin decides what to do next.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("dogbuddy.services.watchdog")


class Watchdog:
    def __init__(self, runs: Any, *, bus: Any = None, db: Any = None,
                 interval: float = 60.0, stalled_after: float = 300.0):
        self.runs = runs
        self.bus = bus
        self.db = db
        self.interval = max(30.0, float(interval))
        self.stalled_after = max(60.0, float(stalled_after))
        self._alerted: set[str] = set()
        self._last_stalled = 0

    def tick(self) -> list[dict[str, Any]]:
        if self.runs is None:
            return []
        rows = self.runs.stalled(self.stalled_after)
        ids = {str(r.get("id") or "") for r in rows}
        # 已恢复的行从告警集合里移除，下次再卡住还能再报
        self._alerted.intersection_update(ids)
        fresh = [r for r in rows if str(r.get("id") or "") not in self._alerted]
        self._last_stalled = len(rows)
        for r in fresh:
            rid = str(r.get("id") or "")
            self._alerted.add(rid)
            payload = {"run_id": rid, "window_id": r.get("window_id", ""),
                       "status": r.get("status", ""), "last_tool": r.get("last_tool", ""),
                       "progress_at": r.get("progress_at", ""),
                       "started_at": r.get("started_at", ""),
                       "stalled_after_s": self.stalled_after}
            try:
                if self.bus is not None:
                    from ..events.bus import Event
                    self.bus.emit(Event(type="run.stalled", payload=payload))
            except Exception:  # noqa: BLE001
                logger.debug("watchdog emit failed", exc_info=True)
            try:
                if self.db is not None:
                    self.db.audit("system", "watchdog:stalled",
                                  f"{rid} window={payload['window_id']} "
                                  f"last_tool={payload['last_tool']} "
                                  f"progress_at={payload['progress_at']}")
            except Exception:  # noqa: BLE001
                logger.debug("watchdog audit failed", exc_info=True)
            logger.warning("run stalled: %s window=%s last_tool=%s",
                           rid, payload["window_id"], payload["last_tool"])
        return fresh

    async def run_loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — 监控失败不能拖垮主进程
                logger.warning("watchdog tick failed", exc_info=True)
            await asyncio.sleep(self.interval)

    def health(self) -> dict[str, Any]:
        return {"stalled": self._last_stalled, "alerted": len(self._alerted),
                "interval_s": self.interval, "stalled_after_s": self.stalled_after}
