# DogBuddyCn — 指挥链服务 supervision_service (L2 Application, Batch 9 · T9.4)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""三层指挥链的中枢：**管理员 → 主管 → 员工**，四条信息流全部走事件总线解耦。

| 事件 | 方向 | 动作 |
|---|---|---|
| `supervisor.command` | 管理员 → 主管 | 把指令投进主管工位队列 |
| `worker.report` | 员工 → 主管 | 找到项目 owner 主管，投递结果/提问 |
| `supervisor.digest` | 主管 → 管理员 | 主管合并后上报 |
| `project.iteration.step` | 迭代执行器 → 全体 | 轮次推进/停止（仅广播，不需寻址） |

**寻址规则**：事件带 `project_id` → `projects.owner_supervisor_id` → 主管工位。
**退化规则**：项目没有主管（或主管工位不存在）时 `worker.report` **直连管理员**
——这样单管理员部署的行为与 v1 完全一致（零回归）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from ..events.bus import (
    JOB_COMPLETED, JOB_FAILED, JOB_QUIET, RATE_LIMIT_STATUS, WORKER_QUESTION, Event)

logger = logging.getLogger("dogbuddy.services.supervision")

#: 四向事件类型
SUPERVISOR_COMMAND = "supervisor.command"
WORKER_REPORT = "worker.report"
SUPERVISOR_DIGEST = "supervisor.digest"
PROJECT_ITERATION_STEP = "project.iteration.step"


