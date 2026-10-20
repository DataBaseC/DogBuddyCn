# DogBuddyCn — 唤醒未完成的运行（v0.10.40）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner：「要确保因为异常导致的循环停止后，管理员能够唤醒未完成的任务。」

查明的硬缺口：
1. retry 从内存 `_manual_last` 取 prompt —— 进程重启后 400「请重新派单」
   （而"异常导致循环停止"最常见的场景就是崩溃/重启）；
2. 管理员**没有**重试工具；
3. 群组运行完全没有重试路径；
4. 撞迭代上限被记成 done —— owner 以为活干完了。

这些测试钉住：prompt 落库、重启后可重试、活着的先取消、群组/定时任务分派、
老数据（无 prompt）诚实拒绝、以及 max_iterations → partial。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.roles import ADMIN_TOOLS  # noqa: E402
from dogbuddy.agents.management.tools import (  # noqa: E402
    SYSTEM_TOOL_NAMES, AdminServices, build_management_tools)
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.run_registry import RunRegistry  # noqa: E402
from dogbuddy.services.run_retry import retry_run  # noqa: E402
from dogbuddy.services.window_manager import _run_terminal  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeWindows:
    def __init__(self):
        self.dispatched: list[tuple[str, str]] = []
        self.cancelled: list[str] = []
        self._manual_last: dict = {}
        self._n = 0

    def dispatch_manual(self, wid, prompt):
        self.dispatched.append((wid, prompt))
        self._n += 1
        self._manual_last[wid] = {"prompt": prompt, "run_id": f"r-new{self._n}"}
        return f"r-new{self._n}"

    def cancel_run(self, rid):
        self.cancelled.append(rid)
        return {"ok": True, "status": "cancelled", "interrupted": True}


class FakeGroups:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def run(self, gid, prompt):
        self.calls.append((gid, prompt))
        return {"status": "done"}


class FakeJobs:
    def __init__(self):
        self.now: list[str] = []

    def run_now(self, jid):
        self.now.append(jid)
        return "dispatched"


def _svc(tmp_path):
    db = Database(str(tmp_path / "r.db"))
    return SimpleNamespace(runs=RunRegistry(db), windows=FakeWindows(),
                           groups=FakeGroups(), jobs=FakeJobs()), db


# ------------------------------------------------------------ prompt 落库
def test_prompt_persists_across_restart(tmp_path):
    """核心：prompt 必须落库——重启后管理员仍能唤回任务原文。"""
    svc, db = _svc(tmp_path)
    run = svc.runs.start(kind="manual", window_id="w1", prompt="把首页做完")
    svc.runs.finish(run.id, "interrupted", summary="进程重启中断")
    # 模拟进程重启：换一个全新的 registry 实例（内存全空）
    fresh = RunRegistry(db)
    loaded = fresh.get(run.id)
    assert loaded is not None
    assert loaded.prompt == "把首页做完", "prompt 必须落库"
    assert loaded.prompt_chars == len("把首页做完"), "prompt_chars 没显式给时按原文长度记"
    db.close()


def test_retry_after_restart_redispatches_original_prompt(tmp_path):
    svc, db = _svc(tmp_path)
    run = svc.runs.start(kind="manual", window_id="w1", prompt="把首页做完")
    svc.runs.finish(run.id, "interrupted")
    # 重启：新 registry + **空的内存派单记录**
    svc.runs = RunRegistry(db)
    out = _run(retry_run(svc, run.id))
    assert out["ok"], out
    assert out["mode"] == "window"
    assert svc.windows.dispatched == [("w1", "把首页做完")], "必须用落库的原文重派"
    assert out["run_id"].startswith("r-new")
    db.close()


def test_retry_refuses_legacy_run_without_prompt(tmp_path):
    """0.10.40 之前的运行没存原文——诚实拒绝，别瞎猜任务内容。"""
    svc, db = _svc(tmp_path)
    run = svc.runs.start(kind="manual", window_id="w1")  # 没有 prompt
    svc.runs.finish(run.id, "failed")
    out = _run(retry_run(svc, run.id))
    assert not out["ok"]
    assert "重新派单" in out["reason"]
    assert svc.windows.dispatched == []
    db.close()


def test_retry_cancels_live_run_first(tmp_path):
    """活着的（卡住的）运行先取消——"唤醒"是重来一次，不是再叠一个。"""
    svc, db = _svc(tmp_path)
    run = svc.runs.start(kind="manual", window_id="w1", prompt="整理工作区",
                         status="paused")
    svc.runs.note_pause(run.id, 12.0)
    out = _run(retry_run(svc, run.id))
    assert out["ok"], out
    assert svc.windows.cancelled == [run.id], "必须先停掉卡住的那次"
    assert "已先停掉" in out["note"]
    assert svc.windows.dispatched == [("w1", "整理工作区")]
    db.close()


