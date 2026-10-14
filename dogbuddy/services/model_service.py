# DogBuddyCn — model catalog service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""ModelService：模型目录（模型管理页，v0.5.2）。

解决的问题：以前"换模型"只能去改 `config.yaml` 或走 LLM 配置套，换之前也
**没有任何地方能记下这个模型的脾气**——擅长什么、限速多凶、支不支持工具调用、
上下文多大。于是每次换模型都得重新试错。

设计要点：
* 目录条目（`models` 表）与运行时配置（`AppConfig.llm`）**解耦**：目录是"备选清单"，
  `activate()` 才把某一条写进运行时配置并落盘。source 默认 `openai_compat`。
* `note` 是核心字段——**能力与限制的备注**，是管理员选型的唯一依据。
* `import_fetched()` 从当前 provider 拉模型列表批量入库；**已存在的条目只补字段，
  绝不覆盖用户手写的 note**。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable

logger = logging.getLogger("dogbuddy.services.models")

# 备注里的可选能力标签（前端做成多选 chip）
CAPABILITY_TAGS = ("工具调用", "推理", "代码", "长上下文", "中文强", "多模态", "快", "便宜")
SPEED_LEVELS = ("", "很快", "快", "中", "慢", "很慢")
COST_LEVELS = ("", "免费", "低", "中", "高")


def make_model_id(provider: str, model: str) -> str:
    """稳定、可读、**路径安全**的 id：`<provider>_<model>`。

    不用 `provider/model` 是因为 id 要作为路径参数出现在
    `PATCH /api/models/{id}` 里——带 `/` 会被路由当成层级分隔符（即使前端
    encodeURIComponent 成 %2F，Starlette 也会先解码再匹配），导致 404。
    provider 在页面上单独成列显示，所以 id 里用 `_` 不影响可读性。
    """
    p = (provider or "openai_compat").strip() or "openai_compat"
    m = (model or "").strip()
    if not m:
        raise ValueError("model 不能为空")
    raw = f"{p}/{m}"
    # 避免超长/含奇怪字符：保留可读前缀，尾部加短哈希防碰撞
    safe = "".join(ch if ch.isalnum() or ch in "-_.:" else "_" for ch in raw)[:80]
    if len(safe) < len(raw):
        safe += "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
    return safe