class SupervisionService:
    """指挥链寻址与投递。不直接跑 Agent——只把事件变成"该给谁"。"""

    def __init__(self, db: Any, windows: Any = None, bus: Any = None,
                 admin_notify: Callable[[str, str], Any] | None = None,
                 route_reports: bool = True):
        self.db = db
        self.windows = windows            # WindowManager（投递到工位队列）
        self.bus = bus
        self.admin_notify = admin_notify  # callable(text, level) → 通知管理员（退化路径）
        self.route_reports = route_reports
        self._subscribed = False

    # ------------------------------------------------------------- 启停
    def start(self) -> None:
        if self._subscribed or self.bus is None:
            return
        self.bus.subscribe(SUPERVISOR_COMMAND, self._on_command)
        self.bus.subscribe(WORKER_REPORT, self._on_worker_report)
        self.bus.subscribe(SUPERVISOR_DIGEST, self._on_digest)
        self._subscribed = True
        logger.info("supervision service subscribed (hierarchical routing=%s)",
                    self.route_reports)

    # ------------------------------------------------------------- 寻址
    def supervisor_for(self, project_id: str, window_id: str = "") -> str:
        """项目 → 主管工位 id。找不到返回 ""（调用方走退化路径）。

        优先用显式 `owner_supervisor_id`；没有再按"该项目的工位里 role=supervisor"兜底
        （老数据没维护 owner 字段时也能工作）。
        """
        pid = str(project_id or "").strip()
        if pid:
            proj = self.db.get_project(pid)
            if proj is not None:
                owner = (getattr(proj, "owner_supervisor_id", "") or "").strip()
                if owner:
                    w = self.db.get_window(owner)
                    if w is not None and (getattr(w, "role", "") or "") == "supervisor":
                        return owner
                    logger.warning("project %s 的主管 %s 不存在或不是 supervisor，走退化路径",
                                   pid, owner)
        members = self._project_windows(pid or self._project_of_window(window_id))
        for w in members:
            if (getattr(w, "role", "") or "") == "supervisor":
                return w.id
        return ""

    def _project_of_window(self, window_id: str) -> str:
        if not window_id:
            return ""
        w = self.db.get_window(window_id)
        return (getattr(w, "project_id", "") or "") if w is not None else ""

    def _project_windows(self, project_id: str) -> list:
        if not project_id:
            return []
        try:
            return [w for w in self.db.list_windows()
                    if (getattr(w, "project_id", "") or "") == project_id]
        except Exception:  # noqa: BLE001
            return []

    def workers_of(self, project_id: str) -> list[dict[str, Any]]:
        """项目里的员工（不含主管自己），给主管派活/控制台用。"""
        out = []
        for w in self._project_windows(project_id):
            if (getattr(w, "role", "") or "") == "supervisor":
                continue
            out.append({"id": w.id, "name": w.name, "role": w.role,
                        "status": getattr(w, "status", "")})
        return out

    # ------------------------------------------------------------- 管理员 → 主管
    async def _on_command(self, event: Event) -> None:
        p = event.payload or {}
        pid = str(p.get("project_id") or "")
        text = str(p.get("text") or p.get("prompt") or "").strip()
        sup = self.supervisor_for(pid, p.get("window_id") or "")
        if not text:
            return
        if not sup:
            # 没主管：管理员直接派给项目里的第一个员工（等价于 v1 直连）
            workers = self.workers_of(pid)
            target = workers[0]["id"] if workers else ""
            if not target:
                logger.warning("supervisor.command: 项目 %s 既没主管也没员工，丢弃", pid)
                return
            self._dispatch(target, text, f"管理员指令（项目 {pid} 无主管，直连）")
            return
        self._dispatch(sup, text, f"管理员指令（项目 {pid}）")

    # ------------------------------------------------------------- 员工 → 主管
    async def _on_worker_report(self, event: Event) -> None:
        p = event.payload or {}
        wid = str(p.get("window_id") or "")
        pid = str(p.get("project_id") or self._project_of_window(wid))
        text = str(p.get("text") or p.get("summary") or "").strip()
        if not text:
            return
        if not self.route_reports:
            self._to_admin(f"[员工 {wid}] {text}")
            return
        sup = self.supervisor_for(pid, wid)
        if sup and sup != wid:
            self._dispatch(sup, f"【员工上报 · 工位 {wid}】\n{text}",
                           "员工上报（逐级：员工→主管）")
            return
        # 退化：无主管 → 直连管理员（v1 行为，零回归）
        self._to_admin(f"[员工 {wid} 上报] {text}")

    # ------------------------------------------------------------- 主管 → 管理员
    async def _on_digest(self, event: Event) -> None:
        p = event.payload or {}
        text = str(p.get("text") or p.get("summary") or "").strip()
        if not text:
            return
        sup = str(p.get("window_id") or "")
        pid = str(p.get("project_id") or self._project_of_window(sup))
        level = str(p.get("level") or "info")
        self._to_admin(f"[主管 {sup or '?'} 汇报 · 项目 {pid or '?'}] {text}", level)

    # ------------------------------------------------------------- 投递
    def _dispatch(self, window_id: str, text: str, why: str) -> None:
        if self.windows is None:
            logger.warning("no WindowManager; dropping %s for %s", why, window_id)
            return
        try:
            self.windows.dispatch(window_id, text, urgent=True)
            logger.info("supervision dispatch → %s (%s)", window_id, why)
        except Exception:  # noqa: BLE001 — 投递失败不能炸总线
            logger.exception("supervision dispatch failed for %s", window_id)

    def _to_admin(self, text: str, level: str = "info") -> None:
        if self.admin_notify is None:
            logger.info("admin notify (no channel): %s", text[:200])
            return
        try:
            self.admin_notify(text, level)
        except Exception:  # noqa: BLE001
            logger.exception("admin notify failed")

    # ------------------------------------------------------------- 审批路由（T9.6）
    def approval_target(self, window_id: str, irreversible: bool = False) -> dict[str, Any]:
        """高危操作该找谁批（Batch 4 审批改**逐级**）。

        * **不可逆**动作（rm -rf / mkfs / 关机…）→ 永远升级给管理员（第一道保险）
        * 普通高危 + 有主管 → 先找主管
        * 没有主管 / 主管就是自己 → 直连管理员（退化，与 v1 一致）
        """
        pid = self._project_of_window(window_id)
        sup = self.supervisor_for(pid, window_id) if self.route_reports else ""
        if irreversible:
            return {"target": "admin", "window_id": "", "project_id": pid,
                    "reason": "不可逆操作必须管理员批准"}
        if not sup or sup == window_id:
            return {"target": "admin", "window_id": "", "project_id": pid,
                    "reason": "该项目无主管，直连管理员"}
        return {"target": "supervisor", "window_id": sup, "project_id": pid,
                "reason": "逐级审批：员工→主管"}

    # ------------------------------------------------------------- 便捷入口
    def dispatch_text(self, window_id: str, text: str, why: str = "supervision") -> None:
        """公开的投递入口（审批升级等场景直接调用）。"""
        self._dispatch(window_id, text, why)

    def report_up(self, window_id: str, text: str, project_id: str = "",
                  level: str = "info") -> None:
        """供工具/服务调用：把一条上报按指挥链送出去。"""
        if self.bus is None:
            return
        wid = window_id or ""
        w = self.db.get_window(wid) if wid else None
        role = (getattr(w, "role", "") or "") if w is not None else ""
        pid = project_id or self._project_of_window(wid)
        if role == "supervisor":
            self.bus.emit(Event(type=SUPERVISOR_DIGEST,
                                payload={"window_id": wid, "project_id": pid,
                                         "text": text, "level": level}))
        else:
            self.bus.emit(Event(type=WORKER_REPORT,
                                payload={"window_id": wid, "project_id": pid,
                                         "text": text, "level": level}))

    def command(self, project_id: str, text: str) -> None:
        if self.bus is None:
            return
        self.bus.emit(Event(type=SUPERVISOR_COMMAND,
                            payload={"project_id": project_id, "text": text}))

    # ------------------------------------------------- 生产者入口（审查报告 P1-1）
    def route_report(self, window_id: str, text: str, level: str = "info") -> bool:
        """**把一条上报按指挥链送出去**——这是 `worker.report` / `supervisor.digest`
        在生产代码里的唯一投递入口。

        初版只有 `report_up()`，而它**零调用者**：处理器写得对、测试也绿
        （`test_batch9_supervision.py` 直接调 `svc.report_up(...)`），但生产路径
        根本不会调用它 → 员工的结果与提问仍然直达用户，"逐级收敛"从未发生。

        返回 True = **已交给指挥链**（调用方不要再直连推送）；
        返回 False = 没有主管可走 → 调用方保持 v1 直连行为（零回归）。
        """
        if self.bus is None:
            return False
        wid = str(window_id or "")
        text = str(text or "").strip()
        if not wid or not text:
            return False
        w = self.db.get_window(wid)
        role = (getattr(w, "role", "") or "") if w is not None else ""
        pid = self._project_of_window(wid)
        if role == "supervisor":
            # 主管自己的产出 = 给管理员的汇总（supervisor.digest）
            self.report_up(wid, text, pid, level=level)
            return True
        if not self.route_reports:
            return False
        sup = self.supervisor_for(pid, wid)
        if sup and sup != wid:
            self.report_up(wid, text, pid, level=level)
            return True
        return False

    def routes_to_supervisor(self, window_id: str) -> bool:
        """只问"会不会逐级"（不投递），供 ReportService 决定裁量路径。"""
        if not self.route_reports:
            return False
        w = self.db.get_window(window_id) if window_id else None
        if w is not None and (getattr(w, "role", "") or "") == "supervisor":
            return True
        pid = self._project_of_window(window_id)
        sup = self.supervisor_for(pid, window_id)
        return bool(sup and sup != window_id)


#: 需要**逐级路由**的事件 → 它们交给指挥链时对应的事件类型。
#: ROLES.md §3：员工 → 主管 传的是「结果 / 提问」，所以任务完成与失败也要先到主管
#: （主管才知道该不该重派），再由主管合并上报管理员。
HIERARCHICAL_EVENTS = {
    WORKER_QUESTION: WORKER_REPORT,   # 员工提问 = 上报的一种
    JOB_COMPLETED: WORKER_REPORT,     # 员工结果（逐级收敛，不再直达用户）
    JOB_FAILED: WORKER_REPORT,        # 员工失败（主管要能重派/兜底）
}
#: 明确**不**逐级的事件：这些是环境状态或需要立刻触达人类的事
DIRECT_EVENTS = (JOB_QUIET, RATE_LIMIT_STATUS)

