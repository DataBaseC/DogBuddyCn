# DogBuddyCn — settings routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Settings: LLM + retry + panel + gateway + report + whitelist."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class ConfigKVIn(BaseModel):
    key: str
    value: Any


class RetryIn(BaseModel):
    base_interval: float | None = None
    max_interval: float | None = None
    max_retries: int | None = None
    backoff: str | None = None


class LLMIn(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    keys: list[str] | None = None
    temperature: float | None = None
    max_context_tokens: int | None = None
    compact_threshold: float | None = None
    request_timeout: float | None = None
    retry: RetryIn | None = None


class PanelIn(BaseModel):
    enabled: bool | None = None
    base_url: str | None = None
    agent_token: str | None = None


class GatewayIn(BaseModel):
    enabled: bool | None = None
    whitelist: list[int] | None = None
    push_target: dict[str, Any] | None = None


class ReportIn(BaseModel):
    default_strategy: str | None = None


class SettingsIn(BaseModel):
    llm: LLMIn | None = None
    panel: PanelIn | None = None
    gateway: GatewayIn | None = None
    report: ReportIn | None = None


class WhitelistIn(BaseModel):
    qq_id: str
    name: str = ""
    role: str = "admin"  # owner | admin | viewer


class LLMProbeIn(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # None → reuse the saved key
    model: str | None = None
    profile: str | None = None  # probe through a named profile instead of ad-hoc params


class ProfileIn(BaseModel):
    """Create/update a named LLM config set."""
    llm: LLMIn | None = None
    set_active: bool = False  # make this profile the default for new windows


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["settings"])
    cfg = svc.app_config

    def _llm_dump(section) -> dict:
        return {
            "provider": section.provider, "base_url": section.base_url,
            "model": section.model,
            "api_key": ("*" * 8 + section.api_key[-4:]) if section.api_key else "",
            "keys": ["*" * 8 + k[-4:] if k else "" for k in section.keys],
            "temperature": section.temperature,
            "max_context_tokens": section.max_context_tokens,
            "compact_threshold": section.compact_threshold,
            "request_timeout": section.request_timeout,
            "retry": section.retry.model_dump(),
        }

    def _dump() -> dict:
        return {
            "llm": _llm_dump(cfg.llm),
            "profiles": {name: _llm_dump(section) for name, section in cfg.llm_profiles.items()},
            "active_llm_profile": cfg.active_llm_profile,
            "panel": cfg.panel.model_dump(),
            "gateway": {"enabled": cfg.gateway.enabled,
                        "whitelist": cfg.gateway.whitelist,
                        "push_target": cfg.gateway.push_target},
            "notify": {"channels": [c.model_dump() for c in cfg.notify.channels]},
            "report": {"default_strategy": svc.config.get("report.default_strategy", "brief")},
        }

    @r.get("/config", dependencies=auth_deps)
    def get_config() -> dict:
        """Generic kv config (approval.*, ledger.*, …) for the console."""
        return {"config": svc.config.dump()}

    @r.get("/audit", dependencies=auth_deps)
    def get_audit(limit: int = 200, action: str = "") -> dict:
        """审计流水（T4.4 留痕可查）：action 支持前缀过滤（如 tool: / approval:）。"""
        rows = svc.db.list_audit(limit=max(1, min(limit, 1000)))
        out = [{"id": e.id, "actor": e.actor, "action": e.action,
                "detail": e.detail, "created_at": e.created_at}
               for e in rows]
        if action:
            out = [r for r in out if r["action"].startswith(action)]
        return {"entries": out, "count": len(out)}

    @r.post("/config", dependencies=auth_deps)
    def set_config(body: ConfigKVIn) -> dict:
        svc.config.set(body.key, body.value)
        return {"key": body.key, "value": body.value,
                "persisted": bool(svc.persist())}

    @r.get("/settings", dependencies=auth_deps)
    def get_settings() -> dict:
        return _dump()

    @r.put("/settings", dependencies=auth_deps)
    def put_settings(body: SettingsIn) -> dict:
        if body.llm:
            for f in ("provider", "base_url", "model", "temperature",
                      "max_context_tokens", "compact_threshold", "request_timeout"):
                v = getattr(body.llm, f)
                if v is not None:
                    setattr(cfg.llm, f, v)
            if body.llm.api_key:
                cfg.llm.api_key = body.llm.api_key
            if body.llm.keys is not None:
                cfg.llm.keys = body.llm.keys
            if body.llm.retry:
                for f in ("base_interval", "max_interval", "max_retries", "backoff"):
                    v = getattr(body.llm.retry, f)
                    if v is not None:
                        setattr(cfg.llm.retry, f, v)
        if body.panel:
            for f in ("enabled", "base_url", "agent_token"):
                v = getattr(body.panel, f)
                if v is not None:
                    setattr(cfg.panel, f, v)
        if body.gateway:
            for f in ("enabled", "whitelist", "push_target"):
                v = getattr(body.gateway, f)
                if v is not None:
                    setattr(cfg.gateway, f, v)
            for qq in cfg.gateway.whitelist:
                svc.db.whitelist_add(str(qq))
        if body.report and body.report.default_strategy:
            svc.config.set_report_strategy(body.report.default_strategy)
        saved = svc.persist()
        svc.db.audit("web", "settings:update", f"settings updated via console (saved={saved})")
        out = _dump()
        out["saved_to"] = saved
        return out

    # --------------------------------------------------- LLM discovery helpers
    @r.get("/settings/models", dependencies=auth_deps)
    async def list_models(provider: str | None = None, base_url: str | None = None,
                          api_key: str | None = None, profile: str | None = None) -> dict:
        """Auto-fetch the model list so the console user picks instead of typing."""
        try:
            if profile:
                section = cfg.llm_for(profile)
                models = await svc.config.fetch_models(section.provider, section.base_url,
                                                       section.api_key or api_key)
            else:
                models = await svc.config.fetch_models(provider, base_url, api_key)
        except Exception as e:  # noqa: BLE001 — readable error to the console
            return {"models": [], "error": str(e)}
        return {"models": models, "error": ""}

    @r.post("/settings/test_llm", dependencies=auth_deps)
    async def test_llm(body: LLMProbeIn) -> dict:
        """Connectivity check (+ optional real chat round-trip when model is given)."""
        if body.profile:
            section = cfg.llm_for(body.profile)
            return await svc.config.test_llm(body.provider or section.provider,
                                             body.base_url or section.base_url,
                                             body.api_key if body.api_key is not None else section.api_key,
                                             body.model or section.model)
        return await svc.config.test_llm(body.provider, body.base_url, body.api_key, body.model)

    # --------------------------------------------------------- LLM profiles
    def _apply_llm(section, body_llm: LLMIn) -> None:
        for f in ("provider", "base_url", "model", "temperature",
                  "max_context_tokens", "compact_threshold", "request_timeout"):
            v = getattr(body_llm, f)
            if v is not None:
                setattr(section, f, v)
        if body_llm.api_key:
            section.api_key = body_llm.api_key
        if body_llm.keys is not None:
            section.keys = body_llm.keys
        if body_llm.retry:
            for f in ("base_interval", "max_interval", "max_retries", "backoff"):
                v = getattr(body_llm.retry, f)
                if v is not None:
                    setattr(section.retry, f, v)

    @r.get("/settings/profiles", dependencies=auth_deps)
    def get_profiles() -> dict:
        return {"active": cfg.active_llm_profile,
                "names": cfg.profile_names(),
                "profiles": {n: _llm_dump(s) for n, s in cfg.llm_profiles.items()}}

    @r.post("/settings/profiles/{name}", dependencies=auth_deps)
    def save_profile(name: str, body: ProfileIn) -> dict:
        try:
            section = cfg.llm_profiles.get(name) or cfg.llm.model_copy(deep=True)
            if body.llm:
                _apply_llm(section, body.llm)
            cfg.save_profile(name, section)
            if body.set_active:
                cfg.active_llm_profile = name
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        saved = svc.persist()
        svc.db.audit("web", "profile:save", f"{name} (saved={saved})")
        return _dump()

    @r.patch("/settings/profiles/{name}", dependencies=auth_deps)
    def patch_profile(name: str, body: ProfileIn) -> dict:
        if name not in cfg.llm_profiles:
            raise HTTPException(status_code=404, detail=f"profile not found: {name}")
        return save_profile(name, body)

    @r.delete("/settings/profiles/{name}", dependencies=auth_deps)
    def delete_profile(name: str) -> dict:
        ok = cfg.delete_profile(name)
        if ok and cfg.active_llm_profile == name:
            cfg.active_llm_profile = ""
        if ok:
            svc.persist()
        svc.db.audit("web", "profile:delete", name)
        return {"deleted": ok, "active": cfg.active_llm_profile}

    # ------------------------------------------------------------- whitelist
    @r.get("/whitelist", dependencies=auth_deps)
    def whitelist() -> list[dict]:
        return svc.db.whitelist_list()

    @r.post("/whitelist", dependencies=auth_deps)
    def whitelist_add(body: WhitelistIn) -> dict:
        svc.db.whitelist_add(body.qq_id, body.name, body.role or "admin")
        if int(body.qq_id) not in cfg.gateway.whitelist:
            try:
                cfg.gateway.whitelist.append(int(body.qq_id))
            except ValueError:
                pass
        return {"added": body.qq_id}

    @r.delete("/whitelist", dependencies=auth_deps)
    def whitelist_remove(body: WhitelistIn) -> dict:
        ok = svc.db.whitelist_remove(body.qq_id)
        try:
            cfg.gateway.whitelist = [q for q in cfg.gateway.whitelist if str(q) != str(body.qq_id)]
        except Exception:  # noqa: BLE001
            pass
        return {"removed": ok}

    return r
