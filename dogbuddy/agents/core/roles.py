# DogBuddyCn — role registry (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""RoleSpec registry — extension point for future employee roles & group workspaces."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RoleSpec:
    name: str
    display_name: str
    system_prompt: str
    tool_names: list[str] = field(default_factory=list)
    default_mode: str = "BUILD"
    is_admin: bool = False
    # ---- 员工画像（T8.4）——管理员据此判断"这个员工能干什么、要不要盯"
    domains: list[str] = field(default_factory=list)   # 擅长域
    can_deliver_alone: bool = False                    # 能否独立交付（不需要人接手）
    needs_approval: bool = False                       # 每个写操作是否都要人先批
    iteration_speed: str = "standard"                  # slow | standard | fast：推进节奏档
    note: str = ""                                     # 画像备注（写给人看）

    @property
    def tool_breadth(self) -> str:
        """工具集广度：由 tool_names 数量推导（narrow < 8 ≤ standard < 18 ≤ broad）。"""
        n = len(self.tool_names)
        if n < 8:
            return "narrow"
        return "broad" if n >= 18 else "standard"

    def profile(self) -> dict:
        """给控制台/迭代计划用的画像快照。"""
        return {
            "name": self.name, "display_name": self.display_name,
            "domains": list(self.domains),
            "tool_breadth": self.tool_breadth,
            "tool_count": len(self.tool_names),
            "can_deliver_alone": self.can_deliver_alone,
            "needs_approval": self.needs_approval,
            "iteration_speed": self.iteration_speed,
            "default_mode": self.default_mode,
            "is_admin": self.is_admin,
            "note": self.note,
        }


_REGISTRY: dict[str, RoleSpec] = {}


def register_role(spec: RoleSpec) -> None:
    _REGISTRY[spec.name] = spec


def get_role(name: str) -> RoleSpec | None:
    return _REGISTRY.get(name)


def list_roles() -> list[RoleSpec]:
    return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# V1 builtin roles

ADMIN_SYSTEM_PROMPT = """You are the DogBuddyCn administrator — the single human interface of a \
digital team of AI employees running on a server.

Your job:
- Talk to the owner (via QQ or the web console) and translate their intent into management actions.
- Manage windows (employee workstations), workspaces, scheduled tasks, config and memory with your tools.
- Receive task results and employee questions via internal events; decide what to forward to the user \
(report strategy: silent / brief / detailed). You may merge several results into one message.
- When asked "what's the status / progress", call progress_report and answer concisely.
- You do NOT do programming work yourself — dispatch it to an employee window instead.
- EXCEPTION — the software itself: when the owner asks about the version, or asks to upgrade / \
update "yourself" / "the software", use self_version (check) and self_update (upgrade). NEVER \
dispatch a self-upgrade to an employee window: an employee cannot restart the service, and its \
sandbox is not the running deployment.

Style: concise, concrete, action-oriented. Reply in the user's language."""

WORKER_PROGRAMMER_SYSTEM_PROMPT = """You are a programmer employee in a persistent workstation (window). \
A long-running project lives in your workspace; you keep working on it across many task runs.

Your toolbox: read/write/edit files, shell, glob/grep, web_search/web_fetch, todos, checkpoints, \
explore_subagent (read-only research), ask_user, server_exec (server commands via the panel).

How to work:
- The todo list drives your autonomous work ("continue the todos" runs): pick the next undone item, \
do it, mark it done, add follow-ups as you learn.
- Before big changes use checkpoint_create; use checkpoint_rollback when something goes wrong.
- Prefer small verified steps: run tests/commands after edits.
- When blocked on a real decision, use ask_user — the admin will relay it to the owner.
- Finish each run with a short summary of what changed and what is next.

Style: terse, technical, no fluff."""

ADMIN_TOOLS = [
    "window_create", "window_list", "window_status", "window_update",
    "window_pause", "window_resume", "window_delete",
    "workspace_create", "workspace_list", "workspace_assign",
    "task_create", "task_list", "task_update", "task_pause", "task_resume",
    "task_remove", "task_run_now",
    "context_view", "context_compress", "context_reset", "context_inject",
    "config_get", "config_set", "report_set",
    "profile_list", "profile_save", "profile_remove",
    "memory_write", "memory_recall",
    "progress_report",
    # self-upgrade of the running deployment (NOT delegable to an employee window)
    "self_version", "self_update",
]
# 注意：ADMIN_TOOLS 必须**恰好等于** `agents/admin/tools.py` 里注册的工具集
# （管理员用的是自己独立的注册表，不含通用工具箱；`schedule`/`reschedule`/
#  `notify_owner` 属于员工通用工具箱，所以只在下面 WORKER_PROGRAMMER_TOOLS 里）。
# 不变量测试：tests/test_self_upgrade_tool.py::test_admin_role_exposes_every_registered_tool

WORKER_PROGRAMMER_TOOLS = [
    "read_file", "write_file", "edit_file", "shell",
    "glob", "grep", "web_search", "web_fetch",
    "todo_view", "todo_add", "todo_done",
    "checkpoint_create", "checkpoint_rollback",
    "explore_subagent", "ask_user", "server_exec",
    # Batch 8 组一：员工也能给自己排期、自决节奏、有事才通知（权限从紧）
    "schedule", "reschedule", "notify_owner",
    # Batch 8 组二：项目迭代轮次的进展汇报（T8.6 停止条件的唯一依据）
    "report_iteration",
]


def register_builtin_roles() -> None:
    register_role(RoleSpec(
        name="admin", display_name="管理员",
        system_prompt=ADMIN_SYSTEM_PROMPT, tool_names=ADMIN_TOOLS,
        default_mode="AUTO", is_admin=True,
        # T8.4 画像：管理员管的是系统本身，改动风险高 → 节奏保守
        domains=["运维", "部署", "配置", "调度"],
        can_deliver_alone=True, needs_approval=False, iteration_speed="slow",
        note="管理员工位：管工位/项目/任务/配置/自升级。改动影响面大，迭代节奏按保守档。"))
    register_role(RoleSpec(
        name="worker_programmer", display_name="程序员",
        system_prompt=WORKER_PROGRAMMER_SYSTEM_PROMPT, tool_names=WORKER_PROGRAMMER_TOOLS,
        default_mode="BUILD",
        # T8.4 画像：能读写文件、跑 shell、建检查点、自助排期 → 可独立交付
        domains=["后端", "前端", "测试", "重构"],
        can_deliver_alone=True, needs_approval=False, iteration_speed="standard",
        note="程序员工位：能读写代码、跑命令、建检查点回滚、给自己排期。默认可独立交付。"))


# register on import so get_role() works everywhere without an init dance
register_builtin_roles()
