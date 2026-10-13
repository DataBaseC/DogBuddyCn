# DogBuddyCn — job management service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""JobManager: CRUD over JobStore + run_now (head-of-queue dispatch)."""
from __future__ import annotations

from typing import Any

from ..cron.jobs import JobStore
from ..ports import Job


class JobManager:
    def __init__(self, store: JobStore, windows: Any):
        self.store = store
        self.windows = windows  # WindowManager

    def create(self, window_id: str, name: str, schedule: str, prompt: str,
               report_strategy: str = "brief", kind: str = "prompt",
               report_channel: str = "", llm_profile: str = "") -> Job:
        w = self.windows.get(window_id)
        if w is None:
            raise ValueError(f"window not found: {window_id}")
        job = self.store.create(w.id, name, schedule, prompt, report_strategy, kind)
        job.report_channel = report_channel or ""
        job.llm_profile = llm_profile or ""
        job = self.store.repo.upsert_job(job)
        self.windows.db.audit("system", "job:create", f"{job.id} {name} {schedule}")
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

    def run_now(self, job_id: str) -> str:
        job = self.store.get(job_id)
        if job is None:
            return "not found"
        self.windows.dispatch(job.window_id, job.prompt, urgent=True, job_id=job.id,
                              kind=job.kind, llm_profile=job.llm_profile)
        return "dispatched (queue head)"

    def runs(self, job_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.windows.db.list_runs(job_id, limit)
        return [{"id": r.id, "job_id": r.job_id, "window_id": r.window_id,
                 "status": r.status, "started_at": r.started_at,
                 "finished_at": r.finished_at, "summary": r.summary, "log_path": r.log_path}
                for r in rows]
