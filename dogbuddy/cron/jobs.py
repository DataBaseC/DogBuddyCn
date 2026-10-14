# DogBuddyCn — cron job store & schedule parsing (L2 Application)
# Derived from Hermes Agent cron/jobs.py (https://github.com/NousResearch/hermes-agent)
# Copyright (c) NousResearch and contributors — MIT License.
# Modifications (2026-10-12, DogBuddyCn contributors):
#   - storage moved from ~/.hermes/cron/jobs.json to the DogBuddyCn SQLite DAO (JobRepo port)
#   - run semantics: jobs inject into a persistent window session (not a fresh session)
#   - 4 schedule formats preserved verbatim: duration "30m"/"2h"/"1d", natural
#     "every 2h"/"every monday 9am", 5-field cron, ISO one-shot timestamp
"""Schedule parsing (Hermes-derived) + job lifecycle over the injected JobRepo."""

from __future__ import annotations

import contextlib
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..ports import Job

logger = logging.getLogger("dogbuddy.cron.jobs")

try:
    from croniter import croniter
except ImportError:  # pragma: no cover - validated in tests with croniter installed
    croniter = None  # type: ignore[assignment]

_DURATION_MULTIPLIERS = {"m": 1, "h": 60, "d": 1440}

MAX_FAIL_STREAK = 3  # consecutive failures -> auto pause (DEV_PROMPT M-E)


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def parse_duration(s: str) -> int:
    """Parse a duration into minutes: "30m" → 30, "2h" → 120, "1d" → 1440, bare "hour" → 60."""
    s = s.strip().lower()
    match = re.match(r'^(\d*)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$', s)
    if not match:
        raise ValueError(
            f"Invalid duration: '{s}'. Use format like '30m', '2h', '1d', "
            "or a bare unit like 'hour' (defaults to 1).")
    value = int(match.group(1)) if match.group(1) else 1
    return value * _DURATION_MULTIPLIERS[match.group(2)[0]]


# Cron weekday numbering is 0=Sunday … 6=Saturday (croniter default).
_WEEKDAY_TO_CRON_DOW = {
    "sunday": "0", "sun": "0",
    "monday": "1", "mon": "1",
    "tuesday": "2", "tue": "2", "tues": "2",
    "wednesday": "3", "wed": "3", "weds": "3",
    "thursday": "4", "thu": "4", "thur": "4", "thurs": "4",
    "friday": "5", "fri": "5",
    "saturday": "6", "sat": "6",
}

_DAYSPEC_TO_CRON_DOW = {
    "day": "*", "daily": "*", "everyday": "*",
    "weekday": "1-5", "weekdays": "1-5",
    "weekend": "0,6", "weekends": "0,6",
}


def _parse_clock_time(text: str) -> Optional[tuple]:
    """Parse ``9am``/``9:30am``/``14:00``/``7``/``noon``/``midnight`` → (hour, minute)."""
    t = text.strip().lower().replace(" ", "")
    if not t:
        return None
    if t in ("noon", "midday"):
        return (12, 0)
    if t == "midnight":
        return (0, 0)
    match = re.match(r'^(\d{1,2})(?::(\d{2}))?(am|pm)?$', t)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return (hour, minute)


def _natural_every_to_cron(rest: str) -> Optional[str]:
    """``<when> [at] <time>`` ("monday 9am", "weekday at 9am") → 5-field cron expr."""
    tokens = rest.lower().replace(",", " ").split()
    if not tokens:
        return None
    dow = _DAYSPEC_TO_CRON_DOW.get(tokens[0])
    idx = 1
    if dow is None:
        days = []
        idx = len(tokens)
        for i, tok in enumerate(tokens):
            if tok == "and":
                continue
            mapped = _WEEKDAY_TO_CRON_DOW.get(tok)
            if mapped is None:
                idx = i
                break
            if mapped not in days:
                days.append(mapped)
        if not days:
            return None
        dow = ",".join(days)
    time_tokens = tokens[idx:]
    if time_tokens and time_tokens[0] == "at":
        time_tokens = time_tokens[1:]
    if not time_tokens:
        return None
    parsed = _parse_clock_time(" ".join(time_tokens))
    if parsed is None:
        return None
    hour, minute = parsed
    return f"{minute} {hour} * * {dow}"


def _cron_schedule(expr: str, display: str, missing_croniter: str, invalid_label: str) -> dict[str, Any]:
    if croniter is None:
        raise ValueError(f"{missing_croniter} Install with: pip install croniter")
    try:
        croniter(expr)
    except Exception as e:
        raise ValueError(f"Invalid {invalid_label} '{display}': {e}") from e
    return {"kind": "cron", "expr": expr, "display": display}


