# DogBuddyCn — 抽检策略与复核闭环（0.12.2 / 闭环 0.13.1）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""不要求"每件都复判"，而是**按风险与预算抽样**，再用真实漏检修正下一批。

闭环（0.13.1 补上了后两段，之前是断的）：

    抽样计划 ──执行──> 复核结论 ──驳回──> 难例池 ──统计──> 漂移告警
       ↑                                   │
       └────── 下一批的 risk_prior ←───────┘（用真实漏检反推）

四个数字都必须"算给人看"：
  1. `plan()`        这一批抽几件，理由是哪一条限制卡的（风险 / 上限 / 预算）
  2. `review()`      每条复核结论；**驳回自动进难例池**，并回灌模型路由的失败率
  3. `close()`       收批：缺陷率 = 缺陷/抽样；漏检 = 缺陷率 × 未抽比例
  4. `drift()`       按「产线 × 模型」滚动统计驳回率，超阈值就告警（去重 1 小时）
"""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("dogbuddy.services.qc")

#: 漂移告警去重：同一 (产线, 模型) 一小时内只写一次审计/事件
_ALERTED: dict[str, float] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QcService:
    def __init__(self, db: Any, *, routing: Any = None, bus: Any = None) -> None:
        self.db = db
        #: 0.13.1：复核驳回要回灌模型路由（比"run 成败"更准的质量信号）
        self.routing = routing
        self.bus = bus

    # ------------------------------------------------------------ 策略
    def recent_miss(self, window: int = 5) -> float | None:
        """最近 N 个已收批次的**平均漏检估计**（没有历史返回 None）。"""
        rows = [b for b in self.db.list_qc_batches(limit=window * 3)
                if b.get("status") == "closed"][:window]
        if not rows:
            return None
        vals = [float(b.get("miss_estimate") or 0) for b in rows]
        return round(sum(vals) / len(vals), 6)

    def plan(self, batch_size: int, *, risk_prior: float = 0.02,
             target_miss: float = 0.05, cost_per_item: float = 0.02,
             budget: float = 0.5, max_ratio: float = 0.2,
             auto_risk: bool = True) -> dict[str, Any]:
        """算这一批该抽几件，以及**为什么**是这个数（理由要能给人看）。

        `auto_risk=True`（默认）：先验缺陷率取**最近几批的真实漏检**，
        没有历史才退回 `risk_prior`（默认 2%）。这是"用结果修正策略"的第一环。
        """
        total = max(1, int(batch_size or 1))
        hist = self.recent_miss() if auto_risk else None
        p = float(hist) if hist is not None else float(risk_prior)
        p = min(max(p, 0.0), 0.99)
        src = (f"历史漏检 {hist:.2%}（最近几批）" if hist is not None
               else f"默认先验 {float(risk_prior):.2%}（还没有收批历史）")
        miss = min(max(float(target_miss), 1e-6), 0.99)
        if p <= 0:
            by_risk = 1
            reason = f"{src} → 先验缺陷率为 0：只做象征性抽检 1 件"
        else:
            by_risk = int(math.ceil(math.log(miss) / math.log(1.0 - p)))
            reason = f"{src}、目标漏检 {miss:.1%} → 至少 {by_risk} 件"
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
                "risk_used": round(p, 6), "risk_source": src,
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
                   cost_estimate: float = 0.0, task_class: str = "qc") -> dict[str, Any]:
        bid = "qc-" + uuid.uuid4().hex[:12]
        row = self.db.create_qc_batch({
            "id": bid, "total": int(total or 0), "sampled": int(sampled or 0),
            "line": line, "pipeline_run_id": pipeline_run_id,
            "cost_estimate": float(cost_estimate or 0.0),
            "policy_json": json.dumps({**(policy or {}), "_task_class": task_class},
                                      ensure_ascii=False),
            "status": "open", "created_at": _now()})
        try:
            self.db.audit("system", "qc:open", f"{bid} line={line} total={total} sample={sampled}")
        except Exception:  # noqa: BLE001
            logger.debug("qc audit failed", exc_info=True)
        return row

    def review(self, batch_id: str, *, item_ref: str = "", model_label: str = "",
               model: str = "", confidence: float = 0.0, reviewer: str = "owner",
               verdict: str = "agree") -> dict[str, Any]:
        """一条复核结论。`verdict`: agree / reject / unknown。

        `model` 是**做这次判定的模型**（本地视觉服务名）——它决定这条结论
        记到谁头上；不传就只当批次内统计用。

        `reject` 会自动：① 进难例池；② 回灌路由的失败率（`p_fail` 上升）。
        这正是 0.13.1 之前缺的那根线：复核结果过去只躺在表里。
        """
        verdict = verdict if verdict in ("agree", "reject", "unknown") else "unknown"
        row = self.db.add_qc_review({"id": "qcr-" + uuid.uuid4().hex[:10],
                                     "batch_id": batch_id, "item_ref": item_ref,
                                     "model_label": model_label, "model": model,
                                     "confidence": float(confidence or 0),
                                     "reviewer": reviewer, "verdict": verdict,
                                     "created_at": _now()})
        batch = self.db.get_qc_batch(batch_id) or {}
        line = str(batch.get("line") or "")
        task_class = self._task_class(batch)
        if verdict == "reject":
            try:
                self.db.add_hard_sample({
                    "id": "hs-" + uuid.uuid4().hex[:10], "batch_id": batch_id,
                    "item_ref": item_ref, "model_label": model_label, "model": model,
                    "confidence": float(confidence or 0), "verdict": verdict,
                    "reviewer": reviewer, "line": line, "task_class": task_class,
                    "ts": _now(),
                    "meta_json": json.dumps({"review_id": row.get("id")}, ensure_ascii=False)})
            except Exception:  # noqa: BLE001 — 难例写不进去不能丢复核结论
                logger.warning("hard sample insert failed", exc_info=True)
        if model and self.routing is not None:
            try:
                self.routing.record_review(task_class, model, verdict != "reject")
            except Exception:  # noqa: BLE001
                logger.debug("routing.record_review failed", exc_info=True)
        return row

    @staticmethod
    def _task_class(batch: dict[str, Any]) -> str:
        try:
            policy = json.loads(batch.get("policy_json") or "{}")
        except Exception:  # noqa: BLE001
            policy = {}
        return str(policy.get("_task_class") or "qc")

    def close(self, batch_id: str, *, defects_found: int | None = None) -> dict[str, Any]:
        """收批：用抽样结果估这一批的缺陷率与漏检，并顺手看一眼漂移。"""
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
        # 收批即检查漂移（坏批次要马上冒头，而不是等下一次有人打开看板）
        try:
            self.drift()
        except Exception:  # noqa: BLE001
            logger.debug("drift check failed", exc_info=True)
        return self.db.get_qc_batch(batch_id) or {}

    # ------------------------------------------------------------ 漂移
    def drift(self, *, window: int = 200, min_samples: int = 10,
              threshold: float = 0.30, write_alert: bool = True,
              cooldown_s: float = 3600.0) -> dict[str, Any]:
        """按「产线 × 模型」看滚动驳回率。超阈值 = 模型/产线在漂。

        为什么要分组：整条线平均 10% 看着正常，但某台相机+某个模型的组合
        可能已经 50% —— 混在一起算会把问题平均掉。
        """
        reviews = self.db.recent_qc_reviews(limit=window)
        batches = {str(b["id"]): b for b in self.db.list_qc_batches(limit=500)}
        groups: dict[tuple[str, str], dict[str, int]] = {}
        for r in reviews:
            b = batches.get(str(r.get("batch_id") or ""), {})
            key = (str(b.get("line") or "-"), str(r.get("model") or "-"))
            g = groups.setdefault(key, {"total": 0, "reject": 0})
            g["total"] += 1
            if r.get("verdict") == "reject":
                g["reject"] += 1
        alerts = []
        for (line, model), g in sorted(groups.items()):
            if g["total"] < int(min_samples):
                continue
            rate = g["reject"] / g["total"]
            if rate >= float(threshold):
                alerts.append({"line": line, "model": model, "samples": g["total"],
                               "reject_rate": round(rate, 4),
                               "threshold": float(threshold)})
        fresh: list[dict[str, Any]] = []
        now = time.monotonic()
        for a in alerts:
            key = f"{a['line']}|{a['model']}"
            if now - _ALERTED.get(key, 0.0) >= float(cooldown_s):
                _ALERTED[key] = now
                fresh.append(a)
        if write_alert:
            for a in fresh:
                try:
                    self.db.audit("system", "qc:drift",
                                  f"{a['line']}/{a['model']} reject={a['reject_rate']:.1%} "
                                  f"n={a['samples']} threshold={a['threshold']:.0%}")
                except Exception:  # noqa: BLE001
                    logger.debug("drift audit failed", exc_info=True)
                if self.bus is not None:
                    try:
                        from ..events.bus import Event
                        self.bus.emit(Event(type="qc.drift", payload=a))
                    except Exception:  # noqa: BLE001
                        logger.debug("drift event failed", exc_info=True)
        return {"alerts": alerts, "new_alerts": fresh,
                "threshold": float(threshold), "reviewed": len(reviews),
                "min_samples": int(min_samples)}

    # ------------------------------------------------------------ 查询
    def batches(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.db.list_qc_batches(limit=limit)

    def detail(self, batch_id: str) -> dict[str, Any]:
        batch = self.db.get_qc_batch(batch_id)
        if batch is None:
            raise ValueError(f"批次不存在：{batch_id}")
        return {"batch": batch, "reviews": self.db.list_qc_reviews(batch_id)}

    def hard_samples(self, limit: int = 100, batch_id: str = "",
                     line: str = "") -> list[dict[str, Any]]:
        return self.db.list_hard_samples(limit=limit, batch_id=batch_id, line=line)

    def health(self) -> dict[str, Any]:
        rows = self.db.list_qc_batches(limit=200)
        closed = [r for r in rows if r.get("status") == "closed"]
        rates = [float(r.get("defect_rate") or 0) for r in closed]
        # health 只看不写：不产生审计/事件
        drift = self.drift(write_alert=False)
        return {"batches": len(rows), "open": len([r for r in rows if r.get("status") == "open"]),
                "closed": len(closed),
                "avg_defect_rate": round(sum(rates) / len(rates), 4) if rates else 0.0,
                "last_miss_estimate": round(float(closed[0].get("miss_estimate") or 0), 4)
                if closed else 0.0,
                # 0.13.1：闭环的两个出口
                "hard_samples": self.db.count_hard_samples(),
                "drift_alerts": len(drift["alerts"])}
