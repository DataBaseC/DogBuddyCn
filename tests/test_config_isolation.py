# DogBuddyCn — 测试隔离守卫（真实用户配置绝不能被测试写坏）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""回归测试：跑一次 `pytest` 不能动到**真实用户配置**。

2026-10-15 实测事故：`test_web_api.py::test_settings_profiles_crud` 打
`/api/settings/profiles/cheap` → `persist()` → `save_config()`，而它没隔离
`DOGBUDDY_CONFIG`，于是真实用户配置被写成
`base_url: http://invalid.local/v1` / `model: fake` / `data_dir: <pytest tmp>`——
用户的 LLM 配置（含密钥）被静默覆盖。

修法在 `tests/conftest.py` 的 autouse fixture；这里钉住"它确实生效"。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig, config_path  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402

TOKEN = "iso-token"


def _client(tmp_path) -> TestClient:
    cfg = AppConfig(
        llm=LLMConfig(base_url="", model="", api_key=""),
        web=WebConfig(auth_token=TOKEN, port=8157),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    return TestClient(app.web_app)


def test_conftest_points_the_config_path_into_tmp():
    p = Path(str(config_path()))
    assert p.name == "dogbuddy-test-config.yaml", f"conftest 隔离没生效：{p}"


def test_settings_save_writes_into_tmp_not_the_real_user_config(tmp_path):
    """事故的直接复现路径：PUT /api/settings 会 persist()，落点必须在 tmp 里。"""
    real_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "dogbuddy"
    with _client(tmp_path) as client:
        client.headers["X-Auth-Token"] = TOKEN
        r = client.put("/api/settings", json={"llm": {"model": "isolated-model"}})
        assert r.status_code == 200
        saved = r.json()["saved_to"]
        assert saved, "没落盘？"
        assert str(tmp_path) in saved, f"配置写到了测试之外：{saved}"
        assert not str(saved).startswith(str(real_dir)), f"写到了真实用户配置目录：{saved}"
        # 真实用户配置（如果存在）不能被这次调用碰过
        real = real_dir / "config.yaml"
        assert "isolated-model" not in (real.read_text(encoding="utf-8") if real.exists() else "")


def test_profile_route_also_stays_inside_tmp(tmp_path):
    """就是这条路径把用户配置写坏的（profiles 保存 → persist）。"""
    real = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "dogbuddy" / "config.yaml"
    before = real.read_bytes() if real.exists() else None
    with _client(tmp_path) as client:
        client.headers["X-Auth-Token"] = TOKEN
        r = client.post("/api/settings/profiles/cheap",
                        json={"llm": {"base_url": "https://cheap.example/v1",
                                      "api_key": "ck", "model": "cheap-model"},
                              "set_active": True})
        assert r.status_code == 200
    after = real.read_bytes() if real.exists() else None
    assert before == after, "真实用户配置被测试改动了"
    assert (tmp_path / "dogbuddy-test-config.yaml").exists()
