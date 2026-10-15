# DogBuddyCn — pytest conftest (make tests/helpers importable + isolate user config)
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """**任何测试都不许写到真实用户配置上。**

    2026-10-15 实测事故：`tests/test_web_api.py::test_settings_profiles_crud` 调
    `/api/settings/profiles/cheap` → `persist()` → `save_config()`，而它没有隔离
    `DOGBUDDY_CONFIG`，于是真实用户配置
    （Windows: `%LOCALAPPDATA%\\dogbuddy\\config.yaml`）被写成了测试垃圾：
    `base_url: http://invalid.local/v1`、`model: fake`、`data_dir: <pytest 临时目录>`。
    本地跑一次 `pytest` 就把用户真实的 LLM 配置（含密钥）覆盖掉——静默、且很难发现。

    这里对**每个测试**都把配置路径指到 `tmp_path`；测试自己想指定路径时照常
    `monkeypatch.setenv`（它在本 fixture 之后生效，仍是隔离路径）。
    """
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "dogbuddy-test-config.yaml"))
    # 顺手把平台默认数据目录也钉在 tmp 里（`default_data_dir()` 在 Windows 读 LOCALAPPDATA）：
    # 任何忘记传 `PathsConfig(data_dir=...)` 的测试都不该落到真实家目录。
    if sys.platform == "win32":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
