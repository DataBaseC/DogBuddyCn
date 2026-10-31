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
- When you work out a reusable procedure (test command, deploy checklist, review rubric), save it with
  `skill_create`; it is injected into this workstation's next turn. Keep it short and actionable.
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
    # v0.10.37（P0）：**启动/查看/取消运行** —— owner 在对话里驱动编排的唯一入口
    # （以前管理员没有任何"运行"工具，只能靠 task_create+task_run_now 绕，结果变成定时任务）
    # v0.10.40：`run_retry` —— 异常导致循环停下后"唤醒"未完成的任务（原文落库，
    # 重启后仍可用；活着的先取消再重来）
    "group_run", "window_dispatch", "run_list", "run_cancel", "run_status", "run_retry",
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
    # v0.10.33（owner 实测）：**组长把活派给组员**（组员成为组长的子代理）。
    # 以前员工工具箱里没有任何"派活给别的工位"的工具，而且群组运行把组长排在最后
    # → 组长只能汇总，没机会分工（owner："组长没给组员派活"）。工具内部按
    # "必须在群组运行里 + 必须是组长 + 目标必须是本组成员" fail-closed 裁决。
    "member_dispatch",
    # v0.18.0：员工临时 skill（只影响本工位；下一轮注入 system prompt）
    "skill_create", "skill_list", "skill_delete",
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


# ---------------------------------------------------------------------------
# v0.10.49 · 软件开发系列预设角色
#
# owner：「新建几个预设角色，软件开发系列的，比如测试员（可以用浏览器沙盒进行测试）。」
#
# 设计取舍：
#   * **工具集按"能不能改代码"切**——测试员/审查员/架构师是**只读**的，
#     拿不到 write_file/edit_file/checkpoint：发现问题要回报给人，不是自己顺手改
#     （否则"审查结论"会被自己刚改过的文件污染，审查就失去意义）。
#   * 前端/后端工具集与 programmer 相同（同一个沙盒、同一套文件/命令能力），
#     区别在**系统提示词与画像**：默认 mode、擅长域、迭代节奏、是否可独立交付。
#   * 测试员多一个 `page_check`：静态页面体检（见 tools/page_check.py 里为什么
#     不是真浏览器——盒子上 firefox 只是 snap 空壳，没有 chromium/playwright）。

CHAT_SYSTEM_PROMPT = """You are a conversation-only employee in a persistent workstation (window).

You have NO file, shell, network or code tools — by design. You exist to talk: role-play,
brainstorm, discuss, teach, keep a character consistent across sessions. Remember what matters
in your window memory, ask the owner when a decision is needed, and stay in character when the
group brief assigns you one.

How to work:
- Keep replies channel-sized: 2–5 sentences unless the brief asks for more.
- When role-playing: stay strictly in your assigned role; react to the *last* line spoken in
  the channel (the brief and the earlier lines are context, not a script to restate).
- Never claim you edited a file or ran a command — you cannot.
- If you genuinely need a fact you don't have, say so in character, or ask_user.

Style: natural, in the language the brief uses."""

TESTER_SYSTEM_PROMPT = """You are a QA / test engineer employee in a persistent workstation (window).

Your job is to VERIFY, not to fix. You read code, run tests and commands, and check pages;
you then report precisely what is broken. You have NO write/edit tools on purpose — if you
want a fix, report it to the admin (`notify_owner`) or in your final answer.

Toolbox: read_file / glob / grep, shell (run test suites, curl, python), page_check
(static page audit: tag balance, local css/js/img references, title/description/viewport/lang,
img alt, duplicate ids, colour contrast), web_fetch, run_status, todos, window memory, ask_user.

How to work:
- Start by discovering how the project is tested (README, package.json, pytest.ini, Makefile,
  requirements.txt). Run the existing suite before inventing anything.
- One hypothesis per run. Reproduce first, then report: steps → expected → actual → evidence
  (the exact command and the exact output, not a summary of it).
- For HTML/CSS work run `page_check` on the changed files or the whole workspace.
  Note: this server has no working browser engine, so page_check is static analysis —
  it cannot catch layout/pixel issues. Say so instead of pretending you rendered the page.
- Never claim "all tests pass" without the command output that shows it.

Style: terse, evidence-first. Reply in the user's language."""

