# DogBuddyCn — 群组运行的挂钟预算回归（v0.10.22）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测（2026-10-16，统一模型实验）：三个工位都指向同一个 key 时，
`deepseek-v4-flash` 的限速一路升级到单次退避 **600 秒**（`retry_memory.json`:
`last_interval=600, waits=7, severity=heavy`），5 分钟只跑完 1/3 个成员。

限速退避本身是"无限耐心"的设计（heavy 档还会追加重试预算），而 `GroupManager.run`
**没有任何总时间上限，也没有取消入口** —— 一次群组运行可以挂几小时。
这里用已有的 `task_timeout` 给每个成员封顶：到点停下并如实记 timeout。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.roles import register_builtin_roles  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.group_manager import _reconcile_timed_out_steps  # noqa: E402
from dogbuddy.llm.provider import LLMResponse  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.services.group_manager import GroupManager  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class SlowLLM:
    """永远"在限速里等"的假模型：一次 chat 要 sleep 很久。"""

    def __init__(self, delay: float = 3.0):
        self.delay = delay

    async def chat(self, messages, tools=None, **kw):
        await asyncio.sleep(self.delay)
        return LLMResponse(content="done")

    async def stream_chat(self, messages, tools=None, **kw):
        await asyncio.sleep(self.delay)
        yield type("C", (), {"delta": "", "tool_calls": [], "finish_reason": "stop",
                             "usage": {}, "done": True})()


def _stack(tmp_path, task_timeout: float):
    register_builtin_roles()
    db = Database(str(tmp_path / "g.db"))
    llm = SlowLLM(delay=3.0)
    wm = WindowManager(db, llm_factory=lambda *a, **k: llm, bus=None,
                       checkpoints_dir=tmp_path / "ck")
    # 生产里参数解析器由 main 装配（config.effective_params）；这里等价注入，
    # 顺带钉住"群组预算取的是工位自己的 task_timeout"。
    wm.param_resolver = lambda w, project=None: {
        "max_iterations": 25, "max_context_tokens": 128000,
        "compact_threshold": 0.8, "task_timeout": task_timeout}
    for name in ("alice", "bob"):
        wm.create(name, workspace_path=str(tmp_path / name))
    gm = GroupManager(db, windows=wm)
    gm.create("slow-team")
    gm.assign("slow-team", db.get_window_by_name("alice").id, role="member")
    gm.assign("slow-team", db.get_window_by_name("bob").id, role="lead")
    return db, gm


def test_成员超过挂钟预算就停下并记timeout(tmp_path):
    db, gm = _stack(tmp_path, task_timeout=0.3)
    result = _run(gm.run("slow-team", "任务"))
    # v0.10.33：组长现在有"开场编排 + 收尾汇总"两轮 → 2 名成员的组一共 3 步
    assert len(result["steps"]) == 3, "每个发言都要有交代（不能无声消失）"
    assert [x.get("phase") for x in result["steps"]] == ["plan", "work", "summary"]
    assert all(s["ok"] is False for s in result["steps"])
    assert all(s.get("timeout") is True for s in result["steps"])
    assert "超时" in result["steps"][0]["summary"]
    assert "task_timeout" in result["steps"][0]["summary"], "要告诉用户怎么放宽"
    db.close()


def test_预算充足时正常跑完(tmp_path):
    db, gm = _stack(tmp_path, task_timeout=30)
    result = _run(gm.run("slow-team", "任务"))
    assert all(s["ok"] for s in result["steps"]), result["steps"]
    assert not any(s.get("timeout") for s in result["steps"])
    db.close()


def test_每步都回报recap用量与提示词长度(tmp_path):
    db, gm = _stack(tmp_path, task_timeout=30)
    result = _run(gm.run("slow-team", "任务"))
    for s in result["steps"]:
        assert "recap_tokens" in s and "prompt_chars" in s
    assert result["recap_total"]["chars"] >= 0
    db.close()


# ---------------------------------------------------------- v0.10.38 终态诚实化
def _mixed_stack(tmp_path, task_timeout: float, slow_all: bool = False):
    """一个慢工位（必超时）+ 一个快工位（能跑完）的组，挂 runs 台账。
    `slow_all=True` 时两个都慢（用于"一步都没成"的场景）。"""
    register_builtin_roles()
    db = Database(str(tmp_path / "m.db"))
    fast, slow = SlowLLM(delay=0.01), SlowLLM(delay=3.0)

    def factory(profile=None, model=None, overrides=None):
        return slow if (slow_all or model == "slow-model") else fast

    wm = WindowManager(db, llm_factory=factory, bus=None,
                       checkpoints_dir=tmp_path / "ck")
    wm.param_resolver = lambda w, project=None: {
        "max_iterations": 25, "max_context_tokens": 128000,
        "compact_threshold": 0.8, "task_timeout": task_timeout}
    wm.create("alice", workspace_path=str(tmp_path / "a"), model="slow-model")
    wm.create("bob", workspace_path=str(tmp_path / "b"))
    gm = GroupManager(db, windows=wm)
    from dogbuddy.services.run_registry import RunRegistry
    gm.runs = RunRegistry(db)
    gm.create("mixed-team")
    gm.assign("mixed-team", db.get_window_by_name("alice").id, role="member")
    gm.assign("mixed-team", db.get_window_by_name("bob").id, role="lead")
    return db, gm


