# DogBuddyCn — LLM profile registry (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Named LLM config sets (profiles) → provider instances.

Config shape: `llm` is the default set, `llm_profiles` holds named sets
(see config.AppConfig.llm_for). Windows and jobs can bind a profile name;
"" resolves to the active profile → default section.

Extension point: swap in any provider builder — the registry only needs
`llm_for(name)` resolution and builds providers through the same factory
used everywhere else, so every window can run on a different model set.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider
from .retry import RetryPolicy

logger = logging.getLogger("dogbuddy.llm.profiles")


class LLMProfileRegistry:
    def __init__(self, app_config: Any, on_wait_hook: Callable[[str, int, float], Any] | None = None):
        """on_wait_hook(profile_name, attempt, delay) — surfaced for UI rate-limit status."""
        self.cfg = app_config
        self.on_wait_hook = on_wait_hook
        # keyed by (profile, model): a per-window model override must not mutate a
        # shared provider, or one window's model would leak into every other one
        self._cache: dict[tuple[str, str], Any] = {}

    # ------------------------------------------------------------- lookup
    def names(self) -> list[str]:
        return self.cfg.profile_names()

    def resolve(self, profile: str | None = None) -> Any:
        """→ the LLMConfig section for this profile name."""
        return self.cfg.llm_for(profile)

    def get(self, profile: str | None = None, model: str | None = None) -> Any:
        """→ a (cached) provider instance for this profile + optional model override."""
        key = ((profile or ""), (model or ""))
        provider = self._cache.get(key)
        if provider is None:
            provider = self._build(profile, model)
            self._cache[key] = provider
        return provider

    def factory(self, profile: str | None = None, model: str | None = None) -> Callable[[], Any]:
        """A zero-arg factory pinned to one profile/model (for windows/tools)."""
        return lambda: self.get(profile, model)

    def provider_for(self, profile: str | None = None, model: str | None = None) -> Any:
        """Callable-compatible resolver: returns a provider instance.

        Every consumer calls it either with no args (`AdminService`,
        `ReportService`, `explore_subagent`), with a profile (`TaskRunner`), or
        with profile + model override (`WindowManager`). Wiring the *unsatisfied*
        `factory` method here was the v1.3 bug that made every real LLM call fail
        with "'function' object has no attribute 'chat'".
        """
        return self.get(profile, model)

    def invalidate(self, profile: str | None = None) -> None:
        """Drop cached provider(s) after a config edit."""
        if profile is None:
            self._cache.clear()
        else:
            name = profile or ""
            for key in [k for k in self._cache if k[0] == name]:
                self._cache.pop(key, None)

    # ------------------------------------------------------------- build
    def _build(self, profile: str | None, model: str | None = None) -> Any:
        section = self.cfg.llm_for(profile)
        name = profile or ""
        effective_model = self.cfg.effective_model(profile, model or "")

        def on_wait(attempt: int, delay: float) -> None:
            if self.on_wait_hook:
                try:
                    self.on_wait_hook(name, attempt, delay)
                except Exception:  # noqa: BLE001 — status reporting never breaks the wait
                    pass

        retry = RetryPolicy.from_config(section.retry, on_wait=on_wait)
        if (section.provider or "").lower() == "ollama":
            return OllamaProvider(
                base_url=section.ollama_base_url or section.base_url,
                model=effective_model, retry=retry)
        return OpenAICompatProvider(
            base_url=section.base_url, api_key=section.api_key, model=effective_model,
            keys=section.keys, temperature=section.temperature,
            timeout=section.request_timeout, retry=retry)