class ModelService:
    def __init__(self, db: Any, app_config: Any = None,
                 persist: Callable[[], str] | None = None,
                 fetch_models: Callable[..., Any] | None = None):
        self.db = db
        self.app = app_config
        self._persist = persist
        self._fetch = fetch_models  # async (provider, base_url, api_key) -> list[str]

    # ------------------------------------------------------------- 读
    def catalog(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        rows = self.db.list_models(enabled_only=enabled_only)
        current = self.current()
        for r in rows:
            r["is_current"] = bool(r.get("model") and r["model"] == current.get("model")
                                   and (not r.get("base_url") or r["base_url"] == current.get("base_url")))
        return rows

    def current(self) -> dict[str, Any]:
        """当前实际生效的模型（运行时配置，可能与目录里的 is_default 不同步）。"""
        cfg = self.app
        if cfg is None:
            return {}
        return {
            "provider": cfg.llm.provider,
            "base_url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "max_context_tokens": cfg.llm.max_context_tokens,
            "active_profile": cfg.active_llm_profile,
        }

    def summary(self) -> dict[str, Any]:
        s = dict(self.db.models_summary())
        cur = self.current()
        s["current_model"] = cur.get("model", "")
        s["current_base_url"] = cur.get("base_url", "")
        # 目录里没有当前模型 → 提示用户"先把在用的这管进目录"
        s["current_in_catalog"] = any(
            r["model"] == cur.get("model") for r in self.db.list_models())
        return s

    # ------------------------------------------------------------- 写
    def upsert(self, **fields: Any) -> dict[str, Any]:
        model = str(fields.get("model") or "").strip()
        provider = str(fields.get("provider") or "openai_compat").strip()
        mid = str(fields.get("id") or "").strip() or make_model_id(provider, model)
        entry = {k: v for k, v in fields.items() if v is not None}
        entry["id"] = mid
        entry["model"] = model or mid
        entry.setdefault("provider", provider)
        if "enabled" in entry:
            entry["enabled"] = 1 if entry["enabled"] else 0
        saved = self.db.upsert_model(entry)
        self.db.audit("web", "model:upsert", f"{mid} ({'新建' if not fields.get('_was') else '更新'})")
        return saved

    def update(self, model_id: str, **fields: Any) -> dict[str, Any]:
        if self.db.get_model(model_id) is None:
            raise KeyError(f"模型不存在：{model_id}")
        patch = {k: v for k, v in fields.items() if v is not None}
        if patch.get("enabled") is not None:
            patch["enabled"] = 1 if patch["enabled"] else 0
        if patch.get("context_window") is not None:
            try:
                patch["context_window"] = max(0, int(patch["context_window"]))
            except (TypeError, ValueError):
                patch.pop("context_window", None)
        saved = self.db.upsert_model({"id": model_id, **patch})
        changed = ", ".join(k for k in patch if k not in ("id",))
        self.db.audit("web", "model:update", f"{model_id} fields=[{changed}]")
        return saved

    def remove(self, model_id: str) -> bool:
        ok = self.db.delete_model(model_id)
        if ok:
            self.db.audit("web", "model:delete", model_id)
        return ok

    def activate(self, model_id: str) -> dict[str, Any]:
        """把目录里的一条**真正生效**：写入运行时配置并落盘。"""
        entry = self.db.get_model(model_id)
        if entry is None:
            raise KeyError(f"模型不存在：{model_id}")
        if not entry.get("enabled", 1):
            raise ValueError(f"模型已停用，先启用再切换：{model_id}")
        cfg = self.app
        if cfg is None:
            raise RuntimeError("配置不可用")
        if entry.get("provider"):
            cfg.llm.provider = entry["provider"]
        if entry.get("base_url"):
            cfg.llm.base_url = entry["base_url"]
        cfg.llm.model = entry["model"]
        cw = int(entry.get("context_window") or 0)
        if cw > 0:
            cfg.llm.max_context_tokens = cw
        self.db.set_default_model(model_id)
        saved = ""
        if self._persist is not None:
            try:
                saved = str(self._persist())
            except Exception:  # noqa: BLE001 — 落盘失败要让调用方看见，但不能吞掉切换结果
                logger.warning("persist after model activate failed", exc_info=True)
        self.db.audit("web", "model:activate",
                      f"{model_id} → provider={cfg.llm.provider} model={cfg.llm.model} "
                      f"ctx={cfg.llm.max_context_tokens} saved={saved}")
        return {"activated": model_id, "model": entry["model"],
                "provider": cfg.llm.provider, "base_url": cfg.llm.base_url,
                "max_context_tokens": cfg.llm.max_context_tokens, "saved_to": saved}

    # ------------------------------------------------------------- 导入
    async def import_fetched(self, provider: str | None = None, base_url: str | None = None,
                             api_key: str | None = None) -> dict[str, Any]:
        """从 provider 拉模型列表并入库。**绝不覆盖已存在的 note**。"""
        if self._fetch is None:
            raise RuntimeError("未接入模型列表拉取能力")
        names = await self._fetch(provider, base_url, api_key)
        names = [str(n).strip() for n in (names or []) if str(n).strip()]
        cfg = self.app
        used_provider = provider or (cfg.llm.provider if cfg else "openai_compat")
        used_base = base_url or (cfg.llm.base_url if cfg else "")
        existing = {r["id"]: r for r in self.db.list_models()}
        added, refreshed, kept_notes = [], [], 0
        for name in names:
            mid = make_model_id(used_provider, name)
            entry = existing.get(mid)
            if entry is None:
                self.db.upsert_model({"id": mid, "provider": used_provider, "model": name,
                                      "base_url": used_base, "source": "fetched",
                                      "enabled": 1, "display_name": name})
                added.append(name)
            else:
                patch: dict[str, Any] = {"id": mid, "provider": used_provider, "base_url": used_base}
                if entry.get("source") != "manual":
                    patch["source"] = "fetched"
                self.db.upsert_model(patch)
                refreshed.append(name)
                if (entry.get("note") or "").strip():
                    kept_notes += 1
        self.db.audit("web", "model:import",
                      f"provider={used_provider} fetched={len(names)} added={len(added)} "
                      f"refreshed={len(refreshed)}")
        return {"fetched": len(names), "added": added, "refreshed": refreshed,
                "kept_notes": kept_notes, "provider": used_provider, "base_url": used_base}
