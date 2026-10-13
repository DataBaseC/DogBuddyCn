# DogBuddyCn — tool tests (file ops, edit uniqueness, todo, checkpoint, registry gate)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database
from dogbuddy.tools.registry import ToolContext, build_registry
from dogbuddy.ports import Window


def _ctx(tmp_path, db=None, mode="BUILD"):
    (tmp_path / "ws").mkdir(exist_ok=True)
    return ToolContext(window_id="w1", workspace=str(tmp_path / "ws"), mode=mode,
                       todo_repo=db, db=db, checkpoint_root=str(tmp_path / "ckpt"))


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_file_write_read_edit(tmp_path):
    reg = build_registry()
    ctx = _ctx(tmp_path)
    r = _run(reg.execute("write_file", {"path": "a.txt", "content": "hello world"}, ctx))
    assert r.ok
    r = _run(reg.execute("read_file", {"path": "a.txt"}, ctx))
    assert "hello world" in r.output
    # non-unique old_text refused
    r = _run(reg.execute("write_file", {"path": "b.txt", "content": "x x x"}, ctx))
    r = _run(reg.execute("edit_file", {"path": "b.txt", "old_text": "x", "new_text": "y"}, ctx))
    assert not r.ok
    r = _run(reg.execute("edit_file", {"path": "b.txt", "old_text": "x", "new_text": "y",
                                       "replace_all": True}, ctx))
    assert r.ok
    assert (tmp_path / "ws" / "b.txt").read_text() == "y y y"


def test_path_escape_denied(tmp_path):
    reg = build_registry()
    ctx = _ctx(tmp_path)
    r = _run(reg.execute("write_file", {"path": "../evil.txt", "content": "x"}, ctx))
    assert not r.ok and "escape" in r.output.lower()


def test_edit_creates_checkpoint_backup(tmp_path):
    reg = build_registry()
    ctx = _ctx(tmp_path)
    _run(reg.execute("write_file", {"path": "c.txt", "content": "one"}, ctx))
    _run(reg.execute("edit_file", {"path": "c.txt", "old_text": "one", "new_text": "two"}, ctx))
    backups = list((tmp_path / "ckpt" / "w1" / "files").glob("*.bak"))
    assert backups, "pre-write snapshot missing"
    assert backups[0].read_text() == "one"


def test_checkpoint_rollback(tmp_path):
    reg = build_registry()
    ctx = _ctx(tmp_path)
    _run(reg.execute("write_file", {"path": "d.txt", "content": "v1"}, ctx))
    r = _run(reg.execute("checkpoint_create", {"label": "v1"}, ctx))
    assert r.ok, r.output
    _run(reg.execute("write_file", {"path": "d.txt", "content": "v2"}, ctx))
    r = _run(reg.execute("checkpoint_rollback", {}, ctx))
    assert r.ok, r.output
    assert (tmp_path / "ws" / "d.txt").read_text() == "v1"


def test_todo_tools(tmp_path):
    db = Database(tmp_path / "t.db")
    db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                            workspace_path=str(tmp_path), model="m"))
    reg = build_registry()
    ctx = _ctx(tmp_path, db=db)
    r = _run(reg.execute("todo_add", {"content": "write tests"}, ctx))
    assert r.ok
    r = _run(reg.execute("todo_view", {}, ctx))
    assert "write tests" in r.output
    todo_id = db.list_todos("w1")[0].id
    r = _run(reg.execute("todo_done", {"id": todo_id}, ctx))
    assert r.ok and db.list_todos("w1")[0].done
    db.close()


def test_registry_gate_denies_and_audits(tmp_path):
    from dogbuddy.agents.core.permissions import PermissionGate
    db = Database(tmp_path / "t.db")
    reg = build_registry(gate=PermissionGate(confirm_available=True), audit=db)
    ctx = _ctx(tmp_path, db=db)
    r = _run(reg.execute("shell", {"command": "rm -rf /"}, ctx))
    assert not r.ok and r.needs_confirm
    # audit trail recorded the denial
    actions = [e.action for e in db.list_audit()]
    assert any("shell" in a for a in actions)
    db.close()


def test_shell_timeout(tmp_path):
    reg = build_registry()
    ctx = _ctx(tmp_path)
    # portable sleeper: `sleep` does not exist on Windows, python always does
    sleeper = f'"{sys.executable}" -c "import time; time.sleep(5)"'
    r = _run(reg.execute("shell", {"command": sleeper, "timeout": 1}, ctx))
    assert not r.ok and "timeout" in r.output.lower()


def test_schemas_filtered_by_tool_names():
    reg = build_registry()
    names = [s["function"]["name"] for s in reg.schemas(only=["read_file", "glob"])]
    assert names == ["glob", "read_file"]