def test_有步骤没完成_终态是partial而不是done(tmp_path):
    """回归（2026-10-17 实测）：4/4 名成员全被限速暂停、零产出的群组运行，
    台账以前也写 `done`——上级探测（progress_report/顶栏/runs API）看到的是
    "完成"，真相藏在 summary 的一行字里。现在"完成了几步"是一等信号。"""
    db, gm = _mixed_stack(tmp_path, task_timeout=1.0)
    result = _run(gm.run("mixed-team", "任务"))
    assert result["status"] == "partial", f"有步骤没完成必须是 partial: {result['status']}"
    assert result["completed_steps"] == 2 and result["total_steps"] == 3
    assert "[完成 2/3 步]" in result["summary"]
    run = gm.runs.get(result["run_id"])
    assert run is not None and run.status == "partial", "台账里也必须是 partial"
    db.close()


def test_全步骤完成_终态还是done(tmp_path):
    """不误报：大家都跑完时保持 done，不因为新逻辑变成 partial。"""
    db, gm = _mixed_stack(tmp_path, task_timeout=30)
    result = _run(gm.run("mixed-team", "任务"))
    assert result["status"] == "done"
    assert result["completed_steps"] == result["total_steps"] == 3
    assert gm.runs.get(result["run_id"]).status == "done"
    db.close()


def test_一步都没成_终态是failed(tmp_path):
    """全员超时（限速把预算吃光）→ 一步都没成 → failed，不是 done 也不是 partial。"""
    db, gm = _mixed_stack(tmp_path, task_timeout=0.3, slow_all=True)
    result = _run(gm.run("mixed-team", "任务"))
    assert result["status"] == "failed"
    assert result["completed_steps"] == 0
    assert "[完成 0/3 步]" in result["summary"]
    db.close()


# ---------------------------------------------- v0.10.42 超时步对账（实测 68 分钟流水线）
class _FakeRuns:
    def __init__(self, rows):
        self._rows = rows

    def recent_for_window(self, wid, limit=3):
        return self._rows


def test_timed_out_step_reconciled_when_finished_in_background():
    """实测（2026-10-20）：组长 plan 步 30 分钟判超时，但它在后台把整个两轮
    流水线跑完了（4 个产物、审查 PASS），台账却记 partial 3/4——活干完了却说
    没干完。对账后用 runs 里的 done 补回 ok。"""
    steps = [{"window_id": "w1", "ok": False, "timeout": True,
              "started_at": "2026-10-20T13:00:00+00:00", "summary": "[超时] 没跑完"}]
    runs = _FakeRuns([{"id": "r-abc", "status": "done",
                       "started_at": "2026-10-20T13:00:03+00:00"}])
    n = _reconcile_timed_out_steps(steps, runs)
    assert n == 1
    assert steps[0]["ok"] is True and steps[0]["reconciled"] is True
    assert steps[0]["run_id"] == "r-abc"
    assert steps[0]["summary"].startswith("[超时后于后台完成]")


def test_reconcile_ignores_run_started_before_the_step():
    """更早的一次 done 不能算到这一步头上（否则会一直"自愈"成 ok）。"""
    steps = [{"window_id": "w1", "ok": False, "timeout": True,
              "started_at": "2026-10-20T13:00:00+00:00", "summary": "[超时]"}]
    runs = _FakeRuns([{"id": "r-old", "status": "done",
                       "started_at": "2026-10-20T12:00:00+00:00"}])
    assert _reconcile_timed_out_steps(steps, runs) == 0
    assert steps[0]["ok"] is False


def test_reconcile_requires_done_not_running_or_failed():
    steps = [{"window_id": "w1", "ok": False, "timeout": True,
              "started_at": "2026-10-20T13:00:00+00:00", "summary": "[超时]"}]
    for status in ("running", "failed", "partial", "cancelled"):
        runs = _FakeRuns([{"id": "r-x", "status": status,
                           "started_at": "2026-10-20T13:00:05+00:00"}])
        assert _reconcile_timed_out_steps(steps, runs) == 0, status
        assert steps[0]["ok"] is False


def test_reconcile_without_registry_or_non_timeout_steps_is_noop():
    steps = [{"window_id": "w1", "ok": False, "timeout": True, "started_at": ""}]
    assert _reconcile_timed_out_steps(steps, None) == 0
    ok_steps = [{"window_id": "w1", "ok": True, "timeout": False}]
    assert _reconcile_timed_out_steps(ok_steps, _FakeRuns([])) == 0