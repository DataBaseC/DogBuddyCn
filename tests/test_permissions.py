# DogBuddyCn — permission gate tests (BUILD/PLAN/AUTO + dangerous blacklist)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.permissions import PermissionGate, is_dangerous_command
from dogbuddy.tools.registry import ToolContext


def _ctx(mode="BUILD", **extra):
    return ToolContext(window_id="w", workspace="/tmp/ws", mode=mode, extra=extra)


def test_plan_blocks_writes_and_shell():
    g = PermissionGate()
    assert not g.check("write_file", "write", {"path": "a.txt"}, _ctx("PLAN")).allowed
    assert not g.check("shell", "shell", {"command": "ls"}, _ctx("PLAN")).allowed
    assert g.check("read_file", "read", {"path": "a.txt"}, _ctx("PLAN")).allowed
    assert g.check("web_search", "network", {"query": "x"}, _ctx("PLAN")).allowed


def test_build_allows_inside_workspace_blocks_outside():
    g = PermissionGate()
    ok = g.check("write_file", "write", {"path": "src/a.py"}, _ctx("BUILD"))
    assert ok.allowed
    out = g.check("write_file", "write", {"path": "../../etc/passwd"}, _ctx("BUILD"))
    assert not out.allowed
    assert "outside workspace" in out.reason


def test_build_blocks_panel_files():
    g = PermissionGate()
    d = g.check("write_file", "write", {"path": "AegisServerPanel/config.json"}, _ctx("BUILD"))
    assert not d.allowed
    assert "panel" in d.reason.lower()


def test_blacklist_detection():
    assert is_dangerous_command("rm -rf /")
    assert is_dangerous_command("rm -rf / --no-preserve-root")
    assert is_dangerous_command("sudo mkfs.ext4 /dev/sda1")
    assert is_dangerous_command("reboot now")
    assert is_dangerous_command("systemctl stop sshd")
    assert is_dangerous_command("dd if=/dev/zero of=/dev/sda")
    assert not is_dangerous_command("rm -rf ./build")
    assert not is_dangerous_command("ls -la /")
    assert not is_dangerous_command("systemctl status dogbuddy")


def test_dangerous_needs_confirm_or_denied():
    g = PermissionGate(confirm_available=True)
    d = g.check("shell", "shell", {"command": "rm -rf /"}, _ctx("BUILD"))
    assert not d.allowed and d.needs_confirm
    # with user confirmation flag the same command passes
    ok = g.check("shell", "shell", {"command": "rm -rf /"}, _ctx("BUILD", confirmed=True))
    assert ok.allowed
    g2 = PermissionGate(confirm_available=False)
    d2 = g2.check("shell", "shell", {"command": "reboot"}, _ctx("BUILD"))
    assert not d2.allowed and not d2.needs_confirm


def test_auto_forbidden_on_scheduled():
    g = PermissionGate(confirm_available=True)
    d = g.check("write_file", "write", {"path": "/etc/x"}, _ctx("AUTO", scheduled=True))
    assert not d.allowed
    assert "unattended" in d.reason
    ok = g.check("write_file", "write", {"path": "/etc/x"}, _ctx("AUTO", manual=True))
    assert ok.allowed


def test_auto_dangerous_still_needs_confirm():
    g = PermissionGate(confirm_available=True)
    d = g.check("shell", "shell", {"command": "rm -rf /"}, _ctx("AUTO", manual=True))
    assert not d.allowed and d.needs_confirm
