# DogBuddyCn — Batch 8 组二：员工画像 / 项目迭代计划 / 迭代执行器
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""(T8.4) 员工画像 → (T8.5) 迭代计划按画像推节奏 → (T8.6) 到点真的跑一轮并判停。

ROADMAP 验收点：三档预设 + 手动覆盖并存；停止条件 = 达成目标 / 无进展 N 轮 /
次数上限（预算上限可选）；重度限速下迭代自动降速。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import SCHEMA_VERSION, Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.ports import Project, Window  # noqa: E402
from dogbuddy.services.iteration_executor import IterationExecutor  # noqa: E402
from dogbuddy.services.iteration_service import (  # noqa: E402
    PRESETS, SPEED_FACTOR, IterationService)
from dogbuddy.tools.automation_ops import ReportIterationTool  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _db(tmp_path) -> Database:
    return Database(tmp_path / "t.db")


def _project(db, pid="p1", root=None, tmp=None) -> Project:
    p = Project(id=pid, name=pid, root_path=str(root or (tmp / pid)))
    db.insert_project(p)
    return p


def _window(db, wid, pid, tmp, role="worker_programmer") -> Window:
    w = Window(id=wid, name=wid, role=role, workspace_path=str(tmp / wid), model="",
               project_id=pid)
    db.insert_window(w)
    return w


def _svc(db, models=None, sev=""):
    return IterationService(db, windows=None, models=models,
                            rate_limit_status=lambda: {"severity": sev})


# --------------------------------------------------------------------------- v7
def test_迁移_v7_建表且幂等(tmp_path):
    assert SCHEMA_VERSION >= 7
    p = tmp_path / "a.db"
    db = Database(p)
    try:
        assert db.schema_version == SCHEMA_VERSION
        cols = {r[1] for r in db._conn.execute("PRAGMA table_info(iterations)")}
        assert {"id", "project_id", "goal", "mode", "status", "round",
                "no_progress_streak", "next_run_at", "budget_tokens"} <= cols
        rcols = {r[1] for r in db._conn.execute("PRAGMA table_info(iteration_rounds)")}
        assert {"iteration_id", "round", "progress", "goal_achieved"} <= rcols
        assert db.list_iterations() == []
    finally:
        db.close()
    db2 = Database(p)
    try:
        assert db2.schema_version == SCHEMA_VERSION and db2.list_iterations() == []
    finally:
        db2.close()


# --------------------------------------------------------------------------- T8.4
def test_T8_4_内置角色带员工画像():
    from dogbuddy.agents.core.roles import get_role
    w = get_role("worker_programmer")
    assert w.domains, "程序员要有擅长域"
    assert w.can_deliver_alone is True
    assert w.iteration_speed in SPEED_FACTOR
    assert w.note, "画像备注要给管理员看"
    a = get_role("admin")
    assert a.iteration_speed == "slow", "管理员改动风险大 → 节奏保守"
    assert a.is_admin is True


def test_T8_4_工具集广度由数量推导():
    from dogbuddy.agents.core.roles import RoleSpec
    assert RoleSpec("x", "x", "", tool_names=["a"] * 3).tool_breadth == "narrow"
    assert RoleSpec("x", "x", "", tool_names=["a"] * 10).tool_breadth == "standard"
    assert RoleSpec("x", "x", "", tool_names=["a"] * 20).tool_breadth == "broad"


