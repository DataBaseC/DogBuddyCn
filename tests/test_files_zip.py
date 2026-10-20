# DogBuddyCn — directory zip download + workspace jump wiring (v0.10.39)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 两条反馈（2026-10-18）：
1. 「项目文件夹目前不能打包一起下载」→ `GET /api/files/zip` 目录打包 tar.gz；
2. 「挨个翻文件夹找成果太麻烦了，我希望群组里或者工位里可以有个跳转入口」→
   工位/群组/项目页的「打开工作区」跳转 + 文件管理器的 workspace 状态。

这些测试钉住：打包内容与排除口径、路径安全、以及前端的跳转接线。
"""
from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402

TOKEN = "zip-token"


def _client(tmp_path):
    cfg = AppConfig(
        llm=LLMConfig(base_url="http://invalid.local/v1", api_key="k", model="fake",
                      request_timeout=5.0),
        web=WebConfig(auth_token=TOKEN),
        paths=PathsConfig(data_dir=str(tmp_path / "data"),
                          workspaces_dir=str(tmp_path / "workspaces")),
    )
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    client = TestClient(app.web_app)
    client.__enter__()
    client.headers["X-Auth-Token"] = TOKEN
    return app, client


def _ws(tmp_path) -> Path:
    ws = tmp_path / "proj"
    (ws / "sub").mkdir(parents=True)
    (ws / "a.txt").write_text("hello", encoding="utf-8")
    (ws / "sub" / "b.txt").write_text("world", encoding="utf-8")
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("[core]", encoding="utf-8")
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "x.pyc").write_bytes(b"\x00\x01")
    return ws


# ------------------------------------------------------------ 后端：打包下载
def test_zip_dir_returns_targz_with_exclusions(tmp_path):
    app, client = _client(tmp_path)
    ws = _ws(tmp_path)
    app.files.roots.append(ws.resolve())
    r = client.get("/api/files/zip", params={"workspace": str(ws)})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/gzip")
    assert "proj.tar.gz" in r.headers.get("content-disposition", "")
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        names = tar.getnames()
        data = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    assert any(n.endswith("a.txt") for n in names), names
    assert any(n.endswith("sub/b.txt") for n in names), names
    assert data[b"hello" if b"hello" in data else list(data)[0]] is not None
    # 排除口径与 list/tree 一致：.git / __pycache__ 不进成果包
    assert not any(".git" in n for n in names), f".git 不该进包: {names}"
    assert not any("__pycache__" in n for n in names), names
    app.db.close()


def test_zip_rejects_missing_path_and_files(tmp_path):
    app, client = _client(tmp_path)
    ws = _ws(tmp_path)
    app.files.roots.append(ws.resolve())
    assert client.get("/api/files/zip", params={"path": "nope",
                                                "workspace": str(ws)}).status_code == 404
    assert client.get("/api/files/zip", params={"path": "a.txt",
                                                "workspace": str(ws)}).status_code == 404
    # 路径逃逸照样拦
    assert client.get("/api/files/zip", params={"path": "../../etc",
                                                "workspace": str(ws)}).status_code == 403
    app.db.close()


# ------------------------------------------------------------ 前端：跳转接线
def test_workspace_jump_is_wired():
    """工位/群组/项目页都要有跳转入口，文件管理器要认 workspace 参数。"""
    js = Path(__file__).resolve().parents[1] / "dogbuddy/web/static/js"
    files_mgr = (js / "filemanager.js").read_text(encoding="utf-8")
    api = (js / "api.js").read_text(encoding="utf-8")
    groups = (js / "views-groups.js").read_text(encoding="utf-8")
    main = (js / "views-main.js").read_text(encoding="utf-8")
    projects = (js / "views-projects.js").read_text(encoding="utf-8")
    # 文件管理器：workspace 状态 + 打包下载
    assert "workspace: ''" in files_mgr, "FileMgr 要有 workspace 状态"
    assert "A.files.list(FileMgr.path, FileMgr.workspace)" in files_mgr
    assert "zipDir" in files_mgr and "A.files.zip(" in files_mgr
    # API：zip 方法 + workspace 参数
    assert "zip: function (path, workspace)" in api
    assert "list: function (path, workspace)" in api
    # 三个入口
    assert "grp-open-ws" in groups and "data-act=\"files\"" in groups
    assert "data-act=\"workspace\"" in main and "App.go('files', { workspace: ws })" in main
    assert "data-prj=\"open-ws\"" in projects and "data-prj=\"zip\"" in projects
    # app.js 透传跳转参数
    assert "global.FileMgr.load(params || {})" in (js / "app.js").read_text(encoding="utf-8")


# ------------------------------------------- v0.10.41 复查：写操作必须也带 workspace
def test_write_ops_pass_workspace():
    """复查发现（2026-10-20）：跳转到项目/工位工作区后，预览/编辑/上传/新建/
    重命名/删除全部**静默打向默认根目录**——在项目里改文件会写到另一个工作区的
    同名路径上。读路径带了 workspace，写路径一个都没带。"""
    js = Path(__file__).resolve().parents[1] / "dogbuddy/web/static/js"
    mgr = (js / "filemanager.js").read_text(encoding="utf-8")
    api = (js / "api.js").read_text(encoding="utf-8")
    for call in ("A.files.read(entry.path, FileMgr.workspace)",
                 "A.files.image(entry.path, FileMgr.workspace)",
                 "A.files.write(p.path, editor.value, FileMgr.workspace)",
                 "A.files.mkdir(FileMgr.path, name, FileMgr.workspace)",
                 "A.files.rename(entry.path, name, FileMgr.workspace)",
                 "A.files.remove(entry.path, FileMgr.workspace)",
                 "A.files.upload(FileMgr.path, f, FileMgr.workspace)"):
        assert call in mgr, f"调用点没带 workspace：{call}"
    for sig in ("upload: function (path, file, workspace)",
                "mkdir: function (path, name, workspace)",
                "rename: function (path, newName, workspace)",
                "remove: function (path, workspace)",
                "write: function (path, content, workspace)"):
        assert sig in api, f"api.js 签名缺 workspace：{sig}"
    # 换工作区时必须重置相对路径（否则会停在上一个工作区的同名子目录里）
    assert "initial.workspace !== FileMgr.workspace" in mgr


def test_upload_respects_workspace(tmp_path):
    """后端上传端点也要认 workspace（否则拖拽上传落错工作区）。"""
    app, client = _client(tmp_path)
    ws = _ws(tmp_path)
    app.files.roots.append(ws.resolve())
    r = client.post("/api/files/upload",
                    files={"file": ("up.txt", b"uploaded-body", "text/plain")},
                    data={"path": "", "workspace": str(ws)})
    assert r.status_code == 200, r.text
    assert (ws / "up.txt").read_bytes() == b"uploaded-body"
    app.db.close()


def test_zip_refuses_oversized_directory(tmp_path, monkeypatch):
    """体积上限：临时 tar.gz 与源数据同一块盘，一次误点不能把盒子写满。"""
    from dogbuddy.services import file_service
    app, client = _client(tmp_path)
    ws = _ws(tmp_path)
    app.files.roots.append(ws.resolve())
    monkeypatch.setattr(file_service, "MAX_ZIP_BYTES", 5)  # 测试目录 ~10 字节
    r = client.get("/api/files/zip", params={"workspace": str(ws)})
    assert r.status_code == 400, r.text
    assert "目录太大" in r.text
    app.db.close()
