# DogBuddyCn — loss-resistant context compaction (L3 Domain)
# ContextCompressor protocol & threshold-trigger idea derived from AstrBot
# (https://github.com/AstrBotDevs/AstrBot) — Copyright (c) AstrBotDevs and contributors, AGPL-3.0.
# Modifications (2026-10-12, DogBuddyCn contributors):
#   - added loss-resistant structured fact extraction (file_changes/todos/decisions/lessons)
#     persisted to compactions + window memory (only conversational wording is dropped)
#   - persistence via injected repos (ports), no SQL here
"""When a window's token usage crosses the threshold (default 80% of model window),
older messages are compacted: an LLM summary + structured facts replace them."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from ..ports import Compaction

logger = logging.getLogger("dogbuddy.windows.compactor")

_KEEP_RECENT = 12  # messages kept verbatim after compaction

_EXTRACT_PROMPT = """You are compressing a long-running work session. Produce a JSON object:
{{
  "narrative": "a faithful summary of what happened and where things stand",
  "file_changes": [{{"path": "...", "action": "created|modified|deleted", "note": "..."}}],
  "todos": ["still-open todo items"],
  "decisions": ["important decisions made"],
  "lessons": ["mistakes/learnings worth remembering"]
}}
Rules: keep facts, drop pleasantries; never invent file paths; answer with ONLY the JSON.

