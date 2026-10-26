# DogBuddyCn — 运营回合（0.14.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""目标 → 回合 → 日志 → 实验：让"持续改进"有记录、可复盘、能停止。

工业线的闭环是「批次 → 复核 → 策略」，运营线是这一套。两者共用同一底座：
**执行**交给 `pipelines`（不另造调度），**花钱**交给 `cost`（预算闸门），
**度量**交给 `metrics`（边缘推上来的指标），这里只做三件事：

  1. 把目标翻译成**一轮可验证的动作**（假设 + 动作 + 验收口径）；
  2. 每轮落 `ops_journal`：预期 vs 实测 → `keep / adjust / stop`；
  3. 用变体（`ops_experiments`）隔离尝试：赢了继续放大，输了换变体。

三条克制（写进代码，防止"AI 运营"变成"AI 乱花钱"）：
* **默认不自动开跑**：`tick()` 只收尾在跑的回合。实时站点这类需求由目标上的
  `auto_interval_s` **显式开启**（默认 0 = 永不），且自动开跑仍先过预算闸门。
  * **不猜指标**：读不到指标就记 `unknown` 并停下判断，不拿 0 冒充数据。
  * **不静默失败**：预算拦下、流水线缺失、指标缺失都写进 journal 的 `notes`。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("dogbuddy.services.operations")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationsService:
    CADENCES = ("manual", "daily", "weekly")
    DIRECTIONS = ("up", "down")
    VERDICTS = ("keep", "adjust", "stop", "unknown")
    #: 小于这个相对变化视为噪声（before=100 时是 1 个点）
    MIN_EFFECT = 0.01

    def __init__(self, db: Any, *, pipelines: Any = None, cost: Any = None,
                 metrics: Any = None, bus: Any = None) -> None:
        self.db = db
        self.pipelines = pipelines
        self.cost = cost
        self.metrics = metrics
        self.bus = bus

    # ------------------------------------------------------------- 目标
    def upsert_goal(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("目标必须有 name")
        direction = str(payload.get("direction") or "up")
        if direction not in self.DIRECTIONS:
            raise ValueError(f"direction 只能是 {self.DIRECTIONS} 之一")
        cadence = str(payload.get("cadence") or "manual")
        if cadence not in self.CADENCES:
            raise ValueError(f"cadence 只能是 {self.CADENCES} 之一")
        gid = str(payload.get("id") or "").strip() or "goal-" + uuid.uuid4().hex[:8]
        pipeline_id = str(payload.get("pipeline_id") or "")
        if pipeline_id and self.pipelines is not None:
            if self.pipelines.get(pipeline_id) is None:
                raise ValueError(f"执行流水线不存在：{pipeline_id}")
        auto_interval = int(payload.get("auto_interval_s") or 0)
        if auto_interval and auto_interval < 60:
            raise ValueError("auto_interval_s 要么 0（关闭），要么 ≥60 秒")
        target = payload.get("target")
        baseline = payload.get("baseline")
        row = self.db.upsert_goal({
            "id": gid, "name": name,
            "description": str(payload.get("description") or ""),
            "metric_name": str(payload.get("metric_name") or "").strip(),
            "metric_unit": str(payload.get("metric_unit") or ""),
            "direction": direction,
            "baseline": float(baseline) if baseline not in (None, "") else None,
            "target": float(target) if target not in (None, "") else None,
            "cadence": cadence, "pipeline_id": pipeline_id,
            "auto_interval_s": auto_interval,
            "enabled": 1 if payload.get("enabled", True) else 0})
        self._audit("ops:goal", f"{gid} metric={row.get('metric_name')} "
                                f"target={row.get('target')} cadence={cadence}")
        return row

    def goals(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        return self.db.list_goals(enabled_only=enabled_only)

    def goal(self, gid: str) -> dict[str, Any]:
        row = self.db.get_goal(gid)
        if row is None:
            raise ValueError(f"目标不存在：{gid}")
        return row

    def delete_goal(self, gid: str) -> bool:
        ok = self.db.delete_goal(gid)
        if ok:
            self._audit("ops:goal:delete", str(gid))
        return ok

    # ------------------------------------------------------------- 度量
    def _metric(self, name: str) -> float | None:
        if not name or self.metrics is None:
            return None
        row = self.metrics.latest(name)
        if not row:
            return None
        try:
            return float(row.get("value"))
        except (TypeError, ValueError):
            return None

    def _target_reached(self, goal: dict[str, Any], value: float | None) -> bool:
        target = goal.get("target")
        if value is None or target is None:
            return False
        target = float(target)
        return value >= target if goal.get("direction") == "up" else value <= target

    def _verdict(self, goal: dict[str, Any], before: float | None,
                 after: float | None) -> tuple[str, float | None]:
        if before is None or after is None:
            return "unknown", None
        delta = after - before
        effect = self.MIN_EFFECT * max(abs(before), 1e-9)
        if abs(delta) < effect:
            return "adjust", delta          # 噪声范围内不算成功
        good = delta > 0 if goal.get("direction") == "up" else delta < 0
        return ("keep" if good else "adjust"), delta

    # ------------------------------------------------------------- 计划
    def plan_round(self, gid: str) -> dict[str, Any]:
        goal = self.goal(gid)
        if not int(goal.get("enabled") or 0):
            raise ValueError(f"目标已停用：{gid}")
        before = self._metric(str(goal.get("metric_name") or ""))
        round_no = self.db.count_journal(gid) + 1
        last = next(iter(self.db.list_journal(gid, limit=1)), None)
        metric = str(goal.get("metric_name") or "")
        unit = str(goal.get("metric_unit") or "")
        direction = goal.get("direction") or "up"
        if not metric:
            hypothesis = "还没有定义可度量指标：先补 metric_name 再谈优化"
            action = "在目标设置里填一个边缘可上报的指标名（如 site.visits）"
            expected = "指标能被读到"
        elif before is None:
            hypothesis = f"指标 {metric} 目前没有上报，优化无从判断"
            action = f"先让边缘/采集器上报 {metric}，再发起正式回合"
            expected = f"能读到 {metric} 的当前值"
        elif last and last.get("verdict") == "keep":
            hypothesis = f"上一轮动作有效，扩大样本后仍应{_word(direction)}"
            action = ("沿用上一轮动作并扩大样本/频次；只改一个参数，"
                      "确保效果可归因")
            expected = self._expect(goal, round_no)
        elif last:
            hypothesis = "上一轮未达预期，说明主因不在那个环节"
            action = ("换一个变体：只改一个变量（渠道/文案/页面/频次），"
                      "其余保持与上一轮一致")
            expected = self._expect(goal, round_no)
        else:
            hypothesis = f"当前 {metric} 有提升空间，先做最小可验证改动"
            action = f"针对 {metric} 做一个最小改动，并记录改动前后差异"
            expected = self._expect(goal, round_no)
        return {"goal": goal, "round_no": round_no, "metric_before": before,
                "hypothesis": hypothesis, "action": action, "expected": expected,
                "acceptance": f"{metric or '指标'} {_word(direction)}且变化超过 "
                              f"{self.MIN_EFFECT:.0%}（相对）；到 {goal.get('target')} "
                              f"{unit} 视为达标" if goal.get("target") is not None else
                              f"{metric or '指标'} {_word(direction)}且变化超过 {self.MIN_EFFECT:.0%}（相对）"}

    def _expect(self, goal: dict[str, Any], round_no: int) -> str:
        metric = goal.get("metric_name") or "指标"
        unit = goal.get("metric_unit") or ""
        target = goal.get("target")
        if target is None:
            return f"{metric} 相对上一轮{_word(goal.get('direction'))} ≥ {self.MIN_EFFECT:.0%}"
        return f"第 {round_no} 轮把 {metric} 推向目标 {target} {unit}".strip()

    # ------------------------------------------------------------- 开跑
    def start_round(self, gid: str, *, execute: bool = True) -> dict[str, Any]:
        # 同一目标只允许一个在跑的回合：自动轮次和手动轮次并发时，两轮会同时
        # 写同一个站点/文件，后结束的那轮会把新内容覆盖回去（实测踩过）。
        running = self.db.list_journal(goal_id=gid, limit=1, status="running")
        if running:
            raise ValueError(
                f"该目标已有在跑的回合（round_no={running[0].get('round_no')}），"
                "等它收尾或先关闭它")
        plan = self.plan_round(gid)
        goal = plan["goal"]
        budget = {"level": "ok", "exceeded": False, "reason": ""}
        if self.cost is not None:
            try:
                budget = self.cost.budget_state(window_id=f"goal:{gid}")
            except Exception:  # noqa: BLE001 — 预算算不出来不能拦住记账与日志
                logger.debug("ops budget check failed", exc_info=True)
        round_no = plan["round_no"]
        jid = "or-" + uuid.uuid4().hex[:10]
        if budget.get("exceeded"):
            row = self.db.create_journal({
                "id": jid, "goal_id": gid, "round_no": round_no, "status": "blocked",
                "hypothesis": plan["hypothesis"], "action": plan["action"],
                "expected": plan["expected"], "metric_before": plan["metric_before"],
                "started_at": _now(), "finished_at": _now(),
                "notes": f"预算拦下：{budget.get('reason') or '预算用尽'}"})
            self._audit("ops:round:blocked", f"{jid} {gid} {budget.get('reason')}")
            return {"journal": row, "plan": plan, "budget": budget, "started": False}

        exp = self._experiment_for(gid, plan, round_no)
        row = self.db.create_journal({
            "id": jid, "goal_id": gid, "experiment_id": exp.get("id"),
            "round_no": round_no, "status": "planned",
            "hypothesis": plan["hypothesis"], "action": plan["action"],
            "expected": plan["expected"], "metric_before": plan["metric_before"],
            "started_at": _now()})
        started = False
        if not execute:
            self.db.update_journal(jid, notes="仅记录计划（execute=false），未执行")
        elif not goal.get("pipeline_id"):
            self.db.update_journal(
                jid, status="planned",
                notes="目标没有绑定执行流水线：先建一条流水线并在目标里选上")
        else:
            try:
                run = self.pipelines.start(str(goal["pipeline_id"]), {
                    "goal_id": gid, "goal_name": goal.get("name"),
                    "round_id": jid, "round_no": round_no,
                    "metric_name": goal.get("metric_name"),
                    "hypothesis": plan["hypothesis"], "action": plan["action"],
                    "expected": plan["expected"]})
                self.db.update_journal(jid, status="running",
                                       pipeline_run_id=str(run.get("id") or ""))
                started = True
                try:
                    asyncio.get_running_loop().create_task(
                        self._execute_and_close(str(run.get("id"))))
                except RuntimeError:
                    pass                      # 没有事件循环（脚本/测试）→ 交给 tick()
            except Exception as e:  # noqa: BLE001
                self.db.update_journal(jid, status="failed", finished_at=_now(),
                                       notes=f"启动流水线失败：{type(e).__name__}: {e}")
        self.db.touch_goal(gid, _now())
        self._audit("ops:round", f"{jid} {gid} round={round_no} started={started}")
        return {"journal": self.db.get_journal(jid), "plan": plan,
                "budget": budget, "started": started,
                "experiment": self.db.get_experiment(str(exp.get("id") or ""))}

    def _experiment_for(self, gid: str, plan: dict[str, Any],
                        round_no: int) -> dict[str, Any]:
        last_exp = next(iter(self.db.list_experiments(gid, limit=1)), None)
        last_journal = next(iter(self.db.list_journal(gid, limit=1)), None)
        if (last_exp and last_exp.get("status") == "running"
                and last_journal and last_journal.get("verdict") == "keep"):
            return last_exp                  # 有效动作继续放大，不新开实验
        return self.db.create_experiment({
            "id": "ex-" + uuid.uuid4().hex[:10], "goal_id": gid,
            "name": f"{plan['goal'].get('name')} · v{round_no}",
            "hypothesis": plan["hypothesis"], "action": plan["action"],
            "variant": round_no, "status": "running",
            "metric_before": plan["metric_before"], "created_at": _now()})

    # ------------------------------------------------------------- 收尾
    def close_round(self, jid: str, *, observed: str = "", verdict: str = "",
                    metric_after: float | None = None) -> dict[str, Any]:
        row = self.db.get_journal(jid)
        if row is None:
            raise ValueError(f"回合不存在：{jid}")
        goal = self.db.get_goal(str(row.get("goal_id"))) or {}
        if metric_after is None:
            metric_after = self._metric(str(goal.get("metric_name") or ""))
        auto_verdict, delta = self._verdict(goal, row.get("metric_before"), metric_after)
        if verdict and verdict not in self.VERDICTS:
            raise ValueError(f"verdict 只能是 {self.VERDICTS} 之一")
        final_verdict = verdict or auto_verdict
        notes = str(row.get("notes") or "")
        if auto_verdict == "unknown":
            notes = (notes + "；" if notes else "") + \
                "指标读不到，本轮无法判定（不要用 0 当数据）"
        if observed:
            notes = (notes + "；" if notes else "") + f"人工观察：{observed}"[:500]
        self.db.update_journal(
            jid, status="done", verdict=final_verdict, metric_after=metric_after,
            delta=delta, finished_at=_now(), observed=observed, notes=notes)
        exp_id = str(row.get("experiment_id") or "")
        if exp_id:
            reached = self._target_reached(goal, metric_after)
            if final_verdict == "keep":
                self.db.update_experiment(
                    exp_id, metric_after=metric_after,
                    status="won" if reached else "running",
                    closed_at=_now() if reached else "")
            elif final_verdict in ("adjust", "stop"):
                self.db.update_experiment(exp_id, metric_after=metric_after,
                                          status="lost", closed_at=_now())
        self._audit("ops:verdict", f"{jid} {final_verdict} "
                                   f"before={row.get('metric_before')} "
                                   f"after={metric_after} delta={delta}")
        return self.db.get_journal(jid) or {}

    async def _execute_and_close(self, run_id: str) -> None:
        """跑完流水线立刻结算这一轮（不等 2 分钟的后台 tick）。"""
        try:
            await self.pipelines.tick(run_id)
        except Exception:  # noqa: BLE001
            logger.warning("ops pipeline run failed", exc_info=True)
        finally:
            self.tick()

    def tick(self) -> list[dict[str, Any]]:
        """只收尾"在跑"的回合；**不会自己发起新回合**。"""
        updated: list[dict[str, Any]] = []
        if self.pipelines is None:
            return updated
        for row in self.db.list_journal(limit=50, status="running"):
            run_id = str(row.get("pipeline_run_id") or "")
            if not run_id:
                continue
            try:
                st = self.pipelines.status(run_id)
            except Exception:  # noqa: BLE001 — 台账还没写到/查不到，下次再看
                continue
            status = str((st.get("run") or {}).get("status") or "")
            if status == "done":
                updated.append(self.close_round(str(row["id"])))
            elif status in ("failed", "blocked"):
                self.db.update_journal(
                    str(row["id"]), status="failed", finished_at=_now(),
                    notes=f"执行失败：{(st.get('run') or {}).get('error') or status}")
                exp_id = str(row.get("experiment_id") or "")
                if exp_id:
                    self.db.update_experiment(exp_id, status="lost", closed_at=_now())
                updated.append(self.db.get_journal(str(row["id"])) or {})
        return updated

    def auto_tick(self) -> list[dict[str, Any]]:
        """到点就开一轮 —— **仅限显式设了 `auto_interval_s` 的目标**。

        三道闸：① 该目标已有 running 回合 → 跳过（不叠跑）；② 距上一轮不够间隔 →
        跳过；③ `start_round` 里照旧走预算闸门（超预算只记 blocked，不执行）。
        """
        started: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for goal in self.db.list_goals(enabled_only=True):
            interval = int(goal.get("auto_interval_s") or 0)
            if interval <= 0:
                continue
            gid = str(goal["id"])
            if self.db.list_journal(goal_id=gid, limit=1, status="running"):
                continue
            last = str(goal.get("last_round_at") or "")
            if last:
                try:
                    when = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    if (now - when).total_seconds() < interval:
                        continue
                except ValueError:
                    pass                  # 时间坏了就按"该跑了"处理
            try:
                started.append(self.start_round(gid))
            except Exception:  # noqa: BLE001 — 单个目标坏了不能拖垮调度
                logger.warning("ops auto round failed: %s", gid, exc_info=True)
        return started

    async def run_loop(self, interval: float = 120.0) -> None:
        """收尾 + 自动更新。自动更新只对显式开启的目标生效，且走预算闸门。"""
        while True:
            try:
                self.auto_tick()
                self.tick()
            except Exception:  # noqa: BLE001
                logger.warning("ops tick failed", exc_info=True)
            await asyncio.sleep(max(30.0, float(interval)))

    # ------------------------------------------------------------- 查询
    def journal(self, goal_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return self.db.list_journal(goal_id=goal_id, limit=limit)

    def experiments(self, goal_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return self.db.list_experiments(goal_id=goal_id, limit=limit)

    def health(self) -> dict[str, Any]:
        goals = self.db.list_goals()
        rows = self.db.list_journal(limit=100)
        verdicts = {v: len([r for r in rows if r.get("verdict") == v])
                    for v in self.VERDICTS}
        return {"goals": len(goals),
                "enabled": len([g for g in goals if int(g.get("enabled") or 0)]),
                "auto_goals": len([g for g in goals
                                   if int(g.get("auto_interval_s") or 0) > 0]),
                "rounds": len(rows),
                "running": len([r for r in rows if r.get("status") == "running"]),
                "blocked": len([r for r in rows if r.get("status") == "blocked"]),
                "verdicts": verdicts,
                "experiments": len(self.db.list_experiments(limit=200))}

    # ------------------------------------------------------------- 审计
    def _audit(self, action: str, detail: str) -> None:
        try:
            self.db.audit("system", action, detail[:500])
        except Exception:  # noqa: BLE001
            logger.debug("ops audit failed", exc_info=True)


def _word(direction: str | None) -> str:
    return "下降" if str(direction) == "down" else "提升"
