# DogBuddyCn — config persistence, version endpoints, schema migration, self-update
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Regression tests for the pieces that were missing in the v1.3 baseline:

* configuration actually reaching disk (`save_config` + `/api/settings`)
* `/healthz` + `/api/version` (the self-update chain's assertions)
* `schema_version` bookkeeping for SQLite upgrades
* `manage.py update` plumbing (bundle extraction guards, lock, version stamp)
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sqlite3
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy import __version__  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig, load_config, save_config  # noqa: E402
from dogbuddy.db import SCHEMA_VERSION, Database  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = "test-token"


# --------------------------------------------------------------------------- manage.py
def _manage():
    """Import manage.py as a module (it is a script, not part of the package)."""
    path = Path(__file__).resolve().parents[1] / "manage.py"
    spec = importlib.util.spec_from_file_location("dogbuddy_manage", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- save_config
def test_save_config_roundtrip(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(cfg_file))

    cfg = AppConfig(
        llm=LLMConfig(base_url="http://example.test/v1", model="m1", api_key="secret"),
        web=WebConfig(auth_token="tok", port=8111),
        paths=PathsConfig(data_dir=str(tmp_path / "data")),
    )
    cfg.save_profile("cheap", LLMConfig(model="tiny"))
    cfg.active_llm_profile = "cheap"

    written = save_config(cfg)
    assert Path(written) == cfg_file and cfg_file.exists()

    # a fresh load must see everything (this is the v1.3 defect)
    again = load_config()
    assert again.llm.model == "m1"
    assert again.llm.api_key == "secret"
    assert again.web.port == 8111
    assert again.active_llm_profile == "cheap"
    assert again.llm_profiles["cheap"].model == "tiny"
    assert again.paths.data_dir == str(tmp_path / "data")

    # no temp litter, and a .bak appears once the file already exists
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    save_config(again)
    assert Path(str(cfg_file) + ".bak").exists()

    if os.name == "posix":
        assert oct(cfg_file.stat().st_mode)[-3:] == "600"


def test_save_config_does_not_leak_env_secrets(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(cfg_file))
    monkeypatch.setenv("DOGBUDDY_WEB_TOKEN", "env-token")

    cfg = load_config()  # env override applied
    assert cfg.web.auth_token == "env-token"
    save_config(cfg)

    raw = cfg_file.read_text(encoding="utf-8")
    assert "env-token" not in raw, "env-supplied secret must not be written to disk"


# --------------------------------------------------------------------------- db schema
def test_schema_version_recorded(tmp_path):
    db = Database(tmp_path / "a.db")
    try:
        assert db.schema_version == SCHEMA_VERSION
    finally:
        db.close()


def test_legacy_db_without_schema_version_is_migrated(tmp_path):
    path = tmp_path / "legacy.db"
    # simulate a v1.3 database: full tables, but windows lacks `llm_profile`
    # and there is no schema_version bookkeeping at all
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE windows(id TEXT PRIMARY KEY, name TEXT, role TEXT, workspace_path TEXT,"
        " model TEXT, mode TEXT DEFAULT 'BUILD', status TEXT DEFAULT 'idle', group_id TEXT,"
        " created_at TEXT, updated_at TEXT)")
    conn.execute(
        "INSERT INTO windows(id,name,role,workspace_path,model,mode,status,group_id,created_at,updated_at)"
        " VALUES('w1','old','worker_programmer','/tmp/ws','m','BUILD','idle','','','')")
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        assert db.schema_version == SCHEMA_VERSION
        cols = {r[1] for r in db._conn.execute("PRAGMA table_info(windows)")}
        assert "llm_profile" in cols
        assert db.get_window("w1").name == "old"
    finally:
        db.close()


# --------------------------------------------------------------------------- web app
def _app(tmp_path, monkeypatch) -> DogBuddyApp:
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(
        llm=LLMConfig(base_url="http://invalid.local/v1", api_key="k", model="fake", request_timeout=5.0),
        web=WebConfig(auth_token=TOKEN, port=8123),
        paths=PathsConfig(data_dir=str(tmp_path / "data"), workspaces_dir=str(tmp_path / "ws")),
    )
    cfg.gateway.enabled = False
    return DogBuddyApp(cfg)


def test_healthz_and_version_are_unauthenticated(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app.web_app)
    with client:
        h = client.get("/healthz")
        assert h.status_code == 200
        body = h.json()
        assert body["ok"] is True
        assert body["version"] == __version__
        assert body["schema_version"] == SCHEMA_VERSION

        v = client.get("/api/version")
        assert v.status_code == 200
        assert v.json()["version"] == __version__
        assert v.json()["name"] == "DogBuddyCn"


def test_settings_put_persists_to_disk(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = TOKEN
    with client:
        r = client.put("/api/settings", json={"llm": {"model": "persisted-model"},
                                             "panel": {"base_url": "http://panel.test:8787"}})
        assert r.status_code == 200
        assert r.json()["saved_to"].endswith("config.yaml")

    on_disk = load_config(tmp_path / "config.yaml")
    assert on_disk.llm.model == "persisted-model"
    assert on_disk.panel.base_url == "http://panel.test:8787"


def test_profile_save_persists(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app.web_app)
    client.headers["X-Auth-Token"] = TOKEN
    with client:
        r = client.post("/api/settings/profiles/cheap", json={"llm": {"model": "tiny"}, "set_active": True})
        assert r.status_code == 200
        # an unknown profile on PATCH must be a clean 404, not a NameError 500
        r404 = client.patch("/api/settings/profiles/nope", json={"llm": {"model": "x"}})
        assert r404.status_code == 404

    on_disk = load_config(tmp_path / "config.yaml")
    assert on_disk.llm_profiles["cheap"].model == "tiny"
    assert on_disk.active_llm_profile == "cheap"


# --------------------------------------------------------------------------- manage.py plumbing
def test_extract_bundle_finds_code_root(tmp_path):
    m = _manage()
    src = tmp_path / "src" / "DogBuddyCn"
    (src / "dogbuddy").mkdir(parents=True)
    (src / "dogbuddy" / "__init__.py").write_text("__version__='9.9.9'\n", encoding="utf-8")
    (src / "manage.py").write_text("# x\n", encoding="utf-8")

    tgz = tmp_path / "b.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(src, arcname="DogBuddyCn")

    root = m._extract_bundle(tgz, tmp_path / "out")
    assert (root / "dogbuddy" / "__init__.py").exists()


def test_extract_bundle_rejects_path_traversal(tmp_path):
    m = _manage()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../../evil.txt", "pwned")
    with pytest.raises(RuntimeError):
        m._extract_bundle(bad, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


# ------------------------------------------------------------------ P0-4 解包逃逸
def test_extract_bundle_拒绝同前缀兄弟目录逃逸(tmp_path):
    """审查报告 P0-4：`startswith` 是字符串前缀判断，会放过同前缀的兄弟目录。

    成员名 `../unpacked-escape/pwned.txt` 解析后是 `.../unpacked-escape/pwned.txt`，
    前缀确实是 `.../unpacked` → 初版守卫通过 → 文件落到解包目录之外。
    """
    import io

    m = _manage()
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as t:
        info = tarfile.TarInfo("../unpacked-escape/pwned.txt")
        payload = b"pwned"
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))

    with pytest.raises(RuntimeError):
        m._extract_bundle(bad, tmp_path / "unpacked")
    assert not (tmp_path / "unpacked-escape" / "pwned.txt").exists(), "文件逃出了解包目录"


def test_extract_bundle_不提取符号链接成员(tmp_path):
    """名字合法但链接目标逃逸——按成员名判断的守卫看不见，所以直接不提取。"""
    m = _manage()
    src = tmp_path / "src" / "DogBuddyCn"
    (src / "dogbuddy").mkdir(parents=True)
    (src / "dogbuddy" / "__init__.py").write_text("__version__='1.0'\n", encoding="utf-8")

    tgz = tmp_path / "sym.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(src, arcname="DogBuddyCn")
        link = tarfile.TarInfo("DogBuddyCn/evil")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        t.addfile(link)

    root = m._extract_bundle(tgz, tmp_path / "out")
    assert (root / "dogbuddy" / "__init__.py").exists()
    assert not (root / "evil").exists(), "符号链接成员不得被提取出来"


def test_extract_bundle_拒绝绝对路径成员(tmp_path):
    import io

    m = _manage()
    bad = tmp_path / "abs.tar.gz"
    with tarfile.open(bad, "w:gz") as t:
        info = tarfile.TarInfo("/tmp/abs-escape.txt")
        payload = b"x"
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))
    with pytest.raises(RuntimeError):
        m._extract_bundle(bad, tmp_path / "out")


