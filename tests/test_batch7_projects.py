# DogBuddyCn — Batch 7 项目层（T7.1/T7.2/T7.3/T7.5/T7.6）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""项目层是"工作区划分"的中间层。改造前：工位与目录一对一硬隔离，于是
①两个工位干同一件活必须共享目录却做不到 ②群组没有共享工作区
③"这个参数到底谁定的"在两态（继承/覆盖）里说不清。

这组测试锁住四件事：
  T7.1  迁移 v6：projects 表 + 老库 `workspace_path` 自动包一层项目
  T7.2  参数链 工位 > 项目 > profile > 全局；工作区 = 项目根(+子目录)
  T7.3  群组挂项目 → 全体成员跑项目根（共享工作区）
  T7.5  文件管理器可见根按项目对齐，不再列出被项目取代的老目录
  T7.6  HTTP 契约 + 参数继承链可视化
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import SCHEMA_VERSION, Database  # noqa: E402
from dogbuddy.ports import Group, Project, Window  # noqa: E402
from dogbuddy.services.workspace_manager import WorkspaceManager  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------- T7.1
def test_T7_1_迁移到_v6_建表且幂等(tmp_path):
    assert SCHEMA_VERSION >= 6
    p = tmp_path / "a.db"
    db = Database(p)
    try:
        assert db.schema_version == SCHEMA_VERSION
        cols = {r[1] for r in db._conn.execute("PRAGMA table_info(projects)")}
        assert {"id", "name", "root_path", "params_json"} <= cols
        wcols = {r[1] for r in db._conn.execute("PRAGMA table_info(windows)")}
        assert {"project_id", "subdir"} <= wcols
        gcols = {r[1] for r in db._conn.execute("PRAGMA table_info(groups)")}
        assert "project_id" in gcols
    finally:
        db.close()
    db2 = Database(p)
    try:
        assert db2.schema_version == SCHEMA_VERSION
        assert db2.list_projects() == []
    finally:
        db2.close()


def test_T7_1_老库的_workspace_path_自动包一层项目(tmp_path):
    """老数据必须无需人工处理：一目录一项目，工位自动挂上去。"""
    p = tmp_path / "legacy.db"
    ws = tmp_path / "ws-old"
    ws.mkdir()
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE windows(id TEXT PRIMARY KEY, name TEXT, role TEXT, "
                 "workspace_path TEXT, model TEXT, mode TEXT, status TEXT, group_id TEXT, "
                 "llm_profile TEXT, params_json TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO windows(id,name,role,workspace_path,model,mode,status,"
                 "created_at,updated_at) VALUES('w1','老工位','worker_programmer',?,'','BUILD','idle','','')",
                 (str(ws),))
    conn.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT)")
    conn.execute("INSERT INTO schema_version(version, applied_at) VALUES(4,'x')")
    conn.commit()
    conn.close()

    db = Database(p)
    try:
        projs = db.list_projects()
        assert len(projs) == 1, "老工位的目录应被自动包成项目"
        assert projs[0].root_path == str(ws)
        assert "自动创建" in projs[0].name
        w = db.get_window("w1")
        assert w.project_id == projs[0].id, "工位应自动挂到该项目"
    finally:
        db.close()

    # 再开一次不应重复建项目
    db2 = Database(p)
    try:
        assert len(db2.list_projects()) == 1
    finally:
        db2.close()


def test_T7_1_项目_CRUD_与删除只解挂(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        p = Project(id="p1", name="主仓库", root_path=str(tmp_path / "repo"),
                    params={"max_iterations": 30})
        db.insert_project(p)
        assert db.get_project("p1").name == "主仓库"
        assert db.get_project("主仓库").id == "p1", "按名字也要能查到"

        db.update_project("p1", params={"max_iterations": 40, "temperature": 0.2})
        assert db.get_project("p1").params["temperature"] == 0.2

        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path="", model="", project_id="p1", subdir="api"))
        db.insert_group(Group(id="g1", name="g1", project_id="p1"))
        assert db.project_summary() == {"total": 1, "mounted_windows": 1}

        assert db.delete_project("p1") is True
        assert db.get_project("p1") is None
        assert db.get_window("w1").project_id == "", "删项目要解挂工位"
        assert db.get_group("g1").project_id == "", "删项目要解挂群组"
        assert db.get_window("w1") is not None, "删项目绝不能删工位"
    finally:
        db.close()


