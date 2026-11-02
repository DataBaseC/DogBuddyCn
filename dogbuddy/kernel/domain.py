# DogBuddyCn — kernel value objects (v0.21.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Stable handles for the one execution entry point.

The runner still lives in WindowManager; this module only gives callers a
RunHandle instead of raw run ids / fire-and-forget side effects.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunRequest:
    prompt: str
    window_id: str
    origin: str = "manual"          # manual | cron | group | member | retry
    kind: str = "prompt"
    urgent: bool = True
    job_id: str | None = None
    llm_profile: str = ""
    sender: str = ""
    work_item_id: str = ""
    parent_run_id: str = ""
    dedupe_key: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunHandle:
    run_id: str
    work_item_id: str = ""
    windows: Any = None
    runs: Any = None

    def status(self) -> dict[str, Any]:
        if self.runs is not None:
            run = self.runs.get(self.run_id)
            if run is not None:
                return run.as_dict() if hasattr(run, "as_dict") else dict(run)
        return {"id": self.run_id, "status": "unknown"}

    def cancel(self) -> dict[str, Any]:
        if self.windows is None:
            return {"ok": False, "reason": "window manager unavailable"}
        return self.windows.cancel_run(self.run_id)

    async def wait(self, timeout: float = 1800.0, poll: float = 0.25) -> dict[str, Any]:
        """Wait for a terminal status; never blocks the event loop."""
        deadline = asyncio.get_event_loop().time() + max(1.0, float(timeout))
        while asyncio.get_event_loop().time() < deadline:
            st = self.status()
            if st.get("status") in ("done", "partial", "failed", "cancelled",
                                    "timeout", "interrupted"):
                return st
            await asyncio.sleep(max(0.05, float(poll)))
        st = self.status()
        st["wait_timeout"] = True
        return st
