# DogBuddyCn — workspace file service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""FileService: explorer-style file management over the configured workspaces.

Path safety is a business rule and lives here (L2), not in the L1 routes.
All paths are workspace-relative; escapes return PermissionError.
"""
from __future__ import annotations

import base64
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_PREVIEW_BYTES = 200 * 1024

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg"}
LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".html": "html",
    ".css": "css", ".json": "json", ".md": "markdown", ".sh": "shell",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".sql": "sql",
    ".go": "go", ".rs": "rust", ".java": "java", ".c": "c", ".cpp": "cpp",
}


class FileService:
    def __init__(self, roots: list[str | Path]):
        """roots: allowed absolute base directories (workspaces)."""
        self.roots = [Path(r).expanduser().resolve() for r in roots]

    def _root_for(self, workspace: str | None) -> Path:
        if workspace:
            p = Path(workspace).expanduser().resolve()
            if p in self.roots or any(p.is_relative_to(r) for r in self.roots):
                return p
            raise PermissionError(f"workspace not allowed: {workspace}")
        return self.roots[0] if self.roots else Path.cwd()

    def resolve(self, rel_path: str, workspace: str | None = None) -> Path:
        root = self._root_for(workspace)
        target = (root / (rel_path or "")).resolve()
        if not target.is_relative_to(root):
            raise PermissionError(f"path escapes workspace: {rel_path}")
        return target

    # ----------------------------------------------------------------- read
    def tree(self, rel_path: str = "", workspace: str | None = None, depth: int = 2) -> list[dict[str, Any]]:
        base = self.resolve(rel_path, workspace)
        out: list[dict[str, Any]] = []
        self._walk(base, base, out, depth)
        return out

    def _walk(self, base: Path, node: Path, out: list[dict[str, Any]], depth: int) -> None:
        if depth < 0 or not node.is_dir():
            return
        try:
            children = sorted(node.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for child in children:
            if child.name in (".git", "node_modules", "__pycache__", ".venv"):
                continue
            item = {"name": child.name,
                    "path": str(child.relative_to(base)),
                    "type": "dir" if child.is_dir() else "file"}
            out.append(item)
            if child.is_dir():
                self._walk(base, child, out, depth - 1)

    def list(self, rel_path: str = "", workspace: str | None = None) -> dict[str, Any]:
        base_root = self._root_for(workspace)
        target = self.resolve(rel_path, workspace)
        entries = []
        for child in sorted(target.iterdir() if target.is_dir() else [],
                            key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name in (".git", "node_modules", "__pycache__", ".venv"):
                continue
            stat = child.stat()
            entries.append({
                "name": child.name,
                "path": str(child.relative_to(base_root)),
                "type": "dir" if child.is_dir() else "file",
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
        parts = Path(rel_path).parts if rel_path else ()
        crumbs = [{"name": str(base_root.name), "path": ""}]
        acc = ""
        for part in parts:
            acc = f"{acc}/{part}" if acc else part
            crumbs.append({"name": part, "path": acc})
        return {"path": rel_path, "breadcrumbs": crumbs, "entries": entries}

    def read(self, rel_path: str, workspace: str | None = None) -> dict[str, Any]:
        target = self.resolve(rel_path, workspace)
        if not target.is_file():
            raise FileNotFoundError(rel_path)
        size = target.stat().st_size
        if size > MAX_PREVIEW_BYTES:
            raise OverflowError(f"file too large to preview ({size} bytes)")
        if target.suffix.lower() in IMAGE_EXTS:
            return {"content": "", "language": "image", "size": size,
                    "image_base64": base64.b64encode(target.read_bytes()).decode()}
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise ValueError("binary file (no preview)") from e
        return {"content": text, "language": LANG_MAP.get(target.suffix.lower(), "text"), "size": size}

    def read_bytes(self, rel_path: str, workspace: str | None = None) -> tuple[Path, str]:
        target = self.resolve(rel_path, workspace)
        if not target.is_file():
            raise FileNotFoundError(rel_path)
        return target, target.name

    # ---------------------------------------------------------------- write
    def write(self, rel_path: str, content: str, workspace: str | None = None) -> int:
        target = self.resolve(rel_path, workspace)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return len(content.encode("utf-8"))

    def save_upload(self, rel_path: str, data: bytes, workspace: str | None = None) -> int:
        target = self.resolve(rel_path, workspace)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return len(data)

    def mkdir(self, rel_path: str, name: str, workspace: str | None = None) -> str:
        parent = self.resolve(rel_path, workspace)
        target = (parent / name).resolve()
        if not target.is_relative_to(self._root_for(workspace)):
            raise PermissionError("path escapes workspace")
        target.mkdir(parents=True, exist_ok=True)
        return str(target)

    def rename(self, rel_path: str, new_name: str, workspace: str | None = None) -> str:
        target = self.resolve(rel_path, workspace)
        if "/" in new_name or "\\" in new_name:
            raise ValueError("new_name must not contain path separators")
        dest = target.with_name(new_name)
        target.rename(dest)
        return str(dest)

    def delete(self, rel_path: str, workspace: str | None = None) -> bool:
        target = self.resolve(rel_path, workspace)
        if target == self._root_for(workspace):
            raise PermissionError("refusing to delete workspace root")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
        else:
            raise FileNotFoundError(rel_path)
        return True
