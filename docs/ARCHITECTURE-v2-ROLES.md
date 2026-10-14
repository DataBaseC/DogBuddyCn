# DogBuddyCn 代码架构 v2 —— 角色化架构

> 版本 v2.0 ｜ 2026-10-14 ｜ 依据用户拍板的三层角色体系（见 `ROLES.md`）对 v1.0 分层架构做增量重构。
> 本文只描述**变更与新增**；未提及部分沿用 `ARCHITECTURE.md` v1.0（五层铁律、DI、单进程 asyncio）。

## 0. 设计目标

支撑「管理员→主管→员工」三层指挥链，且**不推翻 v1 的分层骨架**：
- 最小改动：主管不是新物种，而是「装了管理工具的工位」；
- 权限可组合：角色 × 作用域 × 信任级 × 模式 四维裁决；
- 业务规则不下沉：作用域判断仍在 L3 Domain；
- 契约稳定：既有工具名 / API / 事件名不变，只增不改。

## 1. v1 → v2 变更总览

| 维度 | v1.0 | v2.0 |
|---|---|---|
| 角色 | `admin` / `worker_programmer` | `admin` / **`supervisor`** / `worker_programmer`（+可扩展工种） |
| 管理工具 | `agents/admin/tools.py` 管理员专属 | **`agents/management/tools.py` 角色无关，按 scope 过滤** |
| 权限裁决 | 模式 × 权限 × 危险命令 | 模式 × 权限 × 危险命令 × **作用域(scope)** |
| 汇报 | 员工 → 管理员 一跳 | **员工 → 主管 → 管理员 逐级**（可退化直连） |
| 项目 | 无 | **`projects` 实体，owner = 主管**（Batch 7） |
| 汇报/督导 | 无 | **`supervision_service`（指挥链）** |
| 迭代调度 | 无 | **`iteration_service`（主管执行，Batch 8）** |
| 记忆作用域 | global / window | global / **project** / window |

## 2. 分层重构（仍五层，依赖只许向下）

```
┌────────────────────────────────────────────────────────────────────────┐
│ L1 接入层                                                                │
│   gateway/*（不变）                                                      │
│   web/routes: + projects.py · iterations.py · auth.py(多管理员账号)      │
├────────────────────────────────────────────────────────────────────────┤
│ L2 应用/编排层                                                           │
│   services/admin_service（人类↔管理员，不变）                            │
│   services/project_service        ★新（Batch 7 项目 CRUD / 归属 / 成员） │
│   services/supervision_service    ★新（指挥链四向信息流）                │
│   services/iteration_service      ★新（迭代计划 + 执行器，Batch 8）      │
│   services/report_service         ✦改（汇报改为逐级路由）                │
│   services/*（window/workspace/group/job/decisions/config/session 不变） │
│   cron/ · events/                 ✦事件增补                              │
├────────────────────────────────────────────────────────────────────────┤
│ L3 领域核心层                                                            │
│   agents/core/scope.py            ★新（Scope 值对象 + ScopeResolver）    │
│   agents/core/permissions.py      ✦改（Gate 增作用域维度，fail-closed）  │
│   agents/core/roles.py            ✦改（RoleSpec + scope_level/capability）│
│   agents/core/loop.py             （不变：同一循环服务所有角色）          │
│   agents/management/tools.py      ★迁（由 admin/tools.py 统一化，按 scope）│
│   agents/supervisor/              ★新（主管角色装配：管理工具 + 派活）    │
│   agents/worker_programmer/       （不变，员工一种）                     │
│   windows/ · memory/              ✦改（Window.project_id；memory scope +project）│
├────────────────────────────────────────────────────────────────────────┤
│ L4 能力层                                                                │
│   tools/*（不变，工具只管能力不含业务）                                  │
│   llm/capability.py               ★新（模型能力表，Batch 8 T8.4）        │
│   llm/retry.py                    ✦改（智能退避，Batch 8 T8.7）          │
├────────────────────────────────────────────────────────────────────────┤
│ L5 基础设施层                                                            │
│   db.py（迁移：projects / windows.project_id / groups.project_id / admins）│
│   config.py · storage/ · external/*（不变）                              │
└────────────────────────────────────────────────────────────────────────┘
```

