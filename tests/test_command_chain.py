# DogBuddyCn — 三层指挥链**应用级**接线（审查报告 P1-1）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""审查报告最重的一条：Batch 9 的三层指挥链"设计写了四向，运行时只通一向"。

报告的运行时证据（`_review/design/chain_wiring.py`）表明：

* `supervisor.command` / `worker.report` / `supervisor.digest` 的**唯一 emit 点**
  都在 `command()` / `report_up()` 里，而这两个方法**零调用者** ——
  处理器写得对，生产路径根本不会调用它；
* 员工任务跑完（真实 `job.completed`）→ 投给主管工位的 dispatch 条数 = **0**，
  通知直达用户；
* 员工提问（真实 `worker.question`）→ 同上；
* `HIERARCHICAL_EVENTS` / `DIRECT_EVENTS` 两个常量**从未被任何模块 import**。

而 `test_batch9_supervision.py` 是**直接调 `svc.report_up(...)` 的单元测试**，
所以全绿。这里补的是**应用级**接线测试：走真实 `DogBuddyApp` + 真实事件总线。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.scope import Scope  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import Window  # noqa: E402
from dogbuddy.events.bus import (
    JOB_COMPLETED, JOB_FAILED, RATE_LIMIT_STATUS, WORKER_QUESTION, Event)  # noqa: E402
from dogbuddy.ports import Project  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _RecordingNotifier:
    """记录"有没有直达用户"。"""

    name = "rec"

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


def _app(tmp_path, monkeypatch):
    from dogbuddy.main import DogBuddyApp
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="tok", port=8142),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = "tok"
    return app, client


def _project_with_supervisor(app, tmp_path, with_supervisor=True) -> dict:
    """p1：主管 sup1 + 员工 w1（都挂在项目上，owner 指向 sup1）。"""
    db = app.db
    db.insert_project(Project(id="p1", name="P1", root_path=str(tmp_path / "p1")))
    db.insert_window(Window(id="w1", name="员工一", role="worker_programmer",
                            workspace_path=str(tmp_path / "w1"), model="",
                            mode="BUILD", project_id="p1"))
    if with_supervisor:
        db.insert_window(Window(id="sup1", name="主管一", role="supervisor",
                                workspace_path=str(tmp_path / "sup1"), model="",
                                mode="BUILD", project_id="p1"))
        db.update_project("p1", owner_supervisor_id="sup1")
    return {"project": "p1", "worker": "w1", "supervisor": "sup1" if with_supervisor else ""}


def _observe(app, monkeypatch, event: Event, *, rounds: int = 200):
    """跑一次事件流：返回 (投给工位的 dispatch 列表, 直达用户的通知列表)。"""
    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(app.windows, "dispatch",
                        lambda wid, text, urgent=False: dispatched.append((wid, text)))
    notes = _RecordingNotifier()
    app.report.notifiers.append(notes)

    async def scenario():
        await app.bus.start()
        try:
            await app.bus.publish(event)
            # 指挥链内部是通过 bus.emit 投递的 → 让总线把队列跑完
            for _ in range(rounds):
                if dispatched or notes.sent:
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
        finally:
            await app.bus.stop()

    _run(scenario())
    return dispatched, notes.sent


# ------------------------------------------------------------ 员工 → 主管（结果）
def test_有主管时员工结果先给主管而不是直达用户(tmp_path, monkeypatch):
    """★ 报告运行时证据场景 A：真实 job.completed 投给主管的 dispatch 条数必须是 1。"""
    app, _c = _app(tmp_path, monkeypatch)
    ids = _project_with_supervisor(app, tmp_path)

    dispatched, pushed = _observe(app, monkeypatch, Event(JOB_COMPLETED, {
        "window_id": ids["worker"], "window_name": "员工一", "summary": "我做完了 X"}))

    assert dispatched, "员工结果没有上报给主管——指挥链没接线（P1-1）"
    assert dispatched[0][0] == ids["supervisor"]
    assert "我做完了 X" in dispatched[0][1]
    assert pushed == [], "有主管时结果不该再直达用户（逐级收敛）"


