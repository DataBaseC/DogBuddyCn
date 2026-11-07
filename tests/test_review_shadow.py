# DogBuddyCn — AI review shadow/enforce (0.24.0/0.25.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.roles import get_role  # noqa: E402
from dogbuddy.db import Database, SCHEMA_VERSION  # noqa: E402
from dogbuddy.events.bus import Event  # noqa: E402
from dogbuddy.kernel import KernelStore  # noqa: E402
from dogbuddy.kernel.domain import RunHandle  # noqa: E402
from dogbuddy.services.review_service import ReviewService, parse_verdict  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402
from dogbuddy.tools.review_ops import RunTestsTool  # noqa: E402


class FakeWindows:
    def __init__(self):
        self.calls = []
        self.reviewer = SimpleNamespace(id="reviewer", name="审", role="worker_reviewer",
                                        status="idle")
        self.worker = SimpleNamespace(id="w1", name="写", role="worker_programmer",
                                      status="idle")

    def get(self, wid):
        return {"reviewer": self.reviewer, "w1": self.worker}.get(wid)

    def dispatch_run(self, **kw):
        self.calls.append(kw)
        return RunHandle(run_id=f"r{len(self.calls)}", work_item_id=kw.get("work_item_id", ""))


def _stack(tmp_path):
    db = Database(tmp_path / "rv.db")
    store = KernelStore(db)
    thread = store.ensure_thread(source="web", external_id="c1")
    wi = store.create_work_item(thread_id=thread["id"], title="改登录",
                                origin="router", review_required=True,
                                window_id="w1")
    store.create_mandate(work_item_id=wi["id"], scope="workspace",
                         allowed_actions=["read", "write", "exec"])
    store.update_work_item(wi["id"], status="working")
    return db, store, wi


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_schema_has_review_tables(tmp_path):
    db = Database(tmp_path / "s.db")
    names = {r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert SCHEMA_VERSION == 29
    assert {"review_requests", "review_findings"} <= names
    db.close()


def test_shadow_request_without_reviewer_waits_human(tmp_path):
    db, store, wi = _stack(tmp_path)
    svc = ReviewService(store, FakeWindows(), None, mode="shadow", db=db)
    _run(svc.on_run_finished(Event("run.finished", {
        "id": "r1", "kind": "router", "status": "done",
        "work_item_id": wi["id"], "parent_run_id": "", "summary": "done"})))
    req = store.get_review_by_source_run("r1")
    assert req["status"] == "waiting_human"
    assert store.get_work_item(wi["id"])["review_status"] == "waiting_human"
    _run(svc.on_run_finished(Event("run.finished", {
        "id": "r1", "kind": "router", "status": "done",
        "work_item_id": wi["id"]})))
    assert len(store.list_review_requests()) == 1
    db.close()


def test_shadow_dispatches_reviewer_when_configured(tmp_path):
    db, store, wi = _stack(tmp_path)
    fw = FakeWindows()
    svc = ReviewService(store, fw, None, mode="shadow",
                        window_id="reviewer", db=db)
    _run(svc.on_run_finished(Event("run.finished", {
        "id": "r1", "kind": "router", "status": "done",
        "work_item_id": wi["id"], "parent_run_id": "", "summary": "done"})))
    req = store.get_review_by_source_run("r1")
    assert req["status"] == "running" and req["reviewer_run_id"]
    assert fw.calls and fw.calls[0]["origin"] == "review"
    db.close()


def test_shadow_fail_only_records_not_rework(tmp_path):
    db, store, wi = _stack(tmp_path)
    fw = FakeWindows()
    svc = ReviewService(store, fw, None, mode="shadow", db=db)
    req, _ = store.create_review_request(work_item_id=wi["id"], run_id="r1")
    store.update_review_request(req["id"], reviewer_run_id="rr1")
    body = json.dumps({"verdict": "FAIL", "summary": "缺测试",
                       "findings": [{"severity": "blocking", "file": "a.py",
                                     "issue": "没关子进程"}]})
    _run(svc.on_run_finished(Event("run.finished", {
        "id": "rr1", "kind": "review", "status": "done", "summary": body})))
    assert store.get_review_request(req["id"])["status"] == "failed"
    assert store.get_work_item(wi["id"])["status"] == "working"
    assert not fw.calls
    db.close()


def test_enforce_pass_marks_done(tmp_path):
    db, store, wi = _stack(tmp_path)
    svc = ReviewService(store, FakeWindows(), None, mode="enforce", db=db)
    req, _ = store.create_review_request(work_item_id=wi["id"], run_id="r1")
    store.update_review_request(req["id"], reviewer_run_id="rr1")
    _run(svc.on_run_finished(Event("run.finished", {
        "id": "rr1", "kind": "review", "status": "done",
        "summary": json.dumps({"verdict": "PASS", "summary": "ok"})})))
    got = store.get_work_item(wi["id"])
    assert got["status"] == "done" and got["review_status"] == "passed"
    db.close()


def test_enforce_fail_dispatches_one_rework_then_human(tmp_path):
    db, store, wi = _stack(tmp_path)
    fw = FakeWindows()
    svc = ReviewService(store, fw, None, mode="enforce", db=db, max_rework=1)
    body = json.dumps({"verdict": "FAIL", "summary": "缺测试",
                       "findings": [{"severity": "blocking", "file": "a.py",
                                     "line": 3, "issue": "没关子进程"}]})
    # first failure: dispatch one rework to the original worker
    req, _ = store.create_review_request(work_item_id=wi["id"], run_id="r1")
    store.update_review_request(req["id"], reviewer_run_id="rr1")
    _run(svc.on_run_finished(Event("run.finished", {
        "id": "rr1", "kind": "review", "status": "done", "summary": body})))
    calls = [c for c in fw.calls if c["origin"] == "rework"]
    assert len(calls) == 1 and calls[0]["window_id"] == "w1"
    got = store.get_work_item(wi["id"])
    assert got["status"] == "working" and got["review_status"] == "changes_requested"

    # second failure: max_rework=1 exhausted -> human, no second rework
    req2, _ = store.create_review_request(work_item_id=wi["id"], run_id="r2")
    store.update_review_request(req2["id"], reviewer_run_id="rr2")
    _run(svc.on_run_finished(Event("run.finished", {
        "id": "rr2", "kind": "review", "status": "done", "summary": body})))
    assert len([c for c in fw.calls if c["origin"] == "rework"]) == 1
    assert store.get_work_item(wi["id"])["status"] == "waiting_human"
    db.close()


def test_owner_override_requires_reason_and_marks_done(tmp_path):
    db, store, wi = _stack(tmp_path)
    svc = ReviewService(store, None, None, mode="enforce", db=db)
    req, _ = store.create_review_request(work_item_id=wi["id"], run_id="r1",
                                         status="waiting_human")
    try:
        svc.override(req["id"], actor="owner", reason="")
        assert False, "empty reason must be rejected"
    except ValueError:
        pass
    out = svc.override(req["id"], actor="owner", reason="已人工核对")
    assert out["status"] == "overridden" and out["verdict"] == "OVERRIDE"
    got = store.get_work_item(wi["id"])
    assert got["status"] == "done" and got["review_status"] == "overridden"
    db.close()


def test_review_fail_parse_rules():
    assert parse_verdict('{"verdict":"FAIL"}') is None
    assert parse_verdict(
        '{"verdict":"PASS","findings":[{"severity":"blocking"}]}') is None
    assert parse_verdict('noise {"verdict":"PASS","summary":"ok"}')["verdict"] == "PASS"


def test_reviewer_is_hard_read_only():
    tools = set(get_role("worker_reviewer").tool_names)
    assert not (tools & {"shell", "write_file", "edit_file",
                         "window_memory_write", "checkpoint_create"})
    assert {"read_file", "read_resource", "run_tests"} <= tools
    assert get_role("worker_reviewer").default_mode == "PLAN"


def test_run_tests_only_allowlisted(tmp_path):
    allowed = f'{sys.executable} -c "print(\'ok\')"'
    tool = RunTestsTool([allowed])
    ctx = ToolContext(window_id="reviewer", workspace=str(tmp_path), mode="PLAN")
    ok = _run(tool.execute({"command": allowed}, ctx))
    assert ok.ok and "ok" in ok.output and ok.data["exit_code"] == 0
    bad = _run(tool.execute({"command": "rm -rf /"}, ctx))
    assert not bad.ok and "白名单" in bad.output
    empty = _run(RunTestsTool().execute({}, ctx))
    assert not empty.ok and "未配置" in empty.output
