# DogBuddyCn — todo tools (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Todo tools over the injected ctx.todo_repo (ports.TodoRepo shape:
add_todo(window_id, content, sort=0) / list_todos(window_id) /
set_todo_done(todo_id, done) / delete_todo(todo_id)).

When ctx.todo_repo is None every tool fails softly with a clear message.
"""
from __future__ import annotations

import logging
from typing import Any

from .registry import ToolContext, ToolResult

logger = logging.getLogger("dogbuddy.tools.todo")


def _to_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read a Todo field from a dataclass-like object or a dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _repo(ctx: ToolContext) -> Any:
    repo = getattr(ctx, "todo_repo", None)
    if repo is None:
        return None
    return repo


class TodoViewTool:
    """todo_view — list todos for the current window."""

    name = "todo_view"
    description = (
        "List the current window's todo items with their id and done status, "
        "formatted as '[x] #3 content' / '[ ] #4 content'."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    permission = "read"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            repo = _repo(ctx)
            if repo is None:
                return ToolResult.failure("todo unavailable: no todo_repo injected")
            todos = list(repo.list_todos(str(ctx.window_id)))
            if not todos:
                return ToolResult.success("(todo list is empty)", total=0, done=0, pending=0)
            lines = []
            done = 0
            for t in todos:
                is_done = bool(_field(t, "done", False))
                done += 1 if is_done else 0
                mark = "x" if is_done else " "
                lines.append(f"[{mark}] #{_field(t, 'id', '?')} {_field(t, 'content', '')}")
            pending = len(todos) - done
            out = "\n".join(lines) + f"\n\n[{pending} pending / {done} done]"
            return ToolResult.success(out, total=len(todos), done=done, pending=pending)
        except Exception as e:  # noqa: BLE001
            logger.warning("todo_view failed", exc_info=True)
            return ToolResult.failure(f"todo_view failed: {e}")


class TodoAddTool:
    """todo_add — append a todo item."""

    name = "todo_add"
    description = "Add a new todo item to the current window's todo list."
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Todo item text.",
            }
        },
        "required": ["content"],
    }
    permission = "write"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            repo = _repo(ctx)
            if repo is None:
                return ToolResult.failure("todo unavailable: no todo_repo injected")
            content = args.get("content")
            if content is None or not str(content).strip():
                return ToolResult.failure("content must be a non-empty string")
            todo = repo.add_todo(str(ctx.window_id), str(content), 0)
            return ToolResult.success(
                f"added todo #{_field(todo, 'id', '?')}: {str(content)}",
                todo_id=_field(todo, "id"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("todo_add failed", exc_info=True)
            return ToolResult.failure(f"todo_add failed: {e}")


class TodoDoneTool:
    """todo_done — mark a todo item as done."""

    name = "todo_done"
    description = "Mark the todo item with the given id as done."
    parameters = {
        "type": "object",
        "properties": {
            "id": {
                "type": "integer",
                "description": "Todo id as shown by todo_view.",
            }
        },
        "required": ["id"],
    }
    permission = "write"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            repo = _repo(ctx)
            if repo is None:
                return ToolResult.failure("todo unavailable: no todo_repo injected")
            todo_id = _to_int(args.get("id"))
            if todo_id is None:
                return ToolResult.failure("id must be an integer")
            ok = bool(repo.set_todo_done(todo_id, True))
            if not ok:
                return ToolResult.failure(f"todo not found: #{todo_id}")
            return ToolResult.success(f"todo #{todo_id} marked done", todo_id=todo_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("todo_done failed", exc_info=True)
            return ToolResult.failure(f"todo_done failed: {e}")


def all_tools() -> list:
    return [TodoViewTool(), TodoAddTool(), TodoDoneTool()]
