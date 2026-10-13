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
                 threshold: float = 0.8, max_context_tokens: int = 128000):
        self.llm = llm
        self.repo = compaction_repo
        self.memory = memory_service
        self.threshold = threshold
        self.max_context_tokens = max_context_tokens

    # AstrBot ContextCompressor protocol shape
    def should_compress(self, current_tokens: int, max_tokens: int | None = None) -> bool:
        max_tokens = max_tokens or self.max_context_tokens
        return current_tokens >= self.threshold * max_tokens

    async def maybe_compact(self, session: Any) -> Compaction | None:
        """Compact when over threshold. Returns the Compaction record if one happened."""
        used = session.token_usage()
        if not self.should_compress(used):
            return None
        return await self.compact_now(session)

    async def compact_now(self, session: Any) -> Compaction:
        messages = session.recent(400)
        keep_from = max(len(messages) - _KEEP_RECENT, 0)
        doomed = messages[:keep_from]
        if not doomed:
            # nothing to compress — just record a no-op so callers can proceed
            return self.repo.add_compaction(session.window.id, {"narrative": "", "file_changes": [],
                                                               "todos": [], "decisions": [], "lessons": []}, 0)

        conversation = self._render(doomed)
        summary = await self._summarize(conversation)

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

    async def _summarize(self, conversation: str) -> dict[str, Any]:
        prompt = _EXTRACT_PROMPT.format(conversation=conversation[:60000])
        try:
            resp = await self.llm.chat([{"role": "user", "content": prompt}])
            text = (resp.content or "").strip()
        except Exception as e:  # noqa: BLE001 — fall back to a dumb summary on LLM failure
            logger.warning("compaction LLM failed: %s", e)
            return {"narrative": conversation[:2000], "file_changes": [], "todos": [],
                    "decisions": [], "lessons": []}
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