# --------------------------------------------------------------------------- T7.2
def _wm(tmp_path, **kw):
    from dogbuddy.events.bus import EventBus
    from dogbuddy.memory.service import MemoryService
    from dogbuddy.services.window_manager import WindowManager
    db = Database(tmp_path / "t.db")
    cfg = AppConfig(llm=LLMConfig(max_context_tokens=8000),
                    paths=PathsConfig(workspaces_dir=str(tmp_path / "ws")))
    cfg.max_iterations = 25
    return WindowManager(
        db=db, llm_factory=lambda *a, **k: None, bus=EventBus(),
        memory=MemoryService(tmp_path / "memory"), checkpoints_dir=tmp_path / "ckpt",
        model_resolver=cfg.effective_model,
        param_resolver=cfg.effective_params), db, cfg


def test_T7_2_生效工作区_项目根加子目录(tmp_path):
    wm, db, _cfg = _wm(tmp_path)
    try:
        ws = WorkspaceManager(tmp_path / "ws", db)
        proj = ws.create_project("仓库", str(tmp_path / "repo"))
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path=str(tmp_path / "old"), model=""))

        # 没挂项目 → 老行为
        assert wm.effective_workspace(db.get_window("w1")) == str(tmp_path / "old")

        # 挂项目、无子目录 → 项目根
        ws.mount("w1", proj.id)
        assert wm.effective_workspace(db.get_window("w1")) == str((tmp_path / "repo").resolve())

        # 挂项目 + 子目录 → 项目根/子目录，且自动创建
        ws.mount("w1", proj.id, subdir="services/api")
        got = wm.effective_workspace(db.get_window("w1"))
        assert got == str((tmp_path / "repo" / "services" / "api").resolve())
        assert Path(got).is_dir()
    finally:
        db.close()


def test_T7_2_子目录越出项目根被拒(tmp_path):
    wm, db, _cfg = _wm(tmp_path)
    try:
        ws = WorkspaceManager(tmp_path / "ws", db)
        proj = ws.create_project("仓库", str(tmp_path / "repo"))
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path="", model=""))
        with pytest.raises(ValueError, match="越出项目根"):
            ws.mount("w1", proj.id, subdir="../../etc")
    finally:
        db.close()


def test_T7_2_参数链_工位覆盖项目覆盖全局(tmp_path):
    wm, db, cfg = _wm(tmp_path)
    try:
        proj = Project(id="p1", name="P", root_path=str(tmp_path / "repo"),
                       params={"max_iterations": 40, "temperature": 0.3,
                               "retry": {"max_retries": 9}})
        db.insert_project(proj)
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path="", model="", project_id="p1",
                                params={"temperature": 0.9, "retry": {"base_interval": 5}}))

        p = wm.params_for(db.get_window("w1"))
        assert p["max_iterations"] == 40, "项目层要生效"
        assert p["temperature"] == 0.9, "工位覆盖项目"
        assert p["retry"]["max_retries"] == 9, "retry 逐键合并：项目的 max_retries 保留"
        assert p["retry"]["base_interval"] == 5, "retry 逐键合并：工位的 base_interval 生效"

        src = cfg.param_sources(db.get_window("w1"), None, proj)
        assert src["max_iterations"] == "project"
        assert src["temperature"] == "window"
        assert src["max_context_tokens"] == "inherited"
    finally:
        db.close()


def test_T7_2_没挂项目时行为不变(tmp_path):
    wm, db, cfg = _wm(tmp_path)
    try:
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path="", model="", params={"max_iterations": 7}))
        w = db.get_window("w1")
        assert wm.params_for(w)["max_iterations"] == 7
        assert cfg.param_sources(w)["max_iterations"] == "window"
        assert cfg.param_sources(w)["temperature"] == "inherited"
    finally:
        db.close()


