# DogBuddyCn 开发任务提示词（发给开发 Agent 的完整指令）

> 使用方式：将本文全文发给负责开发的 Agent。本文自包含，无需其他上下文。

---

## 你的任务

你是本项目的**开发工程师 Agent**。请从零实现 **DogBuddyCn**——一个常驻 Ubuntu 服务器的多 Agent 数字团队系统：管理员 Agent 经 QQ 对接用户，程序员员工 Agent 在持久"工位"里被定时任务持续驱动完成编程开发工作，并提供 Web 控制面板。项目**独立于**既有仓库，但与同机已部署的 AegisServerPanel 对接。

## 项目背景

- 同机有一个成熟面板项目 **AegisServerPanel**（Python/FastAPI 服务器管理面板，MIT）。它提供：`POST /api/terminal/exec`（Header `X-Agent-Token: <token>`，Body `{"command": "...", "timeout": 60}`，1~600s）执行服务器命令、常驻应用守护（崩溃拉起/日志轮转/开机自启）。**不要修改它**，只对接它。参考：https://github.com/DataBaseC/AegisServerPanel
- 本项目目标用户是服务器主人本人（单用户，白名单）。

## 硬性约束（不可违反）

1. **许可证 AGPL-3.0**。项目根放 `LICENSE`（AGPL-3.0 全文）+ `NOTICE`（声明吸收了 AstrBot 的代码，保留其版权头）。抄来的源文件**保留原版权头**。
2. **技术栈**：Python 3.11+、FastAPI、SQLite、YAML 配置；前端原生 HTML/CSS/JS（零构建）。LLM 走 OpenAI 兼容 API（可配任意 base_url/model/key，支持多 Key 轮换）+ 可选 Ollama。
3. **部署形态**：独立仓库/独立进程；`install.sh`（创建 venv、装依赖、systemd 单元、初始化配置目录）+ `manage.py`（start/stop/status/logs/backup/restore）+ `README.md`。数据目录默认 `/etc/dogbuddy/`（config.yaml、SQLite、memory/、logs/）。提供 systemd 单元文件（也可被 AegisServerPanel 常驻应用托管）。
4. **安全红线**：
   - IM 白名单：仅授权 QQ 号可与机器人对话。
   - 员工模式三档：`BUILD`（工作区内读写放行，工作区外写/危险命令拦截）、`PLAN`（全写操作拦截）、`AUTO`（全放行，**仅手动会话可用**）。
   - 危险命令黑名单（`rm -rf /`、`mkfs`、`reboot/shutdown`、`systemctl` 关键操作、修改面板配置等）→ 拒绝或要求用户 IM 确认后放行。
   - 定时任务无人值守时**禁止 AUTO 模式**。
5. **禁止**：不抄 AstrBot 的 WebUI/插件市场/Dify 对接（只抄适配器层、事件流水线、LLM Provider 抽象、上下文压缩四块）；不做微信/官方 QQ 适配器（留接口即可）；不做多用户权限分级；不做分布式。

## 参考项目（抄码/借鉴指引）

| 项目 | 链接 | 怎么用 |
|------|------|--------|
| AstrBot（AGPL-3.0） | https://github.com/AstrBotDevs/AstrBot | **照抄**：①平台适配器抽象（BaseAdapter 统一收发）②事件总线+消息流水线 ③LLM Provider 抽象（OpenAI 兼容/Ollama）④会话上下文自动压缩。抄的文件保留版权头 |
| Hermes Agent（MIT） | https://github.com/NousResearch/hermes-agent | **照抄** `cron/jobs.py` + `cron/scheduler.py` 的任务存储与 60s tick 调度逻辑；4 种调度格式解析（`30m`/`2h`/`1d`，`every 2h`/`every monday 9am`，5 段 cron 表达式，ISO 时间戳一次性） |
| AiCode（GPL-3.0） | https://github.com/jieapi/AiCode | **借设计**（Kotlin/Android 代码不可搬）：编程工具箱清单、BUILD/PLAN/AUTO 三模式、检查点与回滚、只读 Explore 子代理、多 Key 轮换、长对话自动压缩 |
| OpenCode / OpenClaw | https://github.com/opencode-ai/opencode | 借设计：持久化会话引擎、事件存储、文件式记忆 |

