# DogBuddyCn — job_runs bookkeeping & task_timeout (v11)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""回归测试：一次触发**一本账**、`task_timeout` 真的能被设进去、`_run_status` 认 `status`。

三个缺陷（生产实测 + 只读根因调查）：
  1. cron 一次触发写 **两行** `job_runs`：调度器开行A、运行时又开行B，两行互不知情。
     症状：运行历史两条相邻记录、「今日运行次数」翻倍、「24h 成功率」被腰斩。
  2. `task_timeout` **不在** `AppConfig.WINDOW_PARAM_KEYS` → `effective_params()` 静默丢弃
     → `_wait_timeout()` 永远退回 1800s。用户设的 15s 是"看着设了、实际无效"。
  3. `_run_status()` 只看 `stop_reason`/`ok`，而真实 runner 返回的 `RunnerResult`
     两者都没有、只有 `status` → **每行都记成 failed**（成功的执行也记假失败）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.runners import RunnerResult  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.llm.provider import LLMResponse  # noqa: E402
from dogbuddy.memory.service import MemoryService  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.services.window_manager import WindowManager, _run_status  # noqa: E402
from dogbuddy.windows.session import WindowSession  # noqa: E402

from helpers import FakeLLM, run  # noqa: E402


# --------------------------------------------------------------- ③ _run_status
def test_run_status_reads_runner_status():
    """真实 runner 返回 `RunnerResult`（只有 status）→ 不许恒判 failed。"""
    assert _run_status(RunnerResult(status="ok", summary="slow done")) == "ok"
    assert _run_status(RunnerResult(status="failed", summary="boom")) == "failed"
    assert _run_status(RunnerResult(status="rate_limited", summary="429")) == "rate_limited"
    # 兼容老的 AgentResult 形状（无 status）
    import types

    assert _run_status(types.SimpleNamespace(stop_reason="done")) == "ok"
    assert _run_status(types.SimpleNamespace(stop_reason="error")) == "failed"
    assert _run_status(types.SimpleNamespace(ok=True)) == "ok"


# --------------------------------------------------------- ① 一次触发一本账
def _wm(tmp_path):
    db = Database(tmp_path / "data" / "t.db")
    mem = MemoryService(tmp_path / "data" / "memory")
    wm = WindowManager(db=db, llm_factory=lambda: FakeLLM([LLMResponse(content="ok")]),
                       bus=EventBus(), memory=mem,
                       checkpoints_dir=tmp_path / "data" / "checkpoints")
    return db, wm


def test_cron_path_does_not_open_a_second_run(tmp_path):
    """`dispatch_and_wait`（cron）已经由调度器记账 → 运行时**不得**再开行。"""
    async def main():
        db, wm = _wm(tmp_path)
        await wm.start()
        w = wm.create(name="dev", role="worker_programmer",
                      workspace_path=str(tmp_path / "ws"), model="fake", mode="BUILD")
        # 模拟调度器先开好行A
        sched_run = db.start_run("job-1", w.id)
        outcome = await wm.dispatch_and_wait(w.id, "做事", job_id="job-1")
        assert outcome.status == "ok"
        rows = db.list_runs("job-1")
        assert len(rows) == 1, f"一次触发写了 {len(rows)} 行（应是 1 行，由调度器持有）"
        assert rows[0].id == sched_run.id and rows[0].status == "running", \
            "调度器那行必须原样保留（它才负责写 timeout）"
        await wm.stop()
        db.close()

    run(main())


def test_manual_path_still_records_one_run(tmp_path):
    """手动/`dispatch`（没有调度器记账）仍要自己开一行——不能为了消重把账本弄丢。"""
    async def main():
        db, wm = _wm(tmp_path)
        await wm.start()
        w = wm.create(name="dev", role="worker_programmer",
                      workspace_path=str(tmp_path / "ws"), model="fake", mode="BUILD")
        wm.dispatch(w.id, "手动跑一次", job_id="job-2")
        for _ in range(100):
            rows = db.list_runs("job-2")
            if rows and rows[0].status != "running":
                break
            await asyncio.sleep(0.05)
        assert len(rows) == 1, f"手动路径应恰好 1 行，实际 {len(rows)}"
        assert rows[0].status == "ok", f"runner 成功却记成 {rows[0].status}"
        await wm.stop()
        db.close()

    run(main())


# --------------------------------------------------------- ② task_timeout 生效
def test_task_timeout_is_a_window_param_and_reaches_wait_timeout(tmp_path):
    cfg = AppConfig(
        llm=LLMConfig(base_url="http://x/v1", model="m", api_key="k"),
        web=WebConfig(auth_token="t", port=8111),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    assert "task_timeout" in AppConfig.WINDOW_PARAM_KEYS, \
        "task_timeout 不在白名单 → effective_params() 会静默丢弃它（生产就是这么失效的）"

    w = Window(id="w1", name="w1", role="worker_programmer",
               workspace_path=str(tmp_path), model="m", params={"task_timeout": 15})
    eff = cfg.effective_params(w)
    assert eff["task_timeout"] == 15, f"窗口覆盖没生效：{eff.get('task_timeout')}"
    assert cfg.param_sources(w)["task_timeout"] == "window", "来源标签也要认它（否则 UI 显示'继承中'）"
    # 项目层同样能设
    proj = {"task_timeout": 42}
    assert cfg.effective_params(w, project=proj)["task_timeout"] == 15, "窗口层应压过项目层"

    db = Database(tmp_path / "data" / "t.db")
    db.insert_window(w)
    wm = WindowManager(db=db, llm_factory=lambda: FakeLLM(), bus=EventBus(),
                       memory=MemoryService(tmp_path / "data" / "memory"),
                       param_resolver=cfg.effective_params)
    assert wm._wait_timeout("w1") == 15.0, \
        f"工位设了 15s，实际等待 {wm._wait_timeout('w1')}s（= 参数被丢掉）"
    db.close()


def test_wait_timeout_default_and_bad_values(tmp_path):
    cfg = AppConfig(
        llm=LLMConfig(base_url="http://x/v1", model="m", api_key="k"),
        web=WebConfig(auth_token="t", port=8111),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    db = Database(tmp_path / "data" / "t.db")
    w = Window(id="w1", name="w1", role="worker_programmer", workspace_path=str(tmp_path),
               model="m", params={"task_timeout": -5})
    db.insert_window(w)
    wm = WindowManager(db=db, llm_factory=lambda: FakeLLM(), bus=EventBus(),
                       memory=MemoryService(tmp_path / "data" / "memory"),
                       param_resolver=cfg.effective_params)
    assert wm._wait_timeout("w1") == float(wm.CRON_WAIT_TIMEOUT), "非法值必须回落默认，不能变负数"
    db.close()


def _session_roundtrip(tmp_path):  # pragma: no cover - helper kept for parity with other tests
    db = Database(tmp_path / "t.db")
    w = Window(id="w1", name="w1", role="worker_programmer", workspace_path=str(tmp_path), model="m")
    db.insert_window(w)
    return db, WindowSession(w, db, db, db)
