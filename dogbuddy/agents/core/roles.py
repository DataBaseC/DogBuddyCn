# DogBuddyCn — role registry (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""RoleSpec registry — extension point for future employee roles & group workspaces."""
from __future__ import annotations

from dataclasses import dataclass, field

# T9.3：主管工具集需要"项目级管理工具"的名字清单。management 模块是角色无关的
# 工具装配层（不反向 import roles），所以这条依赖不会成环。
from ..management.tools import PROJECT_TOOLS


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
    # ---- T9.1 作用域（角色体系 v2 的横切维度）----
    scope_level: str = "none"                          # none | project | system
    capability: str = ""                               # 一句话能力定位（装配/审计用）

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
            "scope_level": self.scope_level,
            "capability": self.capability,
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
- EXCEPTION — the HOST itself: you are the ONLY role that may run commands on the server \
(server_exec, system scope). Use it for host-level chores the owner asks for (tidying \
/var/lib/dogbuddy/workspaces, inspecting disks/logs, restarting a service). NEVER dispatch such \
a chore to an employee window: employees have neither system scope (server_exec is refused \
fail-closed) nor access outside their own workspace (the shell boundary guard blocks absolute \
paths) — the task cannot succeed.

Style: concise, concrete, action-oriented. Reply in the user's language."""

WORKER_PROGRAMMER_SYSTEM_PROMPT = """You are a programmer employee in a persistent workstation (window). \
A long-running project lives in your workspace; you keep working on it across many task runs.

Your toolbox: read/write/edit files, shell, glob/grep, web_search/web_fetch, todos, checkpoints, \
explore_subagent (read-only research), ask_user. You work INSIDE your workspace: absolute paths \
outside it are blocked by the boundary guard, and host-level operations (server_exec) are \
admin-only — use ask_user to ask the admin instead of trying to bypass it.

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
    # v0.10.17：模型**只能从目录里选**（管理员不能自己拉取/编造模型名）。
    # owner 的决定："不搞默认模型了，全从模型目录里自己配；管理员不能自己拉取模型，
    # 只能从模型页里的模型列表选。"
    "model_catalog", "model_select",
    "memory_write", "memory_recall",
    "progress_report",
    # T9.6 审批闭环（P1-6）：主管裁决员工升级上来的高危操作
    "approve_decision", "decision_list",
    # T9.4 指挥链第一跳：管理员 → 主管（P1-1 补的入口；此前四向信息流只有审批一通）
    "supervisor_command",
    # v0.10.20 群组（共享工作频道）：圆桌测试（2026-10-15）里 owner 让管理员
    # "建个群组让他们自己聊"，管理员**没有任何 group 工具**，只能退化成
    # "共享工作区 + 多窗口圆桌"土办法 → DB 0 群组 → 前端群组页永远空白。
    "group_create", "group_list", "group_assign", "group_channel_post",
    # self-upgrade of the running deployment (NOT delegable to an employee window)
    "self_version", "self_update",
    # v0.10.29（owner 实测）：**宿主机运维**。`server_exec` 声明 `required_scope="system"`
    # —— 只有管理员能用；但它以前**只在员工工具箱里**，于是：
    #   ① 员工每次调用都被作用域裁决 fail-closed 拒绝（今天实测：管理员让 worker-1
    #      整理线上工作区 → worker-1 先被拒 server_exec、再被边界守卫拦绝对路径，
    #      任务根本做不到）；
    #   ② 管理员（唯一合法调用者）**没有这个工具**，做不了宿主机级操作，
    #      只能把活派给做不了的员工（它自己在对话里如实报告了"工具未挂载"）。
    "server_exec",
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
    "explore_subagent", "ask_user",
    # 注：**不给** `server_exec`（v0.10.29）。它声明 `required_scope="system"`，
    # 员工的作用域永远过不了作用域裁决 → 挂上只是"白给一个必然被拒的工具"：
    # 白烧 schema token（TPM 紧张时尤其亏），还会把模型引到"被拒→换个方式绕"的死循环
    # （今天实测：worker-1 被拒后改试 shell，又被边界守卫拦 absolute path）。
    # Batch 8 组一：员工也能给自己排期、自决节奏、有事才通知（权限从紧）
    "schedule", "reschedule", "notify_owner",
    # Batch 8 组二：项目迭代轮次的进展汇报（T8.6 停止条件的唯一依据）
    "report_iteration",
    # Batch 9 · T9.5：项目记忆——员工**只读**（防一个员工改掉全项目规矩）
    "project_memory_read",
    # 工位记忆（ROLES.md 权限矩阵「员工 ✅ 本工位」）。只认 ctx.window_id，
    # 模型没有参数可以指向别的工位——所以不需要作用域裁决（审查报告 P1-4）。
    "window_memory_read", "window_memory_write",
    # v0.10.20：只读的本人预算/环境查询（圆桌测试：qa-ops 查不到自己的上下文
    # 预算，`env`/`/proc` 又被边界守卫拒 → 只能瞎跑撞 max iterations）。
    "run_status",
]


SUPERVISOR_SYSTEM_PROMPT = """You are a PROJECT SUPERVISOR (主管) for one or more projects.

