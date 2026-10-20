# DogBuddyCn — 限速续跑守卫（v0.10.41 复查修掉的双跑 bug）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""2026-10-20 复查发现的真 bug：`requeue_after_rate_limit` 的定时器**无条件**
`dispatch(prompt)`，而且不带 run_id。两个后果：

1. 管理员取消 / 唤醒（先 cancel 再重派）之后，60 秒后旧定时器仍会把同一任务
   再跑一遍 —— **双跑、双烧 token，还可能并发写同一批文件**；
2. 续跑没有 run_id → 台账里旧 run 永远停在 `paused`（顶栏一直显示"限速暂停"），
   而实际工作正在无台账状态下跑完。

修法：定时器开火前查原 run 是否已取消/结束；续跑带同一个 run_id 并把它拉回 running。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.services.run_registry import RunRegistry  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _stack(tmp_path):
    db = Database(str(tmp_path / "r.db"))
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=EventBus(),
                       checkpoints_dir=tmp_path / "ck")
    w = wm.create("dev", workspace_path=str(tmp_path / "ws"))
    runs = RunRegistry(db)
    wm.runs = runs
    return db, wm, w, runs


def _paused_run(wm, runs, w) -> str:
    run = runs.start(kind="manual", window_id=w.id, prompt="整理工作区", status="running")
    runs.note_pause(run.id, 12.0)
    wm._manual_last[w.id] = {"prompt": "整理工作区", "attempts": 0, "run_id": run.id}
    return run.id


def test_requeue_skips_after_cancel(tmp_path):
    """取消之后，定时器不能再把同一任务重跑一遍（否则就是双跑）。"""
    db, wm, w, runs = _stack(tmp_path)
    rid = _paused_run(wm, runs, w)
    calls: list[tuple] = []
    wm.dispatch = lambda wid, prompt, **kw: calls.append((wid, prompt, kw.get("run_id")))

    async def scenario():
        wm.requeue_after_rate_limit(w.id, delay=0.01)
        wm.cancel_run(rid)          # 管理员取消
        await asyncio.sleep(0.08)   # 等定时器开火

    _run(scenario())
    assert calls == [], f"已取消的运行不该再续跑：{calls}"
    assert runs.get(rid).status == "cancelled"
    db.close()


def test_requeue_skips_after_wakeup_retry(tmp_path):
    """唤醒（cancel + 重新派单）之后，旧定时器也不能把任务再跑第三遍。"""
    db, wm, w, runs = _stack(tmp_path)
    rid = _paused_run(wm, runs, w)
    calls: list[tuple] = []
    wm.dispatch = lambda wid, prompt, **kw: calls.append((wid, prompt, kw.get("run_id")))

    async def scenario():
        wm.requeue_after_rate_limit(w.id, delay=0.01)
        wm.cancel_run(rid)
        runs.finish(rid, "cancelled", summary="管理员已唤醒并重派")
        await asyncio.sleep(0.08)

    _run(scenario())
    assert calls == [], "已被唤醒的那次不该再被旧定时器重跑"
    db.close()


def test_requeue_resumes_same_run_id(tmp_path):
    """正常续跑：带**同一个 run_id** 重新派单，并把台账从 paused 拉回 running
    （否则顶栏永远显示"限速暂停"，实际却在无台账状态下跑）。"""
    db, wm, w, runs = _stack(tmp_path)
    rid = _paused_run(wm, runs, w)
    calls: list[tuple] = []
    wm.dispatch = lambda wid, prompt, **kw: calls.append((wid, prompt, kw.get("run_id")))

    async def scenario():
        msg = wm.requeue_after_rate_limit(w.id, delay=0.01)
        assert "已自动续跑" in msg
        await asyncio.sleep(0.08)

    _run(scenario())
    assert calls == [(w.id, "整理工作区", rid)], f"续跑必须带原 run_id：{calls}"
    assert runs.get(rid).status == "running", "续跑后台账要回到 running"
    db.close()


def test_requeue_gives_up_after_max_attempts(tmp_path):
    """连续被限速时的既有口径不变：超过上限就放弃并清掉内存派单记录。"""
    db, wm, w, runs = _stack(tmp_path)
    rid = _paused_run(wm, runs, w)
    wm._manual_last[w.id]["attempts"] = wm.RATE_LIMIT_REQUEUE_MAX
    msg = wm.requeue_after_rate_limit(w.id, delay=0.01)
    assert "停止自动续跑" in msg
    assert w.id not in wm._manual_last
    db.close()
