# DogBuddyCn — 运行探测回归（v0.10.38）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 问："上级探测员工是否完成任务的机制呢？"

实测缺口（2026-10-17 群组迭代实验）：
1. `window_status` 只有一个 `last_output`（最近一次运行的摘要，400 字符）——
   连派三个任务时前两个的结果被覆盖，"任务 X 完成没、产出什么"答不上来；
2. `progress_report` 的 "Recent runs" 只是 **cron 的 job_runs**——手动派的活、
   群组接力全在 runs 台账里，管理员看不到那一本账。

修法：window_status 附带该窗口最近 3 次运行；progress_report 增加
"Recent runs (manual/group)" 段。这两个测试钉住它。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.management.tools import (  # noqa: E402
    AdminServices, build_management_tools)
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.admin_service import AdminService  # noqa: E402
from dogbuddy.services.run_registry import RunRegistry  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _stack(tmp_path):
    from dogbuddy.events.bus import EventBus
    db = Database(str(tmp_path / "t.db"))
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=EventBus(),
                       checkpoints_dir=tmp_path / "ck")
    w = wm.create("dev", workspace_path=str(tmp_path / "ws"))
    runs = RunRegistry(db)
    return db, wm, w, runs


def test_window_status_reports_recent_runs(tmp_path):
    """连派多个任务时，最近 3 次运行的状态与摘要要能查到（以前只有 last_output）。"""
    import time
    db, wm, w, runs = _stack(tmp_path)
    for i in range(5):
        r = runs.start(kind="manual", window_id=w.id, window_name="dev",
                       prompt_chars=10 + i)
        runs.finish(r.id, "done" if i % 2 == 0 else "partial",
                    summary=f"第 {i} 次产出")
        time.sleep(0.01)  # started_at 拉开，保证"最近 3 次"可判定
    svc = AdminServices(windows=wm, runs=runs)
    tools = {t.name: t for t in build_management_tools(svc)}
    ctx = ToolContext(window_id="admin", workspace=".", mode="AUTO",
                      actor="admin", db=db)
    res = _run(tools["window_status"].execute({"window_id": w.id}, ctx))
    assert res.ok
    assert "最近运行" in res.output, "window_status 没带最近运行"
    run_lines = [ln for ln in res.output.splitlines() if ln.strip().startswith("r-")]
    assert len(run_lines) == 3, f"只该带最近 3 次，实际 {len(run_lines)} 条"
    assert "第 4 次产出" in res.output, "要能看到最近一次运行的摘要"
    assert "第 1 次产出" not in res.output, "更老的该被裁掉"
    db.close()


def test_window_status_without_runs_still_works(tmp_path):
    """没挂 runs 台账时（老装配/单测）window_status 不许炸。"""
    db, wm, w, _ = _stack(tmp_path)
    svc = AdminServices(windows=wm)
    tools = {t.name: t for t in build_management_tools(svc)}
    ctx = ToolContext(window_id="admin", workspace=".", mode="AUTO",
                      actor="admin", db=db)
    res = _run(tools["window_status"].execute({"window_id": w.id}, ctx))
    assert res.ok and "dev" in res.output
    db.close()


def test_progress_report_includes_manual_and_group_runs(tmp_path):
    """progress_report 要有 manual/group 台账段——以前只有 cron 的 job_runs，
    owner 问"刚才派的活干完了吗"管理员只能看到定时任务那一本账。"""
    db, wm, w, runs = _stack(tmp_path)
    rg = runs.start(kind="manual", window_id=w.id, window_name="dev")
    runs.finish(rg.id, "done", summary="写完了 verify.txt")
    gg = runs.start(kind="group", group_id="g1", group_name="模板站",
                    window_id=w.id)
    runs.finish(gg.id, "partial", summary="[完成 2/3 步] 两名成员被限速暂停")

    admin = AdminService.__new__(AdminService)  # 只测 _progress_dict，不碰 DI 装配
    admin.windows = wm
    admin.jobs = None
    admin.runs = runs
    data = admin._progress_dict()
    assert "Recent runs (manual/group)" in data["text"]
    assert "写完了 verify.txt" in data["text"]
    assert "[完成 2/3 步]" in data["text"], "partial 的摘要也要能看见（完成度是一等信号）"
    assert data["recent_runs"] == 2
    db.close()


# ------------------------------------------------- v0.10.38 群组消息进员工聊天页
def test_recent_messages_dto_exposes_group_id(tmp_path):
    """员工聊天页要能标出"这是群组协作消息"——DTO 必须带 group_id
    （群频道与成员会话是同一批消息的两份投影；以前 DTO 没这个字段，
    前端无法区分，owner 在工人聊天记录里看不到群组上下文）。"""
    from dogbuddy.events.bus import EventBus
    from dogbuddy.services.group_manager import GroupManager
    db, wm, w, _ = _stack(tmp_path)
    gm = GroupManager(db, wm, bus=EventBus())
    g = gm.create("模板站")
    gm.assign(g.id, w.id)
    rt = wm._rt(w.id)  # _rt 每次用 DB 刷新 session.window（group_id 跟着走）
    rt.session.append("user", "群里的发言")
    dto = wm.recent_messages(w.id)
    assert dto, "消息列表不该为空"
    assert dto[-1]["group_id"] == g.id, "DTO 必须暴露 group_id"
    db.close()


def test_worker_chat_view_renders_group_badge():
    """前端：员工聊天页给群组消息渲染「群组」徽标（全 0 不造假，反之要标）。"""
    js = (Path(__file__).resolve().parents[1]
          / "dogbuddy/web/static/js/views-main.js").read_text(encoding="utf-8")
    assert "m.group_id" in js, "renderMsg 没读 group_id"
    assert "group[0].group_id" in js, "renderToolGroup 没读 group_id"
    assert "群组协作消息" in js, "徽标标题不在"
