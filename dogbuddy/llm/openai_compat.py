# DogBuddyCn — OpenAI-compatible chat provider (L4 Capability)
# Derived from AstrBot (https://github.com/AstrBotDevs/AstrBot) OpenAI-compatible provider
# Copyright (c) AstrBotDevs and contributors — AGPL-3.0
# Modifications (2026-10-12, DogBuddyCn contributors):
#   - non-streaming only (V1 contract), function-calling parsed into ToolCall objects
#   - retries delegated to llm/retry.RetryPolicy (rate limit never switches keys)
"""Any OpenAI-compatible /chat/completions endpoint (OpenAI, DeepSeek, free relays, …)."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .provider import LLMResponse, Provider, ToolCall, parse_tool_arguments
from .retry import RetryPolicy

logger = logging.getLogger("dogbuddy.llm.openai")


class OpenAICompatProvider(Provider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        keys: list[str] | None = None,
        temperature: float = 0.7,
        timeout: float = 120.0,
        retry: RetryPolicy | None = None,
    ):
        super().__init__({
            "base_url": base_url, "api_key": api_key, "model": model,
            "keys": keys or [], "temperature": temperature,
        })
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.retry = retry or RetryPolicy()

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        key = self.get_current_key()
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    async def _request(self, payload: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
        if resp.status_code in (429, 529) or resp.status_code >= 500:
            resp.raise_for_status()  # becomes retryable inside RetryPolicy
        if resp.status_code == 401:
            raise ValueError(f"LLM auth failed (401) at {self.base_url} — check api_key")
        if resp.status_code >= 400:
            raise RuntimeError(f"LLM error {resp.status_code}: {resp.text[:500]}")
        return resp

    async def text_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")

        async def _call() -> LLMResponse:
            resp = await self._request(payload)
            return self._parse(resp.json())

        return await self.retry.call(_call)

    @staticmethod
    def _parse(data: dict[str, Any]) -> LLMResponse:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            tool_calls.append(ToolCall(
                id=tc.get("id") or fn.get("name", "call"),
                name=fn.get("name", ""),
                arguments=parse_tool_arguments(fn.get("arguments")),
            ))
        usage = data.get("usage") or {}
        return LLMResponse(
            content=msg.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            usage=_usage_dict(usage),
            raw=data,
        )


def _usage_dict(usage: dict[str, Any]) -> dict[str, Any]:
    """Normalise `usage` without assuming every value is a number.

    Real endpoints return nested objects (OpenAI-compatible relays add
    `prompt_tokens_details`, `completion_tokens_details`, cached/reasoning
    counters, …). A blind `int(v)` blew up the first real turn with
    "int() argument must be ... not 'dict'".
    """
    out: dict[str, Any] = {}
    for key, value in (usage or {}).items():
        if isinstance(value, bool):
            out[key] = int(value)
        elif isinstance(value, (int, float)):
            out[key] = int(value)
        elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
            out[key] = int(value)
        else:
            out[key] = value  # keep nested detail objects as-is
    return out