## 3. 核心设计（三件事）

### 3.1 作用域（Scope）——新的横切维度

- `Scope` 值对象：`{level: system | project, project_ids: frozenset[str]}`。
- **由角色推导**：管理员 → `system`（可见全部）；主管 → `project`（仅自己负责的项目集）；
  员工 → 无管理 scope（不装配管理工具）。
- `ScopeResolver` 在装配时把 actor（当前会话 / 工位）解析为 Scope，注入 `ToolContext`。
- **裁决在 L3**：所有管理工具的每次调用，`PermissionGate` 校验「目标资源 ∈ 调用者 Scope」，
  **未声明 scope 或越界 → 拒绝（fail-closed）**。

### 3.2 管理工具统一化（关键重构）

v1 的 `agents/admin/tools.py` 是管理员专属，v2 改造为 **`agents/management/tools.py`（角色无关）**：

| 工具组 | 所需 scope | 管理员 | 主管 |
|---|---|---|---|
| `window_*` `task_*` `context_*` `group_*` `workspace_*` | `project` | ✅（system 覆盖全部） | ✅ 仅本主管项目 |
| `config_*` `profile_*` `self_update` `admin_*`（账号/任免） | `system` | ✅ | ❌ |
| `memory_*`（global） | `system` | ✅ | ❌（仅项目/工位记忆） |

- **一套工具代码，两个角色复用**；可用性由「角色装配时按 `required_scope` 过滤工具集」+
  「运行时按 Scope 过滤资源」双层保证。
- **工具名、schema 不变**（提示词契约稳定），只加 `required_scope` 元数据。

### 3.3 指挥链服务（`supervision_service`，L2）

承载 ROLES.md §3 的四向信息流，全部经事件总线解耦：

| 事件 | 方向 | 消费方动作 |
|---|---|---|
| `supervisor.command` | 管理员 → 主管 | 投递指令到主管工位队列 |
| `worker.report` | 员工 → 主管 | 定位项目 owner 主管，投递结果/提问 |
| `supervisor.digest` | 主管 → 管理员 | 合并后按策略上报 |
| `iteration.round` / `iteration.stopped` | 迭代执行器 → 全体 | 轮次推进 / 停止判定
  （实际事件名是这两个，不是 `project.iteration.step`） |

- **寻址规则**：事件带 `project_id` → 查 `projects.owner_supervisor_id` → 找主管工位 → 投递。
- **退化规则**：项目无主管时，`worker.report` 直连管理员（兼容现有单管理员部署）。

## 4. 数据模型变更（L5 · 迁移 v5+）

```
projects(id, name, root_path, owner_supervisor_id, params_json, created_at)
  ↑ windows.project_id        (工位归属项目)
  ↑ groups.project_id         (共享频道归属项目)
  ↑ messages.project_id       (可选：项目级消息索引)
admins(id, name, role_level, token_hash, note, enabled, created_at)  # 保留但不再用于鉴权/CRUD
# （v2 最终口径：只有一个管理员；该表列名以上面为准，迁移 v8 已建）
```

- 遗留数据兜底：迁移 v6 把**无 `project_id` 的工位**按工作目录归入默认项目
  （一目录一项目）；**群组不回填、也不设 owner**——需要时手工指定。
  实际实现见 `db.py:_migrate_v6`。
- 记忆作用域扩展：`memory/projects/<id>.md`（项目级记忆，主管读写，员工只读）。

## 5. 目录结构 v2（分层视角，只列变化）

