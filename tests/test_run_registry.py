# DogBuddyCn — 运行台账 P0 回归（v0.10.37）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""起因（owner 实测三连）："你在哪跑呢，我咋没看见" / "他好像卡住了" / "我能看到项目，
但前端根本没显示工位被挂在"。共同病根：**运行状态没有一等实体** —— 状态散在
`windows.status`、`groups.last_run`、cron 的 `job_runs` 和内存里的 asyncio 任务里，
而且运行的存活挂在调用方的 HTTP 连接上。

本文件钉住 P0 的最小可用一半：
1. `runs` 台账（活跃 + 历史 + 父子）；
2. **取消**真的能停（排队中拿掉 / 运行中打断，且队列继续活着）；
3. 启动时把上个进程遗留的 running/paused 标成 `interrupted`（重启不再留僵尸行）；
4. 管理员有了 `group_run`/`window_dispatch`/`run_list`/`run_cancel`/`run_status`，
   且 `progress_report` 能看到正在跑的运行（W1/W2/W3）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.services.run_registry import RunRegistry  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.windows.queue import TaskItem  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _stack(tmp_path):
    db = Database(str(tmp_path / "r.db"))
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=None,
                       checkpoints_dir=tmp_path / "ckpt")
    (tmp_path / "ws").mkdir(exist_ok=True)
    w = db.insert_window(Window(id="w1", name="worker-1", role="worker_programmer",
                                mode="BUILD", workspace_path=str(tmp_path / "ws"),
                                model=""))
    reg = RunRegistry(db)
    wm.runs = reg
    return db, wm, reg, w


# ------------------------------------------------------------ 台账
def test_运行台账记录生命周期并能查历史(tmp_path):
    db, wm, reg, w = _stack(tmp_path)
    run = reg.start(kind="manual", window_id=w.id, window_name=w.name, prompt_chars=12)
    assert run.status == "running" and reg.summary()["total_live"] == 1
    reg.update(run.id, phase="work", step_no=1, iterations=4)
    reg.finish(run.id, "done", summary="做完了")
    assert reg.summary()["total_live"] == 0
    rows = reg.recent(limit=5)
    assert rows[0]["id"] == run.id and rows[0]["status"] == "done"
    assert rows[0]["window_name"] == "worker-1" and rows[0]["live"] is False
    db.close()


def test_重启后遗留的运行标成_interrupted(tmp_path):
    """W5：没有这一步，重启（部署/自升级）会在界面上留下永远"运行中"的僵尸行。"""
    db, wm, reg, w = _stack(tmp_path)
    r1 = reg.start(kind="manual", window_id=w.id)
    r2 = reg.start(kind="group")
    reg.update(r2.id, status="paused")
    # 模拟"进程重启"：新注册表看不到内存，只看到库里的活跃行
    fresh = RunRegistry(db)
    n = fresh.mark_orphans_interrupted()
    assert n == 2, n
    assert fresh.get(r1.id).status == "interrupted"
    assert fresh.get(r2.id).status == "interrupted"
    assert fresh.summary()["total_live"] == 0
    assert "中断" in fresh.get(r1.id).summary
    db.close()


# ------------------------------------------------------------ 取消
def test_取消排队中的运行会从队列拿掉(tmp_path):
    db, wm, reg, w = _stack(tmp_path)
    wm.dispatch_manual(w.id, "长任务")
    run_id = wm._manual_last[w.id]["run_id"]
    rt = wm._rt(w.id)
    assert len(rt.queue.pending()) == 1
    out = wm.cancel_run(run_id)
    assert out["ok"] and out.get("dropped") == 1, out
    assert rt.queue.pending() == [], "排队中的项必须被拿掉"
    assert reg.get(run_id).status == "cancelled"
    db.close()


def test_取消正在跑的那一项且队列继续活着(tmp_path):
    """运行中的取消必须**真的打断**，而且不能把窗口队列一起杀掉。"""
    db, wm, reg, w = _stack(tmp_path)
    started = asyncio.Event()
    finished: list[str] = []

    async def main():
        rt = wm._rt(w.id)

        async def slow(task: TaskItem):
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                finished.append("slow")

        rt.queue.runner = slow
        loop_task = asyncio.ensure_future(rt.queue.run_loop())
        wm.dispatch_manual(w.id, "慢任务")
        run_id = wm._manual_last[w.id]["run_id"]
        await started.wait()
        out = wm.cancel_run(run_id)
        assert out["ok"] and out.get("interrupted") is True, out
        await asyncio.sleep(0.05)
        # 队列还活着：还能再收一个任务
        flag = asyncio.Event()

        async def quick(task: TaskItem):
            flag.set()

        rt.queue.runner = quick
        wm.dispatch(w.id, "第二个任务")
        await asyncio.wait_for(flag.wait(), timeout=2)
        rt.queue.stop()
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    _run(main())
    assert finished, "被打断的任务必须收到取消（finally 应执行）"
    db.close()


