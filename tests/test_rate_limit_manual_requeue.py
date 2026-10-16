# DogBuddyCn — 限速暂停后**人工派单自动续跑**（v0.10.32）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测："他好像卡住了"。

事实（生产数据）：
* 14:03:10 worker-1 落了一条 `[限速暂停] 服务端要求等待 600 秒（heavy 档）；本轮暂停，
  不是失败，**稍后会自动重试**。`
* 然后它 `idle` 了半个多小时。

根因：那句话对**定时任务**是真的（`mark_rate_limited` 会把 next_run_at 排到 60s 后），
但对**人工派单**是假的 —— `dispatch_manual` 没有调度器，被暂停后就没人叫醒它。
v0.10.28 把长限速从"睡 600s"改成"立刻暂停"之后，这个洞才显现出来。

修法：`WindowManager` 记住最后一次人工派单，`main._on_run_finished` 在
`status == "rate_limited" and manual` 时自动续跑（默认 60s 后、最多 15 次），
并把"已续跑 / 已放弃"如实通知用户。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402


def _stack(tmp_path):
    db = Database(str(tmp_path / "w.db"))
    bus = EventBus()
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=bus,
                       checkpoints_dir=tmp_path / "ckpt")
    ws = tmp_path / "ws"
    ws.mkdir()
    w = db.insert_window(Window(id="w1", name="worker-1", role="worker_programmer",
                                mode="BUILD", workspace_path=str(ws), model=""))
    return db, bus, wm, w


def test_人工派单会被记住(tmp_path):
    db, bus, wm, w = _stack(tmp_path)
    wm.dispatch_manual(w.id, "写一个前端站")
    assert wm._manual_last[w.id]["prompt"] == "写一个前端站"
    assert wm._manual_last[w.id]["attempts"] == 0
    db.close()


def test_限速暂停后自动续跑并计数(tmp_path):
    db, bus, wm, w = _stack(tmp_path)
    wm.dispatch_manual(w.id, "长任务")
    note = wm.requeue_after_rate_limit(w.id, delay=0)
    assert "续跑" in note, note
    assert wm._manual_last[w.id]["attempts"] == 1
    note2 = wm.requeue_after_rate_limit(w.id, delay=0)
    assert wm._manual_last[w.id]["attempts"] == 2
    assert "第 2 次" in note2
    # 审计留痕（谁在什么时候续跑过）
    rows = [dict(r) for r in db._conn.execute(
        "SELECT action, detail FROM audit_log WHERE action='window:requeue_rate_limited'")]
    assert len(rows) == 2, rows
    db.close()


def test_续跑次数用尽就明确放弃(tmp_path):
    db, bus, wm, w = _stack(tmp_path)
    wm.dispatch_manual(w.id, "长任务")
    for _ in range(WindowManager.RATE_LIMIT_REQUEUE_MAX):
        wm.requeue_after_rate_limit(w.id, delay=0)
    note = wm.requeue_after_rate_limit(w.id, delay=0)
    assert "停止自动续跑" in note, note
    assert "换模型" in note or "手动" in note, "要告诉用户下一步能做什么"
    assert w.id not in wm._manual_last, "放弃后清掉记录，避免以后误续跑"
    db.close()


def test_没有人工派单记录时不乱续跑(tmp_path):
    db, bus, wm, w = _stack(tmp_path)
    assert "没有可续跑" in wm.requeue_after_rate_limit(w.id)
    db.close()


def test_超长延迟也要真的延迟而不是立刻重跑(tmp_path):
    """delay 参数必须生效（否则每 0 秒重排一次会变成忙等）。"""
    db, bus, wm, w = _stack(tmp_path)
    wm.dispatch_manual(w.id, "长任务")
    # 审计里记的是"多久之后续跑"，不会真等
    note = wm.requeue_after_rate_limit(w.id, delay=600)
    # 测试环境没有事件循环 → 走"立即续跑"分支，但审计仍记下"本应 600s 后"
    assert "续跑" in note, note
    rows = [dict(r) for r in db._conn.execute(
        "SELECT detail FROM audit_log WHERE action='window:requeue_rate_limited'")]
    assert "600s" in rows[0]["detail"], rows
    db.close()
