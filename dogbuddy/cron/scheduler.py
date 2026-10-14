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

from ..events.bus import JOB_COMPLETED, JOB_FAILED, JOB_QUIET, RATE_LIMIT_STATUS, Event
from .jobs import JobStore

logger = logging.getLogger("dogbuddy.cron.scheduler")


@dataclass
class JobOutcome:
    #: ok | failed | rate_limited | timeout
    #: `timeout` 是 P2-7 补的：本轮等待超时但**任务仍在后台跑**，不是失败。
    status: str
    summary: str = ""
    log_path: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    # T8.3 自调度：本次任务自决的下次运行时间（ISO）；None = 用 job 默认 cron
    next_run_at: str | None = None
    # T8.2 条件通知：任务主动要求通知管理员
    notify: bool = False
    notify_text: str = ""
    notify_level: str = "info"


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
        """Run every due job (once each). Returns number dispatched.

        P1-13：先拿跨进程单例锁——`_inflight` 只在进程内有效，自更新期间新旧进程
        会同时 tick 同一批任务，同一个任务被跑两遍。
        """
        repo = getattr(self.store, "repo", None)
        if repo is not None and hasattr(repo, "try_cron_lock"):
            try:
                if not repo.try_cron_lock():
                    logger.debug("cron tick 跳过：另一个进程持有 cron 锁")
                    return 0
            except Exception:  # noqa: BLE001 — 锁出问题不能停掉调度
                logger.warning("cron lock check failed", exc_info=True)
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
            # T8.3 自调度：任务自决的下次运行时间覆盖默认 cron
            self.store.mark_success(job, getattr(outcome, "next_run_at", None))
            self.store.repo.finish_run(run.id, "ok", outcome.summary, outcome.log_path)
            strategy = (getattr(job, "report_strategy", "") or "")
            notified = bool(getattr(outcome, "notify", False))
            # T8.2 条件任务："有事才通知"——conditional 且没调 notify_owner 就静默，
            # 砍掉无用通知（跑动记录仍留在 job_runs 里，控制台查得到）
            if strategy == "conditional" and not notified:
                logger.info("job %s 完成但无事可报（conditional），静默记录", job.id)
                self._emit(JOB_QUIET, {"job_id": job.id, "window_id": job.window_id,
                                       "name": job.name, "summary": outcome.summary,
                                       "reason": "conditional: nothing to report"})
            else:
                summary = outcome.summary
                if strategy == "conditional" and notified:
                    # 有条件通知时，报告用**工位写给人的那段话**，而不是整段执行输出
                    summary = (getattr(outcome, "notify_text", "") or outcome.summary)
                self._emit(JOB_COMPLETED, {"job_id": job.id, "window_id": job.window_id,
                                           "name": job.name, "summary": summary,
                                           "notify": notified,
                                           "notify_level": getattr(outcome, "notify_level", "info"),
                                           "report_strategy": job.report_strategy,
                                           "next_run_at": getattr(job, "next_run_at", None),
                                           "report_channel": getattr(job, "report_channel", "") or ""})
        elif outcome.status == "rate_limited":
            # rate-limit waiting is NOT a failure (M-H counting semantics)
            self.store.mark_rate_limited(job)
            self.store.repo.finish_run(run.id, "rate_limited", outcome.summary, outcome.log_path)
            self._emit(RATE_LIMIT_STATUS, {"job_id": job.id, "window_id": job.window_id,
                                           "summary": outcome.summary})
        elif outcome.status == "timeout":
            # P2-7：等待超时**不是失败**——任务还在后台跑，只是本轮不再等它。
            # 初版把它当 failed → 30 分钟以上的长任务连续 3 轮被自动暂停并通知
            # "失败 3 次"，而任务其实成功了。这里不计连败、不暂停、不报失败。
            self.store.mark_timeout(job)
            self.store.repo.finish_run(run.id, "timeout", outcome.summary, outcome.log_path)
            logger.warning("任务 %s 本轮等待超时（不计失败）：%s", job.id, outcome.summary)
            self._emit(JOB_QUIET, {"job_id": job.id, "window_id": job.window_id,
                                   "name": job.name, "summary": outcome.summary,
                                   "reason": "run still in progress past the wait window"})
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
