# DogBuddyCn 设计文档（v0.3 细节版）

> 状态：细节设计完成 ｜ 最近更新：2026-10-12 ｜ 许可证：AGPL-3.0（用户拍板）
> 交付约定：本文档是唯一设计源；开发由另一个 Agent 完成，指令见同目录 `DEV_PROMPT.md`。
> **软件分层架构详细设计（层职责/接口契约/依赖铁律/时序/并发模型）见同目录 `ARCHITECTURE.md`，开发须遵守其依赖铁律。**
> 标注【暂定】= 设计伙伴按常识补的默认值，用户可在开工前推翻。

## 0. 背景与调研摘要

### 0.1 基座现状地图（AegisServerPanel v1.3.1，MIT）

- 技术栈：Python ≥3.10，FastAPI + Uvicorn，psutil，subprocess/pty，前端原生 JS（零构建），JSON 文件存储
- 复用/对接点：
  - **Agent 接入**：`POST /api/terminal/exec` + `X-Agent-Token`（1~600s 超时，审计已内建）
  - **常驻应用守护**：崩溃拉起 / 日志轮转 / 开机自启（本 Agent 自身可被面板托管）
  - crontab 自启、配置备份迁移、审计时间线、内网双模式安全模型
- 约束：单机 Ubuntu/Debian、root 运行、单用户

### 0.2 借鉴清单与抄码策略

| 项目 | 协议 | 抄什么 / 借什么 | 策略 |
|------|------|----------------|------|
| **AstrBot** | AGPL-3.0 | ①平台适配器层（BaseAdapter 抽象）②事件总线+消息流水线 ③LLM Provider 抽象 ④会话上下文自动压缩 | **照抄源码**（项目已定 AGPL-3.0）；⚠️ 只抄这四块，**不抄** WebUI/插件市场/Dify 对接，避免泥潭 |
| **Hermes Agent** | MIT | cron/jobs.py 任务存储 + scheduler.py tick loop（60s）；4 种调度格式：Duration「30m/2h/1d」、「every 2h」、5 段 cron、ISO 一次性 | 直接抄 |
| **AiCode**（jieapi/AiCode） | GPL-3.0 | 编程工具箱全套设计：文件读写/编辑、Shell/后台终端、代码+网页搜索、待办、提问、检查点回滚、只读 Explore 子代理、多 Key 轮换、BUILD/PLAN/AUTO 三模式 | 借设计（Android/Kotlin 代码不可直接用） |
| **OpenCode / OpenClaw** | MIT | 持久化会话引擎、事件存储、Gateway/Brain 分离、文件式记忆、heartbeat | 设计借鉴 |

### 0.3 IM 接入

- QQ：V1 用 NapCat/Lagrange + OneBot v11 反向 WebSocket（**小号 + Access Token + 白名单**，风控风险自担）；官方机器人适配器**留接口不实现**
- 微信：V1 不做，**留适配器接口**（个人号灰色方案不做；合规路=企微/公众号，后置）

## 1. 定位与目标

- **一句话定位**：常驻 Ubuntu 的多 Agent 数字团队——管理员 Agent 经 QQ 统一对接用户，程序员员工 Agent 在"员工工位（持久窗口）"里被定时任务持续驱动干活，一个项目一直做下去；Web 控制面板管一切。
- 目标用户：用户本人（单租户）
- 成功标准：
  1. QQ 与管理员对话即可建工位/派任务/查进度/改配置
  2. 定时任务回到同一工位持久执行，上下文超限自动压缩，项目上下文持续积累
  3. 程序员员工自主完成编程开发任务（读写代码、跑命令、git、搜索、检查点回滚）
  4. Web 面板可管理一切 + Windows 资源管理器式查看工作区文件

## 2. 核心功能域

