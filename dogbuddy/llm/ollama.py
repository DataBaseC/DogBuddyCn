# DogBuddyCn — Ollama provider (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Optional local Ollama backend via /api/chat (non-streaming)."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .provider import LLMResponse, Provider, ToolCall, parse_tool_arguments
from .retry import RetryPolicy

logger = logging.getLogger("dogbuddy.llm.ollama")


class OllamaProvider(Provider):
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5-coder",
        timeout: float = 300.0,
        retry: RetryPolicy | None = None,
    ):
        super().__init__({"base_url": base_url, "model": model})
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry = retry or RetryPolicy()

    async def text_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload: dict[str, Any] = {"model": self.model_name, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools

        async def _call() -> LLMResponse:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            if resp.status_code >= 500:
                resp.raise_for_status()
            if resp.status_code >= 400:
                raise RuntimeError(f"ollama error {resp.status_code}: {resp.text[:500]}")
            return self._parse(resp.json())

        return await self.retry.call(_call)

    @staticmethod
    def _parse(data: dict[str, Any]) -> LLMResponse:
        msg = data.get("message") or {}
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            tool_calls.append(ToolCall(
                id=fn.get("name", "call"),
                name=fn.get("name", ""),
                arguments=parse_tool_arguments(fn.get("arguments")),
            ))
        return LLMResponse(
            content=msg.get("content"),
            tool_calls=tool_calls,
            finish_reason="stop" if not tool_calls else "tool_calls",
            usage={
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            },
            raw=data,
        )