def test_T8_4_画像进能力清单接口(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        roles = {r["name"]: r for r in client.get("/api/capabilities").json()["roles"]}
        w = roles["worker_programmer"]
        assert w["domains"] and w["tool_breadth"] in ("narrow", "standard", "broad")
        assert w["can_deliver_alone"] is True
        assert w["iteration_speed"] in SPEED_FACTOR


# --------------------------------------------------------------------------- T8.5
def test_T8_5_三档预设默认值():
    db = Database(":memory:") if False else None
    import tempfile, os
    tmp = Path(tempfile.mkdtemp())
    db = Database(tmp / "t.db")
    try:
        svc = _svc(db)
        for mode, preset in PRESETS.items():
            plan = svc.effective_plan({"mode": mode, "window_ids": []})
            assert plan["mode"] == mode
            assert plan["interval_minutes"] == preset["speed_minutes"], mode
            assert plan["max_rounds"] == preset["max_rounds"]
            assert plan["no_progress_limit"] == preset["no_progress_limit"]
            assert plan["derived"]["interval"] == "auto"
    finally:
        db.close()


def test_T8_5_手动覆盖赢过档位(tmp_path):
    db = _db(tmp_path)
    try:
        svc = _svc(db)
        plan = svc.effective_plan({"mode": "aggressive", "speed_minutes": 90,
                                   "max_rounds": 7, "no_progress_limit": 1,
                                   "window_ids": []})
        assert plan["interval_minutes"] == 90
        assert plan["derived"]["interval"] == "manual"
        assert plan["max_rounds"] == 7 and plan["derived"]["max_rounds"] == "manual"
        assert plan["no_progress_limit"] == 1
    finally:
        db.close()


def test_T8_5_未知档位退回标准(tmp_path):
    db = _db(tmp_path)
    try:
        plan = _svc(db).effective_plan({"mode": "疯狂", "window_ids": []})
        assert plan["mode"] == "standard"
    finally:
        db.close()


def test_T8_5_模型慢则迭代慢(tmp_path):
    """T8.8 联动的正向：模型能力表里标注"很慢" → 间隔 ×3。"""
    from dogbuddy.services.model_service import ModelService, make_model_id
    db = _db(tmp_path)
    try:
        cfg = AppConfig(llm=LLMConfig(provider="openai_compat", base_url="http://x/v1",
                                      api_key="k", model="slow-model"))
        ms = ModelService(db, cfg)
        ms.upsert(model="slow-model", speed="很慢")
        svc = _srv_with_models(db, ms)
        plan = svc.effective_plan({"mode": "standard", "window_ids": []})
        assert plan["interval_minutes"] == int(round(60 * 3.0))
        assert "很慢" in plan["derived"]["model_speed"]
    finally:
        db.close()


def _srv_with_models(db, models, sev=""):
    return IterationService(db, windows=None, models=models,
                            rate_limit_status=lambda: {"severity": sev})


def test_T8_5_限速严重时迭代降速(tmp_path):
    """ROADMAP 明确要求：重度限速下项目迭代自动降速，避免排队雪崩。"""
    db = _db(tmp_path)
    try:
        base = _srv_with_models(db, None, sev="").effective_plan(
            {"mode": "standard", "window_ids": []})["interval_minutes"]
        heavy = _srv_with_models(db, None, sev="heavy").effective_plan(
            {"mode": "standard", "window_ids": []})
        banned = _srv_with_models(db, None, sev="banned").effective_plan(
            {"mode": "standard", "window_ids": []})
        assert heavy["interval_minutes"] > base
        assert banned["interval_minutes"] > heavy["interval_minutes"]
        assert "rate_limit" in heavy["derived"]
    finally:
        db.close()


def test_T8_5_最慢的工位决定节奏(tmp_path):
    """木桶原理：参与工位里只要有一个"慢"，整体节奏就慢下来。"""
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w-fast", "p1", tmp_path, role="worker_programmer")
        _window(db, "w-slow", "p1", tmp_path, role="admin")  # admin 画像 = slow
        from dogbuddy.services.window_manager import WindowManager
        from dogbuddy.events.bus import EventBus as EB
        from dogbuddy.memory.service import MemoryService
        wm = WindowManager(db=db, llm_factory=lambda *a, **k: None, bus=EB(),
                           memory=MemoryService(tmp_path / "mem"),
                           checkpoints_dir=tmp_path / "ckpt")
        svc = IterationService(db, windows=wm)
        only_fast = svc.effective_plan({"mode": "standard", "window_ids": ["w-fast"]})
        both = svc.effective_plan({"mode": "standard", "window_ids": ["w-fast", "w-slow"]})
        assert both["interval_minutes"] > only_fast["interval_minutes"]
        assert "staff_speed" in both["derived"]
    finally:
        db.close()


def test_T8_5_停止条件四种(tmp_path):
    db = _db(tmp_path)
    try:
        svc = _svc(db)
        plan = {"interval_minutes": 60, "max_rounds": 3, "no_progress_limit": 2,
                "budget_tokens": 0}
        assert svc.check_stop({"round": 0, "no_progress_streak": 0}, plan) == (False, "")
        assert svc.check_stop({"round": 0}, plan, goal_achieved=True)[1] == "已达成目标"
        assert "次数上限" in svc.check_stop({"round": 3}, plan)[1]
        assert "无进展" in svc.check_stop({"round": 1, "no_progress_streak": 2}, plan)[1]
        # 预算为 0 = 不限（可选字段）
        assert svc.check_stop({"round": 1, "no_progress_streak": 0}, plan,
                              spent_tokens=10 ** 9) == (False, "")
        plan_b = {**plan, "budget_tokens": 1000}
        assert "预算上限" in svc.check_stop({"round": 1, "no_progress_streak": 0}, plan_b,
                                        spent_tokens=1000)[1]
    finally:
        db.close()


def test_T8_5_建计划_自动取项目工位_并立刻排第一轮(tmp_path):
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w1", "p1", tmp_path)
        _window(db, "w2", "p1", tmp_path)
        _window(db, "w9", "p2", tmp_path) if db.get_project("p2") else None
        svc = _svc(db)
        entry = svc.create("p1", "把登录做出来", mode="standard")
        assert sorted(entry["window_ids"]) == ["w1", "w2"], "默认参与者=项目上的工位"
        assert entry["next_run_at"], "建完立刻排第一轮"
        assert svc.effective_plan(entry)["interval_minutes"] == 60
    finally:
        db.close()


def test_T8_5_建计划校验(tmp_path):
    db = _db(tmp_path)
    try:
        svc = _svc(db)
        with pytest.raises(ValueError, match="项目不存在"):
            svc.create("nope", "目标")
        _project(db, tmp=tmp_path)
        with pytest.raises(ValueError, match="目标不能为空"):
            svc.create("p1", "   ")
        with pytest.raises(ValueError, match="没有参与工位"):
            svc.create("p1", "目标")   # 项目没挂工位
        with pytest.raises(ValueError, match="未知档位"):
            svc.create("p1", "目标", window_ids=["w1"], mode="疯狂")
    finally:
        db.close()


def test_T8_5_停止恢复删除(tmp_path):
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w1", "p1", tmp_path)
        svc = _svc(db)
        e = svc.create("p1", "目标", window_ids=["w1"])
        assert svc.stop(e["id"])["status"] == "stopped"
        assert svc.stop(e["id"])["stop_reason"] == "手动停止"
        resumed = svc.resume(e["id"])
        assert resumed["status"] == "active" and resumed["next_run_at"]
        assert svc.update(e["id"], mode="aggressive")["mode"] == "aggressive"
        assert svc.remove(e["id"]) is True
        assert svc.get(e["id"]) is None
    finally:
        db.close()


# --------------------------------------------------------------------------- T8.6
def _executor(db, svc, rounds, bus=None):
    async def fake_round(entry, plan, round_no):
        return rounds.pop(0) if rounds else {"status": "ok", "summary": "noop"}

    return IterationExecutor(service=svc, db=db, run_round=fake_round,
                             bus=bus or EventBus())


def test_T8_6_跑一轮_记轮次_排下次(tmp_path):
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w1", "p1", tmp_path)
        svc = _svc(db)
        e = svc.create("p1", "目标", mode="standard", window_ids=["w1"])
        ex = _executor(db, svc, [{"status": "ok", "summary": "写了 schema",
                                  "progress": 20, "goal_achieved": False}])
        out = _run(ex.run_one(e["id"]))
        assert out["round"] == 1 and out["progress"] == 20
        after = svc.get(e["id"])
        assert after["round"] == 1
        assert after["last_progress"] == "20"
        assert after["no_progress_streak"] == 0
        assert after["next_run_at"], "没到停止条件就要排下一轮"
        rounds = db.list_iteration_rounds(e["id"])
        assert len(rounds) == 1 and rounds[0]["progress"] == 20
        assert "写了 schema" in rounds[0]["summary"]
    finally:
        db.close()


def test_T8_6_达成目标即停并通知(tmp_path):
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w1", "p1", tmp_path)
        svc = _svc(db)
        e = svc.create("p1", "目标", window_ids=["w1"])
        bus = EventBus()
        events: list = []
        bus.emit = lambda ev: events.append(ev)  # type: ignore[assignment]
        ex = _executor(db, svc, [{"status": "ok", "summary": "做完了",
                                  "progress": 100, "goal_achieved": True}], bus=bus)
        out = _run(ex.run_one(e["id"]))
        assert out["stopped"] is True
        after = svc.get(e["id"])
        assert after["status"] == "done" and after["stop_reason"] == "已达成目标"
        assert after["next_run_at"] == ""
        stopped = [ev for ev in events if ev.type == "iteration.stopped"]
        assert stopped and stopped[0].payload["reason"] == "已达成目标"
    finally:
        db.close()


def test_T8_6_连续无进展按上限停(tmp_path):
    """工位没汇报进展（progress=None）也要算"无进展"——否则会永远转下去。"""
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w1", "p1", tmp_path)
        svc = _svc(db)
        e = svc.create("p1", "目标", mode="conservative",   # 2 轮无进展就停
                       window_ids=["w1"])
        ex = _executor(db, svc, [
            {"status": "ok", "summary": "没干活"},
            {"status": "ok", "summary": "还是没干活"},
        ])
        _run(ex.run_one(e["id"]))
        assert svc.get(e["id"])["status"] == "active"
        out = _run(ex.run_one(e["id"]))
        assert out["stopped"] is True
        after = svc.get(e["id"])
        assert after["status"] == "done"
        assert "无进展" in after["stop_reason"]
        assert after["no_progress_streak"] == 2
    finally:
        db.close()


def test_T8_6_进度持平也算无进展_有进展则清零(tmp_path):
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w1", "p1", tmp_path)
        svc = _svc(db)
        e = svc.create("p1", "目标", mode="standard", window_ids=["w1"])
        ex = _executor(db, svc, [
            {"status": "ok", "summary": "a", "progress": 40},
            {"status": "ok", "summary": "b", "progress": 40},   # 持平 → streak 1
            {"status": "ok", "summary": "c", "progress": 55},   # 有进展 → 清零
            {"status": "ok", "summary": "d", "progress": 55},   # 持平 → streak 1
        ])
        _run(ex.run_one(e["id"]))
        assert svc.get(e["id"])["no_progress_streak"] == 0
        _run(ex.run_one(e["id"]))
        assert svc.get(e["id"])["no_progress_streak"] == 1
        _run(ex.run_one(e["id"]))
        assert svc.get(e["id"])["no_progress_streak"] == 0, "有进展必须清零"
        _run(ex.run_one(e["id"]))
        assert svc.get(e["id"])["no_progress_streak"] == 1
    finally:
        db.close()


def test_T8_6_次数上限停(tmp_path):
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w1", "p1", tmp_path)
        svc = _svc(db)
        e = svc.create("p1", "目标", window_ids=["w1"], max_rounds=2)
        ex = _executor(db, svc, [
            {"status": "ok", "summary": "1", "progress": 10},
            {"status": "ok", "summary": "2", "progress": 20},
        ])
        _run(ex.run_one(e["id"]))
        out = _run(ex.run_one(e["id"]))
        assert out["stopped"] is True
        assert "次数上限" in svc.get(e["id"])["stop_reason"]
    finally:
        db.close()


def test_T8_6_单轮崩溃算无进展不炸执行器(tmp_path):
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w1", "p1", tmp_path)
        svc = _svc(db)
        e = svc.create("p1", "目标", window_ids=["w1"])

        async def boom(entry, plan, round_no):
            raise RuntimeError("模型挂了")

        ex = IterationExecutor(service=svc, db=db, run_round=boom, bus=EventBus())
        out = _run(ex.run_one(e["id"]))
        assert out["status"] == "failed"
        assert svc.get(e["id"])["no_progress_streak"] == 1
        assert svc.get(e["id"])["status"] == "active", "单轮失败不该直接停掉迭代"
    finally:
        db.close()


def test_T8_6_非_active_不跑(tmp_path):
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w1", "p1", tmp_path)
        svc = _svc(db)
        e = svc.create("p1", "目标", window_ids=["w1"])
        svc.stop(e["id"])
        ex = _executor(db, svc, [{"status": "ok", "summary": "不该跑"}])
        out = _run(ex.run_one(e["id"]))
        assert out.get("skipped") is True
        assert db.list_iteration_rounds(e["id"]) == []
    finally:
        db.close()


def test_T8_6_到点才被_tick_捞起(tmp_path):
    db = _db(tmp_path)
    try:
        _project(db, tmp=tmp_path)
        _window(db, "w1", "p1", tmp_path)
        svc = _svc(db)
        ex = _executor(db, svc, [{"status": "ok", "summary": "x", "progress": 5}])
        e = svc.create("p1", "目标", window_ids=["w1"])
        _run(ex.tick())            # next_run_at 是刚刚 → 应被捞起
        import time
        time.sleep(0.2)            # 让 create_task 跑完
        assert svc.get(e["id"])["round"] == 1
        # 跑完排到未来 → 立刻再 tick 不该重复跑
        _run(ex.tick())
        time.sleep(0.2)
        assert svc.get(e["id"])["round"] == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- 汇报工具
def test_T8_6_汇报工具写入_ctx_extra(tmp_path):
    ctx = ToolContext(window_id="w1", workspace=str(tmp_path), mode="BUILD", extra={})
    res = _run(ReportIterationTool().execute(
        {"progress_percent": 45, "note": "改了 schema，下一步补测试",
         "goal_achieved": False}, ctx))
    assert res.ok
    assert ctx.extra["iteration_progress"] == 45
    assert "下一步补测试" in ctx.extra["iteration_note"]
    assert ctx.extra["iteration_goal_achieved"] is False


def test_T8_6_汇报工具边界(tmp_path):
    ctx = ToolContext(window_id="w1", workspace=str(tmp_path), mode="BUILD", extra={})
    assert not _run(ReportIterationTool().execute({"note": "没有数字"}, ctx)).ok
    assert not _run(ReportIterationTool().execute(
        {"progress_percent": 10, "note": "  "}, ctx)).ok
    # 超范围被夹住而不是报错（模型给了 150 也不该让整轮失败）
    ok = _run(ReportIterationTool().execute(
        {"progress_percent": 150, "note": "x"}, ctx))
    assert ok.ok and ctx.extra["iteration_progress"] == 100


def test_T8_6_汇报工具在员工工具箱里():
    from dogbuddy.agents.core.roles import WORKER_PROGRAMMER_TOOLS
    assert "report_iteration" in WORKER_PROGRAMMER_TOOLS
    from dogbuddy.tools.registry import build_registry
    assert "report_iteration" in build_registry().names()


# --------------------------------------------------------------------------- HTTP
def _client(tmp_path, monkeypatch):
    from dogbuddy.main import DogBuddyApp
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="tok", port=8130),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = "tok"
    return app, client


