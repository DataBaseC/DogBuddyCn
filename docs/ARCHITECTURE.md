# DogBuddyCn 软件分层架构设计（详细版）

> 版本 v1.0 ｜ 2026-10-12 ｜ 配套 DEV_PROMPT.md 使用，开发必须遵守本文的分层与依赖规则
>
> ⚠️ **v2 增量**：三层角色体系（管理员/主管/员工）的架构重构见 `ARCHITECTURE-v2-ROLES.md`
> （角色职能定义见 `ROLES.md`）。v2 只在本文五层骨架上做增量（新增 Scope 横切、管理工具统一化、
> 指挥链服务），未提及处仍以本文为准。

## 1. 分层总览

自上而下五层，**依赖只允许向下**，同层解耦靠接口注入与进程内事件总线：

```
┌───────────────────────────────────────────────────────────────────────┐
│ L1 接入层 Interface（协议转换与鉴权，禁止业务逻辑）                       │
│    gateway/adapters(OneBot反向WS · 官方QQ留口 · 微信留口)                │
│    web/routes(FastAPI：auth/windows/jobs/files/settings/logs)           │
│    cli(manage.py)                                                       │
├───────────────────────────────────────────────────────────────────────┤
│ L2 应用/编排层 Application（用例编排、事务边界、跨模块协调）              │
│    services/admin_service（用户消息→管理员对话→回执）                    │
│    services/report_service（回报事件→裁量→推送/静默）                   │
│    cron/scheduler + cron/jobs（tick→触发→注入工位）                     │
│    events/bus（job.completed / worker.question / window.status …）     │
├───────────────────────────────────────────────────────────────────────┤
│ L3 领域核心层 Domain（纯业务规则，无 IO 细节，依赖注入）                  │
│    agents/core（AgentLoop：上下文组装→LLM→工具循环→终止）               │
│    agents/admin（管理员角色+工具包装配）  agents/worker_programmer（同）│
│    windows/（WindowSession 持久会话 · TaskQueue 串行队列 ·              │
│              ContextBuilder 上下文组装 · Compactor 自动压缩）            │
│    memory/（MemoryService：global/window 记忆、压缩沉淀）               │
├───────────────────────────────────────────────────────────────────────┤
│ L4 能力层 Capability（可复用能力单元，无业务编排）                        │
│    tools/（ToolRegistry 注册制：file/shell/search/todo/checkpoint/      │
│           explore_subagent/ask_user/server_exec …）                    │
│    llm/（LLMProvider 抽象：openai_compat · ollama · RetryPolicy 限速重试）│
├───────────────────────────────────────────────────────────────────────┤
│ L5 基础设施层 Infrastructure（技术细节与外部系统）                       │
│    db.py(SQLite/WAL/DAO)  config.py(YAML/pydantic)                     │
│    storage/(memory文件 · checkpoints快照 · logs)                       │
│    external/panel_client.py（AegisServerPanel /api/terminal/exec）     │
└───────────────────────────────────────────────────────────────────────┘
```

## 2. 各层详述

### L1 接入层
| 模块 | 职责 | 对外接口（契约） |
|------|------|----------------|
| `gateway/adapters/base.py` | 适配器抽象（照抄 AstrBot BaseAdapter） | `async recv() -> MessageEvent`、`async send(target, content)`、`async push(target, content)`；统一 MessageEvent{platform, session_id, user_id, text, attachments} |
| `gateway/adapters/onebot_v11.py` | 反向 WebSocket server（接 NapCat 上报），Access Token 校验 | 实现 BaseAdapter；白名单过滤在进 L2 前完成 |
| `gateway/router.py` | 消息路由：用户消息→管理员会话；推送→目标 QQ 群/私聊 | `route_in(event)`、`route_out(target, content)` |
| `web/routes/*` | REST API（面板前端调用），薄壳 | 每个 handler 只做：参数校验→调 L2 service→返回 DTO；**禁止**在这里写业务规则 |
| `cli/manage.py` | start/stop/status/logs/backup/restore | 进程管理与运维入口 |

**规则**：L1 只做协议转换、鉴权（QQ 白名单、面板登录 Token）、输入清洗、限流。

### L2 应用/编排层
| 模块 | 职责 | 关键方法 |
|------|------|---------|
| `services/admin_service` | 承载"用户↔管理员"用例：收消息→管理员 AgentLoop→工具执行→回执 | `async handle_user_message(event) -> Reply` |
| `services/report_service` | 报告裁量：消费 job.completed/worker.question 事件，按策略 silent/brief/detailed 合并裁量后推送 | `async on_job_completed(run)`、`async on_worker_question(q)` |
| `cron/scheduler` | 60s tick；解析 4 种调度格式；到点生成执行请求投给目标工位队列 | `tick()`、`next_run(job) -> datetime` |
| `cron/jobs` | Job 生命周期（含连败 3 次自动暂停、run_now 插队） | `create/update/pause/remove/due_jobs()` |
| `events/bus` | 进程内异步事件总线（asyncio Queue per subscriber） | `emit(event)`、`subscribe(type, handler)` |

