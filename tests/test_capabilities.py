# DogBuddyCn — capability catalogue API tests (T1.2)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dogbuddy.web.routes.capabilities import router


def _client():
    app = FastAPI()
    app.include_router(router(SimpleNamespace(), []), prefix="/api")
    return TestClient(app)


def test_catalogue_lists_real_tools():
    data = _client().get("/api/capabilities").json()
    assert data["tool_count"] == len(data["tools"])
    assert data["tool_count"] > 40          # worker(16) + admin(32) 双工具包全集
    names = {t["name"] for t in data["tools"]}
    for expected in ("read_file", "write_file", "shell", "ask_user",
                     "window_create", "config_set", "self_update"):
        assert expected in names


def test_tool_rows_are_self_describing():
    data = _client().get("/api/capabilities").json()
    for t in data["tools"]:
        assert t["description"], f"{t['name']} 缺描述"
        assert t["permission"] in ("read", "write", "shell", "network")
        assert t["risk"] in ("low", "mid", "high")
        assert isinstance(t["args"], list)
        for a in t["args"]:
            assert {"name", "type", "required", "description"} <= set(a)


def test_shell_tools_flagged_high_risk():
    data = _client().get("/api/capabilities").json()
    by_name = {t["name"]: t for t in data["tools"]}
    assert by_name["shell"]["risk"] == "high"
    assert by_name["read_file"]["risk"] == "low"


def test_roles_reference_only_real_tools():
    data = _client().get("/api/capabilities").json()
    tool_names = {t["name"] for t in data["tools"]}
    assert data["roles"], "角色表不能为空"
    role_names = {r["name"] for r in data["roles"]}
    assert {"admin", "worker_programmer"} <= role_names
    for r in data["roles"]:
        assert set(r["tool_names"]) <= tool_names, f"{r['name']} 引用了不存在的工具"
        assert r["tool_count"] == len(r["tool_names"])
        assert r["tool_count"] > 0


def test_worker_sees_construction_tools():
    data = _client().get("/api/capabilities").json()
    worker = next(r for r in data["roles"] if r["name"] == "worker_programmer")
    assert "write_file" in worker["tool_names"]     # 员工能干活
    assert "self_update" not in worker["tool_names"]  # 但不能自升级


# ------------------------------------------- v0.10.39 前端接线（能力栏空白回归）
def test_capabilities_view_is_wired_end_to_end():
    """回归（2026-10-18 owner 报"能力栏前端是空的"）：后端一直是好的
    （73 个工具），但 `api.js` 里**整好漏了** `capabilities()` 方法——
    views-capabilities.js 调 `A.capabilities()` 时同步抛 TypeError，
    `.catch` 接不住（throw 发生在 `.then` 挂上之前），页面整个空白。
    这条钉住三处的接线：API 方法存在、视图调用它、bundle 里有视图。"""
    js = Path(__file__).resolve().parents[1] / "dogbuddy/web/static/js"
    api_src = (js / "api.js").read_text(encoding="utf-8")
    view_src = (js / "views-capabilities.js").read_text(encoding="utf-8")
    bundle = (js / "index.bundle.js").read_text(encoding="utf-8")
    import re
    assert re.search(r"(?m)^\s*capabilities:\s*function", api_src), \
        "api.js 必须有 capabilities()——否则能力栏整个空白"
    assert "A.capabilities()" in view_src, "视图要调 A.capabilities()"
    assert "views-capabilities.js" in bundle, "bundle 里要有能力视图"
