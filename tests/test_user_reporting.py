# DogBuddyCn — 员工干完活要汇报到用户眼前（0.10.14）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 报："两个员工测试完成后，管理员没有向用户做简短汇报。"

三处缺口（都在这条链上）：
  ① 只有 cron 调度器发 `job.completed`；**对话/人工派给员工的任务跑完什么事件都不发**
     → `window_manager._report_manual_run()` 补一发（定时任务不补，避免重复）。
  ② 默认 console 渠道只写日志 → 新增 `ChatNotifier`：把汇报**写进用户的对话流**。
  ③ 前端要能即时追加 → SSE `admin.notice`。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, NotifyChannelConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import ADMIN_NOTICE, JOB_COMPLETED, JOB_FAILED, EventBus  # noqa: E402
from dogbuddy.llm.provider import LLMResponse  # noqa: E402
from dogbuddy.memory.service import MemoryService  # noqa: E402
from dogbuddy.notify.base import build_notifiers  # noqa: E402
from dogbuddy.notify.chat import ChatNotifier  # noqa: E402
from dogbuddy.services.admin_service import AdminService  # noqa: E402
from dogbuddy.services.config_service import ConfigService  # noqa: E402
from dogbuddy.services.job_manager import JobManager  # noqa: E402
from dogbuddy.services.session_manager import SessionManager  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.services.workspace_manager import WorkspaceManager  # noqa: E402
from dogbuddy.cron.jobs import JobStore  # noqa: E402

from helpers import FakeLLM, run  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _stack(tmp_path, bus, script=None):
    db = Database(tmp_path / "t.db")
    mem = MemoryService(tmp_path / "mem")
    cfg = AppConfig(llm=LLMConfig(), paths=PathsConfig(
        data_dir=str(tmp_path), workspaces_dir=str(tmp_path / "ws")))
    llm = FakeLLM(script or [LLMResponse(content="做完了")])
    wm = WindowManager(db=db, llm_factory=lambda: llm, bus=bus, memory=mem,
                       checkpoints_dir=tmp_path / "ckpt")
    admin = AdminService(db=db, llm_factory=lambda: llm, windows=wm,
                         jobs=JobManager(JobStore(db), wm),
                         sessions=SessionManager(wm, db),
                         workspaces=WorkspaceManager(tmp_path / "ws", db),
                         memory=mem, config=ConfigService(db, cfg), bus=bus)
    return db, wm, admin


# ------------------------------------------------------------------ ① 人工派单也发事件
def test_manual_window_run_emits_a_completion_report(tmp_path):
    async def main():
        bus = EventBus()
        seen: list = []
        bus.subscribe("*", lambda e: seen.append(e))
        await bus.start()
        db, wm, _admin = _stack(tmp_path, bus)
        await wm.start()
        w = wm.create(name="tester-1", role="worker_programmer",
                      workspace_path=str(tmp_path / "ws" / "t1"))
        try:
            # 对话/人工派单：没有 job_id
            await wm.dispatch_and_wait(w.id, "算个 1+1")
            await asyncio.sleep(0.05)
        finally:
            await wm.stop()
            await bus.stop()
            db.close()
        types = [e.type for e in seen]
        assert JOB_COMPLETED in types, f"人工派单跑完没有任何完成事件：{types}"
        ev = [e for e in seen if e.type == JOB_COMPLETED][0]
        assert ev.payload["window_id"] == w.id and ev.payload["manual"] is True
        assert ev.payload["report_strategy"] == "brief", "汇报要是简短的"

    run(main())


def test_scheduled_run_does_not_double_emit(tmp_path):
    """定时任务那侧由调度器发事件；窗口管理器不能再发一次（否则用户收到两条）。"""
    async def main():
        bus = EventBus()
        seen: list = []
        bus.subscribe("*", lambda e: seen.append(e))
        await bus.start()
        db, wm, _admin = _stack(tmp_path, bus)
        await wm.start()
        w = wm.create(name="tester-2", role="worker_programmer",
                      workspace_path=str(tmp_path / "ws" / "t2"))
        try:
            await wm.dispatch_and_wait(w.id, "定时跑一次", job_id="job-1")
            await asyncio.sleep(0.05)
        finally:
            await wm.stop()
            await bus.stop()
            db.close()
        assert [e for e in seen if e.type in (JOB_COMPLETED, JOB_FAILED)] == [], \
            "带 job_id 的运行不该由窗口侧再发一次"

    run(main())


# ------------------------------------------------------------------ ② 汇报写进对话流
def test_console_channel_posts_into_the_chat_when_sink_is_given(tmp_path):
    sent: list[str] = []
    notify_cfg = AppConfig(llm=LLMConfig()).notify
    plain = build_notifiers(notify_cfg)
    assert plain and plain[0].name == "console" and not isinstance(plain[0], ChatNotifier)

    wired = build_notifiers(notify_cfg, chat_sink=lambda t: sent.append(t))
    assert isinstance(wired[0], ChatNotifier) and wired[0].name == "console"
    run(wired[0].send("✅ 员工 tester-1 完成：三个算法都过了"))
    assert sent == ["✅ 员工 tester-1 完成：三个算法都过了"]


def test_chat_notifier_survives_a_broken_sink():
    def boom(_text):
        raise RuntimeError("sink down")

    run(ChatNotifier(boom).send("还是不能把任务本身带崩"))


def test_console_channel_keeps_per_job_routing_name(tmp_path):
    """显式配了 console 渠道时，名字/路由语义不能变。"""
    cfg = AppConfig(llm=LLMConfig())
    cfg.notify.channels = [NotifyChannelConfig(type="console", name="console", enabled=True)]
    wired = build_notifiers(cfg.notify, chat_sink=lambda t: None)
    assert [n.name for n in wired] == ["console"] and isinstance(wired[0], ChatNotifier)


# ------------------------------------------------------------------ ③ 落库 + SSE
def test_notify_user_writes_to_the_newest_conversation_and_emits_sse(tmp_path):
    async def main():
        bus = EventBus()
        seen: list = []
        bus.subscribe("*", lambda e: seen.append(e))
        await bus.start()
        db, _wm, admin = _stack(tmp_path, bus)
        try:
            admin.ensure_default_conversation()
            convo = admin.create_conversation("编程测试")
            res = await admin.notify_user("✅ 两个员工都跑完了：tester-1 通过、tester-2 失败 1 条")
            assert res["conversation"] == convo["id"], "没写进最近活跃的那个对话"
            msgs = admin.history(convo["id"], 10)
            assert msgs and "两个员工都跑完了" in msgs[-1]["content"]
            await asyncio.sleep(0.05)
        finally:
            await bus.stop()
            db.close()
        notices = [e for e in seen if e.type == ADMIN_NOTICE]
        assert notices and notices[0].payload["conversation"] == convo["id"]

    run(main())


def test_frontend_appends_the_notice_to_the_open_conversation():
    js = (REPO / "dogbuddy/web/static/js/views-chat.js").read_text(encoding="utf-8")
    assert "global.SSE.on('admin.notice', onNotice)" in js
    assert "payload.conversation" in js and "管理员有新汇报" in js
    assert "state.messages.push" in js
