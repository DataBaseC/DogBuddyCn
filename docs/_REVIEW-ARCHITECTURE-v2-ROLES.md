# 精读报告：角色架构 v2（ARCHITECTURE-v2-ROLES.md + ROLES.md）

> 精读对象：`docs/ROLES.md`（83 行，角色职能权威定义，版本 v1.0）、`docs/ARCHITECTURE-v2-ROLES.md`（181 行，架构落地，版本 v2.0）。两文均为 2026-10-14。
> 校验基准：仓库实际代码（`dogbuddy/`，`SCHEMA_VERSION = 4`）。

## 0. 先说一个贯穿全文的校验结论

**这两份文档是纯设计文档，v2 在代码里零落地。** 硬证据：

- `db.py:84` 仍是 `SCHEMA_VERSION = 4`，全文件 grep `project` 命中 **0** 次；
- `dogbuddy/agents/` 下只有 `admin` / `core` / `worker_programmer` 三个目录，**无 `supervisor/`、无 `management/`**；
- `agents/core/roles.py` 全文无 `scope`/`supervisor`，`RoleSpec` 字段仍是 `name/display_name/system_prompt/tool_names/default_mode/is_admin`，**没有文档承诺的 `scope_level` / `capability`**。

另外要纠正提问里的两处前提：**「审批 approval」「能力 capabilities」「决议 decisions」「台账 ledger」「group/团队」这些词，在指定的这两份文档里并不存在**（ledger / decisions / approval / capabilities 仅出现在 `docs/ROADMAP.md`）。`services/decisions.py`、`services/group_manager.py`、`web/routes/capabilities.py`、`web/routes/ledger.py`、`external/mcp_bridge.py` 都是**代码里已存在**的文件（Batch 1-5 成果），但 v2 两文只在一个地方提到它们——v2 §2 第 41 行把 `decisions` 列为「**不变**」。下文第 4 节会区分「文档提到的」与「用户列出的」。

## 1. v2 角色模型的核心变化

文档实际定义的是**三层三角色**，不是多角色矩阵：

| 角色名 | 职责 | 关键工具 | 默认权限模式 |
|---|---|---|---|
| `admin` 管理员 | 「负责系统」：定战略目标、创建项目、配置资源、任免主管、审批不可逆操作、收主管汇总。可多个，分管不同域（ROLES §1 表：「系统总负责人；可多个（分管系统不同域）」） | 全部管理工具（system scope 覆盖）；**不亲自干活**——「执行任务（文件 / shell / 搜索 / 代码）❌ 不亲自干」（ROLES §2） | 文档未在 v2 两文中给出模式名；代码现状 `default_mode="AUTO"`，v2 承诺「管理员角色改为引用 management + system 装配——**管理员行为逐位不变**」 |
| `supervisor` 主管 | 「负责项目」：拆解目标 / 派活 / 盯进度 / 汇总回报 / 裁决员工提问。**归属员工层** | **`agents/management/tools.py`（角色无关）**中 `required_scope = project` 的部分：`window_*` `task_*` `context_*` `group_*` `workspace_*`（v2 §3.2 表）。**不得**使用 `config_*` `profile_*` `self_update` `admin_*` `memory_*`(global) | scope = `project`（仅自己负责的项目集）。ROLES §4：「主管 = 一个装了「管理工具」的工位（window.role = `supervisor`），作用域 = 其所属项目」 |
| `worker_programmer` 员工 | 「负责执行」：接任务、干活、按时回报、遇阻提问。「程序员」只是一种，**可扩展工种** | 16 个员工工具，**不变**：「`agents/worker_programmer/`（不变，员工一种）」（v2 §2） | 文档未变更；代码现状 `BUILD`。ROLES §2：员工**不能派活**、管理能力全 ❌ |

关键权限边界（ROLES §2 权限矩阵）：

- **管理员 vs 主管的分界线是 scope，不是工具集**：v2 §3.2「**一套工具代码，两个角色复用**；可用性由「角色装配时按 `required_scope` 过滤工具集」+「运行时按 Scope 过滤资源」双层保证」。
- **员工无管理 scope**：v2 §3.1「员工 → 无管理 scope（不装配管理工具）」。
- **记忆三档**：admin 全局 / supervisor 项目内 / 员工本工位（ROLES §2）。
- **fail-closed**：v2 §3.1「未声明 scope 或越界 → 拒绝（fail-closed）」；§7 第 5 项「scope 未声明 / 资源越界一律拒绝，主管越权不可达」。

