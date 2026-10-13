# DogBuddyCn — Windows compatibility tests (cross-platform paths, kill, blacklist)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.permissions import PermissionGate, is_dangerous_command
from dogbuddy.tools.registry import ToolContext
from dogbuddy.tools import shell_ops


def test_windows_blacklist_detection():
    assert is_dangerous_command("format C:")
    assert is_dangerous_command("diskpart /s script.txt")
    assert is_dangerous_command("bcdedit /delete {default}")
    assert is_dangerous_command("del /f /s /q C:\\")
    assert is_dangerous_command("rd /s /q C:\\")
    assert is_dangerous_command(r"reg add HKLM\SYSTEM\CurrentControlSet")
    assert is_dangerous_command("net user hacker P@ss /add")
    assert is_dangerous_command("sc delete EventLog")
    assert is_dangerous_command("cipher /w:C")
    assert is_dangerous_command("taskkill /F /IM csrss.exe")
    # benign windows commands pass
    assert not is_dangerous_command("dir C:\\workspace")
    assert not is_dangerous_command("del /q build\\old.tmp")
    assert not is_dangerous_command("taskkill /F /PID 1234")
    # gate still denies them in BUILD mode
    g = PermissionGate(confirm_available=True)
    ctx = ToolContext(window_id="w", workspace="C:/ws", mode="BUILD")
    d = g.check("shell", "shell", {"command": "format C:"}, ctx)
    assert not d.allowed and d.needs_confirm


def test_default_data_dir_per_platform():
    import dogbuddy.config as cfg
    old = sys.platform
    try:
        sys.platform = "win32"
        import os
        os.environ.setdefault("LOCALAPPDATA", r"C:\Users\test\AppData\Local")
        p = cfg.default_data_dir()
        assert str(p).endswith("dogbuddy")
        assert "AppData" in str(p) or "ProgramData" in str(p)
    finally:
        sys.platform = old
    p2 = cfg.default_data_dir()
    assert str(p2) == "/etc/dogbuddy" or "dogbuddy" in str(p2)


def test_kill_process_tree_windows_branch(monkeypatch):
    """On a fake win32, killing must go through taskkill /F /T (no os.killpg)."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = b""
        return R()

    monkeypatch.setattr(shell_ops.os, "name", "nt")
    monkeypatch.setattr(shell_ops.subprocess if hasattr(shell_ops, "subprocess") else subprocess,
                        "run", fake_run)
    import subprocess as sp
    monkeypatch.setattr(sp, "run", fake_run)

    class FakeProc:
        pid = 4242

        def kill(self):
            raise AssertionError("should not fall back to proc.kill on win32")

    shell_ops._kill_process_group(FakeProc())
    assert calls and calls[0][0] == "taskkill"
    assert calls[0][1:4] == ["/F", "/T", "/PID"]


def test_spawn_kwargs_per_platform(monkeypatch):
    monkeypatch.setattr(shell_ops.os, "name", "nt")
    kw = shell_ops._spawn_kwargs()
    assert "creationflags" in kw and "start_new_session" not in kw
    monkeypatch.setattr(shell_ops.os, "name", "posix")
    kw = shell_ops._spawn_kwargs()
    assert kw == {"start_new_session": True}


def test_manage_py_imports_and_tail_is_pure_python(tmp_path, monkeypatch):
    import manage
    assert manage.IS_WINDOWS in (True, False)
    # logs must not shell out to `tail`
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "dogbuddy.log").write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")

    class Args:
        config = str(tmp_path / "config.yaml")
        lines = 5

    monkeypatch.setattr(manage, "_data_dir", lambda c: tmp_path)
    assert manage.cmd_logs(Args()) == 0


def test_shell_tool_runs_on_this_platform(tmp_path):
    """Sanity: the shell tool still executes normally after the portability patch."""
    reg_shell = shell_ops.ShellTool()
    ctx = ToolContext(window_id="w", workspace=str(tmp_path), mode="BUILD")
    r = asyncio.get_event_loop().run_until_complete(
        reg_shell.execute({"command": "echo hello-win-compat"}, ctx))
    assert r.ok and "hello-win-compat" in r.output
