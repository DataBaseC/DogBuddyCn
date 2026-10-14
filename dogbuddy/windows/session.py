# DogBuddyCn — persistent window session (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""WindowSession: the persistent conversation of one employee window.

Pure domain object — repos are injected (ports); no SQL/HTTP here.
"""
from __future__ import annotations

from typing import Any

from ..ports import Message, Todo, Window
from .context import ContextBuilder


class WindowSession:
    def __init__(self, window: Window, message_repo: Any, todo_repo: Any, compaction_repo: Any):
        self.window = window
        self.messages = message_repo
        self.todos = todo_repo
        self.compactions = compaction_repo

    # ------------------------------------------------------------- messages
    def append(self, role: str, content: str, tokens: int = 0,
               meta: dict[str, Any] | None = None) -> Message:
        # T3.1: a window bound to a shared channel contributes its messages there
        group_id = getattr(self.window, "group_id", "") or ""
        return self.messages.add_message(self.window.id, role, content, tokens, meta,
                                         group_id=group_id)

    def recent(self, limit: int = 200) -> list[Message]:
        return self.messages.list_messages(self.window.id, limit)

    def token_usage(self) -> int:
        return self.messages.sum_tokens(self.window.id)

    def drop_messages(self, before_id: int) -> int:
        return self.messages.delete_messages(self.window.id, before_id)

    # ----------------------------------------------------------------- todos
    def add_todo(self, content: str) -> Todo:
        return self.todos.add_todo(self.window.id, content)

    def todo_list(self) -> list[Todo]:
        return self.todos.list_todos(self.window.id)

    def todo_done(self, todo_id: int, done: bool = True) -> bool:
        return self.todos.set_todo_done(todo_id, done)

    # ----------------------------------------------------------- compactions
    def compaction_history(self) -> list:
        return self.compactions.list_compactions(self.window.id)

    # ------------------------------------------------------------- context
    def build_context(self, builder: ContextBuilder, recent_limit: int = 60) -> list[dict[str, Any]]:
        return builder.build(self, recent_limit=recent_limit)