## 2. 与 v1 的差异

v1 两角色在 v2 的归宿：

- **`admin` 从「工具的拥有者」变成「装配结果的薄壳」**：v2 §5 目录表 `admin/ ✦薄壳：引用 management + system 装配`；§1 表把「管理工具」一栏从「`agents/admin/tools.py` 管理员专属」改为「`agents/management/tools.py` 角色无关，按 scope 过滤」。这依赖一个未说明的等价性：`agents/admin/tools.py` 现有 **32 个** 工具（实测 `roles.py:70-83`）拆成 management 后，`admin` 拿到的仍是同一批，故称「行为逐位不变」（§7 第 7 项）。
- **`worker_programmer` 基本不动**：v2 §2、§5 两处标注「不变」。它成了「员工」这一层的**唯一现存实例**，但 ROLES §1 明确「『程序员』是其中一种，可扩展工种」。
- **新增 `supervisor`**：v1 完全不存在的角色，是 v2 唯一新增角色。

**v2 新增的概念**（按文档原词）：

| 概念 | 出处原句 |
|---|---|
| **scope / 作用域**（第 4 个裁决维度） | v2 §1 表：权限裁决从「模式 × 权限 × 危险命令」→「模式 × 权限 × 危险命令 × **作用域(scope)**」；`Scope{level: system\|project, project_ids: frozenset[str]}` |
| **`ScopeResolver`** | v2 §3.1「在装配时把 actor（当前会话 / 工位）解析为 Scope，注入 `ToolContext`」 |
| **`required_scope` 元数据** | v2 §3.2「工具名、schema 不变（提示词契约稳定），只加 `required_scope` 元数据」 |
| **`projects` 实体，owner = 主管** | v2 §1 表；ROLES §5「Batch 7 项目层｜项目 owner = **主管**（非管理员）」 |
| **团队/群组**: 共享频道 = 项目协作空间，主管是群主 | ROLES §3「**共享频道** = 项目协作空间，**主管是群主**」；§5 决策 10「主管是群组组织者与组长」。注意 `groups` **表 v1 已有**，v2 只是加 `groups.project_id` |
| **审批逐级（approval）** | ROLES §3「**审批逐级**：员工触发的审批先到项目主管（普通操作可就地放行），不可逆 / 越项目边界 → 升级管理员」 |
| **指挥链四向信息流 + 4 个事件** | v2 §3.3 表 |
| **迭代调度 `iteration_service`** | v2 §1 表「iteration_service（主管执行，Batch 8）」 |
| **记忆 `project` 作用域** | v2 §1 表「global / **project** / window」；§4「`memory/projects/<id>.md`（项目级记忆，主管读写，员工只读）」 |
| **多管理员实体化** | v2 §4「`admins(...)` # 多管理员实体化」；ROLES §1「可多个（分管系统不同域）」 |
| **模型能力表 `llm/capability.py`** | v2 §2 L4「★新（模型能力表，Batch 8 T8.4）」 |

**明确未变的**（v2 §2）：五层铁律、DI、单进程 asyncio；`agents/core/loop.py`「不变：同一循环服务所有角色」；`tools/*`「不变，工具只管能力不含业务」；`config.py · storage/ · external/*（不变）`；以及 §1「契约稳定：既有工具名 / API / 事件名不变，只增不改」。

## 3. 新增的协作机制（流程化）

**流程 A：管理员 → 主管下发目标**
管理员（system scope）产出目标 → 发 `supervisor.command` 事件 → `supervision_service` 查 `projects.owner_supervisor_id` 定位主管工位 → **投递到主管工位串行队列** → 主管会话按 project 分段处理。（v2 §3.3；§7 第 9 项「主管工位串行队列天然串行化多项目指令」）

**流程 B：主管派活 → 员工执行**
主管调用 scoped 管理工具（`task_*` / `window_*`，`required_scope=project`）→ `PermissionGate` 校验「目标资源 ∈ 调用者 Scope」→ 越界即拒（fail-closed）→ 合法则投递员工工位队列。（v2 §3.1、§3.2）