## 架构设计（分层，必须遵守）

```
L1 接入层(gateway适配器/web路由/cli：协议转换+鉴权，禁业务)
   ↓
L2 应用层(admin_service/report_service/cron调度/events总线：用例编排+事务边界)
   ↓
L3 领域核心层(agents AgentLoop+角色注册 / windows 持久会话+串行队列+压缩 / memory：纯业务，依赖注入)
   ↓
L4 能力层(tools 工具注册制 / llm Provider抽象+限速重试：可复用能力单元)
   ↓
L5 基础设施层(db SQLite / config / storage / external panel_client：技术细节)
```

**依赖铁律**：①只许向下依赖，禁止跨层直调（如 web 直调 db）②L3 领域层所有外部能力经构造注入接口，必须能脱离 IO 单测 ③业务规则（权限门控/压缩策略/回报裁量的判断逻辑）只住 L3 ④tools 不 import agents；工具经 ToolRegistry 注册（统一 schema+权限裁决）⑤抄来的代码按层归位并保留版权头（AstrBot 适配器/压缩→L1/L3，Hermes cron→L2）。

**并发模型**：单进程 asyncio；每工位 1 条串行任务队列（多工位并行）；shell 用 asyncio.subprocess；SQLite WAL 单写者；cron 60s tick；OneBot WS 长连接。

**扩展点（V1 必须留口）**：角色注册表 register_role（新员工角色/群聊工作区预留 group 字段）；BaseAdapter 注册（官方 QQ/微信）；ToolRegistry（新工具/MCP）；skills/ 目录钩子；Task.kind=prompt|script（脚本任务）。

> 完整细节（层内模块契约、3 条数据流时序、目录分层注释）见 `ARCHITECTURE.md`，建议随 DEV_PROMPT 一并交给开发 Agent。

## 架构总览（旧版示意，以分层设计为准）

```
用户(QQ) ↔ NapCat(OneBot v11 反向WS) ↔ IM网关(适配器抽象)
                                          ↓ 唯一人机入口
                                   管理员 Agent（最高权限，管理工具包）
                                          ↓ 内部事件/派活
                    ┌─────────────────────┼─────────────────────┐
               员工工位A(程序员)        员工工位B(程序员)        ...（未来更多角色/群聊工作区）
               [持久会话+小工作区+      [同左]
                模型+待办+定时任务集]
                        ↑ 注入执行（串行队列）
                  Cron 调度器(60s tick)
                        ↓ 产出事件
                  管理员裁量回报 → QQ 推送/静默

Web 控制面板(FastAPI+原生JS)：总览/工位/任务/设置/文件管理器/日志
存储：SQLite(会话/任务/审计) + 文件(memory/、配置YAML)
```

核心概念——**工位（window）= 员工工位**：`角色 + 工作区路径 + 模型 + 持久会话 + 待办清单 + 挂载的定时任务集`。定时任务**不是**每次新建会话（Hermes 模式），而是**回到同一工位的持久会话**继续执行；上下文超限自动压缩，保证"一个项目一直做下去"。

## 功能需求（按模块）

### M-A IM 网关
- 适配器抽象照抄 AstrBot BaseAdapter；V1 实现 OneBot v11 反向 WebSocket 适配器（对接 NapCat/Lagrange）；官方 QQ、微信留 Adapter 接口不实现。
- 白名单（QQ 号）+ OneBot Access Token；消息路由：用户消息→管理员 Agent 会话；推送：管理员/任务回报→指定 QQ 群或私聊（任务级可配）。

