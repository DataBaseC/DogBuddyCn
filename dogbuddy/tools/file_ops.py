# DogBuddyCn — workspace file tools: read / write / edit (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""file tools: read_file / write_file / edit_file.

Path defense (tool-side, independent of the permission gate): every path is
resolved against ctx.workspace and rejected when it escapes the root
(symlinks included, via Path.resolve()).

Write safety: write_file / edit_file call checkpoint.save_file_snapshot() to
preserve the old content of any file they are about to overwrite.

Cross-window safety (v0.10.39): write_file / edit_file hold a per-path write
lock (file_guard.write_guard) so two windows sharing a project workspace can
never interleave writes, and accept `expect_sha` (from read_file's result) to
reject stale writes — "A reads, B writes, A overwrites" lost updates.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import checkpoint as _checkpoint
from .file_guard import content_sha, current_sha, write_guard
from .registry import ToolContext, ToolResult

logger = logging.getLogger("dogbuddy.tools.file")

DEFAULT_LIMIT = 200


# ------------------------------------------------------------------ helpers


def _resolve_in_workspace(ctx: ToolContext, path: Any) -> Path:
    """Resolve path under ctx.workspace; raise ValueError if it escapes."""
    ws = Path(str(ctx.workspace)).resolve()
    raw = str(path if path is not None else "").strip()
    if not raw:
        raise ValueError("path must be a non-empty string")
    target = (ws / raw).resolve()
    if target != ws and not target.is_relative_to(ws):
        raise ValueError(f"path escapes workspace, rejected: {raw}")
    return target


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off", ""):
        return False
    return default


def _stale_reason(p: Path, expect: str) -> str:
    """expect_sha 与文件当前内容不符 → 返回拒绝原因（空串 = 可以写）。

    防的是共享工作区里的经典丢失更新：工位 A 读文件 → 工位 B 改了它 →
    A 拿着旧印象整段覆盖 → B 的成果无声消失。file_guard 的锁只能防"同时写"，
    防不了"先后写"——这个期望值检查是唯一能拦住后者的手段。
    """
    if not expect:
        return ""
    actual = current_sha(p)
    if actual == expect:
        return ""
    if not actual:
        return (f"expect_sha 校验失败：{p.name} 现在不存在（可能被其他工位移走/删除）——"
                f"请先重新 read_file 确认现状再写")
    return (f"expect_sha 校验失败：{p.name} 自你读取后已被其他工位修改"
            f"（期望 {expect}，当前 {actual}）——请重新 read_file 再决定怎么写，"
            f"不要直接覆盖别人的成果")


def _decode_text(data: bytes) -> str | None:
    """Return decoded UTF-8 text, or None when the content looks binary."""
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ------------------------------------------------------------------- tools


class ReadFileTool:
    """read_file — read a text file from the workspace."""

    name = "read_file"
    description = (
        "Read a UTF-8 text file from the workspace. Long files are returned in "
        "segments: use offset (0-based line index) and limit (default 200). "
        "Binary files are rejected. The result carries `sha` — pass it back as "
        "write_file/edit_file `expect_sha` to fail safely if another window "
        "modified the file since you read it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the workspace root.",
            },
            "offset": {
                "type": "integer",
                "description": "0-based line index to start from (default 0).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to return (default 200).",
            },
        },
        "required": ["path"],
    }
    permission = "read"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            try:
                p = _resolve_in_workspace(ctx, args.get("path"))
            except ValueError as e:
                return ToolResult.failure(str(e))
            rel = p.name
            try:
                rel = p.relative_to(Path(str(ctx.workspace)).resolve()).as_posix()
            except ValueError:
                pass
            if not p.exists():
                return ToolResult.failure(f"file not found: {rel}")
            if p.is_dir():
                return ToolResult.failure(f"is a directory, not a file: {rel}")
            data = p.read_bytes()
            text = _decode_text(data)
            if text is None:
                return ToolResult.failure(
                    f"binary or non-UTF-8 file not supported: {rel} "
                    f"({len(data)} bytes)"
                )
            offset = max(0, _to_int(args.get("offset"), 0))
            limit = max(1, _to_int(args.get("limit"), DEFAULT_LIMIT))
            lines = text.splitlines()
            total = len(lines)
            chunk = lines[offset:offset + limit]
            out = "\n".join(chunk)
            if offset > 0 or offset + len(chunk) < total:
                out += (
                    f"\n\n[... showing lines {offset + 1}-{offset + len(chunk)} of {total}"
                    f"; use offset/limit to read more ...]"
                )
            return ToolResult.success(
                out, total_lines=total, offset=offset, returned=len(chunk),
                # v0.10.39：内容指纹——write_file/edit_file 的 expect_sha 用它
                # 防"你读之后别人改过"的丢失更新（共享工作区多工位并发）。
                sha=content_sha(data), size=len(data),
            )
        except Exception as e:  # noqa: BLE001 — tool errors become tool results
            logger.warning("read_file failed", exc_info=True)
            return ToolResult.failure(f"read_file failed: {e}")