# ------------------------------------------------------------ 管理员入口（W1/W2/W3）
def _admin(runs, windows=None, groups=None):
    from dogbuddy.agents.management.tools import AdminServices, build_admin_tools

    svc = AdminServices(windows=windows, runs=runs, groups=groups)
    tools = {t.name: t for t in build_admin_tools(svc)}
    return svc, tools


def test_管理员有了运行入口工具(tmp_path):
    """W1：以前管理员没有任何"启动运行"的工具，唯一歪路 task_create+task_run_now。"""
    from dogbuddy.agents.core.roles import ADMIN_TOOLS

    for t in ("group_run", "window_dispatch", "run_list", "run_cancel", "run_status"):
        assert t in ADMIN_TOOLS, f"管理员缺 {t}（owner 在对话里就没入口）"


def test_run_list_能看到正在跑的和被限速暂停的次数(tmp_path):
    db, wm, reg, w = _stack(tmp_path)
    run = reg.start(kind="manual", window_id=w.id, window_name=w.name)
    reg.note_pause(run.id, 61.0)
    from dogbuddy.tools.registry import ToolContext

    svc, tools = _admin(reg, windows=wm)
    res = _run(tools["run_list"].execute({}, ToolContext(window_id="admin:default", workspace=".", mode="AUTO", actor="admin")))
    assert res.ok and run.id in res.output
    assert "暂停1次" in res.output and "61" in res.output, res.output
    reg.finish(run.id, "done", "好了")
    res2 = _run(tools["run_list"].execute({}, ToolContext(window_id="admin:default", workspace=".", mode="AUTO", actor="admin")))
    assert "## 正在跑\n- (无)" in res2.output, res2.output
    db.close()


def test_run_cancel_工具真的取消(tmp_path):
    db, wm, reg, w = _stack(tmp_path)
    wm.dispatch_manual(w.id, "任务")
    run_id = wm._manual_last[w.id]["run_id"]
    from dogbuddy.tools.registry import ToolContext

    svc, tools = _admin(reg, windows=wm)
    res = _run(tools["run_cancel"].execute({"run_id": run_id},
                                           ToolContext(window_id="admin:default", workspace=".", mode="AUTO", actor="admin")))
    assert res.ok and ("已取消" in res.output or "已是终态" in res.output), res.output
    assert reg.get(run_id).status == "cancelled"
    # 幂等：再取消一次不报错
    res2 = _run(tools["run_cancel"].execute({"run_id": run_id},
                                            ToolContext(window_id="admin:default", workspace=".", mode="AUTO", actor="admin")))
    assert res2.ok and "终态" in res2.output, res2.output
    db.close()


def test_progress_report_能看到群组运行(tmp_path):
    """W2：以前群组运行中途问"进度怎么样"，管理员会答"没有任务在跑"（错的）。"""
    db, wm, reg, w = _stack(tmp_path)
    reg.start(kind="group", group_id="g1", group_name="前端模板站", phase="plan")
    from dogbuddy.services.admin_service import AdminService

    svc = AdminService(db=db, llm_factory=lambda: None, windows=wm, jobs=None,
                       sessions=None, workspaces=None, memory=None, config=None,
                       runs=reg)
    data = svc._progress_dict()
    assert "正在跑（runs）" in data["text"], data["text"]
    assert "前端模板站" in data["text"], data["text"]
    assert data["live_runs"] == 1
    db.close()


def test_Web层必须能拿到运行台账(tmp_path, monkeypatch):
    """v0.10.37 实测踩到的坑：Web 层用的是**独立服务包** `WebServices`，
    `main.py` 里忘了把它传进去 → `GET /api/runs` 503「运行台账未启用」，
    而运行其实跑得好好的（管理端/控制台都看不到，最坏的一种失败：静默）。"""
    from dogbuddy.main import DogBuddyApp
    from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig

    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="tok", port=8129),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    assert app.web_services.runs is app.runs, "Web 服务包必须带上 runs（否则 /api/runs 503）"
    assert app.windows.runs is app.runs and app.groups.runs is app.runs
