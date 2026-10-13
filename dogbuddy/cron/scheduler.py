# DogBuddyCn — cron tick scheduler (L2 Application)
# Tick loop & due-job execution derived from Hermes Agent cron/scheduler.py
# (https://github.com/NousResearch/hermes-agent) — Copyright (c) NousResearch and
# contributors — MIT License.
# Modifications (2026-10-12, DogBuddyCn contributors):
#   - trimmed to the 60s tick + due-job dispatch core (Hermes incident/fallback
#     machinery, gateway code-skew detection etc. intentionally not carried over)
#   - execution injects into the persistent window session via an injected
#     run_job callback; rate-limit waits never count as task failures
"""60s tick: due jobs → injected runner → job lifecycle updates + events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..events.bus import JOB_COMPLETED, JOB_FAILED, RATE_LIMIT_STATUS, Event
from .jobs import JobStore

logger = logging.getLogger("dogbuddy.cron.scheduler")


@dataclass
class JobOutcome:
    status: str  # ok | failed | rate_limited
    summary: str = ""
    log_path: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


class CronScheduler:
    def __init__(
        self,
        store: JobStore,
        run_job: Callable[[Any], Awaitable[JobOutcome]],
        bus: Any = None,
        tick_seconds: int = 60,
    ):
        self.store = store
        self.run_job = run_job
        self.bus = bus
        self.tick_seconds = tick_seconds
        self._inflight: set[str] = set()
        self._stopped = False

    # ------------------------------------------------------------- runtime
    async def run_loop(self) -> None:
        while not self._stopped:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — a broken tick must not kill the loop
                logger.exception("cron tick failed")
            await asyncio.sleep(self.tick_seconds)

    def stop(self) -> None:
        self._stopped = True

    # --------------------------------------------------------------- tick
    async def tick(self) -> int:
        """Run every due job (once each). Returns number dispatched."""
        jobs = self.store.due()
        dispatched = 0
        for job in jobs:
            if job.id in self._inflight:
                continue
            self._inflight.add(job.id)
            dispatched += 1
            asyncio.get_event_loop().create_task(self._guarded_execute(job))
        return dispatched

    async def _guarded_execute(self, job: Any) -> None:
        try:
            await self._execute(job)
        finally:
            self._inflight.discard(job.id)

    async def _execute(self, job: Any) -> JobOutcome:
        logger.info("cron firing job %s (%s) -> window %s", job.id, job.name, job.window_id)
        run = self.store.repo.start_run(job.id, job.window_id)
        try:
            outcome = await self.run_job(job)
        except Exception as e:  # noqa: BLE001 — unexpected crash counts as one failure
            logger.exception("job %s crashed", job.id)
            outcome = JobOutcome(status="failed", summary=str(e))

        if outcome.status == "ok":
            self.store.mark_success(job)
            self.store.repo.finish_run(run.id, "ok", outcome.summary, outcome.log_path)
            self._emit(JOB_COMPLETED, {"job_id": job.id, "window_id": job.window_id,
                                       "name": job.name, "summary": outcome.summary,
                                       "report_strategy": job.report_strategy,
                                       "report_channel": getattr(job, "report_channel", "") or ""})
        elif outcome.status == "rate_limited":
            # rate-limit waiting is NOT a failure (M-H counting semantics)
            self.store.mark_rate_limited(job)
            self.store.repo.finish_run(run.id, "rate_limited", outcome.summary, outcome.log_path)
            self._emit(RATE_LIMIT_STATUS, {"job_id": job.id, "window_id": job.window_id,
                                           "summary": outcome.summary})
        else:
            fails, auto_paused = self.store.mark_failure(job)
            self.store.repo.finish_run(run.id, "failed", outcome.summary, outcome.log_path)
            self._emit(JOB_FAILED, {"job_id": job.id, "window_id": job.window_id,
                                    "name": job.name, "summary": outcome.summary,
                                    "fail_count": fails, "auto_paused": auto_paused})
        return outcome

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.bus is not None:
            self.bus.emit(Event(type=event_type, payload=payload))
