# DogBuddyCn — LLM retry policy (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Rate-limit / transient-error retry (M-H).

Rules (user requirements 2026-10-12):
  - trigger: HTTP 429 / 529 / timeout / connection errors
  - exponential backoff (10s → 20 → 40 → … capped), or fixed interval mode
  - NEVER switches API key on rate limit — same key waits in place
  - waiting does NOT count as a task failure; only exhausted retries do
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

from ..errors import RETRYABLE_STATUS, classify, is_retryable as _is_retryable  # noqa: F401
from .provider import LLMResponse  # noqa: F401  (re-export convenience)

logger = logging.getLogger("dogbuddy.llm.retry")

T = TypeVar("T")


class RateLimitWaiting(RuntimeError):
    """Emitted as a status event while backing off; not a task failure."""

    def __init__(self, attempt: int, wait_seconds: float, cause: str = ""):
        self.attempt = attempt
        self.wait_seconds = wait_seconds
        self.cause = cause
        super().__init__(f"rate limited, retry {attempt} after {wait_seconds:.0f}s")


class RetryExhausted(RuntimeError):
    """All retries used up — this DOES count as one task failure."""

    def __init__(self, last_error: BaseException, attempts: int):
        self.last_error = last_error
        self.attempts = attempts
        super().__init__(f"retry exhausted after {attempts} attempts: {last_error}")


def is_retryable(exc: BaseException) -> bool:
    """Batch 6 · A1：判定收敛到 `errors.classify` —— "什么算可重试"只有一个定义。

    语义 = 分类为瞬态（超时 / 连接错误 / 429 / 5xx / 522·524）。
    """
    return _is_retryable(exc)


class RetryPolicy:
    def __init__(
        self,
        base_interval: float = 10.0,
        max_interval: float = 300.0,
        max_retries: int = 5,
        backoff: str = "exp",
        on_wait: Callable[[int, float], Any] | None = None,
    ):
        self.base_interval = base_interval
        self.max_interval = max_interval
        self.max_retries = max_retries
        self.backoff = backoff
        self.on_wait = on_wait

    @classmethod
    def from_config(cls, cfg: Any, on_wait: Callable[[int, float], Any] | None = None) -> "RetryPolicy":
        return cls(
            base_interval=cfg.base_interval,
            max_interval=cfg.max_interval,
            max_retries=cfg.max_retries,
            backoff=cfg.backoff,
            on_wait=on_wait,
        )

    def delay_for(self, attempt: int) -> float:
        """attempt is 1-based."""
        if self.backoff == "fixed":
            return self.base_interval
        return min(self.base_interval * (2 ** (attempt - 1)), self.max_interval)

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run fn with retries. Only exhausted retries raise RetryExhausted."""
        last: BaseException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await fn()
            except BaseException as e:  # noqa: BLE001 — filtered below
                if not is_retryable(e):
                    raise
                last = e
                if attempt >= self.max_retries:
                    break
                delay = self.delay_for(attempt)
                logger.warning("LLM call retryable failure (%s), retry %d in %.0fs", e, attempt, delay)
                if self.on_wait:
                    try:
                        self.on_wait(attempt, delay)
                    except Exception:  # status reporting must never break the wait
                        pass
                await asyncio.sleep(delay)
        raise RetryExhausted(last or RuntimeError("unknown"), self.max_retries)