**规则**：L2 编排 L3/L4 能力完成用例；事务边界在此层；**不含**提示词与业务规则细节。

### L3 领域核心层
| 模块 | 职责 | 关键抽象 |
|------|------|---------|
| `agents/core/loop.py` | AgentLoop：ContextBuilder 组装上下文→LLM（含工具调用）→工具执行→循环直至终止 | `run(window_id, input) -> AgentResult`；与平台无关（IM/定时/面板派活共用同一循环） |
| `agents/core/roles.py` | 角色注册表 RoleSpec{name, system_prompt, tool_names, default_mode} | `register_role(spec)`；V1 注册 `admin`、`worker_programmer`；**留接口**给未来角色与群聊工作区 |
| `agents/admin/tools.py` | 管理工具包（window_*/workspace_*/task_*/context_*/config_*/report_*/memory_*/progress_report） | 每工具 = name + JSON schema + execute |
| `agents/worker_programmer/tools.py` | 员工工具装配（从 L4 tools 挑选，含权限模式门控） | 按 RoleSpec.tool_names 装配 |
| `windows/session.py` | WindowSession：持久会话（消息历史+compactions+todo） | `append(role, content)`、`build_context() -> messages` |
| `windows/queue.py` | TaskQueue：工位内任务**串行**，FIFO，run_now/用户插话插队到头部 | `submit(task, urgent=False)`、`async run_loop()` |
| `windows/compactor.py` | 自动压缩：token≥阈值触发 LLM 摘要；**抗损**——文件改动/待办/决策/教训结构化保留 | `maybe_compact(window) -> Compaction` |
| `memory/service.py` | 读写 memory/global.md 与 memory/windows/<id>.md；压缩时沉淀事实 | `recall(scope) -> str`、`write(scope, fact)` |

**规则**：Domain 不 import 任何 L1/L5 具体实现（db、llm、web 全部经构造注入接口）；不出现 HTTP/SQL 细节。

### L4 能力层
- `tools/registry.py`：ToolRegistry 统一注册；每个工具实现 `Tool` 协议：`name / schema / permission(write|shell|network) / async execute(args, ctx)`。
- 工具清单（员工）：read_file / write_file / edit_file / shell(超时+后台终端) / glob / grep / web_search / web_fetch / todo_* / checkpoint_create / checkpoint_rollback / explore_subagent / ask_user / server_exec。
- `llm/provider.py`：`chat(messages, tools) -> LLMResult`；`openai_compat.py`（任意 base_url，多 Key 配置保留但不用于限速切换）、`ollama.py`。
- `llm/retry.py`：RetryPolicy（429/529/超时/连接错误→指数退避，默认 10s 起×2 封顶 300s、最多 5 次；**同一 Key 原地等待重试**；限速重试不计任务失败——向上抛 `RateLimitWaiting` 状态事件供 UI 显示）。

**规则**：能力单元无状态编排；工具内部才允许触碰文件/shell/HTTP（权限门控在 registry 统一裁决：BUILD/PLAN/AUTO + 危险命令黑名单）。

### L5 基础设施层
- `db.py`：SQLite（stdlib `sqlite3`，WAL 模式，同步 DAO + 可重入写锁——本地文件毫秒级，事件循环只在真正 IO 处 await）；DAO 映射 windows/messages/compactions/todos/jobs/job_runs/pending_decisions/groups/group_members/config/whitelist/audit_log；schema 变更走 `_MIGRATIONS` + `SCHEMA_VERSION`。
- `config.py`：pydantic 校验 YAML（llm、retry、gateway、web、panel、paths 段）。
- `storage/`：记忆文件、检查点快照（按 window+timestamp）、日志文件（轮转）。
- `external/panel_client.py`：`exec(command, timeout)` 封装面板 `/api/terminal/exec` + X-Agent-Token。

## 3. 依赖铁律（开发必须遵守，违反即返工）

