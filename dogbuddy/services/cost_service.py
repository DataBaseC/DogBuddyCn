# DogBuddyCn — 成本账本与单价换算（0.11.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""把"钱"变成可查的数字（docs/工业自动化设计.md §4.1）。

为什么要这一层，而不是在工具里各算各的：
  * **换算口径只有一个**（每 1K token 单价、缓存命中折扣、图片按张）——散在各处必然对不上；
  * **幂等**：`run_retry` 与群组步骤续跑会重放调用，没有 `idem_key` 就会重复计费；
  * **改价不回算**：账本行自带 `unit_price`/`amount`，单价表改了只影响之后的调用。

单位约定（写死在这里，避免各处解释）：
  * `in_price` / `out_price` / `cache_price` 都是**每 1000 token** 的价格；
  * `unit='image'` 时 `in_price` 表示**每张图**的价格（视觉模型）；
  * 金额与币种跟 `currency` 走，默认 CNY，不做汇率换算。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("dogbuddy.services.cost")

#: 模型目录里的 `cost` 档位 → 占位单价（每 1K token，CNY）。
#: 这不是真实价目表，只是让"哪个贵哪个便宜"先有排序；控制台标注「待校准」，
#: 一周后按真实账单校准（docs/工业自动化设计.md §9）。
COST_TIER_PRICES: dict[str, tuple[float, float]] = {
    "low": (0.001, 0.002),
    "medium": (0.004, 0.012),
    "high": (0.02, 0.06),
}
DEFAULT_TIER = "medium"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))).isoformat()


