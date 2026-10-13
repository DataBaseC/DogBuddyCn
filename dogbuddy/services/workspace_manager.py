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
        # also include workspaces referenced by windows
        for w in self.db.list_windows():
            if w.workspace_path and w.workspace_path not in out:
                out.append(w.workspace_path)
        return out

    def assign(self, window_id: str, path: str) -> str:
        w = self.db.update_window(window_id, workspace_path=str(Path(path).expanduser().resolve()))
        if w is None:
            raise ValueError(f"window not found: {window_id}")
        self.db.audit("system", "workspace:assign", f"{window_id} -> {path}")
        return w.workspace_path
