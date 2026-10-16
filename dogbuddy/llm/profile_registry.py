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


def _freeze(value: Any) -> Any:
    """Hashable snapshot of nested overrides for the provider cache key."""
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


class LLMProfileRegistry:
    def __init__(self, app_config: Any,
                 on_wait_hook: Callable[[str, int, float], Any] | None = None,
                 retry_memory: Any = None,
                 on_severity_hook: Callable[[str, Any, float, Any], Any] | None = None,
                 on_resume_hook: Callable[[str], Any] | None = None):
        """on_wait_hook(profile_name, attempt, delay) — surfaced for UI rate-limit status.

        T8.7/T8.8：`retry_memory` 让每个 profile 记住上次退避量级；
        `on_severity_hook(profile, severity, delay, exc)` 把限速严重度抛给上层
        （UI 徽标 / 报告 / 项目迭代降速）。
        v0.10.21：`on_resume_hook(profile)` 在"等待后这次调用**成功**"时回调，
        用来灭掉"限速等待中"徽标（以前只有 `window.run_finished` 会灭它，
        群组运行/管理员对话/工具路径永远不灭）。
        """
        self.cfg = app_config
        self.on_wait_hook = on_wait_hook
        self.retry_memory = retry_memory
        self.on_severity_hook = on_severity_hook
        self.on_resume_hook = on_resume_hook
        # v0.10.15：工位从模型目录里选模型时，条目自带的 provider/base_url/密钥要生效。
        # 否则"选了一个配置好的模型"只是换了个名字，请求还是打到 profile 那个端点
        # （换个服务商的模型时会 404/401，还很难看出原因）。
        self.model_lookup: Callable[[str], Any] | None = None
        # keyed by (profile, model): a per-window model override must not mutate a
        # shared provider, or one window's model would leak into every other one
        self._cache: dict[tuple[str, str], Any] = {}

    # ------------------------------------------------------------- lookup
    def names(self) -> list[str]:
        return self.cfg.profile_names()

    def resolve(self, profile: str | None = None) -> Any:
        """→ the LLMConfig section for this profile name."""
        return self.cfg.llm_for(profile)

    def get(self, profile: str | None = None, model: str | None = None,
            overrides: dict | None = None) -> Any:
        """→ a (cached) provider instance for this profile + optional model override.

        `overrides` = per-window params (temperature/request_timeout/retries);
        part of the cache key so two workbenches never share a mutated provider.
        """
        ovr = _freeze(overrides or {})
        key = ((profile or ""), (model or ""), ovr)
        provider = self._cache.get(key)
        if provider is None:
            provider = self._build(profile, model, overrides)
            self._cache[key] = provider
        return provider

    def factory(self, profile: str | None = None, model: str | None = None) -> Callable[[], Any]:
        """A zero-arg factory pinned to one profile/model (for windows/tools)."""
        return lambda: self.get(profile, model)

    def provider_for(self, profile: str | None = None, model: str | None = None,
                     overrides: dict | None = None) -> Any:
        """Callable-compatible resolver: returns a provider instance.

        Every consumer calls it either with no args (`AdminService`,
        `ReportService`, `explore_subagent`), with a profile (`TaskRunner`),
        with profile + model override (`WindowManager`), or with per-window
        overrides (v2 workbench params). Wiring the *unsatisfied* `factory`
        method here was the v1.3 bug that made every real LLM call fail
        with "'function' object has no attribute 'chat'".
        """
        return self.get(profile, model, overrides)

    def _entry_for(self, model: str) -> dict | None:
        """模型目录里按模型名找条目（没有 lookup 或找不到 → None）。"""
        if not model or self.model_lookup is None:
            return None
        try:
            entry = self.model_lookup(model)
        except Exception:  # noqa: BLE001 — 目录查询失败不能挡住 LLM 调用
            logger.debug("model lookup failed for %r", model, exc_info=True)
            return None
        if not entry:
            return None
        if hasattr(entry, "keys"):        # sqlite3.Row 也走这条
            return dict(entry)
        return None

    def invalidate(self, profile: str | None = None) -> None:
        """Drop cached provider(s) after a config edit."""
        if profile is None:
            self._cache.clear()
        else:
            name = profile or ""
            for key in [k for k in self._cache if k[0] == name]:
                self._cache.pop(key, None)

    # ------------------------------------------------------------- build
    def _build(self, profile: str | None, model: str | None = None,
               overrides: dict | None = None) -> Any:
        section = self.cfg.llm_for(profile)
        name = profile or ""
        effective_model = self.cfg.effective_model(profile, model or "")
        ovr = overrides or {}

        def on_wait(attempt: int, delay: float) -> None:
            if self.on_wait_hook:
                try:
                    self.on_wait_hook(name, attempt, delay)
                except Exception:  # noqa: BLE001 — status reporting never breaks the wait
                    pass

        def on_severity(severity: Any, delay: float, exc: Any) -> None:
            """T8.8: 把限速严重度往上抛（UI 徽标 / 报告 / 迭代降速都用它）。"""
            if self.on_severity_hook:
                try:
                    self.on_severity_hook(name, severity, delay, exc)
                except Exception:  # noqa: BLE001
                    pass

        retry_cfg = section.retry
        ovr_retry = ovr.get("retry")
        if isinstance(ovr_retry, dict) and hasattr(retry_cfg, "model_copy"):
            try:
                retry_cfg = retry_cfg.model_copy(
                    update={k: v for k, v in ovr_retry.items() if v is not None})
            except Exception:  # noqa: BLE001 — bad override values fall back to profile
                retry_cfg = section.retry
        # T8.7: 每 profile 一份退避记忆（"上次退避多久才成功"），下次从该量级起跳
        def on_resume() -> None:
            if self.on_resume_hook:
                try:
                    self.on_resume_hook(name)
                except Exception:  # noqa: BLE001 — 状态上报不能打断成功路径
                    pass

        retry = RetryPolicy.from_config(retry_cfg, on_wait=on_wait,
                                        memory=self.retry_memory, profile=name,
                                        on_severity=on_severity,
                                        on_success=on_resume)
        temperature = ovr.get("temperature", section.temperature)
        timeout = ovr.get("request_timeout", section.request_timeout)
        # v0.10.15：工位从**模型目录**里挑的模型，用条目自己的端点与密钥。
        # 目录条目的字段都是"可空"：空就退回 profile —— 绝不用空串覆盖已配好的值。
        entry = self._entry_for(effective_model)
        provider_name = str((entry or {}).get("provider") or section.provider or "")
        base_url = str((entry or {}).get("base_url") or section.base_url or "")
        api_key = str((entry or {}).get("api_key") or section.api_key or "")
        if entry is not None:
            logger.debug("模型目录条目生效：model=%s provider=%s base_url=%s",
                         effective_model, provider_name, base_url)
        if provider_name.lower() == "ollama":
            return OllamaProvider(
                base_url=section.ollama_base_url or base_url,
                model=effective_model, retry=retry)
        return OpenAICompatProvider(
            base_url=base_url, api_key=api_key, model=effective_model,
            keys=section.keys, temperature=temperature,
            timeout=timeout, retry=retry)
