# DogBuddyCn — self-upgrade plumbing + admin self_update tool
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""The agent must be able to upgrade the very process it runs in.

That is impossible *in-process* — the upgrade replaces the code and restarts the
service — so the design is: the tool launches `manage.py update` detached, the
outcome lands in `update_result.json`, and the next turn reports it. These tests
pin the plumbing (result file, command assembly, git inspection) and the two
admin tools.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy import updater  # noqa: E402
from dogbuddy.agents.admin.tools import AdminServices, build_admin_tools  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(tmp_path) -> ToolContext:
    return ToolContext(window_id="admin:test", workspace=".", mode="AUTO",
                       actor="admin:test", checkpoint_root=str(tmp_path / "ckpt"))


def _tool(name: str, **services):
    tools = build_admin_tools(AdminServices(**services))
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name} not registered: {[t.name for t in tools]}")


# --------------------------------------------------------------------------- result file
def test_update_result_roundtrip_is_atomic(tmp_path):
    path = updater.write_result(tmp_path, status="success", ok=True, previous="0.1.0",
                                version="0.1.1", source="git:origin/main")
    assert path == tmp_path / updater.RESULT_NAME
    data = updater.read_result(tmp_path)
    assert data["status"] == "success" and data["ok"] is True
    assert data["previous"] == "0.1.0" and data["version"] == "0.1.1"
    assert "finished_at" in data and "finished_at_iso" in data
    # no temp litter next to the result file
    assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_read_result_handles_missing_and_corrupt(tmp_path):
    assert updater.read_result(tmp_path) is None
    (tmp_path / updater.RESULT_NAME).write_text("{not json", encoding="utf-8")
    assert updater.read_result(tmp_path) is None


def test_read_result_serves_the_next_turn(tmp_path):
    """Simulates: upgrade finished while the process was down, agent asks later."""
    updater.write_result(tmp_path, status="rolled-back", ok=False, previous="0.2.1",
                         version="0.2.2", rolled_back_to="0.2.1",
                         detail="new version failed the health check")
    data = updater.read_result(tmp_path)
    assert data["status"] == "rolled-back"
    assert data["rolled_back_to"] == "0.2.1"


# --------------------------------------------------------------------------- git inspection
def test_git_info_on_non_git_dir(tmp_path):
    info = updater.git_info(tmp_path)
    assert info["is_git"] is False
    assert info["app_dir"] == str(tmp_path.resolve())


def test_git_info_on_real_checkout():
    repo = Path(__file__).resolve().parents[1]
    info = updater.git_info(repo)
    assert info["is_git"] is True
    assert len(info.get("ref", "")) == 40 or info.get("ref") == ""


def test_remote_head_reports_missing_git(tmp_path):
    out = updater.remote_head(app=tmp_path)
    assert out["ok"] is False and "not a git checkout" in out["error"]


# --------------------------------------------------------------------------- command assembly
def test_build_update_command_git_bundle_and_url(tmp_path):
    cfg = tmp_path / "config.yaml"
    git_cmd = updater.build_update_command(cfg, ref="origin/main", start_delay=45)
    assert "update" in git_cmd and "--ref" in git_cmd and "origin/main" in git_cmd
    assert "--start-delay" in git_cmd and "45" in git_cmd
    assert str(cfg) in git_cmd

    bundle_cmd = updater.build_update_command(cfg, bundle="/tmp/x.tar.gz")
    assert "--from-bundle" in bundle_cmd and "--ref" not in bundle_cmd

    url_cmd = updater.build_update_command(cfg, url="https://example/x.tar.gz", force=True)
    assert "--from-url" in url_cmd and "--force" in url_cmd