Conversation:
{conversation}
"""


class Compactor:
    """Threshold-triggered LLM compaction with structured fact preservation."""

    def __init__(self, llm: Any, compaction_repo: Any, memory_service: Any = None,
                 threshold: float = 0.8, max_context_tokens: int = 128000,
                 max_messages: int = 48, summarize_timeout: float = 60.0):
        self.llm = llm
        self.repo = compaction_repo
        self.memory = memory_service
        self.threshold = threshold
        self.max_context_tokens = max_context_tokens
        #: 摘要调用的上限（v0.10.48）。压缩跑在**每轮 LLM 调用之前**，以前没有超时：
        #: provider 卡住时整轮就跟着卡（生产 10-21/10-22 各出现一次 `compaction LLM
        #: failed`）。超时后按降级处理（截断保留原文），绝不让压缩拖死主流程。
        self.summarize_timeout = summarize_timeout
        #: 消息数触发线（v0.10.38）。**为什么需要它**：token 阈值在长上下文库上
        #: 永远不触发——工位 `max_context_tokens=1048576`、阈值 0.8 → 要 84 万 token
        #: 才压缩，而 `ContextBuilder.build()` 只取 `recent(60)`：会话一超过 60 条，
        #: 每轮请求的窗口就向前滑一条 → **消息列表头部位移 → 提供商缓存前缀匹配不上**，
        #: 超出的部分每次全价重付（实测：95 条会话的工位缓存命中只有 4096/25490=13.8%,
        #: 56 条会话的工位 97.5%）。在 60 之前压缩（保留 12 条），窗口再也不滑动，
        #: 前缀稳定 → 缓存恢复，上下文也封顶。
        self.max_messages = max_messages

    # AstrBot ContextCompactor protocol shape
    def should_compress(self, current_tokens: int, max_tokens: int | None = None) -> bool:
        max_tokens = max_tokens or self.max_context_tokens
        return current_tokens >= self.threshold * max_tokens

    def should_compress_by_count(self, session: Any) -> bool:
        """消息数超线就压（与 token 阈值并列的第二个触发维度）。"""
        try:
            return len(session.recent(400)) > self.max_messages
        except Exception:  # noqa: BLE001 — 数不了消息时不阻断压缩路径
            logger.debug("message count failed", exc_info=True)
            return False

    async def maybe_compact(self, session: Any) -> Compaction | None:
        """Compact when over threshold (tokens OR message count). Returns the
        Compaction record if one happened。

        判据必须用**上下文占用**（最近一次调用的 prompt_tokens），不能用累计花费
        （`session.token_usage()` 是 `SUM(tokens)`）：长寿命工位的累计花费只会一直涨，
        用它判压缩 = 会话越老越容易触发压缩，把上下文压碎。
        """
        used = self._used_tokens(session)
        if not (self.should_compress(used) or self.should_compress_by_count(session)):
            return None
        return await self.compact_now(session)

    @staticmethod
    def _used_tokens(session: Any) -> int:
        """上下文占用的取值口径，带兜底：老 session 对象没有 `context_tokens()`。"""
        ctx = getattr(session, "context_tokens", None)
        if callable(ctx):
            try:
                return int(ctx())
            except Exception:  # noqa: BLE001 — 拿不到占用时退回累计值，绝不阻断压缩路径
                logger.debug("context_tokens() failed", exc_info=True)
        return int(session.token_usage())

    async def compact_now(self, session: Any) -> Compaction:
        messages = session.recent(400)
        keep_from = max(len(messages) - _KEEP_RECENT, 0)
        doomed = messages[:keep_from]
        if not doomed:
            # nothing to compress — just record a no-op so callers can proceed
            return self.repo.add_compaction(session.window.id, {"narrative": "", "file_changes": [],
                                                               "todos": [], "decisions": [], "lessons": []}, 0)

        conversation = self._render(doomed)
        summary = await self._summarize(conversation, getattr(session.window, "id", ""))

        compaction = self.repo.add_compaction(
            session.window.id, summary, replaced_count=len(doomed))

        # drop compacted messages; keep the tail verbatim
        session.drop_messages(doomed[-1].id)

        # precipitate durable facts into window memory (anti-loss)
        if self.memory is not None:
            try:
                facts = []
                for key in ("decisions", "lessons"):
                    for item in summary.get(key, [])[:10]:
                        facts.append(f"{key[:-1]}: {item}")
                for fc in summary.get("file_changes", [])[:20]:
                    if isinstance(fc, dict):
                        facts.append(f"file {fc.get('action', 'modified')}: {fc.get('path', '?')}")
                if facts:
                    self.memory.append_window(session.window.id, "compaction沉淀：" + "; ".join(facts))
            except Exception:  # noqa: BLE001 — memory precipitation must not break compaction
                logger.warning("memory precipitation failed", exc_info=True)
        return compaction

    async def _summarize(self, conversation: str, window_id: str = "") -> dict[str, Any]:
        prompt = _EXTRACT_PROMPT.format(conversation=conversation[:60000])
        try:
            resp = await asyncio.wait_for(
                self.llm.chat([{"role": "user", "content": prompt}]),
                timeout=self.summarize_timeout)
            text = (resp.content or "").strip()
        except Exception as e:  # noqa: BLE001 — fall back to a dumb summary on LLM failure
            # v0.10.48：降级**必须留痕**。以前只有一行 WARNING，台账和审计里都看不出
            # 这次压缩是"LLM 总结"还是"原文截断"，出问题时无从判断上下文质量。
            logger.warning(
                "compaction LLM failed (%s: %s) — fell back to truncation, %d chars kept",
                type(e).__name__, e, min(len(conversation), 2000))
            try:
                self.repo.audit("system", "compaction:degraded",
                                f"{window_id} {type(e).__name__} kept={min(len(conversation), 2000)}")
            except Exception:  # noqa: BLE001 — 审计写不进去也不能阻断压缩
                logger.debug("compaction audit failed", exc_info=True)
            return {"narrative": conversation[:2000], "file_changes": [], "todos": [],
                    "decisions": [], "lessons": [],
                    "_degraded": True, "_degraded_reason": type(e).__name__}
        return self._parse_json(text, conversation)

    @staticmethod
    def _parse_json(text: str, fallback_src: str) -> dict[str, Any]:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    data.setdefault("narrative", "")
                    for k in ("file_changes", "todos", "decisions", "lessons"):
                        data.setdefault(k, [])
                    return data
            except json.JSONDecodeError:
                pass
        return {"narrative": text[:2000] or fallback_src[:2000], "file_changes": [],
                "todos": [], "decisions": [], "lessons": []}

    @staticmethod
    def _render(messages: list[Any]) -> str:
        lines = []
        for m in messages:
            lines.append(f"[{m.role}] {m.content}")
        return "\n".join(lines)
