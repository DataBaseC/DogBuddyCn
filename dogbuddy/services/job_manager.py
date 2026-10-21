# DogBuddyCn — job management service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""JobManager: CRUD over JobStore + run_now (head-of-queue dispatch)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..cron.jobs import JobStore
from ..ports import Job

logger = logging.getLogger("dogbuddy.services.jobs")

#: 后台群组运行要留引用（asyncio 只持弱引用，任务可能被 GC 掉）
_BACKGROUND: set[asyncio.Task] = set()


def _log_bg_error(task: asyncio.Task) -> None:
    """后台"立即执行"失败要留痕（否则只在解释器退出时一行无上下文警告）。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("background group run failed: %s", exc, exc_info=True)

#: 一次性任务的下次运行时间超过这个天数 → 视为"挂死"（圆桌实测：管理员把
#: 一次性 prompt 任务 park 到 2030-01-01，`enabled=1` 但永不再触发，
#: 界面显示"启用中"其实不会跑——qa-ops 原话："启用 ≠ 会跑"）。
FAR_FUTURE_DAYS = 30


def far_future_warning(job: Job, now: datetime | None = None) -> str:
    """任务下次运行时间过于遥远时的人话警告；空串 = 正常。"""
    raw = str(getattr(job, "next_run_at", "") or "")
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if dt - now <= timedelta(days=FAR_FUTURE_DAYS):
        return ""
    if getattr(job, "schedule_type", "") == "once":
        return (f"⚠ 这是一次性任务且运行时间在 {FAR_FUTURE_DAYS} 天以上"
                f"（{raw[:10]}）——它不会自动触发，只会静静挂着。"
                f"要现在跑请用 task_run_now；跑完后建议改为间隔（如 '30m'）或停用它。")
    return (f"⚠ 下次运行在 {FAR_FUTURE_DAYS} 天以上（{raw[:10]}），"
            f"期间它不会触发——确认这是预期。")


class JobManager:
    def __init__(self, store: JobStore, windows: Any):
        self.store = store
        self.windows = windows  # WindowManager

    def create(self, window_id: str, name: str, schedule: str, prompt: str,
               report_strategy: str = "brief", kind: str = "prompt",
               report_channel: str = "", llm_profile: str = "",
               group_id: str = "") -> Job:
        w = self.windows.get(window_id)
        if w is None:
            raise ValueError(f"window not found: {window_id}")
        job = self.store.create(w.id, name, schedule, prompt, report_strategy, kind,
                                group_id=group_id)
        job.report_channel = report_channel or ""
        job.llm_profile = llm_profile or ""
        job = self.store.repo.upsert_job(job)
        warn = far_future_warning(job)
        if warn:
            logger.warning("job %s parked far in the future: %s", job.id, job.next_run_at)
        self.windows.db.audit("system", "job:create",
                              f"{job.id} {name} {schedule}" + (f" WARN={warn}" if warn else ""))
        return job

    def list(self, window_id: str | None = None) -> list[Job]:
        return self.store.list(window_id)

    def get(self, job_id: str) -> Job | None:
        return self.store.get(job_id)

    def update(self, job_id: str, **fields: Any) -> Job:
        job = self.store.update(job_id, **fields)
        self.windows.db.audit("system", "job:update", f"{job_id} {list(fields)}")
        return job

    def pause(self, job_id: str) -> str:
        self.store.set_enabled(job_id, False)
        return "paused"

    def resume(self, job_id: str) -> str:
        self.store.set_enabled(job_id, True)
        return "resumed"

    def remove(self, job_id: str) -> str:
        ok = self.store.remove(job_id)
        self.windows.db.audit("system", "job:remove", job_id)
        return "removed" if ok else "not found"

    async def run_now_async(self, job_id: str) -> dict[str, Any]:
        """「立即执行」（v0.10.46）。

        - 群组任务：**后台起一条完整流水线**（以前要么误单派给组长、要么直接拒绝
          —— owner 实测"点了没反应"）；
        - 普通任务：照旧派给工位。

        返回 `{ok, mode, note}`，前端据此显示真实结果（别再假装"已触发"）。
        """
        job = self.store.get(job_id)
        if job is None:
            return {"ok": False, "reason": f"没有这个任务：{job_id}"}
        gid = str(getattr(job, "group_id", "") or "")
        if gid:
            groups = getattr(self, "groups", None)
            if groups is None:
                return {"ok": False, "reason": "群组服务不可用"}
            try:
                task = asyncio.create_task(groups.run(gid, job.prompt))
                _BACKGROUND.add(task)
                task.add_done_callback(_BACKGROUND.discard)
                task.add_done_callback(_log_bg_error)
                return {"ok": True, "mode": "group",
                        "note": f"群组流水线已在后台启动（{job.name}）"}
            except Exception as e:  # noqa: BLE001
                logger.warning("group job run_now failed", exc_info=True)
                return {"ok": False, "reason": f"启动群组运行失败：{e}"}
        return {"ok": True, "mode": "window", "note": self.run_now(job_id)}

    def run_now(self, job_id: str) -> str:
        job = self.store.get(job_id)
        if job is None:
            return "not found"
        # 群组任务走 `run_now_async`（要起后台任务）；同步入口只做单工位派单。
        if getattr(job, "group_id", ""):
            return ("这是群组任务：请用异步入口启动（控制台/API 的 run_now 已支持）；"
                    "单工位派单会把提示词直接给组长，member_dispatch 会被拒")
        self.windows.dispatch(job.window_id, job.prompt, urgent=True, job_id=job.id,
                              kind=job.kind, llm_profile=job.llm_profile)
        # v0.10.20（圆桌实测）：`run_now` 过去**从不更新任务生命周期**——
        # 一次性任务被 park 到 2030-01-01 后靠手动 run_now 触发，跑完依然
        # `enabled=1 / next_run_at=2030` / `last_run_at=null`，控制台显示
        # "启用中"但它永不再跑（qa-ops："启用 ≠ 会跑"）。一次性任务派单即
        #  consumed：它再也不会自动触发，UI 不必再撒谎。
        if getattr(job, "schedule_type", "") == "once" and job.enabled:
            job.enabled = False
            job.next_run_at = None
            self.store.repo.upsert_job(job)
            self.windows.db.audit("system", "job:run_now_once",
                                  f"{job.id} dispatched; once-job disabled")
            logger.info("once-job %s dispatched via run_now; disabled", job.id)
            return "dispatched (queue head); 一次性任务已标记为已派发（不再自动触发）"
        return "dispatched (queue head)"

    def runs(self, job_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.windows.db.list_runs(job_id, limit)
        return [{"id": r.id, "job_id": r.job_id, "window_id": r.window_id,
                 "status": r.status, "started_at": r.started_at,
                 "finished_at": r.finished_at, "summary": r.summary, "log_path": r.log_path}
                for r in rows]