def _interval_schedule(minutes: int) -> dict[str, Any]:
    return {"kind": "interval", "minutes": minutes, "display": f"every {minutes}m"}


def parse_schedule(schedule: str) -> dict[str, Any]:
    """Parse into {"kind": "once"|"interval"|"cron", ...} (Hermes semantics).

    "30m" and "every 30m" are recurring intervals; "every monday 9am" and
    "0 9 * * *" are cron; an ISO timestamp is once; "in 2h" is a one-shot delay.
    """
    schedule = schedule.strip()
    original = schedule
    schedule_lower = schedule.lower()

    is_every = schedule_lower.startswith("every ")
    rest = schedule[6:].strip() if is_every else schedule_lower
    cron_expr = _natural_every_to_cron(rest)
    if cron_expr is not None:
        example = "every monday 9am" if is_every else "weekdays at 9am"
        return _cron_schedule(
            cron_expr, original,
            f"Weekday/time schedules like '{example}' require the 'croniter' package.", "schedule")
    if is_every:
        return _interval_schedule(parse_duration(rest))

    # Cron expression (5-6 fields; letters allowed for named months/weekdays).
    parts = schedule.split()
    if len(parts) >= 5 and all(re.match(r'^[A-Za-z\d\*,-/]+$', p) for p in parts[:5]):
        return _cron_schedule(
            schedule, schedule, "Cron expressions require 'croniter' package.", "cron expression")

    # ISO timestamp (one-shot).
    if 'T' in schedule or re.match(r'^\d{4}-\d{2}-\d{2}', schedule):
        try:
            dt = datetime.fromisoformat(schedule)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_now().tzinfo)
            return {
                "kind": "once",
                "run_at": dt.isoformat(),
                "display": f"once at {dt.strftime('%Y-%m-%d %H:%M')}",
            }
        except ValueError as e:
            raise ValueError(f"Invalid timestamp '{schedule}': {e}") from e

    # "in 30m" one-shot delay; bare duration is RECURRING.
    if schedule_lower.startswith("in "):
        duration_str = schedule[3:].strip()
        try:
            minutes = parse_duration(duration_str)
        except ValueError:
            raise ValueError(
                f"Invalid duration '{duration_str}' after 'in '. Use e.g. 'in 30m', 'in 2h'.") from None
        now = _now()
        run_at = (now.astimezone(timezone.utc) + timedelta(minutes=minutes)).astimezone(now.tzinfo)
        return {"kind": "once", "run_at": run_at.isoformat(), "display": f"once in {duration_str}"}

    with contextlib.suppress(ValueError):
        return _interval_schedule(parse_duration(schedule))

    raise ValueError(
        f"Invalid schedule '{original}'. Use:\n"
        f"  - Interval: '30m', 'every 30m', 'every 2h' (recurring)\n"
        f"  - One-shot delay: 'in 30m', 'in 2h' (fires once)\n"
        f"  - Weekly/daily: 'every monday 9am', 'weekdays at 9am' (recurring)\n"
        f"  - Cron: '0 9 * * *' (cron expression)\n"
        f"  - Timestamp: '2026-02-03T14:00:00' (one-shot at time)")


def _parse_aware(value: Any) -> Optional[datetime]:
    if not value:
        return None
    with contextlib.suppress(ValueError, TypeError):
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_now().tzinfo)
        return dt
    return None


def compute_next_run(schedule: dict[str, Any], last_run_at: Optional[str] = None) -> Optional[str]:
    """Next run time as ISO string, or None if no more runs (Hermes semantics)."""
    now = _now()
    if not isinstance(schedule, dict):
        return None
    kind = schedule.get("kind")
    if kind == "once":
        run_at = _parse_aware(schedule.get("run_at"))
        return run_at.isoformat() if run_at else None
    # Recurring kinds anchor on last_run_at so restarts don't re-anchor.
    base_time = _parse_aware(last_run_at) or now
    if kind == "interval":
        minutes = schedule.get("minutes")
        if minutes is None:
            return None
        next_run = base_time.astimezone(timezone.utc) + timedelta(minutes=int(minutes))
        return next_run.astimezone(base_time.tzinfo).isoformat()
    if kind == "cron":
        expr = schedule.get("expr")
        if not expr:
            return None
        if croniter is None:
            logger.warning("croniter missing; cannot compute next run for %r", expr)
            return None
        zone = base_time.tzinfo
        base_wall = base_time.astimezone(zone).replace(tzinfo=None)
        it = croniter(expr, base_wall)
        # strictly-after guard across DST folds: try both folds, return earliest > base
        base_ts = base_time.timestamp()
        next_wall = it.get_next(datetime)
        for _ in range(2):
            for fold in (0, 1):
                candidate = next_wall.replace(tzinfo=zone, fold=fold)
                if candidate.timestamp() > base_ts:
                    return candidate.isoformat()
            next_wall = it.get_next(datetime)
        return next_wall.replace(tzinfo=zone).isoformat()
    return None