def test_有主管时员工提问先给主管(tmp_path, monkeypatch):
    """★ 报告运行时证据场景 B。"""
    app, _c = _app(tmp_path, monkeypatch)
    ids = _project_with_supervisor(app, tmp_path)

    dispatched, pushed = _observe(app, monkeypatch, Event(WORKER_QUESTION, {
        "window_id": ids["worker"], "window_name": "员工一", "question": "要删库吗"}))

    assert dispatched and dispatched[0][0] == ids["supervisor"]
    assert "要删库吗" in dispatched[0][1]
    assert pushed == []


def test_有主管时员工失败也先给主管(tmp_path, monkeypatch):
    """普通失败（未到火警阈值）交给主管——他要能重派/兜底。"""
    app, _c = _app(tmp_path, monkeypatch)
    ids = _project_with_supervisor(app, tmp_path)

    dispatched, pushed = _observe(app, monkeypatch, Event(JOB_FAILED, {
        "window_id": ids["worker"], "window_name": "员工一", "summary": "编译挂了"}))

    assert dispatched and dispatched[0][0] == ids["supervisor"]


def test_火警不因逐级收敛而静默(tmp_path, monkeypatch):
    """自动暂停是火警：必须触达人，不能只进主管队列（否则项目停了没人知道）。"""
    app, _c = _app(tmp_path, monkeypatch)
    ids = _project_with_supervisor(app, tmp_path)

    dispatched, pushed = _observe(app, monkeypatch, Event(JOB_FAILED, {
        "window_id": ids["worker"], "window_name": "员工一",
        "summary": "连续失败", "auto_paused": True}))

    assert pushed, "自动暂停必须直达用户/管理员"
    assert dispatched == [] or dispatched[0][0] == ids["supervisor"]


# ------------------------------------------------------------ 零回归：无主管
def test_没有主管时保持_v1_直连(tmp_path, monkeypatch):
    """退化路径：项目没主管 → 员工上报直达用户，行为与 v1 完全一致。"""
    app, _c = _app(tmp_path, monkeypatch)
    ids = _project_with_supervisor(app, tmp_path, with_supervisor=False)

    dispatched, pushed = _observe(app, monkeypatch, Event(JOB_COMPLETED, {
        "window_id": ids["worker"], "window_name": "员工一", "summary": "做完了"}))

    assert dispatched == [], "没有主管就不该有逐级投递"
    assert pushed, "没有主管时必须保持 v1 直连推送"


# ------------------------------------------------------------ 管理员 → 主管
def test_管理员用_supervisor_command_下发指令(tmp_path, monkeypatch):
    """指挥链第一跳：管理员→主管。初版 `SupervisionService.command()` 零调用者。"""
    app, _c = _app(tmp_path, monkeypatch)
    ids = _project_with_supervisor(app, tmp_path)

    assert app.admin.registry.get("supervisor_command") is not None, \
        "管理员必须有下发指令给主管的工具（P1-1）"

    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(app.windows, "dispatch",
                        lambda wid, text, urgent=False: dispatched.append((wid, text)))
    ctx = ToolContext(window_id="admin:default", workspace=".", mode="AUTO",
                      actor="admin:default", scope=Scope(level="system", actor="admin:default"))

    async def scenario():
        await app.bus.start()
        try:
            res = await app.admin.registry.execute(
                "supervisor_command", {"project_id": ids["project"], "text": "把登录做完"}, ctx)
            for _ in range(200):
                if dispatched:
                    break
                await asyncio.sleep(0.01)
        finally:
            await app.bus.stop()
        return res

    res = _run(scenario())
    assert res.ok, res.output
    assert dispatched, "指令没有投递到主管工位"
    assert dispatched[0][0] == ids["supervisor"]
    assert "把登录做完" in dispatched[0][1]