```
dogbuddy/
├── services/
│   ├── project_service.py        ★L2
│   ├── supervision_service.py    ★L2
│   ├── iteration_service.py      ★L2
│   └── report_service.py         ✦逐级路由
├── agents/
│   ├── core/scope.py             ★L3
│   ├── management/tools.py       ★L3（原 admin/tools.py 迁移+参数化）
│   ├── supervisor/               ★L3（主管装配）
│   ├── admin/                    ✦薄壳：引用 management + system 装配
│   └── worker_programmer/        （不变）
├── llm/capability.py             ★L4
└── web/routes/
    ├── projects.py               ★L1
    └── iterations.py             ★L1
```

## 6. 迁移路径（v1 → v2 兼容，零停机）

1. **角色**：保留 `admin` / `worker_programmer` 名称，新增 `supervisor`（旧数据不动）。
2. **管理工具**：`admin/tools.py` 逻辑迁入 `management/tools.py`，`admin` 角色改为
   「引用 management 工具集 + system scope 装配」——**管理员行为逐位不变**（回归保障）。
3. **项目**：迁移建「默认项目」，所有存量工位归入，owner 指向管理员工位。
4. **汇报**：`report_service` 增加逐级路由；无 `project_id` 事件走原直连路径（行为不变）。
5. **DB**：走 `_MIGRATIONS` + `SCHEMA_VERSION` 递增，旧库自动升级。

## 7. 合理性审查（自检）

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 依赖方向 | ✅ 新服务全 L2、scope 在 L3、能力 L4，**只向下**，无跨层直调 |
| 2 | 循环依赖 | ✅ `supervision_service` 依赖 `project_service`/`window_manager` 接口，单向；`project` 不反向依赖 |
| 3 | 业务规则归属 | ✅ 作用域裁决在 L3（`scope.py` + `permissions.py`），L1/L2 只传参 |
| 4 | 工具契约稳定 | ✅ 工具名/schema 不变，只加 `required_scope` 元数据，LLM 提示词与已存会话不受影响 |
| 5 | 安全默认 | ✅ **fail-closed**：scope 未声明 / 资源越界一律拒绝，主管越权不可达 |
| 6 | 复用不重造 | ✅ 主管复用窗口/队列/压缩/检查点全套，无新增常驻实体 |
| 7 | 向后兼容 | ✅ 角色名保留、默认项目兜底、汇报退化直连；管理员路径行为逐位不变 |
| 8 | 批次咬合 | ✅ Batch 4 审批逐级 / Batch 7 项目 owner=主管 / Batch 8 迭代执行者=主管，一致 |
| 9 | 并发模型 | ✅ 主管工位串行队列天然串行化多项目指令；事件总线单进程，无锁竞争新点 |
| 10 | 可测试性 | ✅ ScopeResolver、Gate 裁决、指挥链寻址均可脱离 IO 单测 |

### 风险清单（需在批次中盯防）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 管理工具重构触及管理员主路径 | 管理员功能回归 | 工具名不变 + 管理端全量回归测试；旧 admin 角色行为逐位比对 |
| 逐级汇报改造影响现有通知 | 通知漏发/重复 | 保留直连退化路径；`report_service` 加路由单测 |
| scope 漏判导致越权 | 安全缝 | fail-closed 默认拒绝 + 越权专项测试（主管改他人工位须失败） |
| 项目迁移默认归属错误 | 数据错挂 | 迁移幂等 + 迁移测试；默认项目 owner 显式指定 |
| 主管管多项目时上下文串味 | 指令错发 | 主管工位会话按 project 分段（上下文带 project 标记） |

## 8. 落地批次映射

| 能力 | 归属批次 |
|---|---|
| 角色 `supervisor` + 管理工具统一化 + Scope 裁决 | **Batch 9（新增）** |
| 项目层（projects / owner=主管 / 工位归属） | Batch 7 |
| 逐级汇报 + 指挥链服务 | Batch 9 |
| 迭代计划 + 执行器 | Batch 8 |
| 审批逐级路由 | Batch 4（修正为逐级） |
| 模型能力表 / 智能退避 | Batch 8 |