Your job is the middle layer of a three-tier chain:
  管理员(admin) → **你(主管)** → 员工工位(worker)

Responsibilities:
- Break the owner's goal into concrete pieces and dispatch them to YOUR project's
  employee windows (`window_list`/`task_create`/`task_run_now` show and drive them).
- Collect results and questions, merge them, and report UP concisely
  (`progress_report`) — do not forward raw noise.
- You may do a small piece of work yourself when that is faster than explaining it
  (you have the full employee toolbox), but your default is to delegate.
- Keep the project's iteration plan moving: check progress, unblock workers.

Hard rules (enforced by the permission gate, not by good intentions):
- You may ONLY touch windows/tasks/groups that belong to projects you supervise.
  Calling a management tool on another project's window WILL be denied.
- You have NO system-level tools: no config, no profiles, no self-update,
  no global memory, no account management. If a task needs those, report upward.
- Group instructions per project to avoid mixing contexts when you supervise several.

Style: terse, technical, no fluff."""

#: 主管工具集 = 员工工具 + **项目级**管理工具（T9.3）
#: 注意：不含任何 system 级工具（config/self_update/账号/全局记忆）——装配层过滤 + 运行时裁决双层保证
SUPERVISOR_TOOLS = sorted(set(WORKER_PROGRAMMER_TOOLS) | set(PROJECT_TOOLS) | {"project_memory_write"})


def register_builtin_roles() -> None:
    register_role(RoleSpec(
        name="admin", display_name="管理员",
        system_prompt=ADMIN_SYSTEM_PROMPT, tool_names=ADMIN_TOOLS,
        default_mode="AUTO", is_admin=True,
        # T8.4 画像：管理员管的是系统本身，改动风险高 → 节奏保守
        domains=["运维", "部署", "配置", "调度"],
        can_deliver_alone=True, needs_approval=False, iteration_speed="slow",
        note="管理员工位：管工位/项目/任务/配置/自升级。改动影响面大，迭代节奏按保守档。",
        scope_level="system", capability="系统级：全部资源与配置"))
    register_role(RoleSpec(
        name="supervisor", display_name="主管",
        system_prompt=SUPERVISOR_SYSTEM_PROMPT, tool_names=SUPERVISOR_TOOLS,
        default_mode="BUILD",
        # T9.1/T9.3：中间层。scope=project（只认自己负责的项目）
        domains=["拆解", "派活", "汇总", "推进"],
        can_deliver_alone=False, needs_approval=False, iteration_speed="standard",
        note="主管工位：三层指挥链的中间层。能派活给本项目员工并收回报，"
             "**不能**碰别的项目、也没有系统级工具（config/自升级/全局记忆）。",
        scope_level="project", capability="项目级：只管自己负责的项目"))
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