class CostService:
    """账本读写 + 单价换算 + 汇总。DAO 只管存，口径在这里。"""

    def __init__(self, db: Any):
        self.db = db

    # ------------------------------------------------------------- 单价
    def price_for(self, provider: str = "", model: str = "") -> dict[str, Any]:
        """按 `provider:model` → `model` → 任意同 provider → 空（全 0）回退取价。"""
        rows = self.db.list_cost_models()
        model = str(model or "").strip()
        provider = str(provider or "").strip()
        exact = f"{provider}:{model}" if provider else model
        for want in (exact, model):
            for r in rows:
                if str(r.get("id")) == want and int(r.get("enabled") or 0):
                    return r
        for r in rows:
            if (provider and str(r.get("provider")) == provider
                    and str(r.get("model")) == model and int(r.get("enabled") or 0)):
                return r
        # 最后兜底：只按 model 名匹配已启用行（调用方拿不到 provider 时也能算钱）
        for r in rows:
            if model and str(r.get("model")) == model and int(r.get("enabled") or 0):
                return r
        return {"id": "", "provider": provider, "model": model, "unit": "token",
                "in_price": 0.0, "out_price": 0.0, "cache_price": 0.0,
                "currency": "CNY", "note": "", "enabled": 0}

    def amount_for(self, provider: str, model: str, in_tokens: int = 0,
                   out_tokens: int = 0, cache_hit_tokens: int = 0,
                   images: int = 0) -> tuple[float, float]:
        """返回 `(金额, 用于记账的单价)`。

        缓存命中按"少付"处理：`in_price - cache_price`，且不低于 0
        （不同厂商口径不一，这样至少不会算出负数）。
        """
        p = self.price_for(provider, model)
        in_price = float(p.get("in_price") or 0)
        out_price = float(p.get("out_price") or 0)
        cache_price = float(p.get("cache_price") or 0)
        hit = max(0, min(int(cache_hit_tokens or 0), int(in_tokens or 0)))
        miss = max(0, int(in_tokens or 0) - hit)
        amount = (miss / 1000.0) * in_price
        amount += (hit / 1000.0) * max(0.0, in_price - cache_price)
        amount += (int(out_tokens or 0) / 1000.0) * out_price
        amount += int(images or 0) * in_price          # unit=image 时按张计价
        return round(amount, 8), in_price

    def upsert_price(self, entry: dict[str, Any]) -> dict[str, Any]:
        """改单价：**必须留审计**（单价配错会让调度决策整个偏掉）。"""
        model = str(entry.get("model") or "").strip()
        if not model:
            raise ValueError("model 不能为空")
        provider = str(entry.get("provider") or "").strip()
        entry = dict(entry)
        entry["id"] = str(entry.get("id") or (f"{provider}:{model}" if provider else model))
        entry.setdefault("unit", "token")
        for key in ("in_price", "out_price", "cache_price"):
            if key in entry and entry[key] is not None:
                val = float(entry[key])
                if val < 0:
                    raise ValueError(f"{key} 不能为负")
                entry[key] = val
        saved = self.db.upsert_cost_model(entry)
        try:
            self.db.audit("web", "cost:price",
                          f"{saved.get('id')} in={saved.get('in_price')} "
                          f"out={saved.get('out_price')} cache={saved.get('cache_price')} "
                          f"unit={saved.get('unit')} 待校准={saved.get('note') or '-'}")
        except Exception:  # noqa: BLE001 — 审计失败不影响保存
            logger.debug("cost price audit failed", exc_info=True)
        return saved

    def seed_from_models(self, models: list[dict[str, Any]]) -> dict[str, Any]:
        """按模型目录的 `cost` 档位生成占位单价（已存在的不覆盖）。

        目的不是"猜价"，而是**先让贵/便宜有排序**：没有排序，分级路由无从谈起。
        """
        created, skipped = 0, 0
        for m in models or []:
            model = str(m.get("model") or "").strip()
            if not model:
                continue
            provider = str(m.get("provider") or "").strip()
            mid = f"{provider}:{model}" if provider else model
            if self.db.get_cost_model(mid) is not None:
                skipped += 1
                continue
            tier = str(m.get("cost") or DEFAULT_TIER).strip().lower()
            in_price, out_price = COST_TIER_PRICES.get(tier, COST_TIER_PRICES[DEFAULT_TIER])
            self.db.upsert_cost_model({
                "id": mid, "provider": provider, "model": model, "unit": "token",
                "in_price": in_price, "out_price": out_price,
                "cache_price": round(in_price * 0.25, 6),
                "currency": "CNY", "effective_from": _now(),
                "note": f"按档位 {tier} 生成的占位价，待校准",
                "enabled": 1,
            })
            created += 1
        try:
            self.db.audit("system", "cost:seed", f"created={created} skipped={skipped}")
        except Exception:  # noqa: BLE001
            logger.debug("cost seed audit failed", exc_info=True)
        return {"created": created, "skipped": skipped}

    # ------------------------------------------------------------- 记账
    def record(self, *, run_id: str = "", job_id: str = "", group_id: str = "",
               window_id: str = "", provider: str = "", model: str = "",
               in_tokens: int = 0, out_tokens: int = 0, cache_hit_tokens: int = 0,
               images: int = 0, kind: str = "llm", idem_key: str = "",
               meta: dict[str, Any] | None = None) -> dict[str, Any]:
        """记一笔。`idem_key` 命中时返回 `{"stored": False}`（不重复计费）。"""
        amount, unit_price = self.amount_for(provider, model, in_tokens, out_tokens,
                                             cache_hit_tokens, images)
        row = {
            "ts": _now(), "kind": kind, "run_id": run_id, "job_id": job_id,
            "group_id": group_id, "window_id": window_id, "provider": provider,
            "model": model, "in_tokens": int(in_tokens or 0),
            "out_tokens": int(out_tokens or 0),
            "cache_hit_tokens": int(cache_hit_tokens or 0),
            "images": int(images or 0), "unit_price": unit_price,
            "amount": amount, "idem_key": idem_key,
            "meta_json": _dumps(meta or {}),
        }
        stored = self.db.record_usage(row)
        if stored and amount:
            logger.debug("cost +%.6f %s/%s (%s)", amount, provider, model, kind)
        # 不静默：记了 token 却算不出钱，说明单价表没覆盖这个模型 —— 必须吵一声，
        # 否则成本页会一直显示 0，看着像"免费"。
        if stored and not amount and (in_tokens or out_tokens or images):
            logger.warning("cost: 未匹配单价，本次金额记 0（model=%r provider=%r）"
                           "——去「成本」页补这条单价", model, provider)
        return {"stored": stored, "amount": amount, "unit_price": unit_price}

    # ------------------------------------------------------------- 查询
    # ------------------------------------------------------- 预算（v0.11.1）
    BUDGET_ACTIONS = ("downgrade", "queue", "pause")
    BUDGET_PERIODS = ("day", "week")
    BUDGET_SCOPES = ("global", "job", "group", "window", "run")

    def budgets(self) -> list[dict[str, Any]]:
        return self.db.list_budgets()

    def upsert_budget(self, entry: dict[str, Any]) -> dict[str, Any]:
        scope = str(entry.get("scope_type") or "global")
        period = str(entry.get("period") or "day")
        action = str(entry.get("strategy") or "downgrade")
        limit = float(entry.get("limit_amount") or 0)
        if scope not in self.BUDGET_SCOPES:
            raise ValueError(f"scope_type 必须是 {self.BUDGET_SCOPES} 之一")
        if period not in self.BUDGET_PERIODS:
            raise ValueError(f"period 必须是 {self.BUDGET_PERIODS} 之一")
        if action not in self.BUDGET_ACTIONS:
            raise ValueError(f"strategy 必须是 {self.BUDGET_ACTIONS} 之一")
        if limit < 0:
            raise ValueError("limit_amount 不能为负")
        scope_id = str(entry.get("scope_id") or "")
        if scope != "global" and not scope_id:
            raise ValueError(f"{scope} 预算必须给 scope_id")
        bid = str(entry.get("id") or "") or f"{scope}:{scope_id or '*'}"
        saved = self.db.upsert_budget({"id": bid, "scope_type": scope,
                                       "scope_id": scope_id, "period": period,
                                       "limit_amount": limit, "strategy": action,
                                       "enabled": 1 if entry.get("enabled", True) else 0})
        self.db.audit("system", "cost:budget",
                      f"{bid} {period} limit={limit} action={action}")
        return saved

    def delete_budget(self, bid: str) -> bool:
        ok = self.db.delete_budget(bid)
        if ok:
            self.db.audit("system", "cost:budget:delete", str(bid))
        return ok

    @staticmethod
    def _period_start(period: str) -> str:
        now = datetime.now(timezone.utc)
        if period == "week":
            return (now - timedelta(days=7)).isoformat()
        return now.strftime("%Y-%m-%dT00:00:00+00:00")

    def cheaper_model(self, current: str = "") -> str:
        """单价表里比当前更便宜的模型（给 `downgrade` 用）。没有就回空。

        只按**输出单价**排序（输出比输入贵得多，是主要成本项），并且必须满足：
        * 与当前模型**同端点**（`models` 目录里的 base_url host 相同）——
          降档不能跨服务商/跨 key，否则换个端点就等于换了一把不存在的钥匙，
          调用直接 404/401。这条是"降档"与"换供应商"的边界；
        * 在目录里处于启用状态。
        任何一条不满足就当作"没有更便宜的档位"，由调用方按暂停处理。
        """
        from .model_service import endpoint_of

        catalog = {str(m.get("model") or ""): m for m in self.db.list_models(enabled_only=True)}
        cur_entry = catalog.get(current)
        cur_ep = endpoint_of(str((cur_entry or {}).get("base_url") or ""))
        rows = [r for r in self.db.list_cost_models()
                if int(r.get("enabled") or 0) and float(r.get("out_price") or 0) > 0]
        if not rows:
            return ""
        rows.sort(key=lambda r: (float(r.get("out_price") or 0),
                                 float(r.get("in_price") or 0)))
        cur_price = next((float(r.get("out_price") or 0) for r in rows
                          if str(r.get("model") or "") == current), None)
        for r in rows:
            mid = str(r.get("model") or "")
            if not mid or mid == current:
                continue
            entry = catalog.get(mid)
            if entry is None:
                continue                      # 目录里没有 → 不知道它跑哪个端点
            if cur_ep and endpoint_of(str(entry.get("base_url") or "")) != cur_ep:
                continue                      # 跨端点 = 换供应商，不许
            if cur_price is None or float(r.get("out_price") or 0) < cur_price:
                return mid
        return ""

    _SCOPE_KEYS = {"job": "job_id", "group": "group_id",
                   "window": "window_id", "run": "run_id"}

    def _scope_spend(self, stype: str, period: str, ids: dict[str, str]) -> float:
        """某作用域在这个周期内花了多少（账本就是唯一真相）。"""
        key = self._SCOPE_KEYS.get(stype)
        since = self._period_start(period)
        if key is None:
            return float(self.db.spend_since(since)["amount"])
        val = ids.get(key) or ""
        if not val:
            return 0.0
        return float(self.db.spend_since(since, **{key: val})["amount"])

    def budget_states(self, *, window_id: str = "", run_id: str = "",
                      job_id: str = "", group_id: str = "") -> list[dict[str, Any]]:
        """所有预算的实时用量（控制台画进度条用，不参与判定）。"""
        ids = {"window_id": window_id, "run_id": run_id,
               "job_id": job_id, "group_id": group_id}
        out: list[dict[str, Any]] = []
        for b in self.db.list_budgets():
            stype = str(b.get("scope_type") or "global")
            period = str(b.get("period") or "day")
            limit = float(b.get("limit_amount") or 0)
            spent = self._scope_spend(stype, period, ids) if limit > 0 else 0.0
            ratio = (spent / limit) if limit else 0.0
            out.append({**b, "spent": round(spent, 6), "ratio": round(ratio, 4),
                        "level": ("exceeded" if ratio >= 1 else
                                  "warn" if ratio >= 0.8 else "ok")})
        return out

    def budget_state(self, *, window_id: str = "", run_id: str = "",
                     job_id: str = "", group_id: str = "",
                     current_model: str = "") -> dict[str, Any]:
        """这一轮还能不能花钱。返回最严重的那个预算判定。

        * 只看 `enabled=1` 且 `limit_amount > 0` 的预算；
        * 作用域：global 不筛；job/group/window/run 按各自主键筛账本；
        * **取比值最大的那条**（不是最先命中的）：这样"窗口预算已超 120%"不会被
          一条宽松的全局预算掩盖；
        * 到 80% 就 `warn`（前端提示），超过 100% 才 `exceeded`。
        """
        budgets = [b for b in self.db.list_budgets() if int(b.get("enabled") or 0)]
        base = {"level": "ok", "exceeded": False, "action": "", "scope": "",
                "scope_id": "", "period": "", "limit": 0.0, "spent": 0.0,
                "ratio": 0.0, "suggest_model": "", "reason": ""}
        if not budgets:
            return base
        # 两个映射别混：`scope_ids` 是"作用域类型 → 值"（判定用），
        # `ids` 是"账本列名 → 值"（查账用）。混用会让窗口预算永远算成 0（踩过）。
        scope_ids = {"global": "", "job": job_id, "group": group_id,
                     "window": window_id, "run": run_id}
        ids = {"window_id": window_id, "run_id": run_id,
               "job_id": job_id, "group_id": group_id}
        best: dict[str, Any] | None = None
        for b in budgets:
            stype = str(b.get("scope_type") or "global")
            if stype not in scope_ids:
                continue
            limit = float(b.get("limit_amount") or 0)
            if limit <= 0:
                continue
            sid = str(b.get("scope_id") or "")
            scoped = scope_ids[stype]
            if stype != "global":
                if not scoped:
                    continue
                if sid and sid != scoped:
                    continue
            period = str(b.get("period") or "day")
            spent = self._scope_spend(stype, period, ids)
            ratio = spent / limit if limit else 0.0
            entry = {**base, "scope": stype, "scope_id": sid, "period": period,
                     "limit": round(limit, 6), "spent": round(spent, 6),
                     "ratio": round(ratio, 4),
                     "action": str(b.get("strategy") or "downgrade")}
            if ratio >= 1:
                entry["level"] = "exceeded"
                entry["exceeded"] = True
                entry["reason"] = f"{stype} 预算用尽：已用 {spent:.4f} / {limit:.4f}（{period}）"
                if entry["action"] == "downgrade":
                    entry["suggest_model"] = self.cheaper_model(current_model)
                    if not entry["suggest_model"]:
                        entry["reason"] += "；单价表里没有更便宜的档位，按暂停处理"
                if best is None or ratio > float(best.get("ratio") or 0):
                    best = entry
            elif ratio >= 0.8:
                entry["level"] = "warn"
                entry["reason"] = f"{stype} 预算已用 {ratio * 100:.0f}%"
                if best is None or float(best.get("ratio") or 0) < ratio:
                    if best is None or not best.get("exceeded"):
                        best = entry
        return best or base

    def summary(self, days: int = 7, group_by: str = "day") -> dict[str, Any]:
        since = _since(days)
        today = _since(0)
        buckets = self.db.usage_summary(since=since, group_by=group_by)
        total = self.db.usage_totals(since=since)
        day_total = self.db.usage_totals(since=today[:10] + "T00:00:00+00:00")
        by_window = self.db.usage_summary(since=since, group_by="window")
        by_model = self.db.usage_summary(since=since, group_by="model")
        return {
            "days": int(days),
            "group_by": group_by,
            "since": since,
            "buckets": buckets,
            "total": _money(total),
            "today": _money(day_total),
            "by_window": by_window,
            "by_model": by_model,
            "priced_models": len([r for r in self.db.list_cost_models()
                                  if int(r.get("enabled") or 0)]),
            # v0.11.1：预算现状（控制台画进度条、判断"今天还能不能跑"）
            "budgets": self.budget_states(),
        }

    def ledger(self, since: str = "", until: str = "", limit: int = 200) -> list[dict]:
        rows = self.db.usage_rows(since=since, until=until, limit=limit)
        for r in rows:
            r.pop("meta_json", None)      # 明细页不需要，也避免把大字段带出去
        return rows

    def health(self) -> dict[str, Any]:
        """`/healthz.costs`：今天花了多少、有没有配单价。"""
        today = _money(self.db.usage_totals(since=_since(0)[:10] + "T00:00:00+00:00"))
        prices = self.db.list_cost_models()
        unpriced = [r for r in prices if not float(r.get("in_price") or 0)
                    and not float(r.get("out_price") or 0)]
        budgets = self.budget_states()
        worst = max(budgets, key=lambda b: float(b.get("ratio") or 0)) if budgets else None
        return {
            "today_amount": round(float(today.get("amount") or 0), 6),
            "today_calls": int(today.get("calls") or 0),
            "today_tokens": int(today.get("in_tokens") or 0) + int(today.get("out_tokens") or 0),
            "priced_models": len(prices),
            "zero_priced": len(unpriced),
            # v0.11.1：预算到哪了（"今天还能不能跑"必须能一眼看到）
            # 注意字段名：`budget_states()` 返回的是库里的列名（scope_type/limit_amount），
            # 别直接拿 `scope`/`limit` —— 生产实测这里一度是 None（键名对不上）。
            "budget": ({"level": worst.get("level"),
                        "scope": worst.get("scope_type"),
                        "scope_id": worst.get("scope_id") or "",
                        "period": worst.get("period"),
                        "limit": worst.get("limit_amount"),
                        "spent": worst.get("spent"),
                        "ratio": worst.get("ratio")} if worst else None),
            "hint": ("单价表是空的：去「成本」页点「按目录生成占位价」，"
                     "或先只统计用量（金额会是 0）") if not prices else "",
        }


def _money(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "calls": int(row.get("calls") or 0),
        "in_tokens": int(row.get("in_tokens") or 0),
        "out_tokens": int(row.get("out_tokens") or 0),
        "cache_tokens": int(row.get("cache_tokens") or 0),
        "images": int(row.get("images") or 0),
        "amount": round(float(row.get("amount") or 0), 6),
    }


def _dumps(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False)[:2000]
    except Exception:  # noqa: BLE001
        return "{}"
