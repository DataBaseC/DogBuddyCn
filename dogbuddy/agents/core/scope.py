# DogBuddyCn — 作用域（Scope）值对象与解析器 (Batch 9 · T9.1)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""作用域 = 角色体系 v2 的**横切维度**：一个调用者"能碰哪些资源"。

| 角色 | Scope | 能管的资源 |
|---|---|---|
| 管理员 | `system` | 全部（system 覆盖 project） |
| 主管 | `project` | **仅自己负责的项目集**里的工位/任务/群组 |
| 员工 | `none` | 不管资源（也不装配管理工具） |

两条铁律：
1. **由角色推导，不接受自报** —— `ScopeResolver` 从工位/角色算出 Scope，注入 ToolContext。
   模型没法在参数里"声称"自己是管理员。
2. **fail-closed** —— 未声明 scope、无法判定目标资源归属、越界，一律**拒绝**。
   `PermissionGate.check_scope()` 是唯一的裁决点。

数据来源：`projects.owner_supervisor_id`（谁的工位是哪个项目的主管）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("dogbuddy.agents.scope")

#: 作用域等级（数值越大覆盖越广；比较时用）
LEVELS = {"none": 0, "project": 1, "system": 2}


@dataclass(frozen=True)
class Scope:
    """一次调用的作用域快照。不可变，便于安全地传下去。"""

    level: str = "none"                     # none | project | system
    project_ids: frozenset[str] = field(default_factory=frozenset)
    actor: str = ""                         # 谁（工位 id / admin:<user>）
    source: str = ""                        # 推导来源，便于审计与排查

    @property
    def rank(self) -> int:
        return LEVELS.get(self.level, 0)

    @property
    def is_system(self) -> bool:
        return self.level == "system"

    @property
    def is_none(self) -> bool:
        return self.rank == 0

    def allows_project(self, project_id: str) -> bool:
        """能不能管这个项目。system 覆盖一切；project 只认自己的集合。"""
        if self.is_system:
            return True
        if self.rank == 0:
            return False
        pid = str(project_id or "")
        return bool(pid) and pid in self.project_ids

    def covers(self, other: "Scope") -> bool:
        """自己是否覆盖对方（用于"能否管理该资源的属主"判断）。"""
        if self.is_system:
            return True
        if self.rank == 0 or other.rank == 0:
            return False
        return other.project_ids and other.project_ids <= self.project_ids

    def describe(self) -> str:
        if self.is_system:
            return "system(全部)"
        if self.rank == 0:
            return "none(无管理权)"
        return f"project({','.join(sorted(self.project_ids)) or '空'})"


SYSTEM = Scope(level="system", source="system")
NONE = Scope(level="none", source="none")


class ScopeResolver:
    """把工位 / 管理员会话解析成 Scope。**绝不信任入参里的自报身份。**"""

    def __init__(self, db: Any = None):
        self.db = db

    # ---------------------------------------------------------------- 管理员
    def for_admin(self, actor: str = "admin") -> Scope:
        return Scope(level="system", actor=actor, source="role:admin")

    # ---------------------------------------------------------------- 工位
    def for_window(self, window: Any, role: str | None = None) -> Scope:
        """工位 → Scope。主管按 `projects.owner_supervisor_id` 认领项目。"""
        if window is None:
            return NONE
        wid = getattr(window, "id", "") or ""
        role_name = role or getattr(window, "role", "") or ""
        if role_name == "admin":
            return Scope(level="system", actor=wid, source="role:admin")
        if role_name != "supervisor":
            return Scope(level="none", actor=wid, source=f"role:{role_name or 'unknown'}")
        pids = self.supervised_projects(wid)
        return Scope(level="project", project_ids=frozenset(pids), actor=wid,
                     source=f"supervisor:{len(pids)} 个项目")

    def supervised_projects(self, window_id: str) -> set[str]:
        """我是哪些项目的主管。

        先看显式字段 `projects.owner_supervisor_id`；**再按"项目里 role=supervisor 的
        工位"兜底**——老数据没维护 owner 字段时，主管仍然应该拿得到自己的作用域
        （否则会出现"寻址找得到主管、但主管没有任何权限"的死锁）。
        """
        if self.db is None or not window_id:
            return set()
        out: set[str] = set()
        try:
            for p in self.db.list_projects():
                if (getattr(p, "owner_supervisor_id", "") or "") == window_id:
                    out.add(p.id)
            for w in self.db.list_windows():
                if w.id != window_id or (getattr(w, "role", "") or "") != "supervisor":
                    continue
                pid = getattr(w, "project_id", "") or ""
                if pid:
                    out.add(pid)
        except Exception:  # noqa: BLE001 — 解析失败按"没有项目"处理（fail-closed）
            logger.warning("supervised_projects failed for %s", window_id, exc_info=True)
        return out

    # ---------------------------------------------------------------- 资源归属
    def project_of_window(self, window_id: str) -> str:
        if self.db is None or not window_id:
            return ""
        try:
            w = self.db.get_window(window_id)
        except Exception:  # noqa: BLE001
            return ""
        return (getattr(w, "project_id", "") or "") if w is not None else ""

    def project_of_group(self, group_ref: str) -> str:
        if self.db is None or not group_ref:
            return ""
        try:
            g = self.db.get_group(group_ref)
        except Exception:  # noqa: BLE001
            return ""
        return (getattr(g, "project_id", "") or "") if g is not None else ""

    def project_of_job(self, job_id: str) -> str:
        if self.db is None or not job_id:
            return ""
        try:
            job = self.db.get_job(job_id)
        except Exception:  # noqa: BLE001
            return ""
        return self.project_of_window(getattr(job, "window_id", "") if job else "")

    def project_of_iteration(self, iteration_id: str) -> str:
        if self.db is None or not iteration_id:
            return ""
        try:
            entry = self.db.get_iteration(iteration_id)
        except Exception:  # noqa: BLE001
            return ""
        return str((entry or {}).get("project_id") or "")
