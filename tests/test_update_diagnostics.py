# DogBuddyCn — self-update diagnostics (v11)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""回归测试：升级链路的**诊断诚实性**。

生产实测（两次复现）：
  * 不设 `DOGBUDDY_PANEL_PASSWORD` 时 `_panel_restart()` 返回 False，
    升级器把"重启机制不可用"当成"新版本有毛病" → **回滚掉一份本来正确的代码**
    （`status: rolled-back`）。
  * 游离进程（manage.py 自己 `cmd_start` 起的、面板不认识）占着端口时，
    面板重启无效，健康检查读到**旧进程**；只要那次发布没 bump 版本号，
    或 `_installed_version` 退化成 None，就会报 `success`——**假成功**。

这一组测试钉三件事：失败原因可区分、健康检查要看**进程身份（pid）**、
任何退出路径都落终态。
"""
from __future__ import annotations

import argparse
import importlib.util
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig, save_config  # noqa: E402
from dogbuddy.updater import read_result, write_result  # noqa: E402


def _manage():
    path = Path(__file__).resolve().parents[1] / "manage.py"
    spec = importlib.util.spec_from_file_location("dogbuddy_manage_diag", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def manage():
    return _manage()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _config(tmp_path: Path, port: int) -> Path:
    cfg_file = tmp_path / "config.yaml"
    cfg = AppConfig(
        llm=LLMConfig(base_url="http://x/v1", model="m", api_key="k"),
        web=WebConfig(auth_token="t", host="127.0.0.1", port=port),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    save_config(cfg, str(cfg_file))
    return cfg_file


# ------------------------------------------------------- ① 失败原因必须可区分
def test_panel_restart_reasons_are_distinguishable(manage, monkeypatch, tmp_path):
    monkeypatch.delenv("DOGBUDDY_PANEL_APP_ID", raising=False)
    monkeypatch.delenv("DOGBUDDY_PANEL_PASSWORD", raising=False)
    assert manage._panel_restart() == (False, "no_app_id")

    monkeypatch.setenv("DOGBUDDY_PANEL_APP_ID", "abc")
    assert manage._panel_restart() == (False, "no_panel_password"), \
        "缺面板密码必须给出独立原因（初版只说 False，于是被误判成'新版本起不来'）"

    # 有密码但没有 base_url：也要能和"密码缺失"区分开
    cfg_file = _config(tmp_path, _free_port())
    from dogbuddy.config import load_config
    cfg = load_config(str(cfg_file))
    cfg.panel.base_url = ""
    save_config(cfg, str(cfg_file))
    monkeypatch.setattr(manage, "DEFAULT_CONFIG", str(cfg_file))
    monkeypatch.setenv("DOGBUDDY_PANEL_PASSWORD", "pw")
    monkeypatch.setenv("DOGBUDDY_PANEL_URL", "")
    assert manage._panel_restart() == (False, "no_panel_base_url")


# ------------------------------------------------- ② 健康检查要看进程身份（pid）
class _Args:
    def __init__(self, cfg_file: Path):
        self.config = str(cfg_file)
        self.no_restart = False
        self.health_tries = 1
        self.health_delay = 0.0
        self.ref = "origin/main"
        self.app_dir = None
        self._previous_version = "0.9.0"
        self.force = False
        self.no_backup = True
        self.check = False
        self.from_bundle = None
        self.from_url = None
        self.start_delay = 0


def test_finish_same_pid_is_not_success(manage, tmp_path, monkeypatch):
    """重启前后是同一个 pid → 绝不能报 success（这就是"假升级"）。"""
    cfg_file = _config(tmp_path, _free_port())
    args = _Args(cfg_file)
    monkeypatch.setattr(manage, "_installed_version", lambda app_dir: "9.9.9")
    monkeypatch.setattr(manage, "_restart_service", lambda *a, **k: 0)
    monkeypatch.setattr(manage, "_probe", lambda host, port, timeout=3.0: {"pid": 4242})
    monkeypatch.setattr(manage, "_health", lambda *a, **k: {"pid": 4242, "version": "9.9.9",
                                                            "schema_version": 11})
    stamp_calls = []
    monkeypatch.setattr(manage, "_write_version_stamp",
                        lambda app_dir: stamp_calls.append(app_dir) or "stamp")

    rc = manage._finish(tmp_path, args, "20260101-000000", rollback=lambda: None)
    res = read_result(tmp_path / "data")
    assert rc == 1, "重启没生效却返回 0（成功）"
    assert res["status"] == "restart-noop", f"应是 restart-noop，实际 {res['status']}"
    assert res["ok"] is False
    assert res["observed"]["pid"] == 4242 and res["observed"]["before_pid"] == 4242
    assert not stamp_calls, "重启没生效时不能改写 VERSION 戳（会让 /healthz 的 build 也撒谎）"


def test_finish_success_when_pid_changed(manage, tmp_path, monkeypatch):
    """对照组：pid 变了才算真重启，正常写 success。"""
    cfg_file = _config(tmp_path, _free_port())
    args = _Args(cfg_file)
    probes = iter([{"pid": 111}, {"pid": 222}])
    monkeypatch.setattr(manage, "_installed_version", lambda app_dir: "9.9.9")
    monkeypatch.setattr(manage, "_restart_service", lambda *a, **k: 0)
    monkeypatch.setattr(manage, "_probe", lambda host, port, timeout=3.0: next(probes))
    monkeypatch.setattr(manage, "_health", lambda *a, **k: {"pid": 222, "version": "9.9.9",
                                                            "schema_version": 11})
    monkeypatch.setattr(manage, "_write_version_stamp", lambda app_dir: "stamp")

    rc = manage._finish(tmp_path, args, "s", rollback=lambda: None)
    res = read_result(tmp_path / "data")
    assert rc == 0 and res["status"] == "success"
    assert res["observed"] == {"before_pid": 111, "pid": 222, "version": "9.9.9",
                               "schema_version": 11}


def test_finish_says_old_process_still_serving(manage, tmp_path, monkeypatch):
    """健康检查失败时，结果文件要说清"旧进程还在应答"，而不是笼统的"版本不对"。"""
    cfg_file = _config(tmp_path, _free_port())
    args = _Args(cfg_file)
    monkeypatch.setattr(manage, "_installed_version", lambda app_dir: "9.9.9")
    monkeypatch.setattr(manage, "_restart_service", lambda *a, **k: 0)
    monkeypatch.setattr(manage, "_probe",
                        lambda host, port, timeout=3.0: {"pid": 777, "version": "0.9.0"})
    health_calls = {"n": 0}

    def fake_health(host, port, expect_version=None, tries=1, delay=0):
        health_calls["n"] += 1
        return None if expect_version else {"version": "0.9.0", "pid": 777}

    monkeypatch.setattr(manage, "_health", fake_health)
    monkeypatch.setattr(manage, "_write_version_stamp", lambda app_dir: "stamp")

    rc = manage._finish(tmp_path, args, "s", rollback=lambda: None)
    res = read_result(tmp_path / "data")
    assert rc == 1 and res["status"] == "rolled-back"
    assert "旧进程" in res["detail"] and "pid=777" in res["detail"]
    assert res["observed"]["pid"] == 777


# ------------------------------------------------------- ③ 终态必写
def test_finalize_result_never_leaves_running(manage, tmp_path):
    data = tmp_path / "data"
    args = _Args(_config(tmp_path, _free_port()))

    write_result(data, status="running", previous="1.0", version="")
    manage._finalize_result(data, args, "1.0", 1)
    assert read_result(data)["status"] == "failed"

    write_result(data, status="running", previous="1.0", version="")
    manage._finalize_result(data, args, "1.0", 0)
    assert read_result(data)["status"] == "noop", "已是最新（退出码 0）不该留 running"

    write_result(data, status="success", previous="1.0", version="1.1")
    manage._finalize_result(data, args, "1.0", 1)
    assert read_result(data)["status"] == "success", "已有终态不能被覆盖"


def test_cmd_update_already_latest_writes_terminal_status(manage, tmp_path, monkeypatch):
    """`_update_git` 报"已是最新"时（退出码 0），结果文件必须是终态而非 running。"""
    app_dir = tmp_path / "app"
    (app_dir / ".git").mkdir(parents=True)
    cfg_file = _config(tmp_path, _free_port())
    args = _Args(cfg_file)
    args.app_dir = str(app_dir)
    monkeypatch.setattr(manage, "_installed_version", lambda app_dir: "0.10.5")
    monkeypatch.setattr(manage, "_report_port_owner", lambda host, port: None)

    import contextlib

    @contextlib.contextmanager
    def fake_lock(path):
        yield

    monkeypatch.setattr(manage, "_update_lock", fake_lock)
    monkeypatch.setattr(manage, "_backup_config", lambda config, stamp: None)
    monkeypatch.setattr(manage, "_update_git", lambda args, app_dir, stamp: 0)

    rc = manage.cmd_update(args)
    res = read_result(tmp_path / "data")
    assert rc == 0
    assert res["status"] == "noop", f"停在 {res['status']}（界面会永远显示'升级中'）"


def test_cmd_update_unexpected_exception_writes_failed(manage, tmp_path, monkeypatch):
    """`subprocess.TimeoutExpired` 之类的漏网异常也要落终态。"""
    app_dir = tmp_path / "app"
    (app_dir / ".git").mkdir(parents=True)
    cfg_file = _config(tmp_path, _free_port())
    args = _Args(cfg_file)
    args.app_dir = str(app_dir)
    monkeypatch.setattr(manage, "_installed_version", lambda app_dir: "0.10.5")
    monkeypatch.setattr(manage, "_report_port_owner", lambda host, port: None)

    import contextlib
    import subprocess

    @contextlib.contextmanager
    def fake_lock(path):
        yield

    monkeypatch.setattr(manage, "_update_lock", fake_lock)
    monkeypatch.setattr(manage, "_backup_config", lambda config, stamp: None)

    def boom(args, app_dir, stamp):
        raise subprocess.TimeoutExpired("git", 120)

    monkeypatch.setattr(manage, "_update_git", boom)

    rc = manage.cmd_update(args)
    res = read_result(tmp_path / "data")
    assert rc == 1 and res["status"] == "failed"
