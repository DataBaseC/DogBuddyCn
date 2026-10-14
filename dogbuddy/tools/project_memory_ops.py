# DogBuddyCn — 项目记忆工具 (Batch 9 · T9.5)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""项目记忆：`memory/projects/<id>.md`。

* **主管**可读写（它是项目的负责人，负责把"这个项目踩过的坑/定下的约定"记下来）
* **员工只读**（能读到项目约定，但不能篡改——否则一个员工就能改掉全项目的规矩）

为什么单开项目记忆而不复用全局 `memory_write`：全局记忆是 system 级，
主管没有；而"项目里的约定"必须由主管维护，且必须随项目走（换主管也不丢）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .registry import ToolContext, ToolResult

logger = logging.getLogger("dogbuddy.tools.project_memory")

MAX_READ_CHARS = 20000
MAX_WRITE_CHARS = 20000


def _project_id(ctx: ToolContext, args: dict[str, Any]) -> str:
    pid = str(args.get("project_id") or "").strip()
    if pid:
        return pid
    own = str((getattr(ctx, "extra", None) or {}).get("project_id") or "").strip()
    if own:
        return own
    db = getattr(ctx, "db", None)
    if db is not None and getattr(ctx, "window_id", ""):
        w = db.get_window(ctx.window_id)
        if w is not None:
            return getattr(w, "project_id", "") or ""
    return ""


def _memory_dir(ctx: ToolContext) -> Path | None:
    """项目记忆目录 = `<memory root>/projects`（注意已包含 projects 这一层）。"""
    svc = getattr(ctx, "memory_service", None)
    root = getattr(svc, "root", None) if svc is not None else None
    if root:
        return Path(root) / "projects"
    db = getattr(ctx, "db", None)
    path = getattr(db, "path", None) if db is not None else None
    if path:
        return Path(path).parent / "memory" / "projects"
    return None


def project_memory_path(ctx: ToolContext, project_id: str) -> Path | None:
    base = _memory_dir(ctx)
    if base is None or not project_id:
        return None
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in project_id)
    return base / f"{safe}.md"


class ProjectMemoryReadTool:
    name = "project_memory_read"
    description = (
        "Read this project's shared memory (conventions, lessons, decisions the supervisor "
        "wrote down). Everyone in the project may read it. Use it before starting work so you "
        "follow the project's established rules.")
    parameters = {
        "type": "object",
        "properties": {
            "project_id": {"type": "string",
                           "description": "Project id; defaults to your own project."},
        },
    }
    permission = "read"
    # 读级作用域：**项目成员**即可读（员工只读），不要求管理权。
    # 初版写的是 "project"，而员工的 Scope 是 none/rank=0 → `check_scope` 在
    # `rank == 0` 处直接拒绝，连目标项目都不解析 → "员工只读"这个中间态根本
    # 不存在，这个工具只是白名单里的一个死工具（审查报告 P1-4）。
    # 注意不能简单降成 ""：`project_id` 是模型自报的，降成空等于任何工位都能读
    # **任何项目**的记忆。所以单开一个 project_read 级：要求目标 ∈ 你的可读项目集。
    required_scope = "project_read"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pid = _project_id(ctx, args)
        if not pid:
            return ToolResult.failure("无法确定项目：请传入 project_id，或把工位挂到项目上。")
        path = project_memory_path(ctx, pid)
        if path is None:
            return ToolResult.failure("项目记忆目录不可用（memory service 未注入）")
        if not path.exists():
            return ToolResult.success(f"项目 {pid} 还没有项目记忆（主管可用 "
                                      f"project_memory_write 写第一条约定）。",
                                      project_id=pid, content="")
        try:
            content = path.read_text(encoding="utf-8")[:MAX_READ_CHARS]
        except OSError as e:
            return ToolResult.failure(f"读取项目记忆失败：{e}")
        return ToolResult.success(content or "（空）", project_id=pid, content=content)


class ProjectMemoryWriteTool:
    name = "project_memory_write"
    description = (
        "Write/replace this project's shared memory (supervisor only). Record conventions, "
        "architectural decisions, recurring pitfalls — the things a new worker on this project "
        "must know. Keep it short and factual; overwrite only the parts that changed.")
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Markdown body of the project memory."},
            "mode": {"type": "string", "enum": ["replace", "append"],
                     "description": "replace (default) or append a new section."},
            "project_id": {"type": "string", "description": "Project id (defaults to yours)."},
        },
        "required": ["content"],
    }
    permission = "write"
    required_scope = "project"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        content = str(args.get("content") or "").strip()
        if not content:
            return ToolResult.failure("content 不能为空")
        if len(content) > MAX_WRITE_CHARS:
            return ToolResult.failure(f"content 太长（>{MAX_WRITE_CHARS} 字符），请精简。")
        pid = _project_id(ctx, args)
        if not pid:
            return ToolResult.failure("无法确定项目：请传入 project_id，或把工位挂到项目上。")
        path = project_memory_path(ctx, pid)
        if path is None:
            return ToolResult.failure("项目记忆目录不可用（memory service 未注入）")
        mode = str(args.get("mode") or "replace").strip().lower()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append" and path.exists():
                old = path.read_text(encoding="utf-8")
                path.write_text(old.rstrip() + "\n\n" + content + "\n", encoding="utf-8")
            else:
                path.write_text(content + "\n", encoding="utf-8")
        except OSError as e:
            return ToolResult.failure(f"写入项目记忆失败：{e}")
        db = getattr(ctx, "db", None)
        if db is not None:
            try:
                db.audit(ctx.actor, "project_memory:write",
                         f"project={pid} mode={mode} chars={len(content)}")
            except Exception:  # noqa: BLE001
                pass
        logger.info("project memory updated for %s (%s, %d chars)", pid, mode, len(content))
        return ToolResult.success(f"已更新项目 {pid} 的记忆（{mode}，{len(content)} 字符）",
                                  project_id=pid)


def all_tools() -> list:
    return [ProjectMemoryReadTool(), ProjectMemoryWriteTool()]
