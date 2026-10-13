# DogBuddyCn — LLM provider abstraction (L4 Capability)
# Derived from AstrBot (https://github.com/AstrBotDevs/AstrBot)
# Copyright (c) AstrBotDevs and contributors — AGPL-3.0
# Modifications (2026-10-12, DogBuddyCn contributors):
#   - trimmed to text-chat + tool-calling only (STT/TTS/embedding/rerank dropped)
#   - provider registry removed (single runtime provider assembled in main.py)
#   - metrics/config plumbing removed; retry handling lives in llm/retry.py
"""Provider abstraction: any OpenAI-compatible endpoint or Ollama behind one interface."""
from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """One function call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    raw: Any = None


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Model arguments arrive as a JSON string; tolerate dicts and malformed input."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"_raw": str(raw)}


class AbstractProvider(abc.ABC):
    """Provider abstract class (trimmed from AstrBot)."""

    def __init__(self, provider_config: dict[str, Any] | None = None) -> None:
        self.provider_config = provider_config or {}
        self.model_name: str = self.provider_config.get("model", "")

    def set_model(self, model_name: str) -> None:
        self.model_name = model_name

    def get_model(self) -> str:
        return self.model_name

    @abc.abstractmethod
    async def text_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """One chat turn. `messages` is OpenAI-style; `tools` is a JSON-schema tool list."""
        raise NotImplementedError

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Port-compatible alias for :meth:`text_chat` (`ports.LLMProvider.chat`).

        AgentLoop, Compactor, ReportService and `explore_subagent` all call
        `chat(...)`, while the concrete providers implement `text_chat` only —
        so any *real* provider raised `AttributeError` on the first turn. The
        test doubles defined `chat`, which is why this never surfaced in CI.
        """
        return await self.text_chat(messages, tools=tools, **kwargs)


class Provider(AbstractProvider):
    """Multi-key capable base. Keys are kept for config parity only — rate limiting
    NEVER switches keys (user decision 2026-10-12: same key waits out the backoff)."""

    def __init__(self, provider_config: dict[str, Any] | None = None) -> None:
        super().__init__(provider_config)
        self._keys: list[str] = list(self.provider_config.get("keys") or [])
        if self.provider_config.get("api_key"):
            self._keys.insert(0, self.provider_config["api_key"])
        self._key_index = 0

    def get_keys(self) -> list[str]:
        return list(self._keys)

    def get_current_key(self) -> str:
        return self._keys[self._key_index] if self._keys else ""

    def set_key(self, key: str) -> None:
        if key in self._keys:
            self._key_index = self._keys.index(key)
        else:
            self._keys.append(key)
            self._key_index = len(self._keys) - 1
