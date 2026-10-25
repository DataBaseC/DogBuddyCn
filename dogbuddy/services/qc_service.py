# DogBuddyCn — 抽检策略与复核闭环（0.12.2）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""不要求"每件都复判"，而是**按风险与预算抽样**，再用真实漏检修正下一批。

三个函数构成闭环：

  1. `plan()`    —— 这一批抽几件？（置信度 × 预算 × 上限，谁紧听谁的）
  2. `record()`  —— 复核结论回流（逐条，用于算准确率与难例池）
  3. `close()`   —— 收批：算缺陷率与**漏检估计**，写回批次供下一批参考

抽样数怎么来（写清楚，免得被当成玄学）：
  * 目标：以 `target_miss` 的概率漏掉一个缺陷 → `k = ceil(ln(target_miss) / ln(1 - p))`，
    其中 `p` 是**先验缺陷率**（没有历史时用 `risk_prior`，默认 2%）；
  * 硬上限：`max_ratio`（默认 20%）—— 否则低风险批次会被算出"全检"；
  * 预算上限：`floor(budget / cost_per_item)`；
  * 至少 1 件（0 抽样等于不复核，那不是抽检是放任）。
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("dogbuddy.services.qc")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QcService:
    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------ 策略
    def plan(self, batch_size: int, *, risk_prior: float = 0.02,
             target_miss: float = 0.05, cost_per_item: float = 0.02,
             budget: float = 0.5, max_ratio: float = 0.2) -> dict[str, Any]:
        """算这一批该抽几件，以及**为什么**是这个数（理由要能给人看）。"""
        total = max(1, int(batch_size or 1))
        p = min(max(float(risk_prior), 0.0), 0.99)
        miss = min(max(float(target_miss), 1e-6), 0.99)
        if p <= 0:
            by_risk = 1
            reason = "先验缺陷率为 0：只做象征性抽检 1 件"
        else:
            by_risk = int(math.ceil(math.log(miss) / math.log(1.0 - p)))
            reason = f"先验缺陷率 {p:.1%}、目标漏检 {miss:.1%} → 至少 {by_risk} 件"
        by_ratio = max(1, int(math.ceil(total * float(max_ratio))))
        by_budget = (int(math.floor(float(budget) / float(cost_per_item)))
                     if float(cost_per_item) > 0 else total)
        k = max(1, min(by_risk, by_ratio, max(1, by_budget), total))
        limits = []
        if k == by_ratio and by_ratio < by_risk:
            limits.append(f"抽样上限 {max_ratio:.0%}（{by_ratio} 件）")
        if k == by_budget and by_budget < by_risk:
            limits.append(f"预算只够 {by_budget} 件")
        return {"sample": k, "batch": total, "ratio": round(k / total, 4),
                "cost": round(k * float(cost_per_item), 6),
                "by_risk": by_risk, "by_ratio": by_ratio, "by_budget": max(0, by_budget),
                "reason": reason + (("；" + "、".join(limits)) if limits else ""),
                "policy": {"risk_prior": p, "target_miss": miss,
                           "cost_per_item": float(cost_per_item),
                           "budget": float(budget), "max_ratio": float(max_ratio)}}

    @staticmethod
    def needs_review(confidence: float, threshold: float = 0.8) -> bool:
        """置信度低于阈值 → 转人工复核（复用 `pending_decisions`）。"""
        return float(confidence or 0) < float(threshold)

    # ------------------------------------------------------------ 批次
    def open_batch(self, *, total: int, sampled: int = 0, line: str = "",
                   pipeline_run_id: str = "", policy: dict[str, Any] | None = None,
                   cost_estimate: float = 0.0) -> dict[str, Any]:
        bid = "qc-" + uuid.uuid4().hex[:12]
        row = self.db.create_qc_batch({
            "id": bid, "total": int(total or 0), "sampled": int(sampled or 0),
            "line": line, "pipeline_run_id": pipeline_run_id,
            "cost_estimate": float(cost_estimate or 0.0),
            "policy_json": __import__("json").dumps(policy or {}, ensure_ascii=False),
            "status": "open", "created_at": _now()})
        try:
            self.db.audit("system", "qc:open", f"{bid} line={line} total={total} sample={sampled}")
        except Exception:  # noqa: BLE001
            logger.debug("qc audit failed", exc_info=True)
        return row

    def review(self, batch_id: str, *, item_ref: str = "", model_label: str = "",
               confidence: float = 0.0, reviewer: str = "owner",
               verdict: str = "agree") -> dict[str, Any]:
        """一条复核结论。`verdict`: agree（模型对）/ reject（模型错）/ unknown。"""
        verdict = verdict if verdict in ("agree", "reject", "unknown") else "unknown"
        row = self.db.add_qc_review({"id": "qcr-" + uuid.uuid4().hex[:10],
                                     "batch_id": batch_id, "item_ref": item_ref,
                                     "model_label": model_label,
                                     "confidence": float(confidence or 0),
                                     "reviewer": reviewer, "verdict": verdict,
                                     "created_at": _now()})
        return row

    def close(self, batch_id: str, *, defects_found: int | None = None) -> dict[str, Any]:
        """收批：用抽样结果估这一批的缺陷率与漏检。"""
        batch = self.db.get_qc_batch(batch_id)
        if batch is None:
            raise ValueError(f"批次不存在：{batch_id}")
        reviews = self.db.list_qc_reviews(batch_id)
        rejects = len([r for r in reviews if r.get("verdict") == "reject"])
        defects = int(defects_found) if defects_found is not None else rejects
        sampled = int(batch.get("sampled") or 0) or len(reviews)
        total = int(batch.get("total") or 0) or sampled
        rate = (defects / sampled) if sampled else 0.0
        # 未抽到的部分按同一缺陷率外推 → 期望漏检比例
        miss = rate * (total - sampled) / total if total else 0.0
        self.db.update_qc_batch(batch_id, status="closed", closed_at=_now(),
                                sampled=sampled, defects_found=defects,
                                defect_rate=round(rate, 6),
                                miss_estimate=round(miss, 6))
        try:
            self.db.audit("system", "qc:close",
                          f"{batch_id} sampled={sampled} defects={defects} "
                          f"rate={rate:.3f} miss≈{miss:.3f}")
        except Exception:  # noqa: BLE001
            logger.debug("qc audit failed", exc_info=True)
        return self.db.get_qc_batch(batch_id) or {}

    # ------------------------------------------------------------ 查询
    def batches(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.list_qc_batches(limit=limit)

    def detail(self, batch_id: str) -> dict[str, Any]:
        batch = self.db.get_qc_batch(batch_id)
        if batch is None:
            raise ValueError(f"批次不存在：{batch_id}")
        return {"batch": batch, "reviews": self.db.list_qc_reviews(batch_id)}

    def health(self) -> dict[str, Any]:
        rows = self.db.list_qc_batches(limit=200)
        closed = [r for r in rows if r.get("status") == "closed"]
        rates = [float(r.get("defect_rate") or 0) for r in closed]
        return {"batches": len(rows), "open": len([r for r in rows if r.get("status") == "open"]),
                "closed": len(closed),
                "avg_defect_rate": round(sum(rates) / len(rates), 4) if rates else 0.0,
                "last_miss_estimate": round(float(closed[0].get("miss_estimate") or 0), 4)
                if closed else 0.0}