class JobStore:
    """Job lifecycle over the injected JobRepo port (create/update/pause/remove/due)."""

    def __init__(self, repo: Any):
        self.repo = repo

    def create(self, window_id: str, name: str, schedule: str, prompt: str,
               report_strategy: str = "brief", kind: str = "prompt") -> Job:
        parsed = parse_schedule(schedule)
        job = Job(
            id=uuid.uuid4().hex[:12], window_id=window_id, name=name,
            schedule_type=parsed["kind"], schedule_raw=schedule, schedule=parsed,
            prompt=prompt, report_strategy=report_strategy, kind=kind, enabled=True)
        job.next_run_at = compute_next_run(parsed, None)
        return self.repo.upsert_job(job)

    def update(self, job_id: str, **fields: Any) -> Job:
        job = self.repo.get_job(job_id)
        if job is None:
            raise ValueError(f"job not found: {job_id}")
        if "schedule" in fields and fields["schedule"]:
            parsed = parse_schedule(str(fields["schedule"]))
            job.schedule = parsed
            job.schedule_type = parsed["kind"]
            job.schedule_raw = str(fields["schedule"])
            job.next_run_at = compute_next_run(parsed, job.last_run_at)
        for key in ("name", "prompt", "report_strategy", "kind", "report_channel", "llm_profile"):
            if key in fields and fields[key] is not None:
                setattr(job, key, fields[key])
        return self.repo.upsert_job(job)

    def set_enabled(self, job_id: str, enabled: bool) -> Job:
        job = self.repo.get_job(job_id)
        if job is None:
            raise ValueError(f"job not found: {job_id}")
        job.enabled = enabled
        if enabled:
            job.next_run_at = compute_next_run(job.schedule, job.last_run_at)
        return self.repo.upsert_job(job)

    def remove(self, job_id: str) -> bool:
        return self.repo.delete_job(job_id)

    def get(self, job_id: str) -> Job | None:
        return self.repo.get_job(job_id)

    def list(self, window_id: str | None = None) -> list[Job]:
        return self.repo.list_jobs(window_id)

    def due(self) -> list[Job]:
        return self.repo.due_jobs(_now().isoformat())

    def mark_success(self, job: Job, next_run_at: str | None = None) -> None:
        """记成功。`next_run_at` 是 T8.3 自调度的覆盖值（本次任务自决的下次时间）。

        自调度优先于 cron 默认计算，且**即使是一次性任务也会保持启用**
        （工位的意图是"过一会儿再来"，不是"跑完就完"）。
        """
        job.last_run_at = _now().isoformat()
        job.fail_count = 0
        if next_run_at:
            job.next_run_at = next_run_at
            job.enabled = True
        elif job.schedule_type == "once":
            job.enabled = False
            job.next_run_at = None
        else:
            job.next_run_at = compute_next_run(job.schedule, job.last_run_at)
        self.repo.upsert_job(job)

    def mark_failure(self, job: Job) -> tuple[int, bool]:
        """Returns (fail_count, auto_paused)."""
        job.last_run_at = _now().isoformat()
        job.fail_count += 1
        auto_paused = False
        if job.schedule_type == "once":
            job.enabled = False
            job.next_run_at = None
        else:
            if job.fail_count >= MAX_FAIL_STREAK:
                job.enabled = False
                auto_paused = True
                job.next_run_at = None
            else:
                job.next_run_at = compute_next_run(job.schedule, job.last_run_at)
        self.repo.upsert_job(job)
        return job.fail_count, auto_paused

    def mark_rate_limited(self, job: Job) -> None:
        """Rate-limit waiting is NOT a failure — just reschedule soon."""
        job.last_run_at = _now().isoformat()
        job.next_run_at = (_now() + timedelta(minutes=5)).isoformat()
        self.repo.upsert_job(job)