def test_retry_group_run_starts_background(tmp_path):
    svc, db = _svc(tmp_path)
    run = svc.runs.start(kind="group", group_id="g1", group_name="模板站",
                         prompt="迭代首页")

    async def scenario():
        out = await retry_run(svc, run.id)
        await asyncio.sleep(0)  # 让后台任务起步（群组运行不能被 admin 回合阻塞）
        return out

    out = _run(scenario())
    assert out["ok"] and out["mode"] == "group", out
    assert svc.groups.calls == [("g1", "迭代首页")]
    db.close()


def test_retry_job_run_calls_run_now(tmp_path):
    svc, db = _svc(tmp_path)
    run = svc.runs.start(kind="scheduled", job_id="job-1", window_id="w1",
                         prompt="每日汇报")
    out = _run(retry_run(svc, run.id))
    assert out["ok"] and out["mode"] == "job"
    assert svc.jobs.now == ["job-1"]
    db.close()


def test_retry_unknown_run_is_honest(tmp_path):
    svc, db = _svc(tmp_path)
    out = _run(retry_run(svc, "r-nope"))
    assert not out["ok"] and "没有这个运行" in out["reason"]
    db.close()


# ------------------------------------------------------------ 管理员工具
def test_run_retry_tool_registered():
    names = {t.name for t in build_management_tools(AdminServices())}
    assert "run_retry" in names, "管理工具包里必须有 run_retry"
    assert "run_retry" in ADMIN_TOOLS, "ADMIN_TOOLS 漏登记（AgentLoop 不会展示它）"
    assert "run_retry" in SYSTEM_TOOL_NAMES, "只有管理员能给（唤醒是全局动作）"


def test_run_retry_tool_reports_reason(tmp_path):
    svc, db = _svc(tmp_path)
    admin = AdminServices(runs=svc.runs, windows=svc.windows,
                          groups=svc.groups, jobs=svc.jobs)
    tools = {t.name: t for t in build_management_tools(admin)}
    from dogbuddy.tools.registry import ToolContext
    ctx = ToolContext(window_id="admin", workspace=".", mode="AUTO", actor="admin")
    res = _run(tools["run_retry"].execute({"run_id": "r-missing"}, ctx))
    assert not res.ok and "没有这个运行" in res.output
    run = svc.runs.start(kind="manual", window_id="w1", prompt="x")
    res2 = _run(tools["run_retry"].execute({"run_id": run.id}, ctx))
    assert res2.ok and "已唤醒" in res2.output
    db.close()


# ------------------------------------------------------------ 撞上限 → partial
def test_max_iterations_marks_partial_not_done():
    result = SimpleNamespace(stop_reason="max_iterations", summary="做到一半")
    status, prefix, code = _run_terminal(result)
    assert status == "partial", "撞迭代上限 ≠ 完成（owner 会以为活干完了）"
    assert "run_retry" in prefix
    assert code == "max_iterations_reached"


def test_terminal_mapping_stays_honest():
    assert _run_terminal(SimpleNamespace(stop_reason="done", summary=""))[0] == "done"
    assert _run_terminal(SimpleNamespace(stop_reason="error", summary=""))[0] == "failed"
    assert _run_terminal(SimpleNamespace(status="timeout"))[0] == "timeout"
    assert _run_terminal(SimpleNamespace(stop_reason="rate_limited"))[0] == "paused"


# ------------------------------------------- v0.10.41 复查修掉的三个真 bug
def test_prompt_truncated_in_dto_but_full_in_db(tmp_path):
    """/api/runs（50 条）与每个 SSE 事件都会带 prompt——不截断就是几百 KB 白扛；
    但 run_retry 读的是 DB 全文，必须完整。"""
    svc, db = _svc(tmp_path)
    long_prompt = "任务" * 500  # 1000 字
    run = svc.runs.start(kind="manual", window_id="w1", prompt=long_prompt)
    dto = [d for d in svc.runs.recent(limit=5) if d["id"] == run.id][0]
    assert len(dto["prompt"]) == 500, "DTO 必须截断到 500"
    assert svc.runs.get(run.id).prompt == long_prompt, "DB 里必须是全文"
    out = _run(retry_run(svc, run.id))
    assert out["ok"] and svc.windows.dispatched[0][1] == long_prompt, "重试要用全文"
    db.close()


def test_recent_for_window_not_crowded_out_by_busy_windows(tmp_path):
    """window_status 的"最近运行"必须按工位直接查——用 recent(30) 再筛，
    忙碌时该工位的记录会被其他工位挤出前 30 条（假阴性）。"""
    import time
    svc, db = _svc(tmp_path)
    mine = svc.runs.start(kind="manual", window_id="w-mine", prompt="我的活")
    svc.runs.finish(mine.id, "failed", summary="我的失败")
    for i in range(40):  # 其他工位更忙
        r = svc.runs.start(kind="manual", window_id="w-other", prompt=f"别人的活 {i}")
        svc.runs.finish(r.id, "done")
        time.sleep(0.002)
    got = svc.runs.recent_for_window("w-mine", limit=3)
    assert [d["id"] for d in got] == [mine.id], "按工位查必须能查到（不能被挤掉）"
    db.close()
