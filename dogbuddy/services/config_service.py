# DogBuddyCn — runtime config service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""ConfigService: key/value config (db-backed) + report strategy + hot apply."""
from __future__ import annotations

from typing import Any


class ConfigService:
    def __init__(self, db: Any, app_config: Any = None, persist: Any = None):
        self.db = db
        self.app = app_config  # AppConfig (pydantic), hot-applied fields mutate here
        # callable() -> path: writes the live AppConfig to disk. Without it every
        # admin-agent config edit dies with the process (the v1.3 defect).
        self._persist = persist

    def persist(self) -> str:
        """Flush the live AppConfig to YAML (and drop cached LLM providers)."""
        if self._persist is None:
            return ""
        try:
            return str(self._persist())
        except Exception:  # noqa: BLE001 — persistence failure must not break the tool
            return ""

    def get(self, key: str, default: Any = None) -> Any:
        val = self.db.get_config(key)
        return default if val is None else val

    def set(self, key: str, value: Any) -> None:
        self.db.set_config(key, value)
        self._hot_apply(key, value)
        self.persist()
        self.db.audit("system", "config:set", f"{key}={value!r}")

    def _hot_apply(self, key: str, value: Any) -> None:
        """Apply known runtime keys to the live AppConfig object."""
        if self.app is None:
            return
        try:
            if key == "report.default_strategy":
                return
            if key.startswith("llm.retry."):
                field = key.split(".")[-1]
                setattr(self.app.llm.retry, field, value)
            elif key.startswith("llm."):
                field = key.split(".")[-1]
                if hasattr(self.app.llm, field):
                    setattr(self.app.llm, field, value)
        except Exception:  # noqa: BLE001 — config apply must not crash the call
            pass

    def dump(self) -> dict[str, Any]:
        keys = ["report.default_strategy"]
        return {k: self.get(k) for k in keys}

    def set_report_strategy(self, strategy: str) -> None:
        if strategy not in ("silent", "brief", "detailed"):
            raise ValueError(f"invalid report strategy: {strategy}")
        self.set("report.default_strategy", strategy)

    # --------------------------------------------- LLM discovery (console UX)
    def _llm_params(self, provider: str | None, base_url: str | None,
                    api_key: str | None) -> tuple[str, str, str]:
        """Explicit params win; fall back to the live config (key may be omitted → reuse saved)."""
        cfg = self.app.llm if self.app is not None else None
        p = provider or (cfg.provider if cfg else "openai_compat")
        b = base_url or (cfg.base_url if cfg else "")
        k = api_key if api_key is not None else (cfg.api_key if cfg else "")
        return p, b, (k or "")

    def llm_configured(self) -> bool:
        cfg = self.app.llm if self.app is not None else None
        if cfg is None:
            return False
        if (cfg.provider or "") == "ollama":
            return bool(cfg.ollama_base_url or cfg.base_url)
        return bool(cfg.api_key and cfg.base_url and cfg.model)

    async def fetch_models(self, provider: str | None = None, base_url: str | None = None,
                           api_key: str | None = None) -> list[str]:
        from ..llm.models import fetch_model_list
        p, b, k = self._llm_params(provider, base_url, api_key)
        return await fetch_model_list(p, b, k)

    async def fetch_models_meta(self, provider: str | None = None, base_url: str | None = None,
                                api_key: str | None = None) -> list[dict[str, Any]]:
        """同 `fetch_models`，但带上服务商给的上下文长度（读不到就是 0 = 未知）。"""
        from ..llm.models import fetch_model_meta
        p, b, k = self._llm_params(provider, base_url, api_key)
        return await fetch_model_meta(p, b, k)

    async def test_llm(self, provider: str | None = None, base_url: str | None = None,
                       api_key: str | None = None, model: str | None = None) -> dict[str, Any]:
        from ..llm.models import test_connection
        p, b, k = self._llm_params(provider, base_url, api_key)
        return await test_connection(p, b, k, model or "")