| 功能域 | 职责 | 边界 | 状态 |
|--------|------|------|------|
| IM 网关（Gateway） | QQ 收发、适配器抽象、消息路由（用户↔管理员） | 不做业务 | ✅ 定 |
| 管理员 Agent（最高权限） | 全局管理：工位/工作区/模型/任务/上下文/配置/汇报 | 不干编程活 | ✅ 定 |
| 程序员员工 Agent | 编程开发全套工具，工位内自主干活 | 无管理工具 | ✅ 定 |
| 工位会话引擎（Window） | 持久会话、任务队列（串行）、上下文自动压缩 | 不做分布式 | ✅ 定 |
| 定时任务（Cron） | 存储/调度/注入工位执行/回报事件 | 只做 Agent 会话任务 | ✅ 定 |
| Web 控制面板 | 总览/工位/任务/设置/文件管理器 | —— | ✅ 定 |
| 记忆（Memory） | 文件式长期记忆（全局+工位级） | 技能系统后置 | ✅ 定 |
| 服务器操作 | 经面板 `/api/terminal/exec` | 不重复实现面板 | ✅ 定 |

## 3. 主流程

```
管理流：用户 QQ 消息 → Gateway(NapCat/OneBot) → 管理员 Agent → 管理工具（建工位/派任务/调模型/改配置）→ 回执 QQ
任务流：调度器 tick(60s) → 到点 Job → 注入目标工位持久会话（串行队列）→ 员工工具循环执行
        → 产出事件(结果摘要) → 管理员按回报策略裁量 → 推送 QQ / 静默
        → 上下文达阈值 → 自动压缩（丢对话、保事实）→ 工位长期存续
工作流：员工在工作区（git 目录）读写代码/跑命令/搜索/git；改文件前自动检查点，可回滚
```

## 4. 功能明细

### 4.1 管理员 Agent
- **输入**：QQ 用户消息（唯一人机入口）、员工产出事件、任务执行事件
- **输出**：管理工具调用、QQ 回执/推送
- **工具包**（全部管理类）：
  - 工位：`window_create(role, name, workspace, model)` / `window_list` / `window_status` / `window_update`（改模型/模式/名称）/ `window_pause` / `window_resume` / `window_delete`
  - 工作区：`workspace_create(path, git_init)` / `workspace_list` / `workspace_assign`
  - 任务：`task_create(window_id, schedule, prompt, report)` / `task_list` / `task_update` / `task_pause/resume` / `task_remove` / `task_run_now`
  - 上下文：`context_view` / `context_compress` / `context_reset` / `context_inject(text)`
  - 配置：`config_get/set`（全局配置）、`report_set`（回报策略）
  - 记忆：`memory_write` / `memory_recall`
  - 汇报：`progress_report`（汇总各工位进度/任务状态，答用户"现在什么情况"）
- **规则**：
  - 用户只跟管理员对话（QQ 功能有限，不做转接员工）
  - 员工产出/提问经内部事件给管理员，由管理员汇总转述用户
  - **回报裁量**【暂定】：任务回报策略枚举 `{silent, brief, detailed}`，默认 brief；管理员可合并多条任务结果一次推送，可裁量"这事不重要就不推"（用户原话：看管理员心情）

### 4.2 程序员员工（V1 唯一员工角色，其他角色留注册接口）
- **工具箱**（AiCode 式全套）：
  - 文件：`read_file` / `write_file` / `edit_file`（diff 局部编辑）
  - 执行：`shell`（超时控制 + 后台终端）
  - 搜索：`glob` / `grep`（代码搜索）、`web_search` / `web_fetch`
  - 计划：`todo_view/add/done`（待办清单，自主单干的驱动器）
  - 安全：`checkpoint_create` / `checkpoint_rollback`（改文件前自动快照，一键回滚）
  - 调研：`explore_subagent`（只读 Explore 子代理，独立上下文后台调研，不阻塞主会话）
  - 沟通：`ask_user`（向上提问→管理员转达用户）
  - 运维：`server_exec`（经面板 Agent 接口跑服务器命令，受模式门控）
  - 扩展：MCP 工具位【暂定 V1 留接口不实现】
- **权限模式**（AiCode 三模式）：
  - `BUILD`：工作区内读写放行；工作区外写/危险命令拦截
  - `PLAN`：全部写操作拦截，只读规划
  - `AUTO`：全放行——【暂定】**仅手动会话可用，定时无人值守禁用 AUTO**
