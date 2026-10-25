# DogBuddyCn — 模型分级路由（0.12.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""便宜的先用，翻车再升级 —— 但只在**同端点**、**有样本**、**划算**时才动。

为什么需要它：0.11.x 的预算闸门只会在"钱花完了"时降档或暂停，属于**事后**；
真正省钱的是**事前**选档——大多数任务用便宜模型就够，少数才值得上贵档。

三条硬规则（都是踩过的）：
  1. **同端点**：换模型不能跨服务商/跨 key，否则换过去就是 401/404
     （0.11.1 实测抓到 `cheaper_model` 跨端点的问题，这里复用同一判据）。
  2. **冷启动只降不升**：样本 < `MIN_SAMPLES` 时不做"升级"决策——
     否则一个偶然失败就把任务推到最贵的档，成本反而爆炸。
  3. **先验平滑**：`p_fail = (failed + prior*w) / (attempts + w)`，
     没有样本时是 0.15 而不是 0/1，避免"跑一次成功就永远信任"。

决策结果只描述"该用哪个模型、为什么"，**真正切模型由 loop 做**（它持有客户端）。
"""
from __future__ import annotations

import logging
from typing import Any

from .model_service import endpoint_of

logger = logging.getLogger("dogbuddy.services.routing")


class RoutingService:
    #: 先验失败率与权重（相当于先垫 8 次样本）
    PRIOR_FAIL = 0.15
    PRIOR_WEIGHT = 8
    #: 冷启动：样本少于此数时**只降不升**
    MIN_SAMPLES = 100
    #: 便宜档失败率超过它 → 不降（省下来的钱不够赔）
    ESCALATE_AT = 0.30

    def __init__(self, db: Any, cost: Any = None):
        self.db = db
        self.cost = cost

    # ------------------------------------------------------------ 样本
    def stats(self) -> list[dict[str, Any]]:
        """原始聚合（给接口/前端），附带算好的 `p_fail`。"""
        out = []
        for row in self.db.list_route_stats():
            item = dict(row)
            item["p_fail"] = round(self.p_fail(item.get("task_class", ""),
                                               item.get("model", "")), 4)
            out.append(item)
        return out

    def _row(self, task_class: str, model: str) -> dict[str, Any] | None:
        for row in self.db.list_route_stats():
            if (str(row.get("task_class") or "") == str(task_class or "default")
                    and str(row.get("model") or "") == str(model or "")):
                return row
        return None

    def samples(self, task_class: str, model: str) -> int:
        row = self._row(task_class, model)
        return int(row.get("attempts") or 0) if row else 0

    def p_fail(self, task_class: str, model: str) -> float:
        row = self._row(task_class, model)
        n = int(row.get("attempts") or 0) if row else 0
        f = int(row.get("failed") or 0) if row else 0
        return (f + self.PRIOR_FAIL * self.PRIOR_WEIGHT) / (n + self.PRIOR_WEIGHT)

    def record(self, task_class: str, model: str, ok: bool,
               escalated: bool = False) -> None:
        try:
            self.db.bump_route_stat(task_class or "default", model or "",
                                    ok=bool(ok), escalated=bool(escalated))
        except Exception:  # noqa: BLE001 — 统计算挂了不能影响业务
            logger.debug("route stat bump failed", exc_info=True)

    def record_review(self, task_class: str, model: str, ok: bool) -> None:
        """复核结论回灌（0.13.1）。

        为什么不让它跟 run 成败混着看：`run` 的"done"只说明**流程跑完了**，
        不代表判定是对的；而复核是**真值**。两个信号都进同一张统计表，
        `p_fail` 因此同时反映"跑不完"和"判错了"——这正是路由该优化的目标。
        """
        self.record(task_class, model, ok, escalated=not ok)

    # ------------------------------------------------------- 候选模型
    def _catalog(self) -> dict[str, dict[str, Any]]:
        return {str(m.get("model") or ""): m
                for m in self.db.list_models(enabled_only=True)}

    def _priced(self) -> list[dict[str, Any]]:
        rows = [r for r in self.db.list_cost_models()
                if int(r.get("enabled") or 0) and float(r.get("out_price") or 0) > 0]
        rows.sort(key=lambda r: (float(r.get("out_price") or 0),
                                 float(r.get("in_price") or 0)))
        return rows

    def options(self, current_model: str) -> tuple[list[dict], list[dict]]:
        """返回 (更便宜的候选, 更贵的候选)。都要求与当前模型**同端点**。"""
        catalog = self._catalog()
        cur = catalog.get(current_model)
        cur_ep = endpoint_of(str((cur or {}).get("base_url") or ""))
        priced = self._priced()
        cur_price = next((float(r.get("out_price") or 0) for r in priced
                          if str(r.get("model") or "") == current_model), None)
        cheaper: list[dict] = []
        stronger: list[dict] = []
        for r in priced:
            mid = str(r.get("model") or "")
            if not mid or mid == current_model:
                continue
            entry = catalog.get(mid)
            if entry is None:
                continue
            if cur_ep and endpoint_of(str(entry.get("base_url") or "")) != cur_ep:
                continue                      # 跨端点 = 换供应商，不许
            price = float(r.get("out_price") or 0)
            if cur_price is None or price < cur_price:
                cheaper.append(r)
            elif price > cur_price:
                stronger.append(r)
        stronger.sort(key=lambda r: float(r.get("out_price") or 0))
        return cheaper, stronger

    # ------------------------------------------------------------ 决策
    def decide(self, current_model: str = "", *, task_class: str = "default",
               budget: dict[str, Any] | None = None) -> dict[str, Any]:
        """给这一轮挑模型。返回 `{action, model, reason, p_fail, samples, price}`。

        `action`：`stay`（不动）/ `downroute`（降档）/ `uproute`（升级）。
        预算已 `exceeded` 时不在这里动 —— 那是预算闸门的活（它可能暂停整轮）。
        """
        budget = budget or {}
        level = str(budget.get("level") or "ok")
        cheaper, stronger = self.options(current_model)
        base = {"action": "stay", "model": current_model, "reason": "",
                "p_fail": round(self.p_fail(task_class, current_model), 4),
                "samples": self.samples(task_class, current_model), "price": 0.0}
        # 升级优先于降档：当前这档历史很烂时，"省钱"已经不是主要矛盾。
        # 但**预算不宽松时不许升级**（warn/exceeded 还往上走，等于自己打自己）。
        if stronger and level == "ok":
            up = stronger[0]
            mid = str(up.get("model") or "")
            cur_pf = self.p_fail(task_class, current_model)
            cur_n = self.samples(task_class, current_model)
            if cur_n >= self.MIN_SAMPLES and cur_pf > self.ESCALATE_AT:
                return {"action": "uproute", "model": mid,
                        "price": float(up.get("out_price") or 0),
                        "p_fail": round(cur_pf, 4), "samples": cur_n,
                        "reason": f"当前档 {current_model} 失败率 {cur_pf:.0%}，升级到 {mid}"}
        if not cheaper:
            return {**base, "reason": "同端点没有更便宜的档"}

        for cand in cheaper[:1]:              # 单价最低的那档
            mid = str(cand.get("model") or "")
            n = self.samples(task_class, mid)
            pf = self.p_fail(task_class, mid)
            price = float(cand.get("out_price") or 0)
            hot = level in ("warn", "exceeded")
            if pf > self.ESCALATE_AT:
                # 便宜档历史就爱翻车 → 不动（省的钱不够赔），理由要留痕
                return {**base, "p_fail": round(pf, 4), "samples": n,
                        "reason": f"便宜档 {mid} 历史失败率 {pf:.0%} 偏高，不降"}
            if hot:
                return {"action": "downroute", "model": mid, "price": price,
                        "p_fail": round(pf, 4), "samples": n,
                        "reason": f"预算{level}（已用 {budget.get('ratio', 0):.0%}），先跑便宜档 {mid}"}
            if n >= self.MIN_SAMPLES and pf < self.PRIOR_FAIL:
                return {"action": "downroute", "model": mid, "price": price,
                        "p_fail": round(pf, 4), "samples": n,
                        "reason": f"{mid} 在 {task_class} 上 {n} 次失败率 {pf:.0%}，可降档"}
            # 冷启动：样本不足 → 只降不升，保持在当前档

        return {**base, "reason": "维持当前档（样本或预算都不满足切换条件）"}

    def health(self) -> dict[str, Any]:
        rows = self.stats()
        total = sum(int(r.get("attempts") or 0) for r in rows)
        failed = sum(int(r.get("failed") or 0) for r in rows)
        return {"classes": len({r.get("task_class") for r in rows}),
                "models": len({r.get("model") for r in rows}),
                "attempts": total, "failed": failed,
                "min_samples": self.MIN_SAMPLES}