class WriteFileTool:
    """write_file — overwrite a file in the workspace."""

    name = "write_file"
    description = (
        "Create or overwrite a UTF-8 text file in the workspace (parent "
        "directories are created automatically). The previous content of an "
        "existing file is snapshotted to the checkpoint store first. "
        "Returns the number of bytes written. Pass expect_sha (the `sha` from "
        "a previous read_file) to fail instead of overwriting when another "
        "window changed the file since — protects shared workspaces."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the workspace root.",
            },
            "content": {
                "type": "string",
                "description": "Full new content of the file.",
            },
            "expect_sha": {
                "type": "string",
                "description": (
                    "Optional content fingerprint from read_file's result. If the "
                    "file's current sha differs, the write is refused (someone else "
                    "modified it) — re-read and retry. Omit when creating a new file."
                ),
            },
        },
        "required": ["path", "content"],
    }
    permission = "write"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            try:
                p = _resolve_in_workspace(ctx, args.get("path"))
            except ValueError as e:
                return ToolResult.failure(str(e))
            if p.is_dir():
                return ToolResult.failure(f"path is a directory: {args.get('path')}")
            content = args.get("content")
            if content is None:
                return ToolResult.failure("content is required")
            payload = str(content).encode("utf-8")
            expect = str(args.get("expect_sha") or "").strip()

            # v0.10.39：跨工位写入保护——同一路径的写串行化（防交错），
            # expect_sha 不符即拒（防"读-别人写-你覆盖"的丢失更新）。
            async with write_guard(p):
                stale = _stale_reason(p, expect)
                if stale:
                    return ToolResult.failure(stale)
                _checkpoint.save_file_snapshot(ctx, p)  # preserve old content (no-op if new)

                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(payload)
            try:
                rel = p.relative_to(Path(str(ctx.workspace)).resolve()).as_posix()
            except ValueError:
                rel = p.name
            return ToolResult.success(
                f"Wrote {len(payload)} bytes to {rel}", bytes=len(payload),
                sha=content_sha(payload),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("write_file failed", exc_info=True)
            return ToolResult.failure(f"write_file failed: {e}")


class EditFileTool:
    """edit_file — exact text replacement in a file."""

    name = "edit_file"
    description = (
        "Replace exact text in a workspace file. old_text must match exactly. "
        "If it occurs multiple times, either pass a longer unique context or set "
        "replace_all=true. The previous content is snapshotted to the checkpoint "
        "store first. Returns the number of replacements. Pass expect_sha (the "
        "`sha` from a previous read_file) to fail instead of editing when another "
        "window modified the file since."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the workspace root.",
            },
            "old_text": {
                "type": "string",
                "description": "Exact text to replace (must appear at least once).",
            },
            "new_text": {
                "type": "string",
                "description": "Replacement text (may be empty to delete old_text).",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence (default false).",
            },
            "expect_sha": {
                "type": "string",
                "description": (
                    "Optional content fingerprint from read_file's result. If the "
                    "file's current sha differs, the edit is refused (someone else "
                    "modified it) — re-read and retry."
                ),
            },
        },
        "required": ["path", "old_text", "new_text"],
    }
    permission = "write"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            try:
                p = _resolve_in_workspace(ctx, args.get("path"))
            except ValueError as e:
                return ToolResult.failure(str(e))
            try:
                rel = p.relative_to(Path(str(ctx.workspace)).resolve()).as_posix()
            except ValueError:
                rel = p.name
            old_text = args.get("old_text")
            if old_text is None or old_text == "":
                return ToolResult.failure("old_text must be a non-empty string")
            old_text = str(old_text)
            new_text = "" if args.get("new_text") is None else str(args.get("new_text"))
            replace_all = _to_bool(args.get("replace_all"), False)
            expect = str(args.get("expect_sha") or "").strip()

            # v0.10.39：读-改-写全程持锁（同路径跨工位串行），expect_sha 先校验
            async with write_guard(p):
                stale = _stale_reason(p, expect)
                if stale:
                    return ToolResult.failure(stale)
                if not p.is_file():
                    return ToolResult.failure(f"file not found: {rel}")
                data = p.read_bytes()
                text = _decode_text(data)
                if text is None:
                    return ToolResult.failure(f"binary or non-UTF-8 file not supported: {rel}")

                count = text.count(old_text)
                if count == 0:
                    return ToolResult.failure(f"old_text not found in {rel}")
                if count > 1 and not replace_all:
                    return ToolResult.failure(
                        f"old_text found {count} times in {rel}; provide a longer, "
                        f"unique context or set replace_all=true"
                    )

                _checkpoint.save_file_snapshot(ctx, p)

                if replace_all:
                    updated = text.replace(old_text, new_text)
                    n = count
                else:
                    updated = text.replace(old_text, new_text, 1)
                    n = 1
                out_bytes = updated.encode("utf-8")
                p.write_bytes(out_bytes)
            return ToolResult.success(
                f"Replaced {n} occurrence(s) in {rel}", count=n,
                sha=content_sha(out_bytes),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("edit_file failed", exc_info=True)
            return ToolResult.failure(f"edit_file failed: {e}")


def all_tools() -> list:
    return [ReadFileTool(), WriteFileTool(), EditFileTool()]
