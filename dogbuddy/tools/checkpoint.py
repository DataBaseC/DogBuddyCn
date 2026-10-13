# DogBuddyCn — checkpoint / snapshot tools (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Checkpoint utilities: single-file pre-edit snapshots + full workspace snapshots.

Layout under ``ctx.checkpoint_root/<window_id>/``:
  files/<sha256(relpath)[:32]>.bak   — old content of one edited file (+ .json sidecar)
  snapshots/<UTC timestamp>/files/... — full workspace snapshot (mirrors workspace)
  snapshots/<UTC timestamp>/manifest.json — label + file list

Rules:
  - checkpoint failures must NEVER block the caller (save_file_snapshot swallows
    exceptions with a warning; the tools return ToolResult.failure instead of raising)
  - snapshots skip .git / node_modules / __pycache__ / .venv, single files > 5MB
    and stop at 200MB total per snapshot
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .registry import ToolContext, ToolResult

logger = logging.getLogger("dogbuddy.tools.checkpoint")

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
MAX_FILE_BYTES = 5 * 1024 * 1024          # skip single files larger than 5MB
MAX_TOTAL_BYTES = 200 * 1024 * 1024       # stop a snapshot at 200MB total
MAX_SKIPPED_LOG = 200                     # keep the manifest tidy


def _utc_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _checkpoint_root(ctx: ToolContext) -> Path | None:
    """Per-window checkpoint root, or None when checkpointing is not configured."""
    root = str(getattr(ctx, "checkpoint_root", "") or "").strip()
    if not root:
        return None
    return Path(root) / str(getattr(ctx, "window_id", "") or "default")


def _rel_key(path: Path, workspace: Path) -> str:
    """Relative posix path used as snapshot key; falls back to the file name."""
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.resolve().name or path.resolve().as_posix()


# --------------------------------------------------------------- file snapshot