**流程 C：员工上报（逐级收敛）**
员工完成/遇阻 → 发 `worker.report`（带 `project_id`）→ 查 `projects.owner_supervisor_id` → 投递主管 → 主管「合并后按策略上报」发 `supervisor.digest` 给管理员。（v2 §3.3；ROLES §3「汇报不再『员工直达管理员』，而是逐级收敛」）

**流程 D：审批升级**
员工触发审批 → **先到项目主管**，普通操作主管**就地放行** → 若为「不可逆 / 越项目边界」→ **升级管理员**。（ROLES §3；ROLES §5「Batch 4 审批托管｜路由改**逐级**：员工 → 主管 → 管理员」）

**流程 E：退化路径（唯一的兼容开关）**
「**退化规则**：项目无主管时，`worker.report` 直连管理员（兼容现有单管理员部署）」（v2 §3.3）；§6 第 4 条「无 `project_id` 事件走原直连路径（行为不变）」。

**流程 F：迭代轮次**
`project.iteration.step` 事件「迭代执行器 → 全体」，消费方动作「轮次推进 / 停止判定」（v2 §3.3）；ROLES §5「迭代执行者 = **主管**（拉项目团队跑轮次、汇报进展）」。

**防越权机制汇总**：① scope 由角色推导、装配期过滤工具集（静态）；② 运行时 Gate 校验资源归属（动态）；③ 未声明 scope 一律拒绝；④ 专项测试兜底「主管改他人工位须失败」（v2 §7 风险表）。

## 4. 涉及的代码模块

**文档明确提到的（按文档原文）**：

| 模块 | 文档说明 | 代码现状 |
|---|---|---|
| `services/project_service.py` | ★L2「项目 CRUD / 归属 / 成员」（v2 §5） | **不存在** |
| `services/supervision_service.py` | ★L2 指挥链四向信息流（v2 §3.3） | **不存在** |
| `services/iteration_service.py` | ★L2 迭代计划 + 执行器（v2 §5） | **不存在** |
| `services/report_service.py` | ✦改「汇报改为逐级路由」（v2 §2、§5） | 存在 |
| `services/decisions.py` | 仅被列为「**不变**」（v2 §2 第 41 行） | 存在 |
| `agents/core/scope.py` | ★L3 `Scope` 值对象 + `ScopeResolver`（v2 §3.1） | **不存在** |
| `agents/core/permissions.py` | ✦改「Gate 增作用域维度，fail-closed」（v2 §2） | 存在 |
| `agents/core/roles.py` | ✦改「RoleSpec + scope_level/capability」（v2 §2） | 存在，字段未加 |
| `agents/core/loop.py` | 「不变：同一循环服务所有角色」 | 存在 |
| `agents/management/tools.py` | ★迁「由 `admin/tools.py` 统一化，按 scope」（v2 §3.2） | **不存在** |
| `agents/supervisor/` | ★新「主管角色装配：管理工具 + 派活」（v2 §2） | **不存在** |
| `agents/admin/` | ✦「薄壳：引用 management + system 装配」（v2 §5） | 存在（含 tools.py） |
| `agents/worker_programmer/` | 「不变」（v2 §5） | 存在 |
| `llm/capability.py` | ★L4 模型能力表（Batch 8 T8.4） | **不存在** |
| `llm/retry.py` | ✦改「智能退避，Batch 8 T8.7」 | 存在 |
| `web/routes/projects.py` | ★L1（v2 §2、§5） | **不存在** |
| `web/routes/iterations.py` | ★L1（v2 §2、§5） | **不存在** |
| `web/routes/auth.py` | 「(多管理员账号)」（v2 §2） | 存在 |
| `db.py` | 「迁移：projects / windows.project_id / groups.project_id / admins」 | 存在，未迁移 |

