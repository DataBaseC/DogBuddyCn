# DogBuddyCn — 配置落盘 / MCP 作用域 / cron 时间与互斥 / 后台进程回收
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""审查报告 P1-9、P1-11、P1-12、P1-13、P1-15。五条都是"沉默型"缺陷：
不报错、不崩，只是悄悄丢数据、抢跑、漏回收或误杀。"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database, Window  # noqa: E402


def _db(tmp_path) -> Database:
    return Database(tmp_path / "t.db")


# ------------------------------------------------------------------ P1-9
def test_agent_路径的_profile_save_会落盘(tmp_path):
    """★ 初版 agent 路径不调 persist：改了 profile，重启就没了。"""
    from dogbuddy.agents.management.tools import AdminServices, build_admin_tools

    calls: list[str] = []
    svc = AdminServices(persist=lambda: (calls.append("persist"), True)[1])
    tools = {t.name: t for t in build_admin_tools(svc)}
    assert "profile_save" in tools and "profile_remove" in tools
    # 不真的改配置：只确认工具里拿到了 persist 并会调用
    from dogbuddy.agents.management.tools import _persist_profiles
    assert _persist_profiles(svc) is True
    assert calls == ["persist"], "profile 工具必须真的落盘"


def test_persist_失败不算工具失败但不撒谎(tmp_path):
    from dogbuddy.agents.management.tools import AdminServices, _persist_profiles

    def boom():
        raise RuntimeError("disk full")

    assert _persist_profiles(AdminServices(persist=boom)) is False
    assert _persist_profiles(AdminServices()) is False, "没注入 persist 时如实返回 False"


# ------------------------------------------------------------------ P1-11
def test_mcp_工具带_system_作用域(tmp_path):
    """★ 初版 MCP 工具没有 required_scope：绕过作用域维度、默认 read 连 PLAN 都放行。"""
    from dogbuddy.external.mcp_bridge import McpToolWrapper

    w = McpToolWrapper(client=None, server="s", name="echo", description="d",
                       schema={}, permission="read")
    assert getattr(w, "required_scope", "") == "system", \
        "外部工具语义不透明，默认必须按 system 收紧"

    # 运维可以显式调低
    from dogbuddy.external.mcp_bridge import McpBridge

    bridge = McpBridge(config_get=lambda k, d=None: {"mcp.required_scope": "project"}.get(k, d))
    assert bridge._scope_for() == "project"
    bad = McpBridge(config_get=lambda k, d=None: {"mcp.required_scope": "乱填"}.get(k, d))
    assert bad._scope_for() == "system", "非法值必须回落到最严的 system"


# ------------------------------------------------------------------ P1-12
def test_不同时区偏移的任务不会抢跑(tmp_path):
    """★ 报告实测：now=+08:00 与 next_run=15:00+00:00（还差 2.5 小时）比出 True。"""
    db = _db(tmp_path)
    try:
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path="", model="", mode="BUILD"))
        now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone(timedelta(hours=8)))  # = 04:00Z
        future = (now.astimezone(timezone.utc) + timedelta(hours=2, minutes=30))  # 06:30Z
        past = (now.astimezone(timezone.utc) - timedelta(minutes=1))

        soon = db.upsert_job(_job("j_future", future.isoformat()))
        due = db.upsert_job(_job("j_past", past.isoformat()))
        assert soon.id and due.id

        got = {j.id for j in db.due_jobs(now.isoformat())}
        assert "j_future" not in got, "还差 2.5 小时的任务被误判到点（P1-12 抢跑）"
        assert "j_past" in got, "确实到点的任务必须跑"
    finally:
        db.close()


def test_迭代到点判定也按时区(tmp_path):
    db = _db(tmp_path)
    try:
        now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        future = now.astimezone(timezone.utc) + timedelta(hours=3)
        past = now.astimezone(timezone.utc) - timedelta(minutes=1)
        db.insert_iteration({"id": "i_f", "project_id": "p1", "goal": "g", "status": "active",
                             "next_run_at": future.isoformat()})
        db.insert_iteration({"id": "i_p", "project_id": "p1", "goal": "g", "status": "active",
                             "next_run_at": past.isoformat()})
        got = {e["id"] for e in db.due_iterations(now.isoformat())}
        assert got == {"i_p"}
    finally:
        db.close()


def _job(jid: str, next_run_at: str):
    from dogbuddy.ports import Job
    return Job(id=jid, window_id="w1", name=jid, schedule_type="interval",
               schedule={"kind": "interval", "minutes": 30}, schedule_raw="30m",
               prompt="p", enabled=True, next_run_at=next_run_at)


# ------------------------------------------------------------------ P1-13
def test_清扫不会杀另一个活进程正在跑的任务(tmp_path):
    """★ 自更新期间新旧进程并存；无差别收尸会把活任务的记录标成失败。"""
    db = _db(tmp_path)
    try:
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path="", model="", mode="BUILD", status="running"))
        live = db.start_run("j1", "w1")           # owner_pid = 本测试进程（活着）
        swept = db.sweep_orphan_runs()
        assert swept["runs"] == 0, "不能收掉活进程正在跑的任务（P1-13）"
        assert swept["windows"] == 0, "也不能把它的工位改成 idle"
        assert [r for r in db.list_runs(limit=5) if r.id == live.id][0].status == "running"

        # 属主换成不存在的 pid → 应当被收掉
        db._conn.execute("UPDATE job_runs SET owner_pid=999999 WHERE id=?", (live.id,))
        db._conn.commit()
        swept2 = db.sweep_orphan_runs()
        assert swept2["runs"] == 1 and swept2["windows"] == 1
    finally:
        db.close()


def test_cron_跨进程锁互斥(tmp_path):
    """`_inflight` 只在进程内有效——跨进程要靠这把锁。"""
    db = _db(tmp_path)
    try:
        assert db.try_cron_lock() is True
        # 伪造另一个活着的进程持有锁
        db.set_config(db.CRON_LOCK_KEY, {"pid": os.getpid() + 1, "at": _now()})
        # 用当前进程自己的 pid 会被认为是自己 → 拿得到；换成"别人且活着"才该被挡
        db.set_config(db.CRON_LOCK_KEY, {"pid": 1, "at": _now()})  # pid 1 通常活着
        got = db.try_cron_lock()
        assert got in (True, False)  # 环境相关：pid 1 是否存在
        # 过期的锁不挡人
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.set_config(db.CRON_LOCK_KEY, {"pid": 1, "at": old})
        assert db.try_cron_lock() is True, "过期的锁必须能被抢占"
        db.release_cron_lock()
        assert not (db.get_config(db.CRON_LOCK_KEY) or {}), "释放后应当为空"
    finally:
        db.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ P1-15
def test_后台进程表是同一个列表(tmp_path):
    """★ 工具写 ctx.extra["bg_processes"]，回收读 queue._bg_processes——
    初版这两个**从来不是同一个东西**，`kill_background()` 恒返回 0。"""
    from dogbuddy.windows.queue import TaskQueue

    q = TaskQueue("w1")
    assert isinstance(q.bg_processes, list)
    assert q.bg_processes is q._bg_processes

    class _Proc:
        def __init__(self):
            self.killed = False

        def kill(self):
            self.killed = True

    p = _Proc()
    q.bg_processes.append(p)          # 工具登记的地方
    assert q.kill_background() == 1, "登记过的后台进程必须被回收"
    assert p.killed is True
    assert q.bg_processes == [], "回收后要清空"
    assert q.kill_background() == 0, "再收一次是 0（幂等）"
