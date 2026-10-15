# DogBuddyCn — persistent window session (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""WindowSession: the persistent conversation of one employee window.

Pure domain object — repos are injected (ports); no SQL/HTTP here.
"""
from __future__ import annotations

from typing import Any

from ..ports import Message, Todo, Window
from .context import ContextBuilder

#: 兜底估算：中英混排大约 3 字符 ≈ 1 token。只在**本进程还没拿到真实 usage** 时用
#: （例如刚重启、还没跑过任何一轮），真实值一到就立刻被替换。
_CHARS_PER_TOKEN = 3.0


class WindowSession:
    def __init__(self, window: Window, message_repo: Any, todo_repo: Any, compaction_repo: Any):
        self.window = window
        self.messages = message_repo
        self.todos = todo_repo
        self.compactions = compaction_repo
        #: 最近一次 LLM 调用回报的 **prompt_tokens**（= 当前上下文占用的真实值）。
        #: 注意它与 `token_usage()` 是**两种语义**：后者是本会话的累计花费（账本），
        #: 前者是"现在上下文有多满"（压缩判据）。曾经两者共用 SUM(tokens)，
        #: 结果是压缩永不触发、用量条恒 0。
        self.last_prompt_tokens: int = 0
        # T7.2: 挂载项目后，真正生效的工作区 = 项目根 (+ 工位子目录)。
        # 由 WindowManager 每次执行前写入；为空表示沿用 window.workspace_path。
        # 存在的理由：提示词里必须告诉模型**真实的**工作目录，否则它会按老路径找文件。
        self.workspace_override: str = ""

    @property
    def effective_workspace(self) -> str:
        return self.workspace_override or getattr(self.window, "workspace_path", "") or ""

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
        """**累计花费**（账本口径）：本会话所有消息上记的 token 之和。"""
        return self.messages.sum_tokens(self.window.id)

    # ------------------------------------------------------- 上下文占用（v11）
    def note_usage(self, usage: dict[str, Any] | None) -> None:
        """记住最近一次 LLM 调用的 usage —— 只取 `prompt_tokens` 作为上下文占用。

        `total_tokens`（含输出）是花费，不能拿来判"上下文满没满"。
        """
        if not isinstance(usage, dict):
            return
        try:
            prompt = int(usage.get("prompt_tokens") or 0)
        except (TypeError, ValueError):
            prompt = 0
        if prompt > 0:
            self.last_prompt_tokens = prompt

    @property
    def context_tokens_estimated(self) -> bool:
        """True 表示 `context_tokens()` 是粗估（本进程还没拿到真实 usage）。"""
        return self.last_prompt_tokens <= 0

    def context_tokens(self, recent_limit: int = 80) -> int:
        """**当前上下文占用**（压缩判据 / 用量条），不是累计花费。

        优先级：本进程最近一次调用的 `prompt_tokens` → 按在上下文里的消息粗估。
        绝不回落到 `token_usage()`：那是累计花费，长寿命工位会让它线性膨胀，
        压缩器据此只会"越来越容易触发"，正是要修掉的口径错误。
        """
        if self.last_prompt_tokens > 0:
            return int(self.last_prompt_tokens)
        try:
            msgs = self.recent(recent_limit)
        except Exception:  # noqa: BLE001 — 估算失败退化为 0，不影响主流程
            return 0
        chars = 0
        for m in msgs:
            chars += len(m.content or "")
        if chars <= 0:
            return 0
        return max(1, int(chars / _CHARS_PER_TOKEN))

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
