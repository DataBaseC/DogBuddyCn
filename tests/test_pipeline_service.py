# DogBuddyCn — 流水线编排（0.12.1）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""钉住流水线引擎的四条不变量：

  ① **坏图存不进去**：重复 id / 未知 action / 缺依赖 / 环，一律 400；
  ② **依赖顺序**：`needs` 没完成之前不跑；
  ③ **幂等续跑**：同一 run 再 tick，已完成步骤**不会再执行**（不重复花钱/不重复写文件）；
  ④ **失败要留痕**：失败步骤记 error，run 置 failed；`continue_on_error` 的步骤失败
     不拦下游（比如"抽检"失败不该拦住"报告"）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.pipeline_service import (  # noqa: E402
    PipelineError, PipelineService,
)
from dogbuddy.web.routes.pipelines import router as pipelines_router  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture()
def env(tmp_path):
    db = Database(str(tmp_path / "p.db"))
    calls: list[tuple[str, str]] = []

    async def dispatch(window_id: str, prompt: str) -> str:
        calls.append((window_id, prompt))
        if "FAIL" in prompt:
            raise RuntimeError("工位说这个跑不了")
        return f"ran {prompt}"

    svc = PipelineService(db, dispatch=dispatch, workspace_root=str(tmp_path / "ws"))
    yield svc, db, calls, tmp_path
    db.close()


def _linear(svc, steps=None):
    steps = steps or [
        {"id": "a", "action": "noop"},
        {"id": "b", "action": "noop", "needs": ["a"]},
        {"id": "c", "action": "noop", "needs": ["b"]},
    ]
    return svc.upsert({"id": "p1", "name": "t", "steps": steps})


# ------------------------------------------------------------------ 定义校验
@pytest.mark.parametrize("steps,msg", [
    ([], "非空"),
    ([{"id": "a"}, {"id": "a"}], "重复"),
    ([{"id": "a", "action": "teleport"}], "不支持"),
    ([{"id": "a", "needs": ["nope"]}], "不存在"),
    ([{"id": "a", "needs": ["a"]}], "自己"),
    ([{"id": "a", "needs": ["b"]}, {"id": "b", "needs": ["a"]}], "循环"),
])
def test_bad_spec_is_rejected(env, steps, msg):
    svc, db, calls, _ = env
    with pytest.raises(PipelineError) as exc:
        svc.upsert({"id": "x", "steps": steps})
    assert msg in str(exc.value)
    assert db.get_pipeline("x") is None, "坏图不许落库"


def test_upsert_valid_and_list(env):
    svc, db, calls, _ = env
    saved = _linear(svc)
    assert saved["id"] == "p1"
    assert [p["id"] for p in svc.list()] == ["p1"]
    assert len(svc.spec_of(saved)) == 3


# -------------------------------------------------------------------- 执行
def test_linear_run_done(env):
    svc, db, calls, _ = env
    _linear(svc)
    run = svc.start("p1", {"batch": 7})
    st = _run(svc.tick(run["id"]))
    assert st["run"]["status"] == "done"
    assert {s["step_id"] for s in st["steps"]} == {"a", "b", "c"}
    assert all(s["status"] == "done" for s in st["steps"])
    # 输出按步骤落库
    assert st["run"]["output_json"]
    assert set((st["run"]["output_json"] and __import__("json").loads(
        st["run"]["output_json"])) or {}) == {"a", "b", "c"}


def test_dependency_order(env):
    svc, db, calls, _ = env
    _linear(svc, [
        {"id": "second", "action": "window_run",
         "params": {"window_id": "w1", "prompt": "2"}, "needs": ["first"]},
        {"id": "first", "action": "window_run",
         "params": {"window_id": "w1", "prompt": "1"}},
    ])
    run = svc.start("p1")
    _run(svc.tick(run["id"]))
    assert calls == [("w1", "1"), ("w1", "2")], "必须按依赖顺序执行"


def test_tick_is_idempotent(env):
    """同一个 run 再 tick 一遍：已完成的步骤**不能再执行**（否则重复花钱）。"""
    svc, db, calls, _ = env
    _linear(svc, [{"id": "only", "action": "window_run",
                   "params": {"window_id": "w1", "prompt": "hi"}}])
    run = svc.start("p1")
    _run(svc.tick(run["id"]))
    st = _run(svc.tick(run["id"]))
    assert st["run"]["status"] == "done"
    assert len(calls) == 1, f"重复调用了工位：{calls}"


def test_failure_marks_run_failed_and_blocks_downstream(env):
    svc, db, calls, _ = env
    _linear(svc, [
        {"id": "bad", "action": "window_run",
         "params": {"window_id": "w1", "prompt": "FAIL"}},
        {"id": "after", "action": "window_run",
         "params": {"window_id": "w1", "prompt": "never"}, "needs": ["bad"]},
    ])
    run = svc.start("p1")
    st = _run(svc.tick(run["id"]))
    assert st["run"]["status"] == "failed"
    assert "bad" in (st["run"]["error"] or "")
    step = next(s for s in st["steps"] if s["step_id"] == "bad")
    assert step["status"] == "failed" and "跑不了" in step["error"]
    assert [c[1] for c in calls] == ["FAIL"], "下游不该跑"
    acts = [r[0] for r in db._conn.execute(
        "select action from audit_log where action like 'pipeline:%'")]
    assert "pipeline:step_failed" in acts