- **员工自主单干**：工位持有待办清单，定时任务可为"继续推进待办"（heartbeat 型），员工自主决定执行顺序；用户/管理员也可随时插话（紧急指令插队到队列头部）

### 4.3 工位（窗口）会话引擎
- **窗口 = 员工工位**：`角色 + 工作区(小工作区) + 模型 + 持久会话 + 待办清单 + 挂载的定时任务集`
- 管理员创建工位，用户可调定时等设置；一个项目在同一工位一直做下去
- **会话持久化**：SQLite 消息表；上下文组装时取近期消息 + 记忆 + 压缩摘要
- **自动压缩**：token 达阈值（如 80% 模型窗口）触发 LLM 摘要压缩；**抗损设计**：压缩只丢对话措辞，`文件改动清单 + 待办 + 关键决策 + 教训` 以结构化形式保留（存 compactions 表 + 工位记忆文件）
- **任务队列**：同一工位同时只跑一个任务（串行），排队 FIFO；`task_run_now`/用户插话可插队
- **群聊公共工作区**（后置，留接口）：数据模型预留 `group workspace` 概念，多员工可进同一大会话；V1 不实现

### 4.4 定时任务（Cron）
- 数据：`Job{window_id, name, schedule_type, schedule_raw, prompt, report_strategy, enabled, last_run, next_run, fail_count}`
- 调度格式（Hermes 式）：Duration `30m/2h/1d`、`every 2h`、`every monday 9am`、5 段 cron 表达式、ISO 时间戳一次性
- 执行：60s tick → 到点 Job → 注入工位会话执行（**非新建会话**，与 Hermes 的区别）→ 产出回报事件
- 异常：执行失败记录 job_runs；**连续失败 3 次自动暂停 + 通知管理员**（转达用户）
- 创建入口：主要经 QQ 找管理员自然语言创建；Web 面板也可建

### 4.5 IM 网关
- OneBot v11 反向 WebSocket（NapCat 上报）；适配器抽象照抄 AstrBot BaseAdapter
- 白名单（仅授权 QQ 号可对话）+ Access Token
- 路由：用户消息→管理员会话；推送：回报→指定 QQ 群/私聊（任务级可配）
- 管理员会话自身也是持久会话（可问"整体概况"）

### 4.6 Web 控制面板（用户主控制台）
- 页面：
  1. **总览**：工位卡片（状态/当前任务/最近产出）、任务概览、系统状态
  2. **工位管理**：查看工位会话流、暂停/恢复、改模型/模式、手动派活、看待办
  3. **任务管理**：Job CRUD、启用/暂停、立即执行、运行历史
  4. **设置**：LLM（OpenAI 兼容 base_url/model/key，多 Key 轮换；Ollama 可选）、回报策略、白名单、面板鉴权
  5. **文件管理器（Windows 资源管理器式）**：左侧目录树 + 右侧文件列表 + 面包屑；双击进入/返回；文本与代码预览（高亮）、图片预览、下载、上传、重命名、删除、新建文件夹；范围=工作区根目录
  6. **日志/审计**：任务运行记录、命令审计
- 鉴权【暂定】：Token/密码登录，内网使用（对齐基座双模式思路）
- 前端【暂定】：FastAPI + 原生 JS（零构建，对齐基座风格）；不抄 AstrBot 的 Vue WebUI

### 4.7 记忆（Memory）
- 文件式：`memory/global.md`（用户偏好/长期事实）+ `memory/windows/<id>.md`（工位级：项目决策/教训）
- 写入：管理员 `memory_write`；员工在压缩时自动沉淀抗损事实
- 注入：会话组装时加载全局记忆 + 本工位记忆

### 4.8 服务器操作
- `server_exec` 工具走基座 `POST /api/terminal/exec`（X-Agent-Token）；审计由基座负责
- 危险命令门控【暂定】：`rm -rf /`、`mkfs`、`reboot/shutdown`、`systemctl` 关键单元、改面板配置等列入黑名单，需用户 IM 确认后放行