def save_file_snapshot(ctx: ToolContext, path: Any) -> str:
    """Save the CURRENT (soon to be overwritten) content of one file.

    Returns the .bak path, or "" when skipped. Never raises: a failed checkpoint
    must not block the file write that follows it.
    """
    try:
        src = Path(str(path)).resolve()
        if not src.is_file():
            return ""  # nothing to preserve (new file)
        root = _checkpoint_root(ctx)
        if root is None:
            logger.warning("checkpoint: no checkpoint_root configured, skip snapshot of %s", src)
            return ""
        key = _rel_key(src, Path(str(ctx.workspace)))
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        files_dir = root / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        bak = files_dir / f"{digest}.bak"
        shutil.copy2(src, bak)
        sidecar = files_dir / f"{digest}.json"
        sidecar.write_text(
            json.dumps(
                {
                    "original_relpath": key,
                    "saved_at": _now_iso(),
                    "window_id": str(getattr(ctx, "window_id", "")),
                    "source_path": str(src),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(bak)
    except Exception:  # noqa: BLE001 — checkpoint must never break the caller
        logger.warning("checkpoint: save_file_snapshot failed for %s", path, exc_info=True)
        return ""


# ------------------------------------------------------------- full snapshots


def _safe_relname(rel: str) -> bool:
    """Reject manifest entries that could escape the workspace."""
    if not rel or rel.startswith("/") or rel.startswith("\\"):
        return False
    parts = Path(rel).parts
    return ".." not in parts and ":" not in rel


async def _snapshot_workspace(ctx: ToolContext, label: str) -> tuple[str, dict[str, Any]]:
    """Copy every eligible workspace file into a fresh snapshot dir.

    Returns (snapshot_id, manifest). Raises on infrastructure errors — callers
    turn exceptions into ToolResult.failure.
    """
    root = _checkpoint_root(ctx)
    if root is None:
        raise RuntimeError("checkpoint_root not configured")
    ws = Path(str(ctx.workspace)).resolve()
    if not ws.is_dir():
        raise RuntimeError(f"workspace not found: {ws}")

    snap_id = _utc_id()
    snap_dir = root / "snapshots" / snap_id
    files_dir = snap_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    # never snapshot the checkpoint store itself (it may live inside the workspace)
    try:
        excluded: Path | None = root.resolve()
        if not excluded.is_relative_to(ws):
            excluded = None
    except Exception:  # noqa: BLE001
        excluded = None

    manifest: dict[str, Any] = {
        "id": snap_id,
        "label": label,
        "created_at": _now_iso(),
        "window_id": str(getattr(ctx, "window_id", "")),
        "workspace": str(ws),
        "files": [],
        "skipped": [],
        "total_bytes": 0,
    }
    total = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(ws):
        here = Path(dirpath)
        keep: list[str] = []
        for d in sorted(dirnames):
            if d in SKIP_DIRS or d.startswith(".git"):
                continue
            child = (here / d)
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if excluded is not None and (resolved == excluded or excluded.is_relative_to(resolved)):
                continue
            keep.append(d)
        dirnames[:] = keep

        for fn in sorted(filenames):
            src = here / fn
            try:
                rel = src.resolve().relative_to(ws).as_posix()
            except (ValueError, OSError):
                continue
            if not _safe_relname(rel):
                continue
            try:
                size = src.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                if len(manifest["skipped"]) < MAX_SKIPPED_LOG:
                    manifest["skipped"].append(rel)
                continue
            if total + size > MAX_TOTAL_BYTES:
                truncated = True
                if len(manifest["skipped"]) < MAX_SKIPPED_LOG:
                    manifest["skipped"].append(rel)
                continue
            dest = files_dir / rel
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            except OSError:
                if len(manifest["skipped"]) < MAX_SKIPPED_LOG:
                    manifest["skipped"].append(rel)
                continue
            manifest["files"].append(rel)
            total += size
            if truncated:
                continue

    manifest["total_bytes"] = total
    manifest["truncated"] = truncated
    (snap_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return snap_id, manifest


class CheckpointCreateTool:
    """checkpoint_create — full workspace snapshot."""

    name = "checkpoint_create"
    description = (
        "Create a full snapshot of the workspace under the checkpoint root. "
        "Returns a snapshot id (UTC timestamp) usable with checkpoint_rollback."
    )
    parameters = {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Optional human-readable label stored in the snapshot manifest.",
            }
        },
        "required": [],
    }
    permission = "write"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            label = str(args.get("label", "") or "")
            snap_id, manifest = await _snapshot_workspace(ctx, label)
            n = len(manifest["files"])
            return ToolResult.success(
                f"snapshot {snap_id}: {n} files, {manifest['total_bytes']} bytes"
                + (f", {len(manifest['skipped'])} skipped" if manifest["skipped"] else ""),
                snapshot_id=snap_id,
                files=n,
                total_bytes=manifest["total_bytes"],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("checkpoint_create failed", exc_info=True)
            return ToolResult.failure(f"checkpoint_create failed: {e}")


class CheckpointRollbackTool:
    """checkpoint_rollback — restore workspace files from a snapshot."""

    name = "checkpoint_rollback"
    description = (
        "Restore workspace files from a previous checkpoint snapshot (overwrites "
        "files recorded in the snapshot). Empty snapshot_id means the most recent "
        "snapshot. A safety snapshot of the current state is taken first."
    )
    parameters = {
        "type": "object",
        "properties": {
            "snapshot_id": {
                "type": "string",
                "description": "Snapshot id as returned by checkpoint_create. Empty = latest snapshot.",
            }
        },
        "required": [],
    }
    permission = "write"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            root = _checkpoint_root(ctx)
            if root is None:
                return ToolResult.failure("checkpoint_rollback failed: checkpoint_root not configured")
            snaps_dir = root / "snapshots"
            sid = str(args.get("snapshot_id", "") or "").strip()

            if sid:
                if not _safe_relname(sid) or "/" in sid or "\\" in sid:
                    return ToolResult.failure(f"invalid snapshot_id: {sid}")
                snap_dir = snaps_dir / sid
            else:
                candidates = sorted(
                    (d for d in snaps_dir.iterdir() if d.is_dir() and (d / "manifest.json").is_file()),
                    key=lambda d: d.name,
                ) if snaps_dir.is_dir() else []
                if not candidates:
                    return ToolResult.failure("no snapshot found")
                snap_dir = candidates[-1]

            manifest_path = snap_dir / "manifest.json"
            if not manifest_path.is_file():
                return ToolResult.failure(f"snapshot not found: {sid or '(latest)'}")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            snap_id = str(manifest.get("id", snap_dir.name))

            # safety net: snapshot current state before touching anything
            auto_id = ""
            try:
                auto_id, _ = await _snapshot_workspace(ctx, f"auto before rollback to {snap_id}")
            except Exception:  # noqa: BLE001
                logger.warning("checkpoint_rollback: auto safety snapshot failed", exc_info=True)

            ws = Path(str(ctx.workspace)).resolve()
            restored = 0
            missing: list[str] = []
            entries = manifest.get("files", [])
            if not isinstance(entries, list):
                return ToolResult.failure(f"corrupt manifest in snapshot {snap_id}")
            for rel in entries:
                rel_s = str(rel)
                if not _safe_relname(rel_s):
                    if len(missing) < MAX_SKIPPED_LOG:
                        missing.append(rel_s)
                    continue
                src = snap_dir / "files" / rel_s
                dest = ws / rel_s
                try:
                    if not dest.resolve().is_relative_to(ws):
                        if len(missing) < MAX_SKIPPED_LOG:
                            missing.append(rel_s)
                        continue
                except OSError:
                    if len(missing) < MAX_SKIPPED_LOG:
                        missing.append(rel_s)
                    continue
                if not src.is_file():
                    if len(missing) < MAX_SKIPPED_LOG:
                        missing.append(rel_s)
                    continue
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    restored += 1
                except OSError:
                    if len(missing) < MAX_SKIPPED_LOG:
                        missing.append(rel_s)

            return ToolResult.success(
                f"restored {restored} file(s) from snapshot {snap_id}"
                + (f"; {len(missing)} skipped/missing" if missing else "")
                + (f"; safety snapshot: {auto_id}" if auto_id else ""),
                snapshot_id=snap_id,
                restored=restored,
                auto_snapshot=auto_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("checkpoint_rollback failed", exc_info=True)
            return ToolResult.failure(f"checkpoint_rollback failed: {e}")


def all_tools() -> list:
    return [CheckpointCreateTool(), CheckpointRollbackTool()]