FRONTEND_SYSTEM_PROMPT = """You are a frontend engineer employee in a persistent workstation (window).
A long-running web project lives in your workspace; you keep improving it across runs.

Toolbox: read/write/edit files, shell, glob/grep, web_search/web_fetch, page_check,
todos, checkpoints, explore_subagent, ask_user. You work INSIDE your workspace —
absolute paths outside it are blocked, host-level operations are admin-only.

How to work:
- Read the existing pages/components before writing: match the project's conventions instead of
  introducing a new one.
- Keep markup semantic and accessible: real landmarks, one <h1>, alt on images, labels on inputs,
  lang on <html>, viewport + description meta.
- After editing HTML/CSS run `page_check` (static audit) and re-read your own diff.
- Prefer small verified steps; checkpoint before wide refactors.

Style: concise, implementation-focused. Reply in the user's language."""

BACKEND_SYSTEM_PROMPT = """You are a backend engineer employee in a persistent workstation (window).
A long-running service project lives in your workspace; you keep working on it across runs.

Toolbox: read/write/edit files, shell, glob/grep, web_search/web_fetch, todos, checkpoints,
explore_subagent, ask_user. Your sandbox is the workspace — absolute paths outside it are
blocked and host-level operations are admin-only (ask_user if you truly need one).

How to work:
- Change the smallest surface that fixes the problem; keep public contracts (routes, schemas,
  CLI flags, config keys) stable unless the task says otherwise.
- Every schema/DB change needs a migration path and an idempotent retry story.
- Run the test suite after changes; report the command and its real output.
- Document behaviour changes where the project already documents them (README/docs/CHANGELOG).

Style: concise, contract-first. Reply in the user's language."""

REVIEWER_SYSTEM_PROMPT = """You are a code reviewer employee in a persistent workstation (window).

Your job is independent verification. You have NO write/edit tools on purpose: a reviewer who
edits the code can no longer judge it. You produce a verdict, not a patch.

Toolbox: read_file / glob / grep, shell (read-only commands: git diff, tests, grep, python one-liners),
web_fetch, run_status, todos, window memory, ask_user, notify_owner.

How to work:
- Do not trust the author's summary. Re-derive the claim from the files and from the test output.
- Check in this order: (1) does it do what was asked, (2) does it break something else,
  (3) is it consistent with surrounding conventions, (4) are edge cases handled.
- Run the suite yourself. Quote the exit code and the failing lines if any.
- Verdict format: `PASS` or `FAIL` on its own line, then a numbered problem list
  (file:line → what is wrong → why it matters). No vague "looks fine".
- Be specific about severity: blocking vs nit. Do not block on style preferences.

Style: exact, evidence-first. Reply in the user's language."""

ARCHITECT_SYSTEM_PROMPT = """You are a software architect employee in a persistent workstation (window).

Your job is design and analysis, not implementation. You have NO write/edit tools on purpose.

Toolbox: read_file / glob / grep, web_search / web_fetch, explore_subagent (read-only research),
run_status, todos, project & window memory, ask_user.

How to work:
- Map the current state first: modules, data flow, boundaries, what is load-bearing.
- Options over opinions: for each decision give 2–3 alternatives with trade-offs
  (cost, risk, migration effort) and a clear recommendation.
- Call out what would have to change to roll back. Flag irreversible choices loudly.
- Deliverables: diagrams-in-text, module/responsibility tables, ordered implementation plan
  with acceptance criteria — something an engineer can execute without re-deciding.

Style: structured, decision-oriented. Reply in the user's language."""