def test_supervisor_command_在没主管时给出可行动的错误(tmp_path, monkeypatch):
    """没有主管不能说"已下发"——必须告诉管理员怎么建主管。"""
    app, _c = _app(tmp_path, monkeypatch)
    ids = _project_with_supervisor(app, tmp_path, with_supervisor=False)
    ctx = ToolContext(window_id="admin:default", workspace=".", mode="AUTO",
                      scope=Scope(level="system", actor="admin:default"))
    res = _run(app.admin.registry.execute(
        "supervisor_command", {"project_id": ids["project"], "text": "x"}, ctx))
    assert res.ok is False
    assert "主管" in res.output


def test_主管不能给自己下发指令(tmp_path, monkeypatch):
    """`supervisor_command` 是 system 级：主管的工具箱里不该有它。"""
    from dogbuddy.agents.core.roles import SUPERVISOR_TOOLS
    from dogbuddy.agents.management.tools import SYSTEM_TOOL_NAMES
    assert "supervisor_command" in SYSTEM_TOOL_NAMES
    assert "supervisor_command" not in SUPERVISOR_TOOLS, "主管不能命令自己"


# ------------------------------------------------------------ 主管 → 管理员（汇总）
def test_主管自己的产出走_digest_到管理员(tmp_path, monkeypatch):
    """主管工位的上报 → supervisor.digest → 管理员（不是 worker.report → 主管自己）。"""
    app, _c = _app(tmp_path, monkeypatch)
    ids = _project_with_supervisor(app, tmp_path)

    got: list[tuple[str, str]] = []
    monkeypatch.setattr(app.supervision, "_to_admin",
                        lambda text, level="info": got.append((level, text)))

    assert app.supervision.route_report(ids["supervisor"], "本项目进度 60%") is True

    async def drain():
        await app.bus.start()
        try:
            for _ in range(200):
                if got:
                    break
                await asyncio.sleep(0.01)
        finally:
            await app.bus.stop()

    _run(drain())
    assert got, "主管的汇总没有到达管理员（supervisor.digest 无生产者）"
    assert "本项目进度 60%" in got[0][1]


def test_四向事件都有生产者():
    """把"设计意图"变成断言：需要逐级的事件必须在 HIERARCHICAL_EVENTS 里。

    初版这两个常量从未被任何模块 import —— 设计写在常量里，没接进代码。
    """
    from dogbuddy.services.supervision_service import (
        DIRECT_EVENTS, HIERARCHICAL_EVENTS, SUPERVISOR_DIGEST, WORKER_REPORT)

    assert JOB_COMPLETED in HIERARCHICAL_EVENTS
    assert JOB_FAILED in HIERARCHICAL_EVENTS
    assert WORKER_QUESTION in HIERARCHICAL_EVENTS
    assert WORKER_REPORT in HIERARCHICAL_EVENTS.values()
    # `supervisor.digest` 不是由这张表映射出来的，而是 `report_up` 按**角色**分流：
    # 主管自己的产出 → SUPERVISOR_DIGEST。上面 test_主管自己的产出走_digest_到管理员
    # 已经行为级覆盖；这里只确认它确实是被 emit 的事件类型（不是死常量）。
    import inspect

    from dogbuddy.services import supervision_service
    src = inspect.getsource(supervision_service)
    assert "SUPERVISOR_DIGEST," in src and "Event(type=SUPERVISOR_DIGEST" in src
    assert RATE_LIMIT_STATUS not in HIERARCHICAL_EVENTS
    assert set(DIRECT_EVENTS) >= {RATE_LIMIT_STATUS}
    # ReportService 真的会读它（不是摆设）
    from dogbuddy.services import report_service
    assert "HIERARCHICAL_EVENTS" in inspect.getsource(report_service)