# ------------------------------------------------------------------ P2-1 未来库
def test_未来库被拒绝打开(tmp_path):
    """审查报告 P2-1：旧代码打开新库不报错、不告警，照常写入。

    结果是读字段静默丢失、写随机 `OperationalError`。现在必须 fail-fast——
    起不来 → 自更新回滚，而不是带着错版本继续写数据。
    """
    from dogbuddy.db import SchemaTooNewError

    p = tmp_path / "future.db"
    db = Database(p)
    db._conn.execute("INSERT OR REPLACE INTO schema_version(version, applied_at) VALUES(?,?)",
                     (SCHEMA_VERSION + 90, "2026-10-14T00:00:00"))
    db._conn.commit()
    db.close()

    with pytest.raises(SchemaTooNewError) as ei:
        Database(p)
    msg = str(ei.value)
    assert str(SCHEMA_VERSION) in msg and str(SCHEMA_VERSION + 90) in msg


def test_同版本库正常打开(tmp_path):
    db = Database(tmp_path / "ok.db")
    db.close()
    db2 = Database(tmp_path / "ok.db")
    assert db2.schema_version == SCHEMA_VERSION
    db2.close()


def test_升级拒绝降级到更旧的_schema(tmp_path, monkeypatch):
    """ROADMAP 声称 `manage.py update` 缺迁移会拒绝前进——初版无任何版本检查。"""
    m = _manage()
    data = tmp_path / "data"
    data.mkdir()
    db = Database(data / "dogbuddy.db")
    db._conn.execute("INSERT OR REPLACE INTO schema_version(version, applied_at) VALUES(?,?)",
                     (SCHEMA_VERSION, "2026-10-14T00:00:00"))
    db._conn.commit()
    db.close()

    monkeypatch.setattr(m, "_data_dir", lambda cfg: data)
    args = argparse.Namespace(config=str(tmp_path / "c.yaml"))

    code = tmp_path / "code"
    (code / "dogbuddy").mkdir(parents=True)
    (code / "dogbuddy" / "db.py").write_text(
        f"SCHEMA_VERSION = {SCHEMA_VERSION - 1}\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as ei:
        m._assert_no_schema_downgrade(code, args)
    assert "低于现有库" in str(ei.value)

    # 同版本 / 更高版本放行
    (code / "dogbuddy" / "db.py").write_text(
        f"SCHEMA_VERSION = {SCHEMA_VERSION}\n", encoding="utf-8")
    m._assert_no_schema_downgrade(code, args)
    (code / "dogbuddy" / "db.py").write_text(
        f"SCHEMA_VERSION = {SCHEMA_VERSION + 1}\n", encoding="utf-8")
    m._assert_no_schema_downgrade(code, args)


# ------------------------------------------------------------------ P2-4 PID 校验
def test_alive_校验进程命令行(tmp_path, monkeypatch):
    """审查报告 P2-4：/tmp/dogbuddy.pid 可写 + PID 复用 → stop 可能误杀无关进程。"""
    m = _manage()
    if m.IS_WINDOWS:
        pytest.skip("cmdline 校验只在 POSIX 生效（Windows 走 tasklist 精确匹配 pid）")

    monkeypatch.setattr(m, "_proc_cmdline", lambda pid: "nginx: worker process")
    assert m._looks_like_dogbuddy(4321) is False

    monkeypatch.setattr(m, "_proc_cmdline",
                        lambda pid: "/usr/bin/python3 -m dogbuddy.main")
    assert m._looks_like_dogbuddy(4321) is True

    monkeypatch.setattr(m, "_proc_cmdline", lambda pid: None)
    assert m._looks_like_dogbuddy(4321) is None, "读不到 → 无法判定"

    monkeypatch.setattr(m, "_proc_cmdline", lambda pid: "")
    assert m._looks_like_dogbuddy(4321) is False, "僵尸进程"


def test_alive_无法判定时保守认为活着(monkeypatch):
    """误判为活只会拒绝启动（安全）；误判为死会导致重复启动（不安全）。"""
    m = _manage()
    if m.IS_WINDOWS:
        pytest.skip("仅 POSIX 路径")
    monkeypatch.setattr(m, "_proc_cmdline", lambda pid: None)
    assert m._alive(os.getpid()) is True

    monkeypatch.setattr(m, "_proc_cmdline", lambda pid: "nginx")
    assert m._alive(os.getpid()) is False, "不是 dogbuddy → 绝不能当成自己的进程去 kill"



def test_update_lock_is_exclusive_and_releases(tmp_path):
    m = _manage()
    with m._update_lock(tmp_path):
        assert (tmp_path / "update.lock").exists()
    # released → can be taken again
    with m._update_lock(tmp_path):
        pass


def test_update_check_mode_needs_a_source(tmp_path, monkeypatch):
    m = _manage()
    args = argparse.Namespace(
        app_dir=str(tmp_path), config=str(tmp_path / "config.yaml"), ref=None,
        from_bundle=None, from_url=None, check=True, force=False, no_backup=True,
        no_restart=True, health_tries=1, health_delay=0.1)
    # no .git and no bundle → refuse with a helpful message, exit code 1
    assert m.cmd_update(args) == 1


def test_installed_version_reads_disk(tmp_path):
    m = _manage()
    repo = Path(__file__).resolve().parents[1]
    assert m._installed_version(repo) == __version__
    assert (repo / "dogbuddy" / "__init__.py").exists()


# --------------------------------------------------------------------------- llm wiring
def test_app_llm_factory_returns_a_provider_not_a_function(tmp_path, monkeypatch):
    """Regression: v1.3 wired `profiles.factory` (an unsatisfied method) as the
    factory, so `llm_factory()` yielded a lambda and every real LLM call died
    with "'function' object has no attribute 'chat'". Tests hid it because they
    always inject a fake factory."""
    app = _app(tmp_path, monkeypatch)
    provider = app.llm_factory()
    assert not callable(provider), f"llm_factory() returned {provider!r}, not a provider"
    assert hasattr(provider, "chat"), "provider must expose .chat(messages, tools)"

    # a profile-aware call must work too (TaskRunner / WindowManager pass names)
    assert hasattr(app.llm_factory(""), "chat")
    assert app.llm_factory() is app.llm_factory("")  # cached