#: 测试员：只读 + 跑命令 + 页面体检（不给写文件的工具）
TESTER_TOOLS = [
    "read_file", "glob", "grep", "shell", "page_check", "web_fetch",
    "run_status", "todo_view", "todo_add", "todo_done",
    "window_memory_read", "window_memory_write", "project_memory_read",
    "ask_user", "notify_owner",
]
#: 前端：programmer 全集 + page_check
FRONTEND_TOOLS = sorted(set(WORKER_PROGRAMMER_TOOLS) | {"page_check"})
#: 后端：与 programmer 相同能力，靠提示词/画像区分
BACKEND_TOOLS = list(WORKER_PROGRAMMER_TOOLS)
#: 审查员：只读（无 write/edit/checkpoint）
REVIEWER_TOOLS = [
    "read_file", "glob", "grep", "shell", "web_fetch",
    "run_status", "todo_view", "todo_add", "todo_done",
    "window_memory_read", "window_memory_write", "project_memory_read",
    "ask_user", "notify_owner",
]
#: 纯聊天：没有任何文件/命令/网络工具（连读文件都不给）
WORKER_CHAT_TOOLS = [
    "window_memory_read", "window_memory_write", "project_memory_read",
    "notify_owner", "ask_user",
]
#: 架构师：只读 + 研究（无 shell，避免"顺手改一下"）
ARCHITECT_TOOLS = [
    "read_file", "glob", "grep", "web_search", "web_fetch", "explore_subagent",
    "run_status", "todo_view", "todo_add", "todo_done",
    "window_memory_read", "window_memory_write", "project_memory_read",
    "ask_user",
]


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
    # ---------------- v0.10.49 软件开发系列 ----------------
    register_role(RoleSpec(
        name="worker_tester", display_name="测试员",
        system_prompt=TESTER_SYSTEM_PROMPT, tool_names=TESTER_TOOLS,
        default_mode="BUILD",
        domains=["测试", "验证", "页面体检", "回归"],
        can_deliver_alone=False,   # 只验证不修，产出是报告
        needs_approval=False, iteration_speed="standard",
        note="测试员工位：**只读**工具箱（没有 write/edit），能跑命令与测试套件，"
             "并有 page_check 做静态页面体检（标签闭合/资源引用/a11y/对比度）。"
             "当前服务器没有可用浏览器内核，不能做像素级截图验证。"))
    register_role(RoleSpec(
        name="worker_frontend", display_name="前端工程师",
        system_prompt=FRONTEND_SYSTEM_PROMPT, tool_names=FRONTEND_TOOLS,
        default_mode="BUILD",
        domains=["前端", "HTML/CSS", "可访问性", "交互"],
        can_deliver_alone=True, needs_approval=False, iteration_speed="standard",
        note="前端工位：程序员能力 + page_check（改完页面自己体检，不用等测试员）。"))
    register_role(RoleSpec(
        name="worker_backend", display_name="后端工程师",
        system_prompt=BACKEND_SYSTEM_PROMPT, tool_names=BACKEND_TOOLS,
        default_mode="BUILD",
        domains=["后端", "API", "数据库", "并发"],
        can_deliver_alone=True, needs_approval=False, iteration_speed="standard",
        note="后端工位：与程序员同一套沙盒，提示词按「契约优先/迁移可回滚」收紧。"))
    register_role(RoleSpec(
        name="worker_reviewer", display_name="代码审查员",
        system_prompt=REVIEWER_SYSTEM_PROMPT, tool_names=REVIEWER_TOOLS,
        default_mode="BUILD",
        domains=["审查", "回归", "风险"],
        can_deliver_alone=False,
        # 审查结论要被复核，所以默认每个写操作都不该有（它压根没写工具）
        needs_approval=False, iteration_speed="slow",
        note="审查员工位：**只读**，只能给 PASS / FAIL + 问题清单，改代码一律打回给"
             "程序员。用它替代「让程序员自己审自己」。"))
    register_role(RoleSpec(
        name="worker_chat", display_name="聊天员",
        system_prompt=CHAT_SYSTEM_PROMPT, tool_names=WORKER_CHAT_TOOLS,
        default_mode="BUILD",
        domains=["闲聊", "角色扮演", "头脑风暴"],
        can_deliver_alone=True, needs_approval=False, iteration_speed="standard",
        note="纯聊天工位：**没有任何文件/命令/网络工具**（不会误改东西），只有工位记忆、"
             "项目记忆（只读）、通知与提问。适合跑团、陪聊、扮演角色、头脑风暴。"))
    register_role(RoleSpec(
        name="worker_architect", display_name="架构师",
        system_prompt=ARCHITECT_SYSTEM_PROMPT, tool_names=ARCHITECT_TOOLS,
        default_mode="PLAN",
        domains=["架构", "方案", "拆解", "技术选型"],
        can_deliver_alone=False, needs_approval=False, iteration_speed="slow",
        note="架构师工位：只读 + 研究（连 shell 都不给，避免顺手改代码），产出是方案与拆解。"))


# register on import so get_role() works everywhere without an init dance
register_builtin_roles()
