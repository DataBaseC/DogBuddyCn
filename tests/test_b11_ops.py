# DogBuddyCn — 运维/健壮性 P2 回归（审查报告 P2-2/3/5/8/9/10/12）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dogbuddy.db import Database, Window  # noqa: E402
from dogbuddy.ports import Project  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _old_iso(days: int = 200) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ------------------------------------------------------------------ P2-2
def test_历史清理只删已终结的旧记录(tmp_path):
    """5 张表原来只增不减，库里也没有 VACUUM / wal_checkpoint。"""
    db = Database(tmp_path / "t.db")
    try:
        db.insert_window(Window(id="w1", name="w1", role="worker_programmer",
                                workspace_path="", model="", mode="BUILD"))
        db.insert_window(Window(id="w2", name="w2", role="worker_programmer",
                                workspace_path="", model="", mode="BUILD"))
        # 旧消息（该删）与新消息（该留）
        db.add_message("w1", "user", "很久以前")
        db._conn.execute("UPDATE messages SET created_at=? WHERE window_id='w1'", (_old_iso(),))
        db.add_message("w2", "user", "刚刚")
        # 旧 run（已结束 → 删）、旧 run（仍在跑 → 留）
        done = db.start_run("j1", "w1")
        db.finish_run(done.id, "ok", "完成")
        db._conn.execute("UPDATE job_runs SET started_at=? WHERE id=?", (_old_iso(), done.id))
        live = db.start_run("j2", "w1")
        # 已决的旧待决事项（删）与仍未决的（留）
        d1 = db.add_pending_decision("w1", "老问题")
        db.answer_pending_decision(d1.id, "答了", status="answered")
        db._conn.execute("UPDATE pending_decisions SET created_at=? WHERE id=?", (_old_iso(), d1.id))
        db.add_pending_decision("w1", "还没答")
        db._conn.commit()

        out = db.prune_history(keep_days=90)

        assert out["messages"] == 1
        assert out["job_runs"] == 1
        assert out["pending_decisions"] == 1
        kept = [m.content for m in db.list_messages("w2", 10)]
        assert kept == ["刚刚"], f"新消息不能被删：{kept}"
        assert db.get_pending_decision(d1.id) is None
        rows = db.list_pending_decisions(status="pending", limit=10)
        assert any("还没答" in r.question for r in rows), "仍未决的事项绝不能删"
        assert [r for r in db.list_runs(limit=10) if r.id == live.id], "在跑的任务记录不能被删"
    finally:
        db.close()


def test_wal_checkpoint_可调用(tmp_path):
    db = Database(tmp_path / "t.db")
    try:
        db.add_message("w1", "user", "x" * 1000)
        db.wal_checkpoint()
        db.vacuum()
    finally:
        db.close()


