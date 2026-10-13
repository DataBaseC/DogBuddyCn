# DogBuddyCn — ask_user tool (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Relay a question upward (admin → user). Never blocks the run: the answer is
injected into the window session later via context_inject / next dispatch."""
from __future__ import annotations

from typing import Any

from .registry import ToolContext, ToolResult


class AskUserTool:
    name = "ask_user"
    description = ("Ask the human owner a question. The question is relayed through the admin. "
                   "The run continues; the answer will be injected into your session in a later turn.")
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "the question for the user"},
            "options": {"type": "array", "items": {"type": "string"},
                        "description": "optional suggested answers"},
        },
        "required": ["question"],
    }
    permission = "read"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        question = str(args.get("question", "")).strip()
        if not question:
            return ToolResult.failure("question must not be empty")
        options = [str(o) for o in (args.get("options") or [])][:8]
        if ctx.ask_user_fn is not None:
            try:
                reply = ctx.ask_user_fn(question, options)
                if hasattr(reply, "__await__"):
                    reply = await reply
                if isinstance(reply, str) and reply:
                    return ToolResult.success(reply, question=question, answered=True)
            except Exception as e:  # noqa: BLE001 — never break the run
                return ToolResult.failure(f"failed to relay question: {e}")
        return ToolResult.success(
            f"Question relayed to the user: {question!r}. "
            "The answer will be injected into your session later; continue with other work meanwhile.",
            question=question, answered=False)
