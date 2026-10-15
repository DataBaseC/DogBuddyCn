# DogBuddyCn — remote model list & connection test (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Fetch available models from an OpenAI-compatible endpoint or Ollama —
powers the console's "pick a model instead of typing it" flow."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("dogbuddy.llm.models")


class ModelFetchError(RuntimeError):
    pass


def _candidate_urls(base_url: str) -> list[str]:
    """Tolerate base_url with or without the /v1 suffix."""
    b = (base_url or "").rstrip("/")
    if not b:
        return []
    out = [b]
    if not b.endswith("/v1"):
        out.append(b + "/v1")
    else:
        out.append(b[: -len("/v1")])
    return out


#: 各家 OpenAI 兼容网关给"上下文长度"起的字段名（能读到哪个用哪个）
_CONTEXT_FIELDS = ("context_length", "context_window", "max_context_len", "max_model_len",
                   "max_input_tokens", "max_context_tokens")


def _context_of(item: dict) -> int:
    """从一条模型记录里读上下文长度（读不到返回 0 = 服务商没说）。"""
    for key in _CONTEXT_FIELDS:
        v = item.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    for parent in ("top_provider", "meta", "details", "config"):
        sub = item.get(parent)
        if isinstance(sub, dict):
            for key in _CONTEXT_FIELDS:
                v = sub.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    return int(v)
    return 0


async def _fetch_items(provider: str, base_url: str, api_key: str = "") -> list[dict]:
    """原始模型记录（OpenAI 兼容：`data`/`models`；Ollama：`/api/tags`）。"""
    provider = (provider or "openai_compat").lower()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    urls = _candidate_urls(base_url)
    if not urls:
        raise ModelFetchError("base_url is empty")

    last_err = "no endpoint reachable"
    async with httpx.AsyncClient(timeout=15.0) as client:
        if provider == "ollama":
            for u in urls:
                try:
                    resp = await client.get(f"{u}/api/tags")
                    if resp.status_code == 200:
                        data = resp.json()
                        return [m for m in (data.get("models") or []) if isinstance(m, dict)]
                    last_err = f"HTTP {resp.status_code}"
                except httpx.HTTPError as e:
                    last_err = str(e)
            raise ModelFetchError(f"ollama /api/tags unreachable: {last_err}")

        for u in urls:
            try:
                resp = await client.get(f"{u}/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data") or data.get("models") or []
                    items = [m for m in items if isinstance(m, dict)]
                    if items:
                        return items
                    last_err = "endpoint returned an empty model list"
                elif resp.status_code in (401, 403):
                    raise ModelFetchError("authentication failed — check api_key")
                else:
                    last_err = f"HTTP {resp.status_code}"
            except httpx.HTTPError as e:
                last_err = str(e)
    raise ModelFetchError(f"models endpoint unreachable: {last_err}")


async def fetch_model_meta(provider: str, base_url: str, api_key: str = "") -> list[dict[str, Any]]:
    """带上下文长度的模型列表：`[{"id": ..., "context_window": int}]`，按 id 排序。

    注意：**不是每家网关都给**（实测 SiliconFlow 的 `/v1/models` 只有
    `id/object/created/owned_by`）。所以 `context_window = 0` 的含义是
    "服务商没说"——调用方必须把它当"继承/未知"，**不能瞎填一个数**。
    """
    items = await _fetch_items(provider, base_url, api_key)
    out: list[dict[str, Any]] = []
    for it in items:
        name = str(it.get("id") or it.get("name") or "").strip()
        if not name:
            continue
        out.append({"id": name, "context_window": _context_of(it)})
    out.sort(key=lambda m: m["id"])
    return out


async def fetch_model_list(provider: str, base_url: str, api_key: str = "") -> list[str]:
    """Return model ids/names, sorted. Raises ModelFetchError with a readable message."""
    return [m["id"] for m in await fetch_model_meta(provider, base_url, api_key)]


async def test_connection(provider: str, base_url: str, api_key: str = "",
                          model: str = "") -> dict[str, Any]:
    """Lightweight connectivity check: reach the endpoint, count models, measure latency.
    Optionally does one real chat round-trip when a model is given."""
    t0 = time.monotonic()
    try:
        models = await fetch_model_list(provider, base_url, api_key)
    except ModelFetchError as e:
        return {"ok": False, "message": str(e), "latency_ms": int((time.monotonic() - t0) * 1000),
                "model_count": 0}
    out: dict[str, Any] = {
        "ok": True, "model_count": len(models), "models_sample": models[:5],
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "message": f"connected, {len(models)} models available",
    }
    if model:
        try:
            from .ollama import OllamaProvider
            from .openai_compat import OpenAICompatProvider
            from .retry import RetryPolicy

            no_retry = RetryPolicy(max_retries=0)
            if (provider or "").lower() == "ollama":
                p = OllamaProvider(base_url=base_url, model=model, retry=no_retry)
            else:
                p = OpenAICompatProvider(base_url=base_url, api_key=api_key,
                                         model=model, retry=no_retry)
            resp = await p.text_chat([{"role": "user", "content": "Reply with exactly: ok"}])
            out["chat_sample"] = (resp.content or "")[:80]
            out["message"] += "; chat round-trip ok"
        except Exception as e:  # noqa: BLE001 — report but keep ok=True (models endpoint worked)
            out["chat_error"] = str(e)[:200]
            out["message"] += f"; chat test failed: {str(e)[:120]}"
    return out
