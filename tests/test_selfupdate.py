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
