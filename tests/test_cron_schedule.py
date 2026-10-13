# DogBuddyCn — schedule parsing & job lifecycle tests (Hermes-derived semantics)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from dogbuddy.cron.jobs import MAX_FAIL_STREAK, JobStore, compute_next_run, parse_duration, parse_schedule
from dogbuddy.db import Database
from dogbuddy.ports import Job


def test_parse_duration():
    assert parse_duration("30m") == 30
    assert parse_duration("2h") == 120
    assert parse_duration("1d") == 1440
    assert parse_duration("hour") == 60
    with pytest.raises(ValueError):
        parse_duration("banana")


def test_parse_interval_formats():
    for s in ("30m", "every 30m", "every 2h", "1d"):
        p = parse_schedule(s)
        assert p["kind"] == "interval", s
        assert p["minutes"] > 0


def test_parse_natural_cron():
    p = parse_schedule("every monday 9am")
    assert p["kind"] == "cron"
    assert p["expr"] == "0 9 * * 1"

    p2 = parse_schedule("weekdays at 9:30am")
    assert p2["kind"] == "cron"
    assert p2["expr"] == "30 9 * * 1-5"

    p3 = parse_schedule("every day at noon")
    assert p3["expr"] == "0 12 * * *"


def test_parse_cron_expr():
    p = parse_schedule("0 9 * * *")
    assert p["kind"] == "cron"
    assert p["expr"] == "0 9 * * *"


def test_parse_iso_once():
    p = parse_schedule("2026-02-03T14:00:00")
    assert p["kind"] == "once"
    assert "2026-02-03T14:00:00" in p["run_at"]


def test_parse_in_delay_once():
    p = parse_schedule("in 30m")
    assert p["kind"] == "once"
    assert p["display"].startswith("once in")


def test_parse_invalid():
    with pytest.raises(ValueError):
        parse_schedule("banana o'clock")


def test_compute_next_run_interval_and_once():
    p = parse_schedule("30m")
    nxt = compute_next_run(p)
    assert nxt is not None
    p2 = parse_schedule("2020-01-01T00:00:00")
    assert compute_next_run(p2) is not None  # once returns its run_at


def test_job_store_lifecycle(tmp_path):
    db = Database(tmp_path / "t.db")
    db.insert_window.__self__  # touch
    from dogbuddy.ports import Window
    db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                            workspace_path=str(tmp_path), model="m"))
    store = JobStore(db)
    job = store.create("w1", "j1", "30m", "do things")
    assert job.id and job.next_run_at
    assert store.due() == [] or all(j.id != job.id for j in store.due())  # next_run in future

    # failure streak -> auto pause
    job.next_run_at = "2000-01-01T00:00:00+00:00"
    store.repo.upsert_job(job)
    assert len(store.due()) == 1
    for i in range(MAX_FAIL_STREAK):
        job = store.get(job.id)
        fails, paused = store.mark_failure(job)
    assert paused is True
    assert store.get(job.id).enabled is False

    # rate limit is not a failure
    job2 = store.create("w1", "j2", "every 2h", "other")
    job2.fail_count = 2
    store.repo.upsert_job(job2)
    job2 = store.get(job2.id)
    store.mark_rate_limited(job2)
    assert store.get(job2.id).fail_count == 2  # unchanged
    assert store.get(job2.id).enabled is True

    # once job disables after success
    job3 = store.create("w1", "j3", "2030-01-01T00:00:00", "once thing")
    job3 = store.get(job3.id)
    store.mark_success(job3)
    assert store.get(job3.id).enabled is False
    db.close()
