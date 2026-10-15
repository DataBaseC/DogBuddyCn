# DogBuddyCn — 关停必须有界（SIGTERM 卡死 → 端口不放 → 面板重启循环）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""回归测试：关停不许挂住进程。

线上实测（2026-10-15，部署 0.10.7 时）：
  1. `kill`（SIGTERM）之后进程**一直不退**（State=S，epoll_wait），必须 SIGKILL；
  2. 它不退就继续占着 :8100，外部守护启动的新进程 `[Errno 98] address already in use`
     秒退 → 面板进入"启动 → 退出码 3 → 再启动"的循环；
  3. 日志里呈现出"升级失败"的假象（其实代码已经换好了）。

修法：`DogBuddyApp.stop()` 的每一步都有超时上限；uvicorn 再加一道
`timeout_graceful_shutdown`。这两条都不依赖"哪个子系统写得好"。

注意：本文件只用 `helpers.run`（= 套件里统一的 `get_event_loop().run_until_complete`），
**不能用 `asyncio.run()`** —— 它会把当前事件循环置空，导致同进程里后面那些
用 `get_event_loop()` 的测试全红（实测踩过）。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.main import DogBuddyApp, build_uvicorn_config  # noqa: E402

from helpers import run  # noqa: E402


def _app(tmp_path, monkeypatch) -> DogBuddyApp:
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(
        llm=LLMConfig(base_url="", model="", api_key=""),
        web=WebConfig(auth_token="t", port=8163),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    return DogBuddyApp(cfg)


async def _timed_stop(app) -> float:
    t0 = time.monotonic()
    await asyncio.wait_for(app.stop(), timeout=10)   # 10s 内没结束就算失败
    return time.monotonic() - t0


def test_stop_is_bounded_when_a_subsystem_hangs(tmp_path, monkeypatch):
    """`windows.stop()` 永不返回时，`stop()` 仍必须在有限时间内结束。"""
    app = _app(tmp_path, monkeypatch)
    app.SHUTDOWN_STEP_TIMEOUT = 0.3          # 测试里缩短
    app._started = True

    async def never_returns():
        await asyncio.sleep(3600)

    monkeypatch.setattr(app.windows, "stop", never_returns)

    elapsed = run(_timed_stop(app))
    assert elapsed < 5, f"关停被挂住了（{elapsed:.1f}s）——SIGTERM 又会卡住端口"
    assert app._started is False


def test_stop_is_bounded_when_mcp_hangs(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.SHUTDOWN_STEP_TIMEOUT = 0.3
    app._started = True

    async def hang():
        await asyncio.sleep(3600)

    async def ok():
        return None

    monkeypatch.setattr(app.windows, "stop", ok)
    monkeypatch.setattr(app.mcp, "close", hang)

    elapsed = run(_timed_stop(app))
    assert elapsed < 5, f"mcp 挂住把关停拖死了（{elapsed:.1f}s）"


def test_uvicorn_config_bounds_graceful_shutdown(tmp_path, monkeypatch):
    """uvicorn 那道保险必须存在：即使有请求不肯放手，也要在 10s 后强退。"""
    app = _app(tmp_path, monkeypatch)
    cfg = build_uvicorn_config(app, app.config)
    assert cfg.timeout_graceful_shutdown == 10
    assert cfg.port == 8163


def test_shutdown_step_swallows_errors(tmp_path, monkeypatch):
    """关停步骤抛异常也不能中断后续清理（否则 db 都不会关）。"""
    app = _app(tmp_path, monkeypatch)
    app.SHUTDOWN_STEP_TIMEOUT = 0.3
    app._started = True

    async def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(app.windows, "stop", boom)
    run(_timed_stop(app))
    assert app._started is False
