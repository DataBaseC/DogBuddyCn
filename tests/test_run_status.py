# DogBuddyCn — run_status 工具回归（v0.10.20）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""圆桌测试（2026-10-15）qa-ops 想查自己的上下文预算：`env` 被边界守卫以
"可伪造环境"拒、`/proc/*` 以越界拒 → 只能瞎跑，最后撞 `max iterations reached`。
`run_status` 给工人一个**正规的只读入口**替代它去撞 `env`/`/proc`。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_run_status_reports_budget(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    ws = tmp_path / "ws"
    ws.mkdir()
    db.insert_window(Window(id="w1", name="dev", role="worker_programmer",
                            workspace_path=str(ws), model="", mode="BUILD"))
    db.add_message("w1", "assistant", "x" * 100, tokens=500)
    reg = build_registry()
    ctx = ToolContext(window_id="w1", workspace=str(ws), mode="BUILD",
                      actor="worker", db=db)
    res = _run(reg.execute("run_status", {}, ctx))
    assert res.ok, res.output
    out = res.output
    assert "dev" in out and "已用 token" in out and "500" in out
    assert "轮次上限" in out and "单轮等待上限" in out
    assert "信任级" in out and str(ws) in out and "可写=是" in out
    assert "人工/对话触发" in out
    db.close()


def test_run_status_marks_scheduled_runs(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    ws = tmp_path / "ws"
    ws.mkdir()
    db.insert_window(Window(id="w1", name="dev", role="worker_programmer",
                            workspace_path=str(ws), model="", mode="BUILD"))
    reg = build_registry()
    ctx = ToolContext(window_id="w1", workspace=str(ws), mode="BUILD",
                      actor="worker", db=db, extra={"scheduled": True})
    res = _run(reg.execute("run_status", {}, ctx))
    assert "定时任务触发" in res.output
    db.close()


def test_run_status_unknown_window_fails_clearly(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    reg = build_registry()
    ctx = ToolContext(window_id="nope", workspace=str(tmp_path), mode="BUILD",
                      actor="worker", db=db)
    res = _run(reg.execute("run_status", {}, ctx))
    assert res.ok is False and "not found" in res.output
    db.close()


def test_run_status_in_worker_toolset():
    """员工白名单里必须有它——否则模型看不到（P1-3 那类"架子上有、白名单没有"）。"""
    from dogbuddy.agents.core.roles import WORKER_PROGRAMMER_TOOLS
    assert "run_status" in WORKER_PROGRAMMER_TOOLS
