# DogBuddyCn — 项目迭代计划与执行 (Batch 8 组二 · T8.5 / T8.6)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""让一个项目**自动一轮轮推进**到目标，而不是等人每次下指令。

T8.5 迭代计划（本文件 `IterationService`）：
  * 项目级配置 `{目标, 参与工位, 档位(保守/标准/激进)}` —— **方案 c：三档预设 + 手动覆盖并存**
  * 系统据**能力画像**（T8.4 员工画像 + 模型能力表 + T8.8 限速严重度）自动推
    "迭代速度"与"迭代次数"；管理员填了 `speed_minutes`/`max_rounds` 就以填的为准
  * 停止条件：**达成目标 / 无进展 N 轮 / 次数上限**（预算上限是可选字段，0 = 不限）

T8.6 迭代执行器（`IterationExecutor`）：到点把项目团队拉进**共享工作区**跑一轮
（吃 T3 群组 + T7 项目），轮次 +1、记进展；达停止条件即停并通知；
每轮结束由 Agent 汇报进展（复用 T8.3 的 `report_iteration` 工具）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("dogbuddy.services.iteration")

#: 三档预设（方案 c）：档位只提供"默认值"，任何一项都能被显式字段覆盖
PRESETS: dict[str, dict[str, int]] = {
    # 保守：改动风险高/模型慢时用。间隔 4 小时，最多 20 轮，连续 2 轮无进展就停
    "conservative": {"speed_minutes": 240, "max_rounds": 20, "no_progress_limit": 2},
    # 标准：默认档
    "standard": {"speed_minutes": 60, "max_rounds": 50, "no_progress_limit": 3},
    # 激进：目标明确、模型快时用
    "aggressive": {"speed_minutes": 15, "max_rounds": 100, "no_progress_limit": 5},
}
DEFAULT_MODE = "standard"
MODES = tuple(PRESETS)

#: 员工画像的节奏档 → 间隔倍率（慢的工位拖慢整体节奏）
SPEED_FACTOR = {"slow": 2.0, "standard": 1.0, "fast": 0.5}
#: 模型能力表的 `speed` 档 → 间隔倍率（模型越慢，迭代越慢）
MODEL_SPEED_FACTOR = {"很慢": 3.0, "慢": 2.0, "中": 1.2, "快": 0.8, "很快": 0.6}
#: 限速严重度 → 间隔倍率（T8.8 联动：正在被限速就放慢，避免排队雪崩）
SEVERITY_FACTOR = {"": 1.0, "light": 1.3, "heavy": 2.5, "banned": 6.0}

MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 60 * 24 * 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class IterationService:
    """迭代计划的 CRUD + 节奏推导（纯逻辑，可脱离调度器测试）。"""

    def __init__(self, db: Any, windows: Any = None, models: Any = None,
                 rate_limit_status: Any = None):
        self.db = db
        self.windows = windows      # WindowManager（取参与工位的画像）
        self.models = models        # ModelService（取当前模型的 speed 档）
        self.rate_limit_status = rate_limit_status  # callable() -> {severity, ...}

    # ------------------------------------------------------------- 推导
    def effective_plan(self, entry: dict[str, Any]) -> dict[str, Any]:
        """算出这个计划**实际**的节奏与停止条件（预设 → 画像修正 → 手动覆盖）。

        返回的 `derived` 里写明每一项来自哪一层，便于控制台解释
        "为什么它 4 小时才跑一轮"。
        """
        mode = str(entry.get("mode") or DEFAULT_MODE)
        if mode not in PRESETS:
            mode = DEFAULT_MODE
        preset = dict(PRESETS[mode])
        derived: dict[str, str] = {"mode": f"preset:{mode}"}

        # 1) 画像修正：参与工位里最慢的节奏档说了算（木桶原理）
        factor = 1.0
        speeds: list[str] = []
        for wid in (entry.get("window_ids") or []):
            spec_speed = self._role_speed(wid)
            if spec_speed:
                speeds.append(spec_speed)
        if speeds:
            worst = max(speeds, key=lambda s: SPEED_FACTOR.get(s, 1.0))
            factor *= SPEED_FACTOR.get(worst, 1.0)
            if factor != 1.0:
                derived["staff_speed"] = f"slowest:{worst} ×{SPEED_FACTOR.get(worst, 1.0)}"

        # 2) 模型能力表：当前模型的 speed 档 → 倍率
        model_speed = self._current_model_speed()
        if model_speed in MODEL_SPEED_FACTOR:
            mf = MODEL_SPEED_FACTOR[model_speed]
            factor *= mf
            derived["model_speed"] = f"{model_speed} ×{mf}"

        # 3) 限速严重度（T8.8）：正在被限速就把节奏再放慢
        sev = self._current_severity()
        sf = SEVERITY_FACTOR.get(sev, 1.0)
        if sf != 1.0:
            factor *= sf
            derived["rate_limit"] = f"{sev} ×{sf}"

        auto_interval = int(round(preset["speed_minutes"] * factor))
        auto_interval = max(MIN_INTERVAL_MINUTES, min(auto_interval, MAX_INTERVAL_MINUTES))

        # 4) 手动覆盖优先（方案 c：预设 + 覆盖并存）
        speed = int(entry.get("speed_minutes") or 0)
        if speed > 0:
            interval = max(MIN_INTERVAL_MINUTES, min(speed, MAX_INTERVAL_MINUTES))
            derived["interval"] = "manual"
        else:
            interval = auto_interval
            derived["interval"] = "auto"
        cap = int(entry.get("max_rounds") or 0) or int(preset["max_rounds"])
        derived["max_rounds"] = "manual" if int(entry.get("max_rounds") or 0) else f"preset:{mode}"
        npl = int(entry.get("no_progress_limit") or 0) or int(preset["no_progress_limit"])
        derived["no_progress_limit"] = ("manual" if int(entry.get("no_progress_limit") or 0)
                                        else f"preset:{mode}")
        return {
            "mode": mode, "interval_minutes": interval, "auto_interval_minutes": auto_interval,
            "max_rounds": cap, "no_progress_limit": npl,
            "budget_tokens": int(entry.get("budget_tokens") or 0),
            "derived": derived,
            "factor": round(factor, 3),
        }

    def _role_speed(self, window_id: str) -> str:
        if self.windows is None:
            return ""
        try:
            w = self.windows.get(window_id) or self.db.get_window(window_id)
        except Exception:  # noqa: BLE001
            return ""
        if w is None:
            return ""
        from ..agents.core.roles import get_role
        spec = get_role(getattr(w, "role", "") or "")
        return getattr(spec, "iteration_speed", "") if spec else ""

    def _current_model_speed(self) -> str:
        if self.models is None:
            return ""
        try:
            cur = self.models.current().get("model") or ""
            for m in self.db.list_models():
                if m.get("model") == cur:
                    return str(m.get("speed") or "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _current_severity(self) -> str:
        if not callable(self.rate_limit_status):
            return ""
        try:
            return str((self.rate_limit_status() or {}).get("severity") or "")
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------- CRUD
    def create(self, project_id: str, goal: str, mode: str = DEFAULT_MODE,
               window_ids: list[str] | None = None, **overrides: Any) -> dict[str, Any]:
        proj = self.db.get_project(project_id)
        if proj is None:
            raise ValueError(f"项目不存在：{project_id}")
        if not str(goal or "").strip():
            raise ValueError("迭代目标不能为空")
        if mode not in PRESETS:
            raise ValueError(f"未知档位：{mode}（可选 {list(PRESETS)}）")
        ids = [str(x) for x in (window_ids or []) if str(x).strip()]
        if not ids:
            # 默认参与者：挂在项目上的所有工位
            ids = [w.id for w in self.db.list_windows()
                   if (getattr(w, "project_id", "") or "") == proj.id]
        if not ids:
            raise ValueError("没有参与工位：先给项目挂工位，或显式指定 window_ids")
        entry = self.db.insert_iteration({
            "project_id": proj.id, "goal": str(goal).strip(), "mode": mode,
            "window_ids": ids, "status": "active", "round": 0,
            "speed_minutes": int(overrides.get("speed_minutes") or 0),
            "max_rounds": int(overrides.get("max_rounds") or 0),
            "no_progress_limit": int(overrides.get("no_progress_limit") or 0),
            "budget_tokens": int(overrides.get("budget_tokens") or 0),
        })
        plan = self.effective_plan(entry)
        # 立刻排上第一轮（不然新建后要等到下一轮 tick 才算 next_run_at）
        entry = self.db.update_iteration(entry["id"], next_run_at=_iso(_now())) or entry
        self.db.audit("web", "iteration:create",
                      f"{entry['id']} project={proj.id} mode={mode} "
                      f"interval={plan['interval_minutes']}m cap={plan['max_rounds']}")
        return entry

    def list(self, project_id: str | None = None, status: str | None = None) -> list[dict]:
        return self.db.list_iterations(project_id=project_id, status=status)

    def get(self, iteration_id: str) -> dict[str, Any] | None:
        return self.db.get_iteration(iteration_id)

    def update(self, iteration_id: str, **fields: Any) -> dict[str, Any]:
        entry = self.db.get_iteration(iteration_id)
        if entry is None:
            raise ValueError(f"迭代不存在：{iteration_id}")
        allowed = {"goal", "mode", "window_ids", "speed_minutes", "max_rounds",
                   "no_progress_limit", "budget_tokens", "status", "next_run_at"}
        patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if "mode" in patch and patch["mode"] not in MODES:
            raise ValueError(f"未知档位：{patch['mode']}（可选 {list(MODES)}）")
        updated = self.db.update_iteration(iteration_id, **patch) or entry
        self.db.audit("web", "iteration:update", f"{iteration_id} fields={sorted(patch)}")
        return updated

    def stop(self, iteration_id: str, reason: str = "手动停止") -> dict[str, Any]:
        entry = self.db.get_iteration(iteration_id)
        if entry is None:
            raise ValueError(f"迭代不存在：{iteration_id}")
        updated = self.db.update_iteration(iteration_id, status="stopped",
                                           stop_reason=reason, next_run_at="") or entry
        self.db.audit("web", "iteration:stop", f"{iteration_id} {reason}")
        return updated

    def resume(self, iteration_id: str) -> dict[str, Any]:
        entry = self.db.get_iteration(iteration_id)
        if entry is None:
            raise ValueError(f"迭代不存在：{iteration_id}")
        updated = self.db.update_iteration(iteration_id, status="active", stop_reason="",
                                           next_run_at=_iso(_now())) or entry
        self.db.audit("web", "iteration:resume", iteration_id)
        return updated

    def remove(self, iteration_id: str) -> bool:
        ok = self.db.delete_iteration(iteration_id)
        if ok:
            self.db.audit("web", "iteration:delete", iteration_id)
        return ok

    def detail(self, iteration_id: str) -> dict[str, Any]:
        entry = self.db.get_iteration(iteration_id)
        if entry is None:
            raise ValueError(f"迭代不存在：{iteration_id}")
        plan = self.effective_plan(entry)
        return {"iteration": entry, "plan": plan,
                "rounds": self.db.list_iteration_rounds(iteration_id, limit=20)}

    # ------------------------------------------------------------- 停止条件
    def check_stop(self, entry: dict[str, Any], plan: dict[str, Any], *,
                   goal_achieved: bool = False, spent_tokens: int = 0) -> tuple[bool, str]:
        """返回 (是否该停, 原因)。**预算上限是可选字段**（0 = 不限）。"""
        if goal_achieved:
            return True, "已达成目标"
        if int(entry.get("round") or 0) >= int(plan["max_rounds"]):
            return True, f"达到次数上限（{plan['max_rounds']} 轮）"
        if int(entry.get("no_progress_streak") or 0) >= int(plan["no_progress_limit"]):
            return True, f"连续 {plan['no_progress_limit']} 轮无进展"
        budget = int(plan.get("budget_tokens") or 0)
        if budget > 0 and spent_tokens >= budget:
            return True, f"达到预算上限（{budget} tokens）"
        return False, ""

    def next_run_after(self, plan: dict[str, Any]) -> str:
        return _iso(_now() + timedelta(minutes=int(plan["interval_minutes"])))
