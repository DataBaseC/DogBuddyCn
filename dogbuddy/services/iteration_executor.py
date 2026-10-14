# DogBuddyCn — 迭代执行器（项目调度器）(Batch 8 组二 · T8.6)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""到点把项目团队拉进**共享工作区**跑一轮协作，轮次 +1、记进展；
达停止条件即停并通知；每轮结束汇报进展。

与 `CronScheduler` 分开的理由：cron 是"某个工位按时干某件事"，
迭代是"一个**项目**按目标一轮轮推进"，参与者和停止条件都不一样。
但两者共用同一条"tick + 总线事件 + job_runs/iteration_rounds 留痕"的骨架。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("dogbuddy.services.iteration_exec")

ITERATION_ROUND_DONE = "iteration.round"
ITERATION_STOPPED = "iteration.stopped"

#: 交给团队的单轮提示词：把目标、已做的轮次、上一轮结论都摊开
ROUND_PROMPT = """【项目迭代 · 第 {round} 轮】项目「{project}」
目标：{goal}

--- 前几轮进展 ---
{history}
---

请在本轮实际推进一步（改代码/写文件/跑验证都可以，**要真的产生变化**）。
工作目录就是项目共享工作区；团队共享同一批文件，别重复别人已做的。
结束时必须调用 report_iteration 汇报：
  progress_percent：相对目标的完成度（0-100，整数）
  note：这一轮具体做了什么、下一步做什么（写给管理员看，简洁）
  goal_achieved：目标是否已经完全达成（true/false）
  如果**这一轮什么进展都没有**，progress_percent 给和上一轮相同的值并说明卡在哪。
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IterationExecutor:
    """迭代到点执行的一侧。run_round 可注入（测试替身）。"""

    def __init__(self, service: Any, db: Any, windows: Any = None,
                 groups: Any = None, bus: Any = None,
                 run_round: Callable[[dict, dict, int], Any] | None = None,
                 tick_seconds: int = 60):
        self.service = service
        self.db = db
        self.windows = windows
        self.groups = groups
        self.bus = bus
        self._run_round = run_round or self._default_round
        self.tick_seconds = max(5, int(tick_seconds))
        self._stopped = False
        self._inflight: set[str] = set()

    # ---------------------------------------------------------------- 循环
    async def run_loop(self) -> None:
        logger.info("iteration executor started (tick=%ss)", self.tick_seconds)
        while not self._stopped:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — 一轮出错不能停掉整个执行器
                logger.exception("iteration tick failed")
            await asyncio.sleep(self.tick_seconds)

    def stop(self) -> None:
        self._stopped = True

    async def tick(self) -> int:
        """跑所有到点的迭代各一轮。返回执行的轮次数。"""
        due = self.db.due_iterations(_now().astimezone(timezone.utc).isoformat())
        started = 0
        for entry in due:
            if entry["id"] in self._inflight:
                continue
            self._inflight.add(entry["id"])
            started += 1
            asyncio.get_event_loop().create_task(self._guarded(entry["id"]))
        return started

    async def _guarded(self, iteration_id: str) -> None:
        try:
            await self.run_one(iteration_id)
        finally:
            self._inflight.discard(iteration_id)

    # ---------------------------------------------------------------- 单轮
    async def run_one(self, iteration_id: str) -> dict[str, Any]:
        entry = self.db.get_iteration(iteration_id)
        if entry is None or entry.get("status") != "active":
            return {"skipped": True, "reason": "not active"}
        plan = self.service.effective_plan(entry)
        round_no = int(entry.get("round") or 0) + 1
        round_id = self.db.start_iteration_round(iteration_id, round_no,
                                                 window_count=len(entry.get("window_ids") or []))
        try:
            outcome = await self._run_round(entry, plan, round_no)
        except Exception as e:  # noqa: BLE001 — 单轮崩溃算一次无进展
            logger.exception("iteration %s round %s crashed", iteration_id, round_no)
            outcome = {"status": "failed", "summary": str(e), "progress": None,
                       "goal_achieved": False}

        progress = outcome.get("progress")
        prev = entry.get("last_progress") or ""
        goal_achieved = bool(outcome.get("goal_achieved"))
        status = str(outcome.get("status") or "ok")
        summary = str(outcome.get("summary") or "")[:4000]

        # 无进展判定：progress 没写（Agent 没汇报）或与上一轮持平 → 记一次
        no_progress = (progress is None) or (str(progress) == str(prev))
        streak = int(entry.get("no_progress_streak") or 0) + (1 if no_progress else 0)
        if not no_progress:
            streak = 0

        self.db.finish_iteration_round(
            round_id, status, summary, progress=(progress if isinstance(progress, int) else 0),
            goal_achieved=goal_achieved)
        updated = self.db.update_iteration(
            iteration_id, round=round_no,
            last_progress=("" if progress is None else str(progress)),
            no_progress_streak=streak,
            status="active") or entry

        stop, reason = self.service.check_stop(
            updated, plan, goal_achieved=goal_achieved,
            spent_tokens=int(outcome.get("spent_tokens") or 0))
        if stop:
            self.db.update_iteration(iteration_id, status="done", stop_reason=reason,
                                     next_run_at="")
            self._emit(ITERATION_STOPPED, {
                "iteration_id": iteration_id, "project_id": entry.get("project_id", ""),
                "goal": entry.get("goal", ""), "round": round_no, "reason": reason,
                "summary": summary})
            logger.info("iteration %s 停止：%s（第 %d 轮）", iteration_id, reason, round_no)
        else:
            self.db.update_iteration(iteration_id,
                                     next_run_at=self.service.next_run_after(plan))
            self._emit(ITERATION_ROUND_DONE, {
                "iteration_id": iteration_id, "project_id": entry.get("project_id", ""),
                "goal": entry.get("goal", ""), "round": round_no,
                "progress": progress, "summary": summary,
                "next_run_at": self.service.next_run_after(plan),
                "no_progress_streak": streak})

        return {"iteration_id": iteration_id, "round": round_no, "status": status,
                "progress": progress, "stopped": stop, "stop_reason": reason,
                "summary": summary, "round_id": round_id}

    # ---------------------------------------------------------------- 默认单轮
    async def _default_round(self, entry: dict, plan: dict, round_no: int) -> dict[str, Any]:
        """把项目团队拉进共享工作区跑一轮。

        优先用**项目上已有的群组**（那样天然共享工作区、有频道记录）；
        没有群组就退回逐个工位派发（仍然都在项目共享工作区里）。
        """
        project_id = entry.get("project_id") or ""
        proj = self.db.get_project(project_id)
        if proj is None:
            return {"status": "failed", "summary": f"项目不存在：{project_id}"}

        history = self._history(entry["id"])
        prompt = ROUND_PROMPT.format(round=round_no, project=proj.name,
                                     goal=entry.get("goal", ""), history=history)

        group = None
        if self.groups is not None:
            group = next((g for g in self.db.list_groups()
                          if (getattr(g, "project_id", "") or "") == project_id), None)
        if group is not None and self.groups is not None:
            try:
                res = await self.groups.run(group.id, prompt)
                steps = res.get("steps") or []
                return {"status": "ok" if any(s.get("ok") for s in steps) else "failed",
                        "summary": res.get("summary") or "",
                        "progress": self._pick_progress(steps),
                        "goal_achieved": False}
            except Exception as e:  # noqa: BLE001 — 群组跑失败退回逐个派发
                logger.warning("group round failed, falling back to per-window", exc_info=True)
                fallback_reason = str(e)
        else:
            fallback_reason = ""

        # 逐个工位派发（同一共享工作区）
        from ..agents.core.loop import AgentLoop
        results: list[dict] = []
        for wid in entry.get("window_ids") or []:
            w = self.db.get_window(wid)
            if w is None or self.windows is None:
                continue
            try:
                rt = self.windows._rt(wid)
                ctx_ws = self.windows.effective_workspace(w)
                rt.session.workspace_override = ctx_ws
                loop = AgentLoop(
                    llm=self.windows._llm(w.llm_profile or None, w.model or None),
                    registry=rt.registry, session=rt.session,
                    builder=self.windows.builder,
                    compactor=self.windows._compactor_for(w),
                    tool_names=None,
                    max_iterations=self.windows.params_for(w).get("max_iterations", 25))
                from ..tools.registry import ToolContext
                ctx = ToolContext(window_id=w.id, workspace=ctx_ws, mode=w.mode,
                                  actor="worker", todo_repo=self.db, db=self.db,
                                  panel=self.windows.panel,
                                  ask_user_fn=self.windows._make_ask_user(w.id),
                                  checkpoint_root=self.windows.checkpoints_dir)
                res = await loop.run(prompt, ctx)
                results.append({"window_id": wid, "ok": res.stop_reason == "done",
                                "summary": res.content or "",
                                "progress": ctx.extra.get("iteration_progress")})
            except Exception as e:  # noqa: BLE001 — 一个工位失败不影响整轮
                logger.exception("iteration round step failed for %s", wid)
                results.append({"window_id": wid, "ok": False, "summary": f"步骤失败：{e}"})
        if not results:
            return {"status": "failed",
                    "summary": f"没有可执行的工位{('；群组失败：' + fallback_reason) if fallback_reason else ''}"}
        best = next((r for r in reversed(results) if r.get("progress") is not None), None)
        return {"status": "ok" if any(r["ok"] for r in results) else "failed",
                "summary": "\n\n".join(f"[{r['window_id']}] {r['summary'][:600]}" for r in results)[:4000],
                "progress": (best or {}).get("progress"),
                "goal_achieved": bool((best or {}).get("iteration_goal_achieved"))}

    @staticmethod
    def _pick_progress(steps: list[dict]) -> int | None:
        for s in reversed(steps or []):
            if isinstance(s.get("progress"), int):
                return s["progress"]
        return None

    def _history(self, iteration_id: str, limit: int = 5) -> str:
        rounds = self.db.list_iteration_rounds(iteration_id, limit=limit)
        if not rounds:
            return "（这是第一轮）"
        lines = []
        for r in reversed(rounds):  # 时间正序读起来更顺
            note = (r.get("summary") or "").strip().replace("\n", " ")[:300]
            lines.append(f"第 {r.get('round')} 轮：进度 {r.get('progress')}% —— {note or '(无汇报)'}")
        return "\n".join(lines)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.bus is None:
            return
        try:
            from ..events.bus import Event
            self.bus.emit(Event(type=event_type, payload=payload))
        except Exception:  # noqa: BLE001
            logger.warning("iteration event emit failed", exc_info=True)
