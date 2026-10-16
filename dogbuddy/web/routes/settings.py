# DogBuddyCn — settings routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Settings: LLM + retry + panel + gateway + report + whitelist."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...config import is_masked_secret, mask_secret


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
    model_ref: str | None = None  # probe through a **catalog entry** (自带密钥的那条)


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
            "api_key": mask_secret(section.api_key),
            "keys": [mask_secret(k) for k in section.keys],
            "temperature": section.temperature,
            "max_context_tokens": section.max_context_tokens,
            "compact_threshold": section.compact_threshold,
            "request_timeout": section.request_timeout,
            "retry": section.retry.model_dump(),
        }

    def _panel_dump() -> dict:
        """面板段也**必须**掩码。

        审查外的实测发现（2026-10-15）：这里原来是 `cfg.panel.model_dump()`，
        于是 `GET /api/settings` 把面板的 `agent_token` **明文**返回，前端还把它直接
        渲染进 `<input value=...>`。那个 token 能通过面板的 `/api/terminal/exec`
        **在服务器上以 root 执行任意命令**——等于任何拿到控制台 web token 的人
        都能拿到 root。密钥只该"能改、不能读"。
        """
        return {"enabled": cfg.panel.enabled, "base_url": cfg.panel.base_url,
                "agent_token": mask_secret(cfg.panel.agent_token)}

    def _dump() -> dict:
        return {
            "llm": _llm_dump(cfg.llm),
            "profiles": {name: _llm_dump(section) for name, section in cfg.llm_profiles.items()},
            "active_llm_profile": cfg.active_llm_profile,
            "panel": _panel_dump(),
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
            # 密钥：掩码 = 保持不变。
            # 这里原来没有这道判断，于是前端把掩码原样回传时，**真密钥会被
            # "********abcd" 覆盖掉**（实测复现）→ LLM 静默变成认证失败。
            #
            # v0.10.25（owner 要求）：**全局密钥在设置页停用** —— 密钥统一归属
            # 「模型管理」页的具体模型条目（`activate()` 会把条目的密钥切到运行时）。
            # 真收到一把新密钥就明确拒绝，而不是默默写进去或默默忽略。
            if body.llm.api_key and not is_masked_secret(body.llm.api_key):
                raise HTTPException(
                    status_code=400,
                    detail="全局 API Key 已在设置页停用：请到「模型管理」页新增/编辑"
                           "该模型条目并填写 API Key，然后点「设为管理员模型」——"
                           "密钥会随模型一起切换（每个模型可自带密钥）。")
            if body.llm.keys is not None:
                if all(not is_masked_secret(k) for k in body.llm.keys):
                    cfg.llm.keys = [str(k).strip() for k in body.llm.keys if str(k).strip()]
            if body.llm.retry:
                for f in ("base_interval", "max_interval", "max_retries", "backoff"):
                    v = getattr(body.llm.retry, f)
                    if v is not None:
                        setattr(cfg.llm.retry, f, v)
        if body.panel:
            for f in ("enabled", "base_url"):
                v = getattr(body.panel, f)
                if v is not None:
                    setattr(cfg.panel, f, v)
            # 密钥：留空 / 回传掩码 = 保持不变（绝不把 ********abcd 存成真 token）
            token = body.panel.agent_token
            if token is not None and str(token).strip() and not is_masked_secret(token):
                cfg.panel.agent_token = str(token).strip()
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
                          api_key: str | None = None, profile: str | None = None,
                          model_ref: str | None = None) -> dict:
        """Auto-fetch the model list so the console user picks instead of typing.

        `model_ref`：按**目录条目**自带的 provider/base_url/密钥去拉（条目自己的密钥不会出接口，
        所以只能服务端取）。这与 `test_llm` 的 `model_ref` 是同一套口径。

        返回值里 `details` 是 `[{id, context_window}]`：**服务商愿意给就给**（有的网关只回
        id/object/created/owned_by，实测 SiliconFlow 就是），给不了 `context_window=0` 表示
        "服务商没说"——前端据此把条目的上下文留空（继承），**不要替服务商编一个数**。
        """
        entry = None
        if model_ref:
            entry = svc.db.get_model(model_ref)
            if entry is None:
                raise HTTPException(status_code=404, detail=f"模型不存在：{model_ref}")
        async def _meta(p: str | None, b: str | None, k: str | None) -> list[dict]:
            """带上下文的拉取；服务对象没有新方法时退回旧口径（context_window=0 = 未知）。"""
            fn = getattr(svc.config, "fetch_models_meta", None)
            if fn is not None:
                return await fn(p, b, k)
            names = await svc.config.fetch_models(p, b, k)
            return [{"id": n, "context_window": 0} for n in names]

        try:
            if entry is not None:
                details = await _meta(
                    provider or entry.get("provider") or None,
                    base_url or entry.get("base_url") or None,
                    api_key if api_key is not None else (entry.get("api_key") or None))
            elif profile:
                section = cfg.llm_for(profile)
                details = await _meta(section.provider, section.base_url, section.api_key or api_key)
            else:
                details = await _meta(provider, base_url, api_key)
        except Exception as e:  # noqa: BLE001 — readable error to the console
            from ...services.model_service import endpoint_of, fetch_error_hint
            # v0.10.21：把"用的哪个端点"和"连不上 vs 真没有"一起说清楚——
            # 本地部署时界面上的"模型不识别"十有八九只是端点连不上。
            return {"models": [], "details": [], "error": str(e) + fetch_error_hint(e),
                    "hint": fetch_error_hint(e),
                    "used_base_url": base_url or (entry.get("base_url") if entry else "")
                    or (cfg.llm.base_url if not profile else ""),
                    "used_endpoint": endpoint_of(
                        base_url or (entry.get("base_url") if entry else "")
                        or (cfg.llm.base_url if not profile else ""))}
        return {"models": [m["id"] for m in details], "details": details, "error": ""}

    @r.post("/settings/test_llm", dependencies=auth_deps)
    async def test_llm(body: LLMProbeIn) -> dict:
        """Connectivity check (+ optional real chat round-trip when model is given)."""
        if body.model_ref:
            # 按目录条目测：用它自带的 base_url/密钥（掩码不会回传，所以只能服务端取）
            entry = svc.db.get_model(body.model_ref)
            if entry is None:
                raise HTTPException(status_code=404, detail=f"模型不存在：{body.model_ref}")
            return await svc.config.test_llm(
                body.provider or entry.get("provider") or None,
                body.base_url or entry.get("base_url") or None,
                body.api_key if body.api_key is not None else (entry.get("api_key") or None),
                body.model or entry.get("model") or None)
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
        # 密钥：掩码 = 保持不变（同 `/settings`，配置套也不能被掩码覆盖）
        if body_llm.api_key and not is_masked_secret(body_llm.api_key):
            section.api_key = body_llm.api_key
        if body_llm.keys is not None and all(not is_masked_secret(k) for k in body_llm.keys):
            section.keys = [str(k).strip() for k in body_llm.keys if str(k).strip()]
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