def test_T8_6_路由需要鉴权(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        assert client.get("/api/iterations", headers={"X-Auth-Token": ""}).status_code == 401


def test_T8_6_路由_建计划与预设(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        presets = client.get("/api/iterations/presets").json()["presets"]
        assert {x["mode"] for x in presets} == set(PRESETS)
        assert all("hint" in x and "label" in x for x in presets)

        proj = client.post("/api/projects", json={"name": "P", "root_path": str(tmp_path / "p")}
                           ).json()["project"]
        w = app.windows.create("工人", "worker_programmer", str(tmp_path / "w"))
        client.post("/api/projects/mount", json={"window_id": w.id, "project_id": proj["id"]})

        created = client.post("/api/iterations", json={
            "project_id": proj["id"], "goal": "把模型管理页做出来",
            "mode": "conservative"}).json()
        iid = created["iteration"]["id"]
        assert created["plan"]["interval_minutes"] == PRESETS["conservative"]["speed_minutes"]
        assert created["iteration"]["window_ids"] == [w.id]

        listed = client.get("/api/iterations", params={"project_id": proj["id"]}).json()
        assert len(listed["iterations"]) == 1 and "plan" in listed["iterations"][0]

        detail = client.get(f"/api/iterations/{iid}").json()
        assert detail["plan"]["max_rounds"] == PRESETS["conservative"]["max_rounds"]
        assert detail["rounds"] == []

        # 手动覆盖
        patched = client.patch(f"/api/iterations/{iid}",
                               json={"speed_minutes": 5}).json()
        assert patched["plan"]["interval_minutes"] == 5
        assert patched["plan"]["derived"]["interval"] == "manual"

        # 停止 / 恢复 / 删除
        assert client.post(f"/api/iterations/{iid}/stop").json()["iteration"]["status"] == "stopped"
        assert client.post(f"/api/iterations/{iid}/resume").json()["iteration"]["status"] == "active"
        assert client.delete(f"/api/iterations/{iid}").status_code == 200
        assert client.get(f"/api/iterations/{iid}").status_code == 404


def test_T8_6_路由_手动推一轮(tmp_path, monkeypatch):
    """run_now 走真实执行器：注入一个假单轮，确认 HTTP 层能把结果带回来。"""
    app, client = _client(tmp_path, monkeypatch)
    with client:
        proj = client.post("/api/projects", json={"name": "P2", "root_path": str(tmp_path / "p2")}
                           ).json()["project"]
        w = app.windows.create("工人2", "worker_programmer", str(tmp_path / "w2"))
        client.post("/api/projects/mount", json={"window_id": w.id, "project_id": proj["id"]})
        iid = client.post("/api/iterations", json={
            "project_id": proj["id"], "goal": "g"}).json()["iteration"]["id"]

        async def fake_round(entry, plan, round_no):
            return {"status": "ok", "summary": "做了一点", "progress": 30,
                    "goal_achieved": False}

        app.iteration_exec._run_round = fake_round
        r = client.post(f"/api/iterations/{iid}/run_now").json()["result"]
        assert r["round"] == 1 and r["progress"] == 30 and r["stopped"] is False
        # 手动推的那一轮也记进轮次表
        assert len(client.get(f"/api/iterations/{iid}").json()["rounds"]) == 1


def test_T8_6_路由_建计划参数错误返回400(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        assert client.post("/api/iterations", json={
            "project_id": "nope", "goal": "g"}).status_code == 400
        proj = client.post("/api/projects", json={"name": "P3", "root_path": str(tmp_path / "p3")}
                           ).json()["project"]
        # 项目没挂工位 → 400
        assert client.post("/api/iterations", json={
            "project_id": proj["id"], "goal": "g"}).status_code == 400
        assert client.get("/api/iterations/nope").status_code == 404
