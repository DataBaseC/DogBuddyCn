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
from urllib.parse import urlsplit

from ..config import SECRET_MASK, is_masked_secret, mask_secret

logger = logging.getLogger("dogbuddy.services.models")

# 备注里的可选能力标签（前端做成多选 chip）
CAPABILITY_TAGS = ("工具调用", "推理", "代码", "长上下文", "中文强", "多模态", "快", "便宜")
SPEED_LEVELS = ("", "很快", "快", "中", "慢", "很慢")
COST_LEVELS = ("", "免费", "低", "中", "高")

#: 掩码前缀——**凡是要出接口的密钥都必须过这里**；口径统一在 `config.mask_secret`
MASK = SECRET_MASK

#: 常见服务商预设（模型管理页的"一键填入"）。**只预填 base_url/provider，不预填密钥。**
PROVIDER_PRESETS: tuple[dict[str, str], ...] = (
    {"label": "SenseNova（商汤）", "provider": "openai_compat",
     "url": "https://token.sensenova.cn/v1"},
    {"label": "DeepSeek", "provider": "openai_compat", "url": "https://api.deepseek.com/v1"},
    {"label": "OpenAI", "provider": "openai_compat", "url": "https://api.openai.com/v1"},
    {"label": "Moonshot Kimi", "provider": "openai_compat", "url": "https://api.moonshot.cn/v1"},
    {"label": "阿里百炼 DashScope", "provider": "openai_compat",
     "url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"label": "硅基流动", "provider": "openai_compat", "url": "https://api.siliconflow.cn/v1"},
    {"label": "Ollama 本地", "provider": "ollama", "url": "http://127.0.0.1:11434"},
    {"label": "自定义 / 中转站", "provider": "openai_compat", "url": ""},
)


def mask_key(key: str | None) -> str:
    """`sk-abcdef123456` → `********3456`。空 key 回空串（口径：`config.mask_secret`）。"""
    return mask_secret(key)


def is_masked(value: str | None) -> bool:
    """前端回传掩码时表示"没改密钥"，不能把它当成新密钥存下去。"""
    return is_masked_secret(value)


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


def _safe_id(raw: str) -> str:
    """把任意串收敛成路径安全的 id（id 会出现在 `PATCH /api/models/{id}` 里）。"""
    return "".join(ch if ch.isalnum() or ch in "-_.:@" else "_" for ch in raw)[:110]


def endpoint_of(base_url: str) -> str:
    """`base_url` 的规范化**端点标识**：`host[:port][/path]`（去 scheme、尾斜杠、小写）。

    为什么需要它：目录条目的 id 只由 `provider + model` 决定，**不含地址**。
    于是"同一个模型名、不同地址"的两条必然撞 id —— 新增/重拉时把原来那条的
    `base_url` 静默改掉（owner 实测："我好像新增的会覆盖掉原来的"），而后来的
    请求还带着上一家的 key → 401。用端点做区分后两条可以共存。
    """
    raw = str(base_url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw if "://" in raw else "http://" + raw)
    except ValueError:
        return raw.lower()
    host = (parts.netloc or "").lower()
    path = (parts.path or "").rstrip("/")
    return f"{host}{path}"


def fetch_error_hint(exc: Any) -> str:
    """把"拉不到模型列表"的底层异常翻译成一句人能用的提示。

    本地部署（LM Studio / Ollama / vLLM）最常见的失败原因**不是**"模型不存在"，
    而是端点根本连不上：服务器与它不在同一网段、没开局域网服务、防火墙。
    owner 实测：LM Studio 在 `192.168.3.78:1234`，而服务器只有 `192.168.1.2/24`
    （路由表里只有 192.168.1.0/24）→ 100% 丢包，但界面上看起来像"模型不识别"。
    """
    name = type(exc).__name__.lower() if not isinstance(exc, str) else ""
    text = str(exc).lower()
    unreachable = ("timeout" in name or "connect" in name or "network" in name
                   or "timeout" in text or "connect" in text or "refused" in text
                   or "unreachable" in text or "no route" in text
                   or "getaddrinfo" in text or "name or service" in text)
    if not unreachable:
        return ""
    return ("（端点连不上，**不是模型不存在**：服务器访问不了这个地址。"
            "本地 LM Studio/Ollama 常见原因：① 与服务器不在同一网段；"
            "② LM Studio 未开 Serve on Local Network；③ 防火墙/端口未放行。"
            "可在服务器上 `curl <base_url>/models` 自检。）")


class ModelService:
    def __init__(self, db: Any, app_config: Any = None,
                 persist: Callable[[], str] | None = None,
                 fetch_models: Callable[..., Any] | None = None,
                 fetch_meta: Callable[..., Any] | None = None):
        self.db = db
        self.app = app_config
        self._persist = persist
        self._fetch = fetch_models  # async (provider, base_url, api_key) -> list[str]
        self._fetch_meta = fetch_meta  # async (...) -> [{id, context_window}]（服务商没给就是 0）

    # ------------------------------------------------------------- 读
    def catalog(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        rows = self.db.list_models(enabled_only=enabled_only)
        current = self.current()
        for r in rows:
            r["is_current"] = bool(r.get("model") and r["model"] == current.get("model")
                                   and (not r.get("base_url") or r["base_url"] == current.get("base_url")))
            r.update(self._public(r))
        return rows

    @staticmethod
    def _public(entry: dict[str, Any]) -> dict[str, Any]:
        """出接口的形状：**永不回传明文密钥**，只回掩码 + 是否自带 + 端点。"""
        raw = entry.get("api_key") or ""
        ep = endpoint_of(entry.get("base_url") or "")
        return {"api_key": mask_key(raw), "has_api_key": bool(raw.strip()),
                # 端点与密钥来源给前端显示：同名模型靠它区分（"新增覆盖原来的"的可见化）
                "endpoint": ep, "endpoint_host": ep.split("/")[0],
                "key_source": "entry" if raw.strip() else "global"}

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

    # ------------------------------------------------------------- 收编/校验
    def adopt_current(self) -> dict[str, Any]:
        """把**正在运行的模型**收进目录。

        场景（owner 实测）：他直接改了运行时配置（或从别处切过来），于是模型管理页
        的列表里根本没有这个模型，页面上显示"未纳入目录"——看起来就像"管理员在用一个
        列表里不存在的模型"。这里一键把它登记成条目（密钥留空 = 继续用全局那把）。
        """
        cfg = self.app
        if cfg is None:
            raise RuntimeError("配置不可用")
        model = str(cfg.llm.model or "").strip()
        if not model:
            raise ValueError("当前没有配置模型，先把 base_url / model / api_key 配好")
        existing = self.db.get_model(make_model_id(cfg.llm.provider or "openai_compat", model))
        if existing is not None:
            return {**existing, **self._public(existing)}
        return self.upsert(provider=cfg.llm.provider or "openai_compat", model=model,
                           base_url=cfg.llm.base_url or "", display_name=model,
                           source="adopted", enabled=True,
                           note="由控制台从运行配置收进目录")

    async def verify_current(self) -> dict[str, Any]:
        """校验"当前模型"是否真的存在于服务商返回的列表里。

        防的是另一种静默失败：配置里写了个拼错/已下线的模型名，调用时才 400，
        而界面上看不出任何异常。
        """
        cfg = self.app
        model = str(getattr(cfg.llm, "model", "") if cfg else "").strip()
        if not model:
            return {"ok": False, "model": "", "error": "当前没有配置模型"}
        if self._fetch_meta is None:
            return {"ok": False, "model": model, "error": "未接入模型列表拉取能力"}
        try:
            details = await self._fetch_meta(cfg.llm.provider, cfg.llm.base_url, cfg.llm.api_key)
        except Exception as e:  # noqa: BLE001 — 校验失败要说原因，不能吞
            return {"ok": False, "model": model, "error": str(e) + fetch_error_hint(e),
                    "hint": fetch_error_hint(e)}
        ids = [str(d.get("id") or "") for d in (details or [])]
        tail = model.split("/")[-1].lower()
        near = [i for i in ids if tail[:6] and tail[:6] in i.lower()][:5]
        return {"ok": model in ids, "model": model, "model_count": len(ids),
                "in_provider_list": model in ids, "similar": near}

    # ------------------------------------------------------------- 写
    def _resolve_id(self, provider: str, model: str, base_url: str,
                    explicit: str = "") -> tuple[str, str]:
        """决定目录条目该用哪个 id；返回 `(id, 端点冲突说明)`。

        显式 id（PATCH/更新已有条目）原样使用。否则 = `make_model_id(provider, model)`
        这个"同名同 provider 只有一条"的旧口径——**这正是覆盖的根源**。
        现在补一条规则：若同名条目已存在、且指向**另一个端点**，就追加 `@<host>`
        另开一条，两条共存（前端用「端点」列/标签区分）。同一个端点的重复新增
        仍然幂等（沿用原条目）。
        """
        if explicit:
            return explicit, ""
        base = make_model_id(provider, model)
        row = self.db.get_model(base)
        if row is None:
            return base, ""
        new_ep = endpoint_of(base_url)
        old_ep = endpoint_of(row.get("base_url") or "")
        if not new_ep or not old_ep or new_ep == old_ep:
            return base, ""
        host = new_ep.split("/")[0] or "alt"
        safe = _safe_id(f"{base}@{host}")
        n = 1
        while True:
            row = self.db.get_model(safe)
            if row is None or endpoint_of(row.get("base_url") or "") == new_ep:
                break
            n += 1
            safe = _safe_id(f"{base}@{host}-{n}")
        return safe, f"同名条目已指向 {old_ep or '(空)'}，本条另存为 {safe}（新端点 {new_ep}）"

    def upsert(self, **fields: Any) -> dict[str, Any]:
        model = str(fields.get("model") or "").strip()
        provider = str(fields.get("provider") or "openai_compat").strip()
        entry_id, conflict = self._resolve_id(
            provider, model, str(fields.get("base_url") or ""),
            str(fields.get("id") or "").strip())
        entry = {k: v for k, v in fields.items() if v is not None}
        entry.pop("_was", None)
        existed = self.db.get_model(entry_id) is not None
        entry["id"] = entry_id
        entry["model"] = model or entry_id
        entry.setdefault("provider", provider)
        # 密钥：留空 = 保持不变；回传掩码 = 保持不变（前端会把掩码原样回传）
        key = str(entry.get("api_key") or "").strip()
        if not key or is_masked(key):
            entry.pop("api_key", None)
        else:
            entry["api_key"] = key
        if "enabled" in entry:
            entry["enabled"] = 1 if entry["enabled"] else 0
        saved = self.db.upsert_model(entry)
        self.db.audit("web", "model:upsert",
                      f"{entry_id} ({'更新' if existed else '新建'}) "
                      f"key={'自带' if saved.get('api_key') else '全局'}"
                      + (f" | {conflict}" if conflict else ""))
        return {**saved, **self._public(saved),
                "endpoint_conflict": bool(conflict), "conflict_detail": conflict}

    def update(self, model_id: str, **fields: Any) -> dict[str, Any]:
        if self.db.get_model(model_id) is None:
            raise KeyError(f"模型不存在：{model_id}")
        patch = {k: v for k, v in fields.items() if v is not None}
        # 密钥留空/回传掩码 → 保持原值（不能把 ********abcd 存成真密钥）
        if "api_key" in patch and (not str(patch["api_key"]).strip() or is_masked(patch["api_key"])):
            patch.pop("api_key")
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
        return {**saved, **self._public(saved)}

    def remove(self, model_id: str) -> bool:
        ok = self.db.delete_model(model_id)
        if ok:
            self.db.audit("web", "model:delete", model_id)
        return ok

    def activate(self, model_id: str) -> dict[str, Any]:
        """把目录里的一条**真正生效**：写入运行时配置并落盘。

        v12：条目自带的密钥也一起切过去——否则跨服务商切模型会带着上一家的 key
        去请求（401），用户还得回设置页手改。
        """
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
        key_switched = False
        if (entry.get("api_key") or "").strip():
            cfg.llm.api_key = entry["api_key"].strip()
            key_switched = True
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
                      f"ctx={cfg.llm.max_context_tokens} key={'自带' if key_switched else '沿用全局'} "
                      f"saved={saved}")
        return {"activated": model_id, "model": entry["model"],
                "provider": cfg.llm.provider, "base_url": cfg.llm.base_url,
                "api_key_switched": key_switched,
                "max_context_tokens": cfg.llm.max_context_tokens, "saved_to": saved}

    # ------------------------------------------------------------- 导入
    async def import_fetched(self, provider: str | None = None, base_url: str | None = None,
                             api_key: str | None = None,
                             only: list[str] | None = None, q: str = "") -> dict[str, Any]:
        """从 provider 拉模型列表并入库。**绝不覆盖已存在的 note**。

        v0.10.12：能拿到服务商给的上下文长度就写进条目（`context_window`）；
        **只填"还没有值"的条目**——用户手填过的数一律不覆盖。
        服务商没给（`0`）就留空，表示"继承服务商/全局默认"，不替它编一个数。

        v0.10.21（owner 实测）：
        * `q` / `only` —— 拉取**过多**是真的（SiliconFlow 一次 99 条）。
          先按关键词过滤，或只导入前端勾选的那些；`filtered_from` 回报原始条数。
        * **不再改已存在条目的 `base_url`/`provider`** —— 以前重拉会把同名条目的
          端点悄悄改成这次的地址，而 key 仍是上一家的 → 之后必然 401。
          端点不同 = 另一条，走 `_resolve_id` 另存（`conflicts` 里回报）。
        * 返回值带 `used_base_url`/`key_source`：让界面能明说"这次是拿**哪个端点、
          哪把 key** 拉的"，不再出现"顺手把默认 key 的模型也拉进来了"这种黑盒。
        """
        if self._fetch_meta is None and self._fetch is None:
            raise RuntimeError("未接入模型列表拉取能力")
        cfg = self.app
        used_provider = provider or (cfg.llm.provider if cfg else "openai_compat")
        used_base = base_url or (cfg.llm.base_url if cfg else "")
        key_source = "entry" if (api_key or "").strip() else "global"
        ctx_of: dict[str, int] = {}
        if self._fetch_meta is not None:
            meta = await self._fetch_meta(provider, base_url, api_key)
            names = [str(m.get("id") or "").strip() for m in (meta or [])]
            ctx_of = {str(m.get("id")): int(m.get("context_window") or 0)
                      for m in (meta or []) if m.get("id")}
        else:
            names = await self._fetch(provider, base_url, api_key)
        names = [n for n in names if n]
        fetched_total = len(names)
        only_set = {str(x).strip() for x in (only or []) if str(x).strip()}
        if only_set:
            names = [n for n in names if n in only_set]
        ql = str(q or "").strip().lower()
        if ql:
            names = [n for n in names if ql in n.lower()]
        existing = {r["id"]: r for r in self.db.list_models()}
        added, refreshed, kept_notes, ctx_filled, conflicts = [], [], 0, 0, []
        for name in names:
            mid, conflict = self._resolve_id(used_provider, name, used_base)
            if conflict:
                # 同名但另一个端点 → 另存一条，**不动原来那条**
                conflicts.append({"model": name, "id": mid, "detail": conflict})
            entry = existing.get(mid)
            ctx = ctx_of.get(name, 0)
            if entry is None:
                new_entry: dict[str, Any] = {"id": mid, "provider": used_provider, "model": name,
                                             "base_url": used_base, "source": "fetched",
                                             "enabled": 1, "display_name": name}
                # 用哪把 key 拉的列表，新条目就带上哪把（跨服务商导入后可直接"设为当前"）
                if (api_key or "").strip():
                    new_entry["api_key"] = api_key.strip()
                if ctx > 0:
                    new_entry["context_window"] = ctx
                    ctx_filled += 1
                self.db.upsert_model(new_entry)
                added.append(name)
            else:
                patch: dict[str, Any] = {"id": mid}
                # 端点/服务商只在**空**的时候补——绝不改写用户已经配好的那条
                if not (entry.get("base_url") or "").strip():
                    patch["base_url"] = used_base
                if not (entry.get("provider") or "").strip():
                    patch["provider"] = used_provider
                if entry.get("source") != "manual":
                    patch["source"] = "fetched"
                if ctx > 0 and not int(entry.get("context_window") or 0):
                    patch["context_window"] = ctx      # 只补空，不覆盖用户填过的值
                    ctx_filled += 1
                self.db.upsert_model(patch)
                refreshed.append(name)
                if (entry.get("note") or "").strip():
                    kept_notes += 1
        self.db.audit("web", "model:import",
                      f"provider={used_provider} base={used_base or '-'} key={key_source} "
                      f"fetched={fetched_total} imported={len(names)} added={len(added)} "
                      f"refreshed={len(refreshed)} ctx_filled={ctx_filled} "
                      f"endpoint_conflicts={len(conflicts)}")
        return {"fetched": len(names), "added": added, "refreshed": refreshed,
                "kept_notes": kept_notes, "context_filled": ctx_filled,
                "provider": used_provider, "base_url": used_base,
                # v0.10.21：把"这次到底用了哪个端点 / 哪把 key / 过滤掉多少"说清楚
                "used_base_url": used_base, "key_source": key_source,
                "filtered_from": fetched_total, "filtered": fetched_total - len(names),
                "conflicts": conflicts}
