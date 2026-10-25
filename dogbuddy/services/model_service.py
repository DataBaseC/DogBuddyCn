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
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from ..config import SECRET_MASK, is_masked_secret, mask_secret

logger = logging.getLogger("dogbuddy.services.models")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

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


class ModelExistsError(ValueError):
    """新增时撞上同名同端点的既有条目 —— **不覆盖**，报错让人决定。"""


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


def key_signature(api_key: str) -> str:
    """密钥的**可读身份后缀**（不是密钥本身）：后 4 位，界面上本来就以掩码显示。

    用于给"同 provider + 同模型 + 同端点、但**换了 key**"的条目起一个能区分的 id
    （owner 实测需求：两个服务商一样、模型一样、key 不同，要能各存一条）。
    """
    raw = "".join(ch for ch in str(api_key or "").strip() if ch.isalnum())
    return raw[-4:] if raw else ""


def key_identity(api_key: str) -> str:
    """密钥的**身份指纹**（判断"是不是同一把 key"用，不泄露密钥）。

    只看后 4 位不够（两把 key 可能同尾），所以身份比较用短哈希；
    id 里则用可读的后 4 位。
    """
    raw = str(api_key or "").strip()
    if not raw:
        return ""
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


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
                 fetch_meta: Callable[..., Any] | None = None,
                 probe: Callable[..., Any] | None = None):
        self.db = db
        self.app = app_config
        self._persist = persist
        self._fetch = fetch_models  # async (provider, base_url, api_key) -> list[str]
        self._fetch_meta = fetch_meta  # async (...) -> [{id, context_window}]（服务商没给就是 0）
        # v0.10.21：真调用探测（= ConfigService.test_llm）——"在列表里"≠"能用"
        self._probe = probe

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
        # v0.10.23：按**身份**找 —— 同名 + 同端点 + 同一把 key。
        # 注意"条目没写 key"= 它用的就是全局那把 key，所以这种条目也算同身份
        # （否则每次收编都会因为 id 带了后缀而再造一条重复条目）。
        cur_ep = endpoint_of(cfg.llm.base_url or "")
        cur_kid = key_identity(cfg.llm.api_key or "")
        existing = None
        for row in self.db.list_models():
            if str(row.get("model") or "") != model:
                continue
            row_ep = endpoint_of(row.get("base_url") or "")
            if row_ep and cur_ep and row_ep != cur_ep:
                continue
            row_kid = key_identity(row.get("api_key") or "")
            if not row_kid or row_kid == cur_kid:
                existing = row
                break
        if existing is None:
            existing = self.db.get_model(
                make_model_id(cfg.llm.provider or "openai_compat", model))
        if existing is not None:
            return {**existing, **self._public(existing)}
        # 不复制密钥：条目留空 = 继续用全局那把（原语义，别改）
        return self.upsert(provider=cfg.llm.provider or "openai_compat", model=model,
                           base_url=cfg.llm.base_url or "",
                           display_name=model,
                           source="adopted", enabled=True,
                           note="由控制台从运行配置收进目录")

    async def verify_current(self, probe: bool = False) -> dict[str, Any]:
        """校验"当前模型"：只在服务商列表里够不够？**不够**。

        防的是另一种静默失败：配置里写了个拼错/已下线的模型名，调用时才 400，
        而界面上看不出任何异常。

        v0.10.21（owner 实测 ds4.1）：还有一种更坑的情况——模型**在**列表里，
        但这把 key 的套餐不含它，一调就是
        `403 permission_denied_error: model is not available in the current token plan`。
        列表校验会显示绿灯，等真跑任务才炸。所以 `probe=True` 时**再发一个 1-token
        的真调用**，把"能不能用"也验掉（前端「验证」按钮走这条）。
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
        out: dict[str, Any] = {"ok": model in ids, "model": model, "model_count": len(ids),
                               "in_provider_list": model in ids, "similar": near}
        if probe and self._probe is not None:
            t0 = time.monotonic()
            try:
                res = await self._probe(cfg.llm.provider, cfg.llm.base_url,
                                        cfg.llm.api_key, model)
            except Exception as e:  # noqa: BLE001 — 探测失败要说原因
                out["probe"] = {"ok": False, "error": str(e),
                                "latency_ms": int((time.monotonic() - t0) * 1000)}
                out["usable"] = False
                return out
            chat_err = str(res.get("chat_error") or "")
            ok = bool(res.get("ok")) and not chat_err
            out["probe"] = {"ok": ok, "latency_ms": res.get("latency_ms"),
                            "sample": str(res.get("chat_sample") or "")[:80],
                            "error": chat_err or ("" if ok else str(res.get("message") or ""))}
            out["usable"] = ok
            # 在列表里 + 真调用失败 = 最需要喊出来的那种情况
            if out["in_provider_list"] and not ok:
                out["warning"] = ("模型在服务商列表里，但**真调用被拒**："
                                  f"{chat_err or res.get('message')}。"
                                  "常见原因：套餐/额度不含该模型（如 SenseNova 的 "
                                  "`model is not available in the current token plan`）、"
                                  "或该模型未开通。列表校验看不出这个。")
        return out

    # ------------------------------------------------------------- 写
    def _identity_of(self, row: dict[str, Any], model: str, endpoint: str,
                     key_id: str, match_keys: bool = True) -> bool:
        """一条目录条目是不是"同一个身份"。

        * `match_keys=True`（**手动新增/更新**）：同名 + **同端点** + 同密钥。
          这是 owner 要的口径——同一个服务商、同一个模型、两把 key 要能各存一条。
        * `match_keys=False`（**导入同步**）：只要求同名 + 端点一致（原条目没写端点
          也算命中）。导入是"把服务商的列表同步进来"，不该因为 key 不同就再造一条，
          也不该把用户手写的备注挤掉。
        """
        if str(row.get("model") or "") != model:
            return False
        row_ep = endpoint_of(row.get("base_url") or "")
        if not match_keys:
            return (not row_ep) or (not endpoint) or row_ep == endpoint
        if row_ep != endpoint:
            return False
        return key_identity(row.get("api_key") or "") == key_id

    def find_by_identity(self, model: str, base_url: str, api_key: str) -> dict | None:
        """按"同名+同端点+同密钥"找条目（收编/新增都用它，避免造重复条目）。"""
        ep = endpoint_of(base_url)
        kid = key_identity(api_key)
        for row in self.db.list_models():
            if self._identity_of(row, model, ep, kid):
                return row
        return None

    def _resolve_id(self, provider: str, model: str, base_url: str,
                    api_key: str = "", explicit: str = "",
                    match_keys: bool = True) -> tuple[str, str]:
        """决定目录条目该用哪个 id；返回 `(id, 另存说明)`。

        **身份 = 模型名 + 端点 + 密钥**（owner 三次实测才收敛的口径）：
        * 0.10.21 之前：id 只看 `provider+model` → 同名就覆盖（换地址也覆盖）。
        * 0.10.21：加上端点 → 换地址可共存；但**同端点换 key 仍被当成"同一条"**，
          于是"两个服务商一样、模型一样、key 不同"存不进去（owner 第三次指出）。
        * 0.10.23：身份再补上**密钥指纹** —— 同端点不同 key = 两条，新条目 id 带
          `@端点` / `-k<key 尾 4 位>`，人能一眼区分；完全同身份才复用（幂等更新）。
        """
        if explicit:
            return explicit, ""
        base = make_model_id(provider, model)
        new_ep = endpoint_of(base_url)
        new_kid = key_identity(api_key)
        same_model = [r for r in self.db.list_models()
                      if str(r.get("model") or "") == model]
        if not same_model:
            return base, ""
        # 1) 同身份 → 复用该条目（更新语义）
        for row in same_model:
            if self._identity_of(row, model, new_ep, new_kid, match_keys=match_keys):
                return str(row["id"]), ""
        # 2) 身份不同 → 造一个能区分的新 id：@端点 + -k<密钥尾>
        #    注意分隔符必须**路径安全**：`#` 会在 URL 里被当 fragment 截断
        #    （`PATCH /api/models/{id}` 直接 404），所以用 `@`（pchar 合法）与 `-k`。
        ref = same_model[0]
        parts = [base]
        if endpoint_of(ref.get("base_url") or "") != new_ep:
            parts.append("@" + (new_ep.split("/")[0] or "alt"))
        if key_identity(ref.get("api_key") or "") != new_kid:
            parts.append("-k" + (key_signature(api_key) or "global"))
        cand = _safe_id("".join(parts))
        n = 1
        while True:
            row = self.db.get_model(cand)
            if row is None or self._identity_of(row, model, new_ep, new_kid):
                break  # 空位，或同身份已存在（幂等）
            n += 1
            cand = _safe_id("".join(parts) + f"-{n}")
        return cand, (f"同名条目已有别的身份（端点 "
                      f"{endpoint_of(ref.get('base_url') or '') or '—'}，密钥 "
                      f"…{key_signature(ref.get('api_key') or '') or '全局'}）→ 本条另存为 {cand}")

    # ------------------------------------------------- 导出 / 粘贴导入（v0.10.57）
    #: 导出**只带这些字段**（白名单，不是黑名单）——新增字段默认不外泄。
    #: 密钥永远不在其中：只给 `has_api_key`；若库里存的是 `env:VAR` / `${VAR}`
    #: 这种引用形式，额外给 `api_key_ref`（名字，不是值）。
    _EXPORT_FIELDS = ("provider", "model", "base_url", "display_name", "note",
                      "capability", "context_window", "speed", "cost",
                      "enabled", "source")
    #: 两种引用写法：`env:NAME` 与 `${NAME}` / `$NAME`（导出只给名字，永不给值）
    _REF_ENV_RX = re.compile(r"^env:([A-Za-z_][A-Za-z0-9_]*)$")
    _REF_DOLLAR_RX = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")

    @classmethod
    def _ref_name(cls, raw: str) -> str:
        """`env:FOO` / `${FOO}` / `$FOO` → `FOO`；不是引用 → 空串。"""
        text = str(raw or "").strip()
        m = cls._REF_ENV_RX.match(text) or cls._REF_DOLLAR_RX.match(text)
        return m.group(1) if m else ""

    @classmethod
    def _env_ref(cls, raw: str) -> str:
        """库里存的是不是环境变量引用？是就返回 `env:NAME`（**只有名字**）。"""
        name = cls._ref_name(raw)
        return f"env:{name}" if name else ""

    def export_models(self) -> dict[str, Any]:
        """把模型目录导成可粘贴的 JSON —— **绝不包含任何明文密钥**。

        用途（owner）：换机器/换环境时不用一条条手填，导出一次、下次粘回去。
        因此导出物必须"能公开"：只给 `has_api_key`（有没有配），以及
        `api_key_ref`（若原来是 env 引用，给变量名）。真要带密钥的场景，
        请走密码管理器/环境变量，不要走这份 JSON。
        """
        items: list[dict[str, Any]] = []
        for row in self.db.list_models():
            item = {k: row.get(k) for k in self._EXPORT_FIELDS if k in row}
            raw = str(row.get("api_key") or "")
            item["has_api_key"] = bool(raw.strip())
            ref = self._env_ref(raw)
            if ref:
                item["api_key_ref"] = ref
            items.append(item)
        return {
            "kind": "dogbuddy.models", "version": 1,
            "exported_at": _now_iso(),
            "count": len(items),
            "note": "不含密钥；api_key_ref 只是环境变量名。导入后未配置密钥的条目会沿用同名条目已有的密钥。",
            "models": items,
        }

    def _resolve_import_key(self, raw: dict[str, Any]) -> tuple[str, str]:
        """返回 (要写入的密钥, 说明)。空串 = 不改（沿用库里已有的）。

        三种来源，优先级从高到低：
          1. `api_key_ref`（`env:NAME` / `${NAME}`）→ 从环境变量取（**值不进日志**）
          2. `api_key` 明文
          3. 掩码（`sk-***abcd`）或不给 → 返回空 = 保持不变
        """
        ref = str(raw.get("api_key_ref") or "").strip()
        if ref:
            name = self._ref_name(ref)
            if not name:
                return "", f"api_key_ref 格式不对：{ref[:24]}"
            val = str(os.environ.get(name, "") or "").strip()
            if not val:
                return "", f"环境变量 {name} 没设置"
            return val, f"key←env:{name}"
        key = str(raw.get("api_key") or "").strip()
        if not key or is_masked(key):
            return "", ""
        return key, "key←明文"

    def _match_import_target(self, fields: dict[str, Any], key: str) -> str:
        """把一条导入项对到已有条目上（对不到就返回空 = 新建）。

        为什么要自己找：`upsert` 的条目身份 = **模型名 + 端点 + 密钥指纹**（0.10.23
        为了支持"同端点两把 key 各存一条"）。而导出的 JSON **不带密钥**，所以直接
        upsert 会算出"新身份"→ 凭空多一条重复条目（还会让旧条目的 key 看起来"丢了"）。
        这里按"模型名 + 端点"放宽匹配：
          * 给了密钥 → 优先密钥指纹相同的；
          * 只剩一条候选 → 就用它（更新语义，key 保留）；
          * 多条且无法用 key 区分 → 挑唯一"没配 key"的那条，否则判歧义不写。
        """
        model = str(fields.get("model") or "").strip()
        ep = endpoint_of(str(fields.get("base_url") or ""))
        cands = []
        for row in self.db.list_models():
            if str(row.get("model") or "").strip() != model:
                continue
            row_ep = endpoint_of(str(row.get("base_url") or ""))
            if ep and row_ep and row_ep != ep:
                continue
            cands.append(row)
        if not cands:
            return ""
        if key:
            kid = key_identity(key)
            for row in cands:
                if key_identity(str(row.get("api_key") or "")) == kid:
                    return str(row["id"])
        if len(cands) == 1:
            return str(cands[0]["id"])
        empty = [r for r in cands if not str(r.get("api_key") or "").strip()]
        return str(empty[0]["id"]) if len(empty) == 1 else ""

    def import_models(self, models: list[dict[str, Any]], mode: str = "merge") -> dict[str, Any]:
        """粘贴导入。`merge` = 按条目身份更新/新增；`replace` = 额外删掉清单外的条目。

        复用 `upsert()` 的既有语义（空/掩码密钥 = 不改动），所以**导入一份导出的
        JSON 不会把已有密钥冲掉**——这一点是这次改动的核心安全属性。
        """
        mode = "replace" if str(mode).lower() == "replace" else "merge"
        if not isinstance(models, list) or not models:
            raise ValueError("models 必须是非空数组")
        result: dict[str, Any] = {"mode": mode, "input": len(models), "created": 0,
                                 "updated": 0, "skipped": 0, "need_key": 0,
                                 "errors": [], "warnings": []}
        touched: set[str] = set()
        before_ids = {str(r.get("id")) for r in self.db.list_models()}
        for idx, raw in enumerate(models, 1):
            if not isinstance(raw, dict):
                result["errors"].append(f"第 {idx} 条不是对象，已跳过")
                continue
            model = str(raw.get("model") or "").strip()
            if not model:
                result["errors"].append(f"第 {idx} 条缺 model 字段，已跳过")
                continue
            fields = {k: raw.get(k) for k in self._EXPORT_FIELDS
                      if k in raw and raw.get(k) is not None}
            key, why = self._resolve_import_key(raw)
            if key:
                fields["api_key"] = key
            elif why:
                result["warnings"].append(f"{model}：{why}")
            target = self._match_import_target(fields, key)
            if target:
                fields["id"] = target           # 对到已有条目 → 更新（key 不传即保留）
            try:
                entry = self.upsert(**fields)
            except Exception as e:  # noqa: BLE001 — 单条失败不拦整批
                result["errors"].append(f"{model}：{type(e).__name__}: {e}")
                continue
            eid = str(entry.get("id") or "")
            touched.add(eid)
            if eid in before_ids:
                result["updated"] += 1
            else:
                result["created"] += 1
            if not str(entry.get("api_key") or "").strip():
                result["need_key"] += 1
        if mode == "replace":
            for row in self.db.list_models():
                rid = str(row.get("id") or "")
                if rid and rid not in touched:
                    self.db.delete_model(rid)
                    result["skipped"] += 1
        try:
            self.db.audit("system", "models:import",
                          f"mode={mode} in={result['input']} new={result['created']} "
                          f"upd={result['updated']} del={result['skipped']} "
                          f"need_key={result['need_key']}")
        except Exception:  # noqa: BLE001 — 审计失败不影响导入结果
            logger.debug("models import audit failed", exc_info=True)
        return result

    def upsert(self, **fields: Any) -> dict[str, Any]:
        model = str(fields.get("model") or "").strip()
        provider = str(fields.get("provider") or "openai_compat").strip()
        raw_key = str(fields.get("api_key") or "").strip()
        entry_id, conflict = self._resolve_id(
            provider, model, str(fields.get("base_url") or ""),
            "" if (not raw_key or is_masked(raw_key)) else raw_key,
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

    def create(self, **fields: Any) -> dict[str, Any]:
        """**新增**：绝不静默覆盖同名条目（owner 实测三次，口径收敛到"身份"）。

        身份 = **模型名 + 端点 + 密钥**：
        * 0.10.21 只解决了"同名但不同端点"；**同名同端点**仍走 upsert 的更新语义，
          把第一条的备注/密钥/参数悄悄盖掉（复现：HTTP 200、条数不变、note 变新值）；
        * owner 第三次指出真正想要的是：**同一个服务商、同一个模型、两把 key，各存一条**。
          所以身份必须带上密钥：完全同身份 ⇒ 409；同端点换 key ⇒ 各存一条
          （新条目 id 形如 `...@host#<key尾4>`）；换端点 ⇒ 也各存一条。
        要改某一条走 `PATCH /models/{id}`（页面上是「备注」按钮）。
        """
        model = str(fields.get("model") or "").strip()
        provider = str(fields.get("provider") or "openai_compat").strip()
        if not model:
            raise ValueError("model 不能为空")
        base_url = str(fields.get("base_url") or "")
        api_key = str(fields.get("api_key") or "").strip()
        mid, conflict = self._resolve_id(provider, model, base_url, api_key)
        if conflict:
            # 身份不同（换了 key 或换了端点）→ **另存一条**，不是覆盖
            return self.upsert(**fields)
        existing = self.db.get_model(mid)
        if existing is not None:
            tail = key_signature(existing.get("api_key") or "")
            raise ModelExistsError(
                f"目录里已经有**完全同身份**的条目「{mid}」"
                f"（端点 {endpoint_of(existing.get('base_url') or '') or '—'}，"
                f"密钥 {'自带 …' + tail if tail else '沿用全局'}，"
                f"备注：{(existing.get('note') or '（空）')[:40]}）。"
                f"新增不会覆盖它：要改配置请点那一行的「备注」编辑；"
                f"要**同一个模型用另一把 key**，请在新增时把 API Key 填成另一把"
                f"（端点相同、key 不同会自动各存一条）；换个 API 地址也会另存一条。")
        return self.upsert(**fields)

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
            # 导入 = "同步服务商列表"：同名同端点**不因 key 不同而重复建条目**
            # （match_keys=False），也不动已存在条目的备注/密钥/端点。
            mid, conflict = self._resolve_id(used_provider, name, used_base,
                                             api_key or "", match_keys=False)
            if conflict:
                # 同名但身份不同（端点/key 不同）→ 另存一条，**不动原来那条**
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