1. **只许向下依赖**：L1→L2→L3→L4→L5；跨层直调禁止（如 web/routes 直接调 db）。
2. **依赖注入**：L3 所有外部能力（DB/LLM/工具）经构造函数注入接口，Domain 可脱离 IO 单测。
3. **同层解耦**：模块间优先接口或事件总线；禁止循环依赖。
4. **业务规则只住 L3**：权限门控、压缩策略、回报裁量规则的"判断逻辑"在 Domain；L1/L2 只传参与编排。
5. **工具是能力不是业务**：tools/ 不 import agents/；一个工具只做一件事。
6. **抄码归位**：AstrBot 的适配器/流水线/压缩代码按层归位安置，保持原文件版权头；Hermes cron 归 L2。

## 4. 关键数据流（时序）

**流 1 · 用户消息→回复**
```
NapCat --WS事件--> L1 onebot_v11（白名单/清洗）→ L1 router
  → L2 admin_service.handle_user_message
  → L3 AgentLoop.run（ContextBuilder 组装→LLM→工具循环；管理工具在 L3/L4 执行）
  → L2 admin_service 回执 → L1 router.push → NapCat → QQ
```

**流 2 · 定时任务执行**
```
L2 scheduler.tick(60s) → jobs.due_jobs()
  → L2 投递 Task{window_id, prompt} → L3 windows/queue.submit
  → L3 AgentLoop.run（员工角色+工位上下文，工具在 L4 执行，server_exec 走 L5→面板）
  → 完成：emit(job.completed) → L2 report_service 裁量（silent/brief/detailed）
  → push QQ 或静默；连败计数+1/清零（限速重试不计）
  → 达阈值：L3 compactor.maybe_compact（抗损摘要入库+记忆沉淀）
```

**流 3 · 上下文压缩（抗损）**
```
ContextBuilder 估算 token ≥ 80% 窗口 → L3 compactor
  → L4 llm 生成摘要（对话措辞） + 结构化事实抽取（文件改动/待办/决策/教训）
  → L5 db.compactions 插入 + memory/windows/<id>.md 追加
  → 后续 build_context 用"摘要+结构化事实"替换被压缩消息
```

## 5. 并发与进程模型

- **单进程 asyncio**（uvicorn + FastAPI）；所有服务都是 asyncio task。
- 每个工位 = 1 条串行 TaskQueue + 1 个 worker task；**多工位天然并行**；同工位任务排队。
- shell 工具：`asyncio.subprocess`，超时杀进程；后台终端登记子进程表（随窗口暂停/终止回收）。
- OneBot WS 长连接 1 个 task；cron tick 1 个 task（60s）。
- SQLite WAL；写操作经 DAO 串行化（单写者队列），读并发。
- LLM 调用全部走 RetryPolicy；长任务流式输出可后置（V1 整段返回即可）。

## 6. 扩展点（V1 必须留好的口子）

| 扩展 | 接口位置 | 用法 |
|------|---------|------|
| 新员工角色（测试/运维…） | `agents/core/roles.py` register_role | 新增 RoleSpec + 工具装配，`window_create(role=...)` 即可用 |
| 群聊公共工作区 | `windows/session.py` | 会话模型预留 group_id 字段与多参与者概念，V1 不实现 |
| 新 IM 适配器（官方 QQ/企微） | `gateway/adapters/base.py` | 实现 BaseAdapter 并注册 |
| 新工具 / MCP | `tools/registry.py` | register 即暴露给角色；MCP bridge 留接口 |
| 技能系统（Skills） | `memory/` 旁挂 skills/ 目录 | V1 不实现，目录与加载钩子留口 |
| 脚本任务（no-agent） | `windows/queue.py` | Task 类型字段预留 kind=prompt|script |

## 7. 目录结构（分层视角）

```
dogbuddy/
├── LICENSE  NOTICE  README.md  install.sh  manage.py  config.example.yaml
├── dogbuddy/
│   ├── main.py                 # 装配根（DI 组装各层、启动 task）
│   ├── config.py  db.py        # L5
│   ├── gateway/                # L1: adapters/ base.py onebot_v11.py ｜ router.py
│   ├── web/                    # L1: routes/ + static/（原生JS面板）
│   ├── services/               # L2: admin_service.py report_service.py
│   ├── cron/                   # L2: scheduler.py jobs.py（抄 Hermes）
│   ├── events/                 # L2: bus.py
│   ├── agents/                 # L3: core/ loop.py roles.py ｜ admin/ ｜ worker_programmer/
│   ├── windows/                # L3: session.py queue.py context.py compactor.py
│   ├── memory/                 # L3: service.py
│   ├── tools/                  # L4: registry.py + file/shell/search/todo/checkpoint/subagent/server_exec
│   ├── llm/                    # L4: provider.py openai_compat.py ollama.py retry.py
│   └── external/               # L5: panel_client.py
├── systemd/dogbuddy.service
└── tests/                      # 分层单测：Domain 全部可脱离 IO 测
```
