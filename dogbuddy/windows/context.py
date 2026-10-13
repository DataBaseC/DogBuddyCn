# DogBuddyCn — context builder (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Assemble the LLM message list for a window:

    [system: role prompt + global memory + window memory + compaction facts]
    + recent messages (with tool-call round trips preserved)

Business rule (compression policy glue) lives here in Domain, not in L1/L2.
"""
from __future__ import annotations

import json
from typing import Any

from ..ports import Message

# keep at most this many compaction blocks in context
MAX_COMPACTION_BLOCKS = 3


def message_to_llm(m: Message) -> dict[str, Any]:
    """Rebuild one OpenAI-style message from persisted state."""
    meta = m.meta or {}
    if m.role == "assistant":
        out: dict[str, Any] = {"role": "assistant", "content": m.content or ""}
        if meta.get("tool_calls"):
            out["tool_calls"] = meta["tool_calls"]
        return out
    if m.role == "tool":
        out = {"role": "tool", "content": m.content or ""}
        if meta.get("tool_call_id"):
            out["tool_call_id"] = meta["tool_call_id"]
        else:
            # legacy/unknown shape: present as user text so the model still sees it
            out = {"role": "user", "content": f"[tool result] {m.content or ''}"}
        return out
    return {"role": "user", "content": m.content or ""}


class ContextBuilder:
    def __init__(self, memory_service: Any = None):
        self.memory = memory_service

    def system_block(self, session: Any) -> str:
        w = session.window
        parts = [self._role_prompt(w)]
        if self.memory is not None:
            g = self.memory.recall("global")
            if g:
                parts.append(f"## Global memory\n{g}")
            wm = self.memory.recall_window(w.id)
            if wm:
                parts.append(f"## Window memory ({w.name})\n{wm}")
        facts = self.compaction_block(session)
        if facts:
            parts.append(facts)
        parts.append(f"## Workstation\nwindow_id={w.id} name={w.name} role={w.role} "
                     f"workspace={w.workspace_path} mode={w.mode}")
        return "\n\n".join(parts)

    @staticmethod
    def _role_prompt(w: Any) -> str:
        from ..agents.core.roles import get_role
        spec = get_role(w.role)
        return spec.system_prompt if spec else f"You are an employee agent ({w.role})."

    @staticmethod
    def compaction_block(session: Any) -> str:
        """Structured-loss-resistant facts from recent compactions."""
        comps = session.compaction_history()[-MAX_COMPACTION_BLOCKS:]
        if not comps:
            return ""
        chunks = []
        for c in comps:
            s = c.summary or {}
            chunks.append(
                f"### Compacted context ({c.created_at})\n"
                f"Summary: {s.get('narrative', '')}\n"
                f"File changes: {json.dumps(s.get('file_changes', []), ensure_ascii=False)}\n"
                f"Decisions: {json.dumps(s.get('decisions', []), ensure_ascii=False)}\n"
                f"Lessons: {json.dumps(s.get('lessons', []), ensure_ascii=False)}"
            )
        return "## Compacted conversation facts (older wording dropped)\n" + "\n\n".join(chunks)

    def build(self, session: Any, recent_limit: int = 60) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": self.system_block(session)}]
        for m in session.recent(recent_limit):
            msgs.append(message_to_llm(m))
        return msgs
