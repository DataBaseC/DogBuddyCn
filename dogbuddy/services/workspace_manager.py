# DogBuddyCn — workspace management service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""WorkspaceManager: create/list/assign employee workspaces (git-friendly dirs)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class WorkspaceManager:
    def __init__(self, workspaces_root: str | Path, db: Any):
        self.root = Path(workspaces_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = db

    def create(self, path: str, git_init: bool = False) -> dict[str, str]:
        p = Path(path).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        if git_init and not (p / ".git").exists():
            subprocess.run(["git", "init", "-q", str(p)], check=False,
                           capture_output=True, timeout=30)
        self.db.audit("system", "workspace:create", str(p))
        return {"path": str(p.resolve()), "git": str((p / ".git").exists()).lower()}

    def list(self) -> list[str]:
        out = []
        for child in sorted(self.root.iterdir()) if self.root.exists() else []:
            if child.is_dir():
                out.append(str(child))
        # T7.5: 边界按**项目**对齐——挂了项目的工位，其可见根是项目根，
        # 而不是 legacy 的 workspace_path（否则文件管理器会列出被项目取代的老目录）。
        for p in self.db.list_projects():
            if p.root_path and p.root_path not in out:
                out.append(p.root_path)
        # 没挂项目的工位保留老行为（兼容老数据）
        for w in self.db.list_windows():
            if getattr(w, "project_id", ""):
                continue
            if w.workspace_path and w.workspace_path not in out:
                out.append(w.workspace_path)
        return out

    def assign(self, window_id: str, path: str) -> str:
        w = self.db.update_window(window_id, workspace_path=str(Path(path).expanduser().resolve()))
        if w is None:
            raise ValueError(f"window not found: {window_id}")
        self.db.audit("system", "workspace:assign", f"{window_id} -> {path}")
        return w.workspace_path

    # --------------------------------------------------------- projects (T7.5)
    def create_project(self, name: str, root_path: str, git_init: bool = False,
                       params: dict | None = None) -> Any:
        """建项目：项目=根目录+默认参数，工位/群组再挂上来。"""
        from ..db import new_id
        from ..ports import Project
        if not str(name or "").strip():
            raise ValueError("项目名不能为空")
        root = Path(root_path).expanduser() if str(root_path or "").strip() else \
            (self.root / str(name).strip())
        root.mkdir(parents=True, exist_ok=True)
        if git_init and not (root / ".git").exists():
            subprocess.run(["git", "init", "-q", str(root)], check=False,
                           capture_output=True, timeout=30)
        p = Project(id=new_id(), name=str(name).strip(), root_path=str(root.resolve()),
                    params=params if isinstance(params, dict) else {})
        self.db.insert_project(p)
        self.db.audit("system", "project:create", f"{p.id} {p.name} root={p.root_path}")
        return p

    def mount(self, window_id: str, project_id: str, subdir: str = "") -> Any:
        """把工位挂到项目上（可选子目录 = 工位自己的工作边界）。"""
        proj = self.db.get_project(project_id)
        if proj is None:
            raise ValueError(f"项目不存在：{project_id}")
        sub = str(subdir or "").strip().strip("/\\")
        if sub:
            target = Path(proj.root_path) / sub
            # 防越界：子目录必须落在项目根内
            try:
                target.resolve().relative_to(Path(proj.root_path).resolve())
            except ValueError as e:
                raise ValueError(f"子目录越出项目根：{subdir}") from e
            target.mkdir(parents=True, exist_ok=True)
        w = self.db.update_window(window_id, project_id=proj.id, subdir=sub)
        if w is None:
            raise ValueError(f"工位不存在：{window_id}")
        self.db.audit("system", "project:mount",
                      f"window={window_id} project={proj.id} subdir={sub or '(根)'}")
        return w