**用户列出但这两份文档未提及的**：`web/routes/capabilities.py`、`services/group_manager.py`、`external/mcp_bridge.py`、`web/routes/approval*`（此文件不存在）、`web/routes/decisions.py`、`web/routes/ledger.py`、`services/window_manager.py`。其中 `window_manager` 仅作为依赖被一笔带过（v2 §7 第 2 项）。**并请注意 `services/group_manager.py` 与 v2 用词不一致**：v2 §2 把 `group` 归入「services/*（…group…不变）」。

## 5. 数据模型变化

v2 §4 原文：

```
projects(id, name, root_path, owner_supervisor_id, params_json, created_at)
  ↑ windows.project_id        (工位归属项目)
  ↑ groups.project_id         (共享频道归属项目)
  ↑ messages.project_id       (可选：项目级消息索引)
admins(id, name, qq_id, role_level, status, created_at)   # 多管理员实体化
```

- 三张既有表加外键列：`windows` / `groups`（标为必加）、`messages`（标为**可选**）；
- 一张新表 `projects`；一张新表 `admins`；
- 记忆侧无表，改文件布局 `memory/projects/<id>.md`，权限「主管读写，员工只读」（v2 §4）；
- 现状对照：`db.py` 只有 `windows/messages/compactions/todos/jobs/job_runs/pending_decisions/groups/group_members/config/whitelist/audit_log/schema_version`，**无 projects、无 admins、无 project_id**。

## 6. 明确的迁移 / 兼容要求

v2 §6「迁移路径（v1 → v2 兼容，**零停机**）」五条：

1. **角色**：「保留 `admin` / `worker_programmer` 名称，新增 `supervisor`（**旧数据不动**）」——两个老角色名不改，不重命名；
2. **管理工具**：「`admin/tools.py` 逻辑迁入 `management/tools.py`，`admin` 角色改为引用 management + system scope 装配——**管理员行为逐位不变**（回归保障）」；
3. **项目**：「迁移建「默认项目」，所有存量工位归入，owner 指向管理员工位」；
4. **汇报**：「`report_service` 增加逐级路由；**无 `project_id` 事件走原直连路径（行为不变）**」；
5. **DB**：「走 `_MIGRATIONS` + `SCHEMA_VERSION` 递增，**旧库自动升级**」。

遗留数据兜底：v2 §4「无 `project_id` 的工位/群组 → 自动归入「默认项目」，owner = 初始管理员工位」；ROLES §6「无项目归属的遗留资源 → 自动归入「默认项目」，避免迁移断档」。

**破坏性变更判断**：文档**声明零破坏性**（§7 第 7 项「✅ 向后兼容——角色名保留、默认项目兜底、汇报退化直连；管理员路径行为逐位不变」；§0「契约稳定：既有工具名 / API / 事件名不变，只增不改」）。但需注意：老 `jobs` 表**在两文中完全未被提及**（无字段变更、无归属项目的要求），而 `windows/jobs` 的现状是两者都有 `window_id` 关联——文档没有说明 job 是否也要挂 `project_id`。

## 7. 承诺但可能没实现的点，以及含糊 / 矛盾 / 未定义处

**A. 文档承诺 vs 代码事实（全部未实现）**：第 4 节表中标「**不存在**」的 7 个 ★新模块 + `agents/management/` 全部缺失，`roles.py` 未加 `scope_level`/`capability`，`db.py` 仍是 v4 且无 projects。ROADMAP 自称「Batch 1-5 全部完成」，而 v2 把主管/Scope/指挥链归入「**Batch 9（新增）**」（v2 §8），项目层 Batch 7、迭代 Batch 8——**即 Batch 6-9 均未开始**，v2 属前瞻设计。

**B. 批次咬合存在时序矛盾（文档自称已检查）**：v2 §7 第 8 项打勾「✅ Batch 4 审批逐级 / Batch 7 项目 owner=主管 / Batch 8 迭代执行者=主管，一致」，并在 §8 把「角色 `supervisor` + 管理工具统一化 + Scope 裁决」与「逐级汇报 + 指挥链服务」都放在 **Batch 9**。但 Batch 4（审批逐级）与 Batch 8（迭代执行者=主管）都**早于 Batch 9**，即这两批要依赖尚未存在的 `supervisor` 与 scope 机制。**这是文档内部最实质的矛盾**，§7 第 8 项的「✅」不成立。同理 §8 注明「审批逐级路由｜Batch 4（**修正为逐级**）」，等于要求回头改已完成批次。

**C. 未定义 / 需要自行决策的点**：

1. **`worker_report` 寻址失败怎么办**：§3.3 只给了「无主管时直连管理员」。若**有**主管但主管工位被 pause/delete/上下文溢出，「投递失败」无兜底，文档未定义。
2. **`messages.project_id` 到底是必做还是可选**：§4 注释「(可选：项目级消息索引)」与迁移条款的语气不一致，无法判定是否为验收项。
3. **多管理员的权限模型完全没有定义**：§4 只给了 `admins(role_level, status)` 两张表，但 ROLES §1 说「可多个（分管系统不同域）」，**「域」如何表达？`role_level` 的取值？多个 admin 是否都是 system scope（即互相全可见）？** 全部未定义。这是与「决策 5 暂不做多用户」边界最容易打架的地方。
4. **`RoleSpec` 上 `scope_level` 与 `capability` 的取值域未给**：§2 只写字段名。`capability` 与 `llm/capability.py`（模型能力表）**同名不同义**，是明显的命名碰撞风险。
5. **主管能否自我任免 / 主管的工位由谁创建**：ROLES §2 只写「创建项目 / 任免主管」是管理员 ✅、主管 ❌，但「主管工位的生命周期」（谁建、谁删、项目删除时主管工位如何处置）未定义。
6. **「必要时可亲自下场干一小段活」（ROLES §2「执行任务…主管：必要时可」）与工具集冲突**：主管的工具集来自 `management/tools.py` 的 `project` scope 子集（`window_*` `task_*` `context_*` `group_*` `workspace_*`），**不含 `read_file`/`shell` 等员工工具**。ROLES §4 说「必要时可亲自下场干一小段活」，但**没有任何一处说明主管如何获得员工工具**——ROLES §4 只说差异是「① 工具集多出作用域受限的管理工具」。此处 ROLES 与 ARCHITECTURE-v2 相互矛盾（ROADMAP 第 336 行倒是写了「主管工具集 = 员工工具 + scoped 管理工具」，但那不在指定的两文内）。
7. **「普通操作就地放行」无判定标准**：ROLES §3 区分「普通操作」与「不可逆 / 越项目边界」，但普通操作的判据、由谁裁定（主管自己判？）未定义。
8. **`project.iteration.step` 的「停止判定」无规则**：§3.3 消费方动作只写「轮次推进 / 停止判定」，停止条件、最大轮数、失败重试全部未定义。
9. **scope 与既有权限模式的交互未定义**：v1 有模式（AUTO/BUILD 等）+ 危险命令黑名单（v2 §1 表原文「模式 × 权限 × 危险命令」），v2 加了第 4 维但**没有给出四维裁决的优先级/短路顺序**（谁先拒？）。
10. **「零停机」缺乏支撑**：§6 标题称「零停机」，但新增 `admins` 表 + 把存量工位挂到默认项目，在单进程 asyncio 下如何热迁移未说明；也没有**回滚方案**（迁移失败怎么办）。
11. **风险表的「主管管多项目时上下文串味」缓解不充分**：§7 缓解写「主管工位会话按 project 分段（上下文带 project 标记）」，但**未说明同一工位的上下文压缩/检查点如何按 project 隔离**——这与 ROLES §4「上下文压缩…全套不变」直接张力：一套压缩机制 + 多项目混装会话，冲突未解。
12. **`report_service` 的「合并策略」无定义**：v2 §3.3「合并后**按策略**上报」，策略内容、与既有 `report_set`（代码 `roles.py:77` 有 `report_set` 工具）的关系均未提。

---

## 附：两个清单

### 清单 1 · v2 明确要求实现的功能

1. 新增角色 `supervisor`，`window.role = supervisor`，复用既有工位机制（串行队列/持久会话/上下文压缩/工作区/检查点全套不变）；
2. `agents/core/roles.py` 的 `RoleSpec` 增加 `scope_level` 与 `capability` 字段；注册 supervisor RoleSpec；
3. 新建 `agents/core/scope.py`：`Scope` 值对象 `{level: system|project, project_ids: frozenset[str]}` + `ScopeResolver`（装配期把 actor 解析为 Scope 并注入 `ToolContext`）；
4. `agents/core/permissions.py` 的 `PermissionGate` 增加作用域维度，**fail-closed**（未声明 scope 或越界一律拒绝）；
5. `agents/admin/tools.py` → `agents/management/tools.py`：角色无关、加 `required_scope` 元数据、**工具名与 schema 不变**；`agents/admin/` 退化为引用 management + system 装配的薄壳；
6. 工具按 scope 分两组：`window_* task_* context_* group_* workspace_*` = `project`（管理员+主管）；`config_* profile_* self_update admin_* memory_*`(global) = `system`（仅管理员）；
7. `agents/supervisor/`：主管角色装配（管理工具 + 派活）；
8. 新服务 `services/project_service.py`（项目 CRUD / 归属 / 成员）、`services/supervision_service.py`（指挥链四向信息流）、`services/iteration_service.py`（迭代计划 + 执行器）；
9. `services/report_service.py` 改为逐级路由，且无 `project_id` 时走原直连路径；
10. 四个事件：`supervisor.command`、`worker.report`、`supervisor.digest`、`project.iteration.step`；
11. 寻址规则：事件带 `project_id` → 查 `projects.owner_supervisor_id` → 找主管工位 → 投递；退化：无主管时 `worker.report` 直连管理员；
12. 审批逐级路由：员工 → 主管（普通操作就地放行）→ 不可逆/越边界升级管理员；
13. DB 迁移（`_MIGRATIONS` + `SCHEMA_VERSION` 递增）：新表 `projects(id, name, root_path, owner_supervisor_id, params_json, created_at)`、`admins(id, name, qq_id, role_level, status, created_at)`；加列 `windows.project_id`、`groups.project_id`（`messages.project_id` 标为可选）；
14. 迁移兜底：建「默认项目」，存量工位/群组自动归入，owner 指向初始管理员工位；迁移幂等 + 迁移测试；
15. 项目级记忆 `memory/projects/<id>.md`，主管读写、员工只读；记忆作用域扩为 global / project / window；
16. 新增 L1 路由 `web/routes/projects.py`、`web/routes/iterations.py`；`auth.py` 支持多管理员账号；
17. 新增 L4 `llm/capability.py`（模型能力表）、`llm/retry.py` 智能退避；
18. 保留角色名 `admin` / `worker_programmer`，旧数据不动，**管理员行为逐位不变**；
19. 回归/专项测试：管理端全量回归 + 旧 admin 行为逐位比对；`report_service` 路由单测；越权专项测试（**主管改他人工位必须失败**）。

### 清单 2 · 文档没说清、实现时需要自己决策的点

1. `messages.project_id` 是必做还是可选（§4 标「可选」）；
2. 多管理员模型：`role_level` 取值、「分管系统不同域」如何表达、多 admin 是否共享 system scope；与「决策 5 暂不做多用户」的边界；
3. `RoleSpec.scope_level` / `capability` 的取值域；`capability` 与 `llm/capability.py` 的命名冲突如何处理；
4. 四维裁决（模式 × 权限 × 危险命令 × scope）的优先级与短路顺序；
5. 主管如何「亲自下场干一小段活」——主管工具集是否并入员工工具（ROLES §2 vs ARCHITECTURE §3.2 冲突）；
6. 「普通操作就地放行」的判据与裁定者；
7. `worker.report` 在有主管但主管工位不可用（pause/delete/溢出）时的兜底；
8. `project.iteration.step` 的「停止判定」规则（停止条件、最大轮数、失败重试）；
9. 「默认项目」的名称、ID 规则、owner 工位的确定方式（「owner = 初始管理员工位」中的"初始"如何判定）；
10. 主管工位与项目的生命周期（谁创建、项目删除时如何处置、一个主管多项目时工位数量）；
11. 主管「会话按 project 分段」与「上下文压缩/检查点全套不变」的冲突如何解（多项目混装会话的隔离与压缩粒度）；
12. `supervisor.digest` 的「按策略上报」策略内容，与既有 `report_set` / 回报策略（silent/brief/detailed）的关系；
13. `jobs` 表是否需要 `project_id`（两文完全未提，但 jobs 关联 window_id）；
14. 迁移失败的回滚方案（「零停机」仅一句标题，无操作细节）；
15. 主管的 memory scope 边界：ROLES §2 写「项目内」，v2 §3.2 写「仅项目/工位记忆」——项目级记忆文件是每项目一份还是每主管一份共享；
16. 审批升级到管理员后的超时/兜底（`pending_decisions` 已有 expired 状态，但 v2 两文未定义逐级审批的超时归属）。
