# DogBuddyCn — workspace file service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""FileService: explorer-style file management over the configured workspaces.

Path safety is a business rule and lives here (L2), not in the L1 routes.
All paths are workspace-relative; escapes return PermissionError.
"""
from __future__ import annotations

import base64
import logging
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("dogbuddy.services.files")

MAX_PREVIEW_BYTES = 200 * 1024

#: 打包下载排除的目录（与 list/tree 的可见性口径一致——这些不进用户的成果包）
ZIP_EXCLUDE_DIRS = {".git", ".venv", "__pycache__", "node_modules"}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg"}
LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".html": "html",
    ".css": "css", ".json": "json", ".md": "markdown", ".sh": "shell",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".sql": "sql",
    ".go": "go", ".rs": "rust", ".java": "java", ".c": "c", ".cpp": "cpp",
}


class FileService:
    def __init__(self, roots: list[str | Path] | Any):
        """roots: allowed absolute base directories (workspaces).

        也可以传**可调用对象**（返回根列表）：工位/项目是运行中新建的，允许根必须
        能跟着变。审查报告 P2-3：初版在启动时把根冻结成列表，于是运行中新建的
        工位目录一律 `PermissionError → workspace not allowed`（403），必须重启服务。
        """
        self._roots_provider = roots if callable(roots) else None
        self._provider_roots: list[Path] = []
        self.roots: list[Path] = [Path(r).expanduser().resolve()
                                  for r in ([] if callable(roots) else roots)]
        self.refresh_roots()

    def refresh_roots(self) -> None:
        """重算"来自提供者"的根；调用方手动加进 `self.roots` 的条目保留。"""
        if self._roots_provider is None:
            return
        try:
            fresh = [Path(r).expanduser().resolve() for r in (self._roots_provider() or [])]
        except Exception:  # noqa: BLE001 — 根解析失败不该让文件管理整体不可用
            logger.warning("refreshing allowed roots failed", exc_info=True)
            return
        extras = [r for r in self.roots if r not in self._provider_roots]
        self._provider_roots = fresh
        self.roots[:] = fresh + [r for r in extras if r not in fresh]

    def _allowed(self, p: Path) -> bool:
        return p in self.roots or any(p.is_relative_to(r) for r in self.roots)

    def _root_for(self, workspace: str | None) -> Path:
        if workspace:
            p = Path(workspace).expanduser().resolve()
            if self._allowed(p):
                return p
            # 可能是**运行中**才建出来的工位/项目目录 → 重算一次再判（P2-3）
            self.refresh_roots()
            if self._allowed(p):
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

    # ------------------------------------------------------------------ zip
    def zip_dir(self, rel_path: str = "", workspace: str | None = None) -> tuple[Path, str]:
        """把目录打包成 tar.gz（v0.10.39：项目/工位成果一键下载）。

        owner 原话："项目文件夹目前不能打包一起下载" + "挨个翻文件夹找成果太麻烦"。
        返回 (临时文件路径, 下载文件名)——**调用方负责发完后删除临时文件**
        （路由用 BackgroundTasks；本方法中途失败也会留下半成品，由调用方的
        except 分支清理）。排除 `.git`/`.venv`/`__pycache__`/`node_modules`：
        与 list/tree 的可见性口径一致，成果包里不该有这些。
        """
        target = self.resolve(rel_path, workspace)
        if not target.is_dir():
            raise FileNotFoundError(f"not a directory: {rel_path or '(root)'}")
        fd, tmp_name = tempfile.mkstemp(prefix="dogbuddy-zip-", suffix=".tar.gz")
        os.close(fd)
        out = Path(tmp_name)

        def _keep(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
            if any(part in ZIP_EXCLUDE_DIRS for part in Path(ti.name).parts):
                return None
            return ti

        try:
            with tarfile.open(out, "w:gz") as tar:
                tar.add(target, arcname=target.name or "workspace", filter=_keep)
        except Exception:
            # 半成品不留盘（调用方的 except 也会兜底一次）
            try:
                out.unlink()
            except OSError:
                pass
            raise
        return out, f"{target.name or 'workspace'}.tar.gz"