def test_过期快照目录会被清理(tmp_path):
    from dogbuddy.main import _prune_snapshots

    root = tmp_path / "ckpt" / "snapshots"
    fresh = root / "20260101-000000"
    stale = root / "20200101-000000"
    for d in (fresh, stale):
        d.mkdir(parents=True)
        (d / "manifest.json").write_text("{}", encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    os.utime(stale, (old, old))

    assert _prune_snapshots(tmp_path / "ckpt", keep_days=14) == 1
    assert fresh.exists() and not stale.exists()
    assert _prune_snapshots(tmp_path / "ckpt", keep_days=14) == 0, "幂等"
    assert _prune_snapshots(tmp_path / "不存在的目录") == 0, "目录不存在不该炸"


# ------------------------------------------------------------------ P2-8
def test_audit_ui_strict_现在是可用的门禁():
    """报告实测 HEAD 上 `--strict` 恒 exit=1，所以它当不了 CI 门禁。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "audit_ui.py"), "--strict"],
                          capture_output=True, text=True, cwd=str(REPO), env=env,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, f"audit_ui --strict 必须通过：\n{proc.stdout}\n{proc.stderr}"
    assert "0 个没有定义" in proc.stdout


# ------------------------------------------------------------------ P2-9 / P2-10
def _pm_ctx(tmp_path, project_id: str, db=None) -> ToolContext:
    from dogbuddy.memory.service import MemoryService

    mem = MemoryService(tmp_path / "memory")
    return ToolContext(window_id="w1", workspace=".", mode="BUILD", db=db,
                       memory_service=mem, extra={"project_id": project_id})


def test_项目不存在时拒绝读写记忆(tmp_path):
    """P2-9：初版不校验存在性 → 可造任意垃圾记忆文件、也能覆写猜到的真实项目。"""
    from dogbuddy.tools.project_memory_ops import ProjectMemoryReadTool, ProjectMemoryWriteTool

    db = Database(tmp_path / "t.db")
    try:
        db.insert_project(Project(id="p1", name="P1", root_path=str(tmp_path / "p1")))
        bad = _pm_ctx(tmp_path, "no-such-project", db=db)
        r = _run(ProjectMemoryReadTool().execute({}, bad))
        assert r.ok is False and "不存在" in r.output
        w = _run(ProjectMemoryWriteTool().execute({"content": "x"}, bad))
        assert w.ok is False and "不存在" in w.output
        # 真实项目放行
        good = _pm_ctx(tmp_path, "p1", db=db)
        assert _run(ProjectMemoryWriteTool().execute({"content": "# 约定"}, good)).ok is True
    finally:
        db.close()


def test_超长_project_id_给出可读错误(tmp_path):
    """P2-10：初版会以 `[Errno 22]` 失败并被 OSError 吞成含糊的一句话。"""
    from dogbuddy.tools.project_memory_ops import ProjectMemoryWriteTool

    ctx = _pm_ctx(tmp_path, "x" * 200)
    res = _run(ProjectMemoryWriteTool().execute({"content": "y"}, ctx))
    assert res.ok is False
    assert "太长" in res.output, f"要明确说『名字太长』：{res.output}"


# ------------------------------------------------------------------ P2-3
def test_运行中新建的工位目录立刻可用(tmp_path):
    """P2-3：允许根原来在启动时冻结，新建工位目录一律 403，必须重启服务。"""
    from dogbuddy.services.file_service import FileService

    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    boxes = {"extra": []}

    def provider():
        return [ws_root, *boxes["extra"]]

    fs = FileService(provider)
    new_dir = tmp_path / "late-window"
    new_dir.mkdir()
    (new_dir / "a.txt").write_text("hi", encoding="utf-8")

    with pytest.raises(PermissionError):
        fs._root_for(str(new_dir))

    boxes["extra"].append(new_dir)          # 运行中新建了一个工位
    assert fs._root_for(str(new_dir)) == new_dir.resolve(), "重算后必须立刻放行"

    # 手动加进 roots 的条目不能被 refresh 冲掉
    manual = tmp_path / "manual"
    manual.mkdir()
    fs.roots.append(manual.resolve())
    fs.refresh_roots()
    assert fs._root_for(str(manual)) == manual.resolve()


# ------------------------------------------------------------------ P2-12
def test_配置目录不可写时快速失败(tmp_path):
    """P2-12：初版 `mkstemp` 会在不可写目录里按 TMP_MAX 次重试随机名，长时间空转。"""
    from dogbuddy.config import save_config

    import time as _time

    blocked = tmp_path / "ro"
    blocked.mkdir()
    try:
        os.chmod(blocked, 0o500)
    except OSError:
        pytest.skip("无法在平台上设置只读目录")

    if os.access(blocked, os.W_OK):
        pytest.skip("当前用户仍可写（例如 root），无法复现只读场景")

    from dogbuddy.config import AppConfig, LLMConfig

    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""))
    t0 = _time.monotonic()
    with pytest.raises((PermissionError, OSError)):
        save_config(cfg, blocked / "config.yaml")
    elapsed = _time.monotonic() - t0
    assert elapsed < 5, f"必须快速失败，实际耗时 {elapsed:.1f}s（初版会空转很久）"
    os.chmod(blocked, 0o700)


# ------------------------------------------------------------------ P2-5
def test_日志目录不可写不影响启动(tmp_path, monkeypatch):
    """P2-5：初版第一步就 mkdir，不可写直接抛，而那时文件处理器还没装 →
    磁盘上没有任何日志可查，只能看 stdout。"""
    import logging
    import logging.handlers

    from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig
    from dogbuddy.main import DogBuddyApp

    blocked = tmp_path / "logs"
    blocked.mkdir()
    try:
        os.chmod(blocked, 0o500)
    except OSError:
        pytest.skip("无法设置只读目录")
    if os.access(blocked, os.W_OK):
        pytest.skip("当前用户仍可写，无法复现")

    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="t", port=8151),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws"),
                                      logs_dir=str(blocked)))
    app = DogBuddyApp(cfg)   # 不该抛
    # stderr 处理器必须在
    root = logging.getLogger()
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    for h in list(root.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler):
            root.removeHandler(h)
    os.chmod(blocked, 0o700)