def test_T7_2_执行时用项目工作区且提示词同步(tmp_path):
    """端到端：_execute 的 ctx.workspace 与提示词里的 workspace 都要是项目根。"""
    wm, db, _cfg = _wm(tmp_path)
    try:
        ws = WorkspaceManager(tmp_path / "ws", db)
        proj = ws.create_project("仓库", str(tmp_path / "repo"))
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path=str(tmp_path / "old"), model=""))
        ws.mount("w1", proj.id)

        captured: dict = {}

        class ProbeRunner:
            kind = "probe"

            async def run(self, task, env):
                from dogbuddy.agents.core.runners import RunnerResult
                captured["workspace"] = env.ctx.workspace
                captured["prompt_ws"] = env.session.effective_workspace
                msg = env.builder.system_block(env.session)
                captured["system_block"] = msg
                return RunnerResult(status="ok", summary="probe")

        from dogbuddy.agents.core.runners import RunnerRegistry
        reg = RunnerRegistry()
        reg.register(ProbeRunner())
        wm.runners = reg
        rt = wm._rt("w1")
        _run(wm._execute(rt, type("T", (), {"prompt": "x", "kind": "probe",
                                            "job_id": None, "llm_profile": ""})()))

        expect = str((tmp_path / "repo").resolve())
        assert captured["workspace"] == expect, "工具边界必须是项目根"
        assert captured["prompt_ws"] == expect, "会话里的生效工作区要同步"
        assert expect in captured["system_block"], "提示词必须告诉模型真实目录"
    finally:
        db.close()


# --------------------------------------------------------------------------- T7.3
def test_T7_3_群组挂项目后成员共享工作区(tmp_path):
    """群组挂了项目 → 每个成员的 ctx.workspace 都是项目根（共享工作区）。"""
    wm, db, _cfg = _wm(tmp_path)
    try:
        ws = WorkspaceManager(tmp_path / "ws", db)
        proj = ws.create_project("共享仓库", str(tmp_path / "shared"))
        for wid in ("w1", "w2"):
            db.insert_window(Window(id=wid, name=wid, role="worker_programmer",
                                    workspace_path=str(tmp_path / f"own-{wid}"), model=""))

        from dogbuddy.services.group_manager import GroupManager
        gm = GroupManager(db=db, windows=wm)
        g = gm.create("小组")
        for wid in ("w1", "w2"):
            gm.assign(g.id, wid)
        db.update_group(g.id, project_id=proj.id)

        seen: list[str] = []

        class CaptureLoop:
            def __init__(self, *a, **k):
                pass

            async def run(self, prompt, ctx, on_delta=None):
                seen.append(ctx.workspace)
                from dogbuddy.agents.core.loop import AgentResult
                return AgentResult(content="ok", stop_reason="done")

        # 该模块在执行时 `from ..agents.core.loop import AgentLoop`，
        # 所以要在**定义处**打补丁才生效
        import dogbuddy.agents.core.loop as loop_mod
        orig = loop_mod.AgentLoop
        loop_mod.AgentLoop = CaptureLoop
        try:
            res = _run(gm.run(g.id, "一起做个东西"))
        finally:
            loop_mod.AgentLoop = orig

        expect = str((tmp_path / "shared").resolve())
        # v0.10.33：组长开场（编排）+ 组员执行 + 组长收尾（汇总）= 3 次运行，
        # 三次都必须落在**同一个共享工作区**（这正是 T7.3 要保证的）
        assert len(seen) == 3, f"组长两轮 + 组员一轮都该跑，实际 {seen}"
        assert all(x == expect for x in seen), f"成员必须共享项目根，实际 {seen}"
        # v0.10.38：summary 带步骤完成度前缀
        assert res["summary"] == "[完成 3/3 步] ok"
        assert res["status"] == "done"
    finally:
        db.close()