def test_continue_on_error_allows_downstream(env):
    svc, db, calls, _ = env
    _linear(svc, [
        {"id": "sample", "action": "window_run",
         "params": {"window_id": "w1", "prompt": "FAIL"}, "continue_on_error": True},
        {"id": "report", "action": "write_file",
         "params": {"path": "out/report.md", "content": "ok"}, "needs": ["sample"]},
    ])
    run = svc.start("p1")
    st = _run(svc.tick(run["id"]))
    assert st["run"]["status"] == "done", st["run"]
    assert "report" in st["run"]["output_json"]


def test_resume_retries_only_failed_step(env):
    svc, db, calls, _ = env
    _linear(svc, [
        {"id": "ok1", "action": "window_run",
         "params": {"window_id": "w1", "prompt": "fine"}},
        {"id": "boom", "action": "window_run",
         "params": {"window_id": "w1", "prompt": "FAIL"}, "needs": ["ok1"]},
    ])
    run = svc.start("p1")
    st = _run(svc.tick(run["id"]))
    assert st["run"]["status"] == "failed"
    assert len(calls) == 2

    # 修好的方式：把这一步的参数换掉（idem 变了才会重跑）—— 这里直接改 spec
    svc.upsert({"id": "p1", "name": "t", "steps": [
        {"id": "ok1", "action": "window_run",
         "params": {"window_id": "w1", "prompt": "fine"}},
        {"id": "boom", "action": "window_run",
         "params": {"window_id": "w1", "prompt": "now-ok"}, "needs": ["ok1"]},
    ]})
    st2 = _run(svc.resume(run["id"]))
    assert st2["run"]["status"] == "done"
    # 第一步没被重跑（幂等），只补跑第二步
    assert [c[1] for c in calls] == ["fine", "FAIL", "now-ok"], calls


def test_write_file_stays_in_workspace(env):
    svc, db, calls, tmp_path = env
    _linear(svc, [{"id": "w", "action": "write_file",
                   "params": {"path": "sub/x.txt", "content": "hello"}}])
    run = svc.start("p1")
    st = _run(svc.tick(run["id"]))
    assert st["run"]["status"] == "done"
    assert (tmp_path / "ws" / "sub" / "x.txt").read_text(encoding="utf-8") == "hello"


def test_write_file_escape_is_failed_step(env):
    svc, db, calls, _ = env
    _linear(svc, [{"id": "w", "action": "write_file",
                   "params": {"path": "../../etc/passwd", "content": "x"}}])
    run = svc.start("p1")
    st = _run(svc.tick(run["id"]))
    assert st["run"]["status"] == "failed"
    assert "越出工作区" in next(s for s in st["steps"])["error"]


def test_window_run_without_dispatch_fails_clearly(env, tmp_path):
    svc, db, calls, _ = env
    bare = PipelineService(db, workspace_root=str(tmp_path / "ws2"))
    bare.upsert({"id": "p2", "steps": [
        {"id": "s", "action": "window_run",
         "params": {"window_id": "w", "prompt": "x"}}]})
    run = bare.start("p2")
    st = _run(bare.tick(run["id"]))
    assert st["run"]["status"] == "failed"
    assert "未装配派活通道" in next(s for s in st["steps"])["error"]


def test_start_rejects_unknown_or_disabled(env):
    svc, db, calls, _ = env
    with pytest.raises(PipelineError):
        svc.start("nope")
    svc.upsert({"id": "off", "enabled": False, "steps": [{"id": "a"}]})
    with pytest.raises(PipelineError):
        svc.start("off")


# -------------------------------------------------------------------- 接口
@pytest.fixture()
def client(env):
    svc, db, calls, _ = env
    app = FastAPI()
    app.include_router(pipelines_router(SimpleNamespace(pipelines=svc), []), prefix="/api")
    return TestClient(app), svc, db


def test_pipeline_routes(client):
    c, svc, db = client
    assert c.get("/api/pipelines").json()["pipelines"] == []
    body = {"name": "线A", "steps": [
        {"id": "a", "action": "noop"},
        {"id": "b", "action": "noop", "needs": ["a"]}]}
    r = c.put("/api/pipelines/line-a", json=body)
    assert r.status_code == 200, r.text
    assert c.get("/api/pipelines/line-a").json()["name"] == "线A"
    # 起跑：立刻返回 run_id（真正执行在后台）
    rr = c.post("/api/pipelines/line-a/run", json={"payload": {"k": 1}})
    assert rr.status_code == 200 and rr.json()["run_id"].startswith("pr-")
    rid = rr.json()["run_id"]
    # 后台任务可能还没跑完：直接同步跑一次，再看状态
    _run(svc.tick(rid))
    st = c.get(f"/api/pipelines/runs/{rid}").json()
    assert st["run"]["status"] == "done"
    assert len(c.get("/api/pipelines/runs").json()["runs"]) == 1
    # 坏图 → 400
    assert c.put("/api/pipelines/bad", json={
        "steps": [{"id": "a", "needs": ["b"]}, {"id": "b", "needs": ["a"]}]}
    ).status_code == 400
    # 未知 run → 404
    assert c.get("/api/pipelines/runs/pr-nope").status_code == 404


def test_health_counts(client):
    c, svc, db = client
    svc.upsert({"id": "h", "steps": [{"id": "a"}]})
    run = svc.start("h")
    _run(svc.tick(run["id"]))
    h = svc.health()
    assert h["pipelines"] == 1 and h["done"] == 1 and h["failed"] == 0