### M-B 管理员 Agent（唯一人机对话面）
- LLM Agent 循环（工具调用），自身也是持久会话；用户问"进度/概况"时用工具汇总回答。
- 管理工具包（仅管理员可用）：
  - 工位：window_create(role,name,workspace,model) / window_list / window_status / window_update(改模型/模式/名称) / window_pause / window_resume / window_delete
  - 工作区：workspace_create(path,git_init) / workspace_list / workspace_assign
  - 任务：task_create(window_id,schedule,prompt,report_strategy) / task_list / task_update / task_pause / task_resume / task_remove / task_run_now
  - 上下文：context_view / context_compress / context_reset / context_inject(text)
  - 配置：config_get / config_set / report_set
  - 记忆：memory_write / memory_recall
  - 汇报：progress_report（汇总全部工位/任务状态）
- 回报裁量：任务回报策略枚举 `silent / brief / detailed`（默认 brief）；任务完成事件先到管理员，由管理员合并、裁量后决定推不推、推多详细（可静默）。
- 员工的 `ask_user` 提问、任务产出均经内部事件交管理员转述用户；**用户不直接与员工对话**。

### M-C 程序员员工 Agent（V1 唯一角色，留角色注册接口）
- 工具箱（全部实现）：read_file / write_file / edit_file（局部 diff 编辑）/ shell（超时+后台终端）/ glob / grep / web_search / web_fetch / todo_view / todo_add / todo_done / checkpoint_create / checkpoint_rollback / explore_subagent（只读调研子代理，独立上下文）/ ask_user / server_exec（调 AegisServerPanel `/api/terminal/exec`，Header X-Agent-Token）。
- 改文件前自动 checkpoint，支持一键回滚。
- 权限模式 BUILD/PLAN/AUTO 按硬性约束执行。
- **自主单干**：工位待办清单驱动；支持 heartbeat 型定时任务（"继续推进待办"），员工自主决定顺序；用户/管理员指令可插队（队列头部）。

### M-D 工位会话引擎
- 持久会话存 SQLite；上下文组装 = 近期消息 + 压缩摘要 + 全局记忆 + 工位记忆 + 系统提示词。
- 自动压缩：token 达阈值（默认 80% 模型窗口）触发 LLM 摘要；**抗损**：文件改动清单、待办、关键决策、教训以结构化字段保留（compactions 表 + memory/windows/<id>.md），只丢对话措辞。
- 工位内任务**串行队列**（同时只跑一个），FIFO；run_now/插话插队。

### M-E 定时任务（抄 Hermes）
- 调度格式 4 种（见参考表）；60s tick；Job→注入工位持久会话执行（非新建会话）。
- job_runs 记录每次执行（起止/状态/摘要/日志路径）；任务连续失败 3 次自动暂停并通知管理员（**LLM 限速重试不算失败**，见 M-H）。
- 创建入口：QQ 找管理员自然语言创建 + Web 面板创建。

### M-F Web 控制面板（用户主控制台）
- 鉴权：Token/密码登录。
- 页面：①总览（工位卡片：状态/当前任务/最近产出；任务概览）②工位管理（会话流、暂停/恢复、改模型/模式、手动派活、看待办）③任务管理（CRUD/启停/立即执行/运行历史）④设置（LLM base_url/model/key 多 Key、Ollama、**限速重试：重试间隔/最大重试次数/退避模式**、回报策略、白名单、鉴权）⑤**文件管理器**：Windows 资源管理器式——左侧目录树+右侧文件列表+面包屑导航，双击进入/返回，文本与代码预览（语法高亮）、图片预览、下载、上传、重命名、删除、新建文件夹；范围=工作区根目录 ⑥日志/审计（任务运行记录、命令审计）。
- 前端零构建原生 JS，风格可参考 AegisServerPanel 的静态面板。

### M-G 记忆
- 文件式：memory/global.md + memory/windows/<id>.md（Markdown）；管理员可读写；压缩时自动沉淀事实；会话组装自动注入。