def test_spawn_update_starts_a_detached_process(tmp_path, monkeypatch):
    """The updater must not be a child of the agent process group (it restarts us)."""
    captured: dict = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            self.pid = 4242

    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)
    res = updater.spawn_update(tmp_path / "config.yaml", tmp_path / "data",
                               ref="origin/main", start_delay=45)
    assert res["started"] and res["pid"] == 4242
    assert res["start_delay"] == 45
    if updater.IS_WINDOWS:
        assert captured["kwargs"]["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert captured["kwargs"]["start_new_session"] is True
    assert Path(res["log"]).name == "update.log"


# --------------------------------------------------------------------------- admin tools
def test_self_version_tool_reports_version_and_last_upgrade(tmp_path):
    updater.write_result(tmp_path, status="success", ok=True, previous="0.2.1", version="0.2.2")
    tool = _tool("self_version",
                 self_status=lambda check: {"version": "0.2.2", "build": "abc123",
                                            "git": {"is_git": True, "describe": "abc123",
                                                    "remote": "https://github.com/DataBaseC/DogBuddyCn.git"},
                                            "last_update": updater.read_result(tmp_path)})
    r = _run(tool.execute({}, _ctx(tmp_path)))
    assert r.ok
    assert "0.2.2" in r.output
    assert "0.2.2" in r.output and "0.2.1" in r.output   # previous → version
    assert "github.com/DataBaseC/DogBuddyCn" in r.output
    assert r.data.get("version") == "0.2.2"


def test_self_version_tool_flags_bundle_install(tmp_path):
    tool = _tool("self_version",
                 self_status=lambda check: {"version": "0.2.2", "build": "",
                                            "git": {"is_git": False}, "last_update": None})
    r = _run(tool.execute({}, _ctx(tmp_path)))
    assert r.ok and "bundle" in r.output and "上次升级: 无记录" in r.output


def test_self_version_tool_reports_remote_check(tmp_path):
    tool = _tool("self_version",
                 self_status=lambda check: {"version": "0.2.2", "build": "", "git": {"is_git": True},
                                            "last_update": None,
                                            "remote": {"ok": True, "remote": "deadbeefcafe",
                                                       "update_available": True}})
    r = _run(tool.execute({"check_remote": True}, _ctx(tmp_path)))
    assert r.ok and "有新版本" in r.output


def test_self_update_tool_launches_and_tells_the_agent_to_stop(tmp_path):
    seen: dict = {}

    def fake_update(**kw):
        seen.update(kw)
        return {"started": True, "pid": 777, "log": "/var/lib/dogbuddy/logs/update.log",
                "start_delay": kw.get("start_delay", 45)}

    tool = _tool("self_update", self_update=fake_update)
    r = _run(tool.execute({"ref": "origin/main"}, _ctx(tmp_path)))
    assert r.ok
    assert seen["ref"] == "origin/main" and seen["start_delay"] == 45
    assert "777" in r.output
    assert "结束本轮" in r.output          # the agent must not keep calling tools


def test_self_update_tool_surfaces_launch_failure(tmp_path):
    tool = _tool("self_update", self_update=lambda **kw: {"started": False, "error": "no .git"})
    r = _run(tool.execute({}, _ctx(tmp_path)))
    assert not r.ok and "no .git" in r.output


def test_self_tools_fail_clearly_when_not_wired(tmp_path):
    """A degraded deployment must say so, not pretend the upgrade happened."""
    v = _run(_tool("self_version").execute({}, _ctx(tmp_path)))
    assert not v.ok
    u = _run(_tool("self_update").execute({}, _ctx(tmp_path)))
    assert not u.ok and "self_update" in u.output


def test_status_endpoint_exposes_last_update(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig
    from dogbuddy.main import DogBuddyApp

    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    data_dir = tmp_path / "data"
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="tok", port=8125),
                    paths=PathsConfig(data_dir=str(data_dir), workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    updater.write_result(data_dir, status="success", ok=True, previous="0.2.1", version="0.2.2")

    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = "tok"
        st = client.get("/api/status").json()
        assert st["last_update"]["status"] == "success"
        assert st["last_update"]["version"] == "0.2.2"
        assert st["llm_model"] == ""