# --------------------------------------------------------------------------- T7.5
def test_T7_5_文件管理器可见根按项目对齐(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        root = tmp_path / "wsroot"
        root.mkdir()
        wsm = WorkspaceManager(root, db)
        proj = wsm.create_project("P", str(tmp_path / "repo"))

        # 挂了项目的工位：老 workspace_path 不该再出现
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path=str(tmp_path / "legacy-dir"), model="",
                                project_id=proj.id))
        # 没挂项目的工位：保留老行为
        db.insert_window(Window(id="w2", name="w2", role="worker_programmer",
                                workspace_path=str(tmp_path / "standalone"), model=""))

        roots = wsm.list()
        assert str(tmp_path / "repo") in roots, "项目根必须可见"
        assert str(tmp_path / "legacy-dir") not in roots, "被项目取代的老目录不该再列出"
        assert str(tmp_path / "standalone") in roots, "没挂项目的工位仍按老行为"
    finally:
        db.close()


# --------------------------------------------------------------------------- T7.6
def _client(tmp_path, monkeypatch):
    from dogbuddy.main import DogBuddyApp
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="tok", port=8128),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = "tok"
    return app, client


def test_T7_6_路由需要鉴权(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        assert client.get("/api/projects", headers={"X-Auth-Token": ""}).status_code == 401


def test_T7_6_项目_CRUD_挂载与参数链(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        # 建项目
        created = client.post("/api/projects", json={
            "name": "主仓库", "root_path": str(tmp_path / "repo"), "git_init": False,
            "params": {"max_iterations": 33}}).json()
        pid = created["project"]["id"]
        assert created["project"]["root_path"].endswith("repo")

        # 建工位并挂载
        w = app.windows.create("工人", "worker_programmer", str(tmp_path / "w1"))
        r = client.post("/api/projects/mount", json={
            "window_id": w.id, "project_id": pid, "subdir": "svc"}).json()
        proj = [p for p in r["projects"] if p["id"] == pid][0]
        assert proj["window_count"] == 1
        assert proj["windows"][0]["subdir"] == "svc"

        # 生效工作区 = 项目根/svc
        assert app.windows.effective_workspace(
            app.db.get_window(w.id)) == str((tmp_path / "repo" / "svc").resolve())

        # 参数链：项目层生效且来源标为 project
        chain = client.get(f"/api/projects/{pid}/params-chain?window_id={w.id}").json()
        assert chain["effective"]["max_iterations"] == 33
        assert chain["sources"]["max_iterations"] == "project"
        assert chain["order"] == ["window", "project", "profile", "global"]

        # 工位自己再覆盖 → 来源变 window
        client.patch(f"/api/windows/{w.id}", json={"params": {"max_iterations": 11}})
        chain2 = client.get(f"/api/projects/{pid}/params-chain?window_id={w.id}").json()
        assert chain2["effective"]["max_iterations"] == 11
        assert chain2["sources"]["max_iterations"] == "window"

        # 解挂
        assert client.post(f"/api/projects/unmount/{w.id}").status_code == 200
        assert app.db.get_window(w.id).project_id == ""

        # 删除项目：目录保留
        assert client.delete(f"/api/projects/{pid}").status_code == 200
        assert client.get(f"/api/projects/{pid}").status_code == 404
        assert (tmp_path / "repo").is_dir(), "删项目绝不能删目录"


def test_T7_6_挂载不存在的项目返回400(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        w = app.windows.create("工人2", "worker_programmer", str(tmp_path / "w2"))
        r = client.post("/api/projects/mount", json={"window_id": w.id, "project_id": "nope"})
        assert r.status_code == 400


def test_T7_6_群组挂项目并可查共享工作区(tmp_path, monkeypatch):
    """T7.3 的入口必须能从 UI/HTTP 走通：群组建/改时绑定项目，
    DTO 要回 `project_name` + `shared_workspace`（否则用户不知道共享的是哪个目录）。"""
    app, client = _client(tmp_path, monkeypatch)
    with client:
        proj = client.post("/api/projects", json={
            "name": "共享", "root_path": str(tmp_path / "shared")}).json()["project"]
        w = app.windows.create("组员", "worker_programmer", str(tmp_path / "gw"))

        g = client.post("/api/groups", json={
            "name": "协作组", "project_id": proj["id"]}).json()
        assert g["project_id"] == proj["id"]
        assert g["project_name"] == "共享"
        assert g["shared_workspace"] == proj["root_path"]

        # 成员挂上后，run 时用共享工作区（用 capture 验证 ctx.workspace）
        client.post(f"/api/groups/{g['id']}/members", json={"window": w.id})

        seen: list[str] = []

        class CaptureLoop:
            def __init__(self, *a, **k):
                pass

            async def run(self, prompt, ctx, on_delta=None):
                seen.append(ctx.workspace)
                from dogbuddy.agents.core.loop import AgentResult
                return AgentResult(content="ok", stop_reason="done")

        import dogbuddy.agents.core.loop as loop_mod
        orig = loop_mod.AgentLoop
        loop_mod.AgentLoop = CaptureLoop
        try:
            r = client.post(f"/api/groups/{g['id']}/run", json={"prompt": "一起干"})
            assert r.status_code == 200
        finally:
            loop_mod.AgentLoop = orig
        # v0.10.33：这个组**只有一名成员**（他既是成员也是组长）→ 只跑一轮
        # （有多个成员时才是"组长开场 + 组员 + 组长收尾"三轮）；无论几轮都必须跑共享目录
        assert seen == [str((tmp_path / "shared").resolve())],             f"应全部跑共享目录，实际 {seen}"

        # 解除绑定（PATCH 传空字符串）
        patched = client.patch(f"/api/groups/{g['id']}", json={"project_id": ""}).json()
        assert patched["project_id"] == ""


def test_T7_6_列表含汇总与可见根(tmp_path, monkeypatch):
    app, client = _client(tmp_path, monkeypatch)
    with client:
        client.post("/api/projects", json={"name": "A", "root_path": str(tmp_path / "a")})
        d = client.get("/api/projects").json()
        assert d["summary"]["total"] == 1
        assert any(str(tmp_path / "a") in r for r in d["workspaces"])


def test_群组挂项目的工位也要在项目页显示(tmp_path, monkeypatch):
    """v0.10.35（owner 实测）："我能看到项目，但是前端根本没有显示工位被挂在"。

    工位有两条挂载路径：① 直接 `window.project_id`；② **通过群组**（群组挂了项目）。
    初版 DTO 只统计 ① → 群组挂项目时项目页 `windows: []`、`window_count: 0`，
    看起来什么都没挂上，实际有 3 个工位正在这个项目里干活。
    """
    app, client = _client(tmp_path, monkeypatch)
    with client:
        proj = client.post("/api/projects", json={
            "name": "共享2", "root_path": str(tmp_path / "shared2")}).json()["project"]
        w = app.windows.create("组员甲", "worker_programmer", str(tmp_path / "gw2"))
        direct = app.windows.create("直属", "worker_programmer", str(tmp_path / "gw3"))
        g = client.post("/api/groups", json={
            "name": "队", "project_id": proj["id"]}).json()
        client.post(f"/api/groups/{g['id']}/members", json={"window": w.id, "role": "lead"})
        client.post(f"/api/projects/mount", json={"window_id": direct.id,
                                                  "project_id": proj["id"]})

        p = [x for x in client.get("/api/projects").json()["projects"]
             if x["id"] == proj["id"]][0]
        assert [x["name"] for x in p["windows"]] == ["直属"]
        gm = p["group_members"]
        assert [x["name"] for x in gm] == ["组员甲"], p
        assert gm[0]["via"] == "group" and gm[0]["group_name"] == "队"
        assert gm[0]["group_role"] == "lead"
        assert p["window_count"] == 2, "项目页的「工位 N 个」要把群组成员算上"
        assert (p["direct_window_count"], p["group_window_count"]) == (1, 1)
