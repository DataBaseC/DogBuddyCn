# DogBuddyCn — Batch 3 tests: shared work-channel groups (T3.x)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dogbuddy.db import Database
from dogbuddy.ports import Group, Window
from dogbuddy.services.group_manager import GroupManager


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _db(tmp_path):
    return Database(str(tmp_path / "t.db"))


def _win(db, wid, name, role="worker_programmer", group_id=""):
    w = Window(id=wid, name=name, role=role, workspace_path="/tmp",
               model="", mode="BUILD", group_id=group_id)
    db.insert_window(w)
    return w


# ---------------------------------------------------------------- schema
def test_migration_v4_adds_groups(tmp_path):
    import sqlite3
    p = str(tmp_path / "old.db")
    conn = sqlite3.connect(p)
    conn.executescript("""
    CREATE TABLE windows(id TEXT PRIMARY KEY, name TEXT, role TEXT, workspace_path TEXT,
      model TEXT, mode TEXT DEFAULT 'BUILD', status TEXT DEFAULT 'idle', group_id TEXT,
      llm_profile TEXT DEFAULT '', params_json TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT);
    CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, window_id TEXT, role TEXT,
      content TEXT, tokens INTEGER DEFAULT 0, created_at TEXT, meta_json TEXT DEFAULT '{}');
    INSERT INTO windows VALUES('w1','old','worker_programmer','/tmp','','BUILD','idle',NULL,'','{}',NULL,NULL);
    INSERT INTO messages(window_id,role,content,created_at) VALUES('w1','assistant','hi',datetime('now'));
    """)
    conn.commit()
    conn.close()
    db = Database(p)
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(messages)").fetchall()}
    assert "group_id" in cols
    tables = {r[0] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"groups", "group_members"} <= tables
    assert db.get_window("w1") is not None


# ---------------------------------------------------------------- CRUD
def test_group_lifecycle(tmp_path):
    db = _db(tmp_path)
    _win(db, "w1", "alice")
    _win(db, "w2", "bob")
    g = GroupManager(db, windows=None)
    created = g.create("team-a", description="写个爬虫")
    assert created.name == "team-a"

    g.assign("team-a", "alice", role="lead")
    g.assign("team-a", "bob")
    members = g.members("team-a")
    assert {(m["window_name"], m["role"]) for m in members} == {("alice", "lead"), ("bob", "member")}
    assert db.get_window("w2").group_id == created.id  # 窗口绑定频道

    g.unassign("team-a", "bob")
    assert [m["window_name"] for m in g.members("team-a")] == ["alice"]
    assert db.get_window("w2").group_id == ""

    g.delete("team-a")
    assert g.get("team-a") is None


def test_group_name_unique(tmp_path):
    db = _db(tmp_path)
    g = GroupManager(db, windows=None)
    g.create("x")
    try:
        g.create("x")
        assert False, "重名必须拒绝"
    except ValueError:
        pass


# ---------------------------------------------------------------- channel
def test_bound_window_messages_join_channel(tmp_path):
    from dogbuddy.windows.session import WindowSession
    db = _db(tmp_path)
    g = GroupManager(db, windows=None)
    created = g.create("team-a")
    w = _win(db, "w1", "alice", group_id=created.id)
    session = WindowSession(w, db, db, db)
    session.append("assistant", "我的产出：parser.py 写好了")
    msgs = g.channel("team-a")
    assert len(msgs) == 1
    assert msgs[0]["window_name"] == "alice"
    assert "parser.py" in msgs[0]["content"]


# ---------------------------------------------------------------- strict tools
def test_strict_tools_intersection(tmp_path):
    from dogbuddy.agents.core.roles import register_builtin_roles
    db = _db(tmp_path)
    register_builtin_roles()
    _win(db, "w1", "coder", role="worker_programmer")
    _win(db, "w2", "ops", role="worker_programmer")
    g = GroupManager(db, windows=None)
    tools = g._strict_tools(["w1", "w2"])
    assert "write_file" in tools and "shell" in tools  # 同角色=全集
    assert len(tools) == len(set(tools))


# ---------------------------------------------------------------- run flow
def test_group_run_relay_and_summary(tmp_path):
    from dogbuddy.agents.core.roles import register_builtin_roles
    from dogbuddy.services.window_manager import WindowManager
    from tests.helpers import FakeLLM
    from dogbuddy.llm.provider import LLMResponse

    register_builtin_roles()
    db = _db(tmp_path)
    llm = FakeLLM([
        LLMResponse(content="组员A产出：接口定义完成"),
        LLMResponse(content="汇总：接口+实现完成，待集成测试"),
    ])
    wm = WindowManager(db, llm_factory=lambda *a, **k: llm, bus=None,
                       checkpoints_dir=tmp_path / "ck")
    groups = GroupManager(db, windows=wm)
    groups.create("team-a")
    groups.assign("team-a", wm.create("alice", workspace_path=str(tmp_path / "a")).id,
                  role="member")
    groups.assign("team-a", wm.create("bob", workspace_path=str(tmp_path / "b")).id,
                  role="lead")

    result = _run(groups.run("team-a", "做一个用户注册接口"))
    assert len(result["steps"]) == 2
    assert result["summary"] == "汇总：接口+实现完成，待集成测试"
    assert all(s["ok"] for s in result["steps"])
    # 每轮 user 提示 + assistant 产出都进频道（2 轮 × 2 条）
    channel = groups.channel("team-a")
    assert len(channel) == 4
    assert {m["window_name"] for m in channel} == {"alice", "bob"}


def test_group_run_requires_members(tmp_path):
    db = _db(tmp_path)
    g = GroupManager(db, windows=None)
    g.create("empty-team")
    try:
        _run(g.run("empty-team", "任务"))
        assert False, "空组必须报错"
    except ValueError:
        pass


# ---------------------------------------------------------------- T3.4
def test_reset_refuses_while_running(tmp_path):
    from dogbuddy.services.window_manager import WindowManager
    from dogbuddy.services.session_manager import SessionManager
    db = _db(tmp_path)
    wm = WindowManager(db, llm_factory=lambda *a, **k: None, bus=None,
                       checkpoints_dir=tmp_path / "ck")
    w = wm.create("busy", workspace_path=str(tmp_path / "ws"))
    wm._rt(w.id).session.append("assistant", "hello")
    # 模拟运行中
    wm._rt(w.id).current_task_label = "running something"
    sm = SessionManager(wm, db)
    try:
        sm.reset(w.id)
        assert False, "运行中必须拒绝 reset"
    except ValueError:
        pass
    wm._rt(w.id).current_task_label = ""
    assert sm.reset(w.id) == 1