### 4.9 LLM 调用与限速重试（用户新增：免费 token 常限速）
- 触发重试：HTTP 429 / 529（过载）/ 超时 / 连接错误
- 策略【暂定默认】：指数退避，基础间隔 10s（10→20→40→80→160，封顶 300s），最大重试 5 次；可切固定间隔模式
- 设置项（config.yaml + Web 设置页，用户可调）：`llm.retry.base_interval`（重试间隔）、`llm.retry.max_retries`、`llm.retry.backoff: exp|fixed`、`llm.retry.max_interval`
- **限速行为**：限速时**不切换 Key**，用同一 Key 按退避策略原地等待后重试（多 Key 配置不参与限速应对）
- 计数语义：重试等待**不计**任务失败；重试用尽才算一次任务失败（才进入"连续失败 3 次自动暂停"）
- 重试期间工位状态显示"限速等待重试（第 n 次）"

## 5. 形态与技术约束

- 形态：**独立新项目**（独立仓库/进程，与 AegisServerPanel 同机部署；可被面板常驻应用托管）
- 技术栈【暂定】：Python 3.11+、FastAPI、SQLite（会话/任务/审计）+ 文件（记忆/配置 YAML）；LLM=OpenAI 兼容 + 可选 Ollama；多 Key 轮换
- 部署：install.sh + systemd 单元 + README（对齐基座交付风格）；pip 依赖精简
- 许可证：**AGPL-3.0**（照抄 AstrBot 需保留其版权声明并在 LICENSE 声明）

## 6. Scope 边界（明确不做）

- V1 不做微信/官方 QQ 适配器（留接口）；不做 no-agent 脚本任务；不做技能系统（留接口）
- 不做多用户权限分级（白名单单用户）；不做分布式调度
- 不抄 AstrBot 的 WebUI/插件市场/Dify 对接；不做群聊公共工作区（留数据模型接口）
- 非程序员员工角色后置（留角色注册接口）

## 7. 待定与风险

| 项 | 类型 | 说明 | 优先级 |
|----|------|------|--------|
| 无人值守 shell 安全边界 | 风险 | 定时任务跑 root 命令，靠 BUILD 模式+黑名单+AUTO 禁用兜底 | ★ 已给暂定方案 |
| NapCat 封号风控 | 风险 | 小号+白名单+Access Token；文档明示 | ★ 已缓解 |
| AGPL 传染 | 已决策 | 项目开源；LICENSE 保留 AstrBot 声明 | ✅ |
| 压缩信息损耗 | 存疑 | 抗损设计（结构化保留事实）缓解 | ⚠️ 已缓解 |
| 窗口内任务串行的吞吐 | 存疑 | 单用户场景够用；多工位天然并行 | ✅ |
| 管理员"心情"回报的不可预期 | 存疑 | 策略枚举兜底（silent/brief/detailed），裁量只在 brief/detailed 间浮动 | ⚠️ 已缓解 |

## 8. 设计决策记录

| 日期 | 决策 | 来源 |
|------|------|------|
| 2026-10-12 | 独立项目；QQ=NapCat 主+官方留口；微信留口不做；只做 Agent 会话任务 | 用户第 1 轮 |
| 2026-10-12 | 持久窗口模型：任务回到同一窗口、自动压缩、项目持续 | 用户新需求 |
| 2026-10-12 | 管理员（管理工具包）+ 程序员员工（AiCode 式编程全套），先做程序员 | 用户新需求 |
| 2026-10-12 | AGPL-3.0 照抄 AstrBot；LLM=OpenAI 兼容+Ollama；记忆进 V1 技能后置 | 用户第 1 轮 |
| 2026-10-12 | 窗口=员工工位（小工作区、管理员创建、员工可自主单干）；群聊公共工作区后置留口 | 用户第 2 轮 |
| 2026-10-12 | 用户只与管理员对话（不转接员工）；Web 面板=主控制面板；回报可推可静默由管理员裁量 | 用户第 2 轮 |
| 2026-10-12 | 新增：Windows 资源管理器式工作区文件查看页面 | 用户第 2 轮 |
| 2026-10-12 | 新增：LLM 限速重试设置（重试间隔/次数/退避，免费 token 常限速）；限速重试不计任务失败 | 用户追加需求 |
| 2026-10-12 | 限速时不切换 Key，同一 Key 原地等待后重试 | 用户修订 |