### M-H LLM 调用与限速重试（免费中转常限速，必做）
- LLM Provider 层统一实现限速重试：触发条件 = HTTP 429 / 529 / 超时 / 连接错误。
- 策略：指数退避（默认基础间隔 10s：10→20→40→80→160 封顶 300s）或固定间隔模式；最大重试次数默认 5。
- config.yaml 配置项：`llm.retry.base_interval`、`llm.retry.max_retries`、`llm.retry.backoff: exp|fixed`、`llm.retry.max_interval`；Web 设置页（M-F④）暴露可调。
- **限速行为**：限速时**不切换 Key**，用同一 Key 按退避策略原地等待后重试（多 Key 配置不参与限速应对，仅作配置保留）。
- 计数语义：重试等待不计任务失败；重试用尽才计一次失败（M-E 的连续失败计数以此为准）。重试期间工位状态显示"限速等待重试（第 n 次）"。

## 数据模型草案（SQLite）

- `windows(id, name, role, workspace_path, model, mode, status, created_at, updated_at)`
- `messages(id, window_id, role, content, tokens, created_at)`
- `compactions(id, window_id, summary_json, replaced_count, created_at)`（summary_json 含文件改动/待办/决策/教训）
- `todos(id, window_id, content, done, sort)`
- `jobs(id, window_id, name, schedule_type, schedule_raw, prompt, report_strategy, enabled, last_run_at, next_run_at, fail_count)`
- `job_runs(id, job_id, window_id, started_at, finished_at, status, summary, log_path)`
- `config(key, value)`；`whitelist(qq_id, name)`；`audit_log(id, actor, action, detail, created_at)`

## 目录结构建议

```
dogbuddy/
├── LICENSE  NOTICE  README.md  install.sh  manage.py  config.example.yaml
├── dogbuddy/
│   ├── main.py            # FastAPI + 生命周期（启动调度器/网关/面板）
│   ├── config.py          # YAML 配置加载
│   ├── db.py              # SQLite schema + 访问层
│   ├── llm/               # LLM Provider 抽象（OpenAI 兼容/Ollama/多Key轮换）
│   ├── gateway/           # 适配器抽象 + onebot_v11 反向WS + 路由
│   ├── agents/            # agent_core(循环/工具注册) + admin(工具包) + worker_programmer(工具包)
│   ├── windows/           # 工位会话引擎：session.py 队列/压缩/上下文组装
│   ├── cron/              # jobs.py + scheduler.py（抄 Hermes）
│   ├── tools/             # 员工工具实现（file/shell/search/todo/checkpoint/subagent/server_exec）
│   ├── memory/            # 文件记忆读写
│   └── web/               # 面板 API + static/(原生JS前端，含文件管理器)
├── systemd/dogbuddy.service
└── tests/                 # pytest
```

## 里程碑与交付要求

- **M1 核心骨架**：db + llm + agent_core + 员工工具箱 + 管理员工具包 + 工位会话引擎（含压缩/队列）+ 记忆。可脱离 IM 用 CLI/测试脚本跑通"建工位→派任务→员工干活→压缩"。
- **M2 IM + 定时**：gateway(onebot) + cron（调度/注入/失败暂停/回报事件）+ 管理员回报裁量 → QQ 闭环。
- **M3 Web 面板**：全部页面 + 文件管理器 + 安全门控完善 + install.sh/systemd/README。
- **测试**：pytest 覆盖调度解析、压缩、工具权限门控、任务队列、适配器消息路由；`manage.py status` 可自查。
- **验收标准**：①QQ 对管理员说"每天早8点在XX项目推进待办，完成后简要汇报"可全程自然语言完成建工位/设任务；②到点任务回同一工位执行、上下文超限自动压缩、待办持续积累；③面板可管一切且文件管理器可用；④PLAN 模式/黑名单/AUTO 限制全部生效（有测试证明）。

## 禁止事项（重申）

1. 不修改 AegisServerPanel 本体；2. 不抄 AstrBot 的 WebUI/插件市场/Dify；3. 不实现微信/官方 QQ/群聊工作区/技能系统/多用户（留接口）；4. 交付代码不带密钥，配置用 config.yaml + 环境变量；5. 保留抄来文件的版权头，NOTICE 声明来源。
