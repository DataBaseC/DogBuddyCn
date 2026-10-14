# DogBuddyCn — Batch 7 · T7.4 shell 越界守卫
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""`shell` 曾经是唯一的边界逃逸口：文件工具有 `_resolve_in_workspace` 锁死，
但 `/bin/sh` 里一句 `cd /` 就全绕过了。这些测试锁住 T7.4 的行为。

原则：**拦"cd 越界"和"写越界"，不拦"读越界"**（读由 PermissionGate 的危险命令
黑名单负责；一刀切禁读会把 uname/df/which 这类正常运维命令全废掉）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402
from dogbuddy.tools.shell_guard import analyze_command, is_elevated  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(tmp_path, trust: str = "", db=None) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(window_id="w1", workspace=str(ws), mode="BUILD",
                       actor="worker", db=db, checkpoint_root=str(tmp_path / "ckpt"),
                       extra={"trust": trust} if trust else {})


# --------------------------------------------------------------------------- 分析器
def test_工作区内的普通命令不报警(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in [
        "ls -la",
        "python -m pytest -q",
        "git status --short",
        "echo hello > out.txt",
        "cat README.md | head -20",
        "mkdir -p src/tests && touch src/main.py",
        "npm run build 2>&1",
        "rm -rf build/",
        "cp a.txt b.txt",
    ]:
        assert analyze_command(cmd, ws) == [], f"不该报警: {cmd}"


def test_cd_越界被识别(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in ["cd /etc && ls", "cd / && rm -rf tmp", "cd ..", "cd ../../..", "cd ~"]:
        risks = analyze_command(cmd, ws)
        assert risks and risks[0]["kind"] in ("cd", "traverse"), f"应识别越界: {cmd}"


def test_cd_工作区内不报警(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws" / "src").mkdir(parents=True, exist_ok=True)
    assert analyze_command("cd src && ls", ws) == []
    assert analyze_command(f"cd {ws}/src && ls", ws) == []


def test_重定向写出界被识别(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    risks = analyze_command("echo x > /etc/passwd", ws)
    assert any(r["kind"] == "write" and "/etc/passwd" in r["target"] for r in risks)
    assert analyze_command("cat a > /dev/null", ws) == [], "/dev/null 是正常丢弃"


def test_写命令的绝对路径被识别(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in ["rm -rf /var/log/x", "cp secret.txt /tmp/leak", "mkdir /opt/newdir",
                "chmod 777 /etc/shadow", "dd if=/dev/zero of=/dev/sda",
                "tee /etc/hosts"]:
        assert analyze_command(cmd, ws), f"应识别越界写: {cmd}"


def test_读越界不拦(tmp_path):
    """uname / df / which / cat /etc/os-release 这类必须能跑（归 PermissionGate 管）。"""
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    for cmd in ["uname -a", "df -h", "which python3", "cat /etc/os-release",
                "ls /var/log", "python3 --version"]:
        assert analyze_command(cmd, ws) == [], f"读命令不该被边界拦: {cmd}"


def test_多目标去重(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir(exist_ok=True)
    risks = analyze_command("rm -rf /tmp/a && rm -rf /tmp/a", ws)
    assert len(risks) == 1, "同一目标只报一次"


def test_elevated_判定(tmp_path):
    assert is_elevated(_ctx(tmp_path, trust="elevated")) is True
    assert is_elevated(_ctx(tmp_path, trust="ELEVATED")) is True
    assert is_elevated(_ctx(tmp_path, trust="standard")) is False
    assert is_elevated(_ctx(tmp_path)) is False


# --------------------------------------------------------------------------- 工具层
def test_标准信任级下越界命令被拒且留痕(tmp_path):
    from dogbuddy.db import Database
    db = Database(tmp_path / "t.db")
    reg = build_registry(audit=db)
    ctx = _ctx(tmp_path, db=db)
    (tmp_path / "ws").mkdir(exist_ok=True)

    res = _run(reg.execute("shell", {"command": "cd /etc && ls"}, ctx))
    assert not res.ok and res.data.get("boundary") is True
    assert "越界拦截" in res.output and "elevated" in res.output
    # 留痕
    entries = [e for e in db.list_audit() if e.action == "shell:boundary"]
    assert entries, "越界尝试必须进审计"
    assert "elevated=False" in entries[0].detail
    db.close()


def test_elevated_信任级放行但留痕(tmp_path):
    from dogbuddy.db import Database
    db = Database(tmp_path / "t.db")
    reg = build_registry(audit=db)
    (tmp_path / "ws").mkdir(exist_ok=True)
    ctx = _ctx(tmp_path, trust="elevated", db=db)

    # 放行：命令真的执行了（用跨平台命令，避免依赖 POSIX 工具）
    res = _run(reg.execute("shell", {"command": "cd .. && echo crossed"}, ctx))
    assert res.ok, f"elevated 应放行，实际: {res.output}"
    assert "crossed" in res.output
    entries = [e for e in db.list_audit() if e.action == "shell:boundary"]
    assert entries and "elevated=True" in entries[0].detail, "放行也要留痕"
    db.close()


def test_工作区内命令不受影响(tmp_path):
    reg = build_registry()
    ctx = _ctx(tmp_path)
    res = _run(reg.execute("shell", {"command": "echo hello > ok.txt"}, ctx))
    assert res.ok
    assert (tmp_path / "ws" / "ok.txt").exists()
    assert "hello" in (tmp_path / "ws" / "ok.txt").read_text(encoding="utf-8")
