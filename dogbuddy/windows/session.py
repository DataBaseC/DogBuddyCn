# DogBuddyCn — persistent window session (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""WindowSession: the persistent conversation of one employee window.

Pure domain object — repos are injected (ports); no SQL/HTTP here.
"""
from __future__ import annotations

import logging
from typing import Any

from ..ports import Message, Todo, Window
from .context import ContextBuilder

logger = logging.getLogger(__name__)

#: 兜底估算：中英混排大约 3 字符 ≈ 1 token。只在**本进程还没拿到真实 usage** 时用
#: （例如刚重启、还没跑过任何一轮），真实值一到就立刻被替换。
_CHARS_PER_TOKEN = 3.0


def _cache_int(*values: Any) -> int | None:
    """取第一个能解释成整数的值；都不是数字返回 None（= 服务商没给）。"""
    for v in values:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return int(v)
    return None


class WindowSession:
    def __init__(self, window: Window, message_repo: Any, todo_repo: Any, compaction_repo: Any,
                 on_message: Any = None):
        self.window = window
        self.messages = message_repo
        self.todos = todo_repo
        self.compactions = compaction_repo
        #: v0.10.36：每落一条消息回调一次（控制台实时推送用）。
        #: owner 实测："员工调用工具的时候，并没显示，得刷新一下才行" ——
        #: 后端以前只推 `window.stream`（助手**文本增量**），tool/system 消息
        #: 从来不推，界面只能靠刷新重拉 `/messages`。
        self.on_message = on_message
        #: 最近一次 LLM 调用回报的 **prompt_tokens**（= 当前上下文占用的真实值）。
        #: 注意它与 `token_usage()` 是**两种语义**：后者是本会话的累计花费（账本），
        #: 前者是"现在上下文有多满"（压缩判据）。曾经两者共用 SUM(tokens)，
        #: 结果是压缩永不触发、用量条恒 0。
        self.last_prompt_tokens: int = 0
        # v0.10.20：最近一次调用的缓存命中/未命中 token（DeepSeek 的
        # `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens`，OpenAI 风格的
        # `prompt_tokens_details.cached_tokens` 由 provider 层规范化后进来）。
        # owner 要在控制台看"缓存命中"——数据 provider 一直给，只是没人解析。
        self.last_cache_hit: int = 0
        self.last_cache_miss: int = 0
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
        # v0.10.21：群组**编排提示词**不是成员的产出，不进共享频道。
        # 不拦的话它会自指增长：提示词里有 recap、recap 又从频道读回提示词 ——
        # 实测 3 人一轮把最后一条提示词撑到 3028 字符、recap 重复注入 ≈3523 tokens
        # （嵌套深度 10 层）。`channel_mute` 由 GroupManager 在每步开跑前设置，
        # 只静音"恰好那一条"带签名的 user 消息（不劫持正常的 user 输入）。
        mute = getattr(self, "channel_mute", None)
        if (group_id and role == "user" and isinstance(mute, dict)
                and mute.get("left", 0) > 0
                and str(mute.get("marker") or "") in str(content or "")):
            group_id = ""
            mute["left"] = int(mute.get("left", 0)) - 1
        # v0.10.43：**工具执行细节不进共享频道**（owner 2026-10-20 实测反馈：
        # "群组里写入的工具显示了页面代码，会不会浪费 token"——会）。
        # 实测一条群组流水线：58 条频道消息里 33 条是 role=tool，内容是
        # read_file 返回的整份 HTML/CSS（最大 4.7KB）：
        #   ① 群组页刷屏，人看到的是整段源码；
        #   ② recap 从频道读消息 → 每个后续步骤的提示词都被这些工具输出占满
        #      （3600 字符预算被吃光，真正的成员产出反被挤掉）。
        # 工具调用不是"沟通"，是执行细节——留在自己的会话里就够了。
        # 例外：assistant 消息**带文字**时照常进频道（那是发言）。
        if group_id and role == "tool":
            group_id = ""
        if (group_id and role == "assistant" and not str(content or "").strip()
                and isinstance(meta, dict) and meta.get("tool_calls")):
            group_id = ""
        m = self.messages.add_message(self.window.id, role, content, tokens, meta,
                                      group_id=group_id)
        self._notify(m)
        return m

    def _notify(self, msg: Message) -> None:
        """把刚落的这条消息交给调用方（控制台实时推送）。**绝不能影响正常流程**。"""
        if not self.on_message:
            return
        try:
            self.on_message(msg)
        except Exception:  # noqa: BLE001 — 推送失败不能把会话写坏
            logger.warning("session on_message hook failed", exc_info=True)

    def recent(self, limit: int = 200) -> list[Message]:
        return self.messages.list_messages(self.window.id, limit)

    def token_usage(self) -> int:
        """**累计花费**（账本口径）：本会话所有消息上记的 token 之和。"""
        return self.messages.sum_tokens(self.window.id)

    # ------------------------------------------------------- 上下文占用（v11）
    def note_usage(self, usage: dict[str, Any] | None) -> None:
        """记住最近一次 LLM 调用的 usage —— 只取 `prompt_tokens` 作为上下文占用。

        `total_tokens`（含输出）是花费，不能拿来判"上下文满没满"。
        v0.10.20：同时记缓存命中/未命中（`cache_hit_tokens`/`cache_miss_tokens`，
        由 provider 层从 DeepSeek/OpenAI 两种形状规范化）。
        """
        if not isinstance(usage, dict):
            return
        try:
            prompt = int(usage.get("prompt_tokens") or 0)
        except (TypeError, ValueError):
            prompt = 0
        if prompt > 0:
            self.last_prompt_tokens = prompt
        # 缓存命中：provider 层（openai_compat._usage_dict）已规范化成
        # cache_hit_tokens/cache_miss_tokens；这里兜底再认一次原始形状
        # （DeepSeek 顶层 / OpenAI 嵌套），防其他 provider 或旧路径漏规范化。
        hit = _cache_int(usage.get("cache_hit_tokens"),
                         usage.get("prompt_cache_hit_tokens"))
        if hit is None:
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                hit = _cache_int(details.get("cached_tokens"))
        miss = _cache_int(usage.get("cache_miss_tokens"),
                          usage.get("prompt_cache_miss_tokens"))
        if hit is not None and miss is None and prompt > 0:
            miss = max(0, prompt - hit)
        if hit is not None or miss is not None:
            self.last_cache_hit = int(hit or 0)
            self.last_cache_miss = int(miss or 0)

    def cache_stats(self) -> dict[str, Any]:
        """最近一次调用的缓存命中情况（控制台用量条显示用）。

        没拿到过缓存字段的服务商返回全 0——前端据此不显示那一行，
        而不是显示一个假的 0%。
        """
        hit = int(getattr(self, "last_cache_hit", 0) or 0)
        miss = int(getattr(self, "last_cache_miss", 0) or 0)
        total = hit + miss
        return {"cache_hit_tokens": hit, "cache_miss_tokens": miss,
                "cache_hit_ratio": round(hit / total, 4) if total else 0.0}

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
