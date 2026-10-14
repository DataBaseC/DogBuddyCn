# DogBuddyCn 开发路线图 / 外包交接文档

> 面向接手本项目的开发 Agent。本文档 = **决策定稿** + **批次任务卡** + 已确认缺陷（带代码位置）+ 验收标准。
> 最后更新：2026-10-14，对应版本 `0.5.2`，状态：**Batch 1-6 已完成**（Batch 1-5：工位参数/能力清单/SSE/待决队列/账本/流式/共享频道群组/审批托管/MCP 桥；Batch 6：错误分类体系 + C1-C5 纠错加固）**+ 模型管理页（用户追加需求）+ Batch 7 T7.4（shell 逃逸缝）**。**249 测试全绿**。
>
> ### 模型管理页（2026-10-14 追加，v0.5.2）
> 用户诉求原话："得有个模型管理页，不然用户怎么替换模型？每个模型后边可以写备注，方便管理员了解模型能力和限制。"
>
> 落地：`models` 表（**迁移 v5**，`projects` 因此顺延到 v6）+ `dogbuddy/services/model_service.py` +
> `web/routes/models.py` + 前端 `views-models.js`（导航「模型」）。要点：
> * 目录与运行时配置**解耦**：目录是备选清单，`POST /api/models/{id}/activate` 才写
>   `AppConfig.llm` 并**落盘**（否则重启回退）——这是"换模型"的唯一入口。
> * **`note` 是核心字段**：能力与限制备注；`import_fetched` 批量导入时
>   **绝不覆盖用户手写的 note**（有专项测试）。
> * 附 `capability`(能力标签) / `context_window` / `speed` / `cost` / `enabled` / `is_default` / `source`。
> * 页面：当前生效横幅、统计、搜索、只看启用、「拉取模型」批量导入、行内改备注、
>   设为当前、启停、删除；切换后提示"已写盘，重启后仍生效"。
> * 这同时提前兑现了 T8.4 的**模型能力表**部分，T8.4 只剩 `RoleSpec` 员工画像。
> 设计方案与选项对比见 `docs/PROPOSALS.md`（决策依据，已全部拍板）。

---

## 0. 交接须知（先读这一节）

### 0.1 这是什么

常驻 Ubuntu 服务器的**个人服务器生产力系统**（多 Agent 数字团队）：管理员 Agent 经 QQ / Web 控制台对接用户，
程序员员工 Agent 在持久"工位（window）"里被定时任务持续驱动干活，Web 控制台管理一切。
**定位是生产用途**：可靠性 / 审计 / 成本可见 / 产出导向优先于交互花哨。

产品叙事（对外）："QQ 驱动的服务器管家"；内核保持数字团队模型。
**默认用户画像 = 股东**：长时间不在场，数据自己看（拉取为主），系统要能在无人值守下安全推进，
只在"需要拍板"和"着火"时才触达用户。

代码基线来自 `AegisAgent v1.3`（见 `docs/` 下的原始设计文档），已在 `DogBuddyCn` 下重命名并修复了一批
阻断性缺陷。**本仓库 = 可运行的真实系统，不是原型。**

### 0.2 技术栈与目录

```
dogbuddy/            Python 包（五层架构，依赖只许向下）
  gateway/adapters/  L1 IM 适配器（OneBot v11 反向 WS）
  web/               L1 FastAPI 控制台 + 静态前端（原生 JS，零构建）
  main.py            DI 装配根 / 生命周期
  services/          L2 应用服务（admin/report/window/job/session/workspace/file/config）
  cron/  events/     L2 定时调度 / 事件总线
  agents/core/       L3 AgentLoop / roles / permissions / runners
  agents/admin/      L3 管理员工具包（32 个工具）
  windows/           L3 工位会话 / 串行队列 / 上下文组装 / 抗损压缩
  tools/             L4 工具集 + ToolRegistry（统一权限裁决 + 审计）
  llm/               L4 provider / openai_compat / ollama / retry / profile_registry
  db.py  config.py  ports.py  memory/  updater.py   L5 基础设施
manage.py            CLI：start|stop|restart|status|logs|backup|restore|version|update
scripts/             make_bundle.py（打包）/ deploy_bundle.py（远程部署）/ bootstrap_git.sh
tests/               130 个用例，pytest 全绿
docs/                设计文档（ARCHITECTURE / DESIGN / DEV_PROMPT / EXTENSIBILITY / PROPOSALS）+ 本文档
```

### 0.3 本地开发（Windows / Linux 均可）

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # 运行时依赖 + pytest
.venv/bin/python -m pytest -q                     # 必须全绿再提交
```

### 0.4 部署与验证（服务器）

线上实例：Ubuntu 24.04 (PRoot/aarch64, Python 3.12.3)，代码在 `/opt/dogbuddy`，
配置 `/etc/dogbuddy/config.yaml`，数据 `/var/lib/dogbuddy`。

```bash
# 打包（含目标平台 wheels，可离线安装）
python scripts/make_bundle.py --with-wheels \
  --platform manylinux2014_aarch64 --python-version 3.12 --abi cp312

# 远程部署（登录取 cookie + 上传包 + 触发服务器端自更新）
python scripts/deploy_bundle.py --bundle dist/dogbuddy-<ver>.tar.gz \
  --panel http://<面板>:8787 --password '<面板密码>' --agent-token '<Agent令牌>' \
  --app-dir /opt/dogbuddy --config /etc/dogbuddy/config.yaml --panel-app-id <应用ID>
```

> ⚠️ **本仓库是公开仓库，凭据一律不要写进代码或文档**。面板地址、密码、Agent 令牌、
> LLM API key 由项目 owner 私下提供。

### 0.5 环境约束（必须遵守）

| 约束 | 说明 |
|---|---|
| **不要修改 AegisServerPanel** | 面板是已上线的独立资产，只走它的 HTTP/CLI 契约 |
| **不要 import 面板的模块** | `app.config` / `app.apps` / `app.auth` 是模块级单例，import 即绑定 `/etc/serverpanel/config.json`，会互相覆盖 |
| **服务器无 systemd** | PRoot 环境；进程由面板「常驻应用」托管（`restart=always` / `restart_delay=5` / `max_restarts=100`） |
| **pip 可能无外网/极慢** | 依赖必须能离线装：`wheels/` 目录随包携带，`_pip_install` 会优先 `--no-index`，失败自动回退联网 |
| **自更新会重启进程** | 任何"改完代码立即生效"的设计都要考虑重启，见 `dogbuddy/updater.py` |
| **配置改动必须落盘** | 只有 `ConfigService.persist()` / `DogBuddyApp.persist_config()` 这一条写路径，绕过它重启即丢 |

---

## 1. 设计决策定稿（2026-10-14 用户拍板，不再讨论，直接执行）

| # | 决策 | 结论 | 落地影响 |
|---|------|------|---------|
| 1 | 产品定位 | **个人服务器生产力**（生产用途） | 功能取舍以可靠性/审计/成本/产出为先 |
| 2 | 前端路线 | **不用 Vue**；原生 JS + 工程化补强（tsc 类型检查、缓存指纹） | 不做框架迁移 |
| 3 | 实时推送 | **SSE**（不做 WebSocket） | `GET /api/events` 单向事件流 |
| 4 | 用户角色 | **股东模式**：用户长时间不在场，数据自取（拉取为主）；**不做日报/周报内容汇报** | 只保留"通知最小集"（见 1.1） |
| 5 | 多用户 | **暂不做**多用户/多租户 | `whitelist.role` 保留字段不动 |
| 6 | 插件生态 | **先 MCP**（bridge 接入），skills 后置等真实场景 | 工具面扩展走 ToolRegistry |
| 7 | 高危命令审批 | **发 QQ 人工审批**；用户开启**管理员托管模式**后由管理员 agent 代审 | 三道保险见 1.2 |
| 8 | 工位参数 | 做**全套工位级覆盖机制**（压缩阈值/上下文窗口/温度等），不做单点补丁 | 见 Batch 1 |
| 9 | 可发现性 | 能力清单页 + 管理员主动引导（从注册表自动生成） | 见 Batch 1 |
| 10 | 员工协作 | **共享工作频道**（不做自由多智能体会话；主从编排后置看需要） | 见 Batch 3 |

### 1.1 通知最小集（"不汇报"的边界，必须遵守）

砍掉的是**内容汇报**（日报/周报/进度播报）。但以下两类**必须触达**（可合并为一条通知）：

- **待决事项**：审批请求、员工提问、需要用户拍板的选择 —— 合并通知如"有 3 件事等您拍板"
- **重大异常**：任务连续失败自动暂停、服务故障、资源/额度告警

原则：**内容不推、决策点和火警才推**；其余数据用户自己上面板看。

### 1.2 托管代审三道保险（必须遵守）

1. **保守默认**：不可逆动作（删数据/改生产配置/重启关键服务）一律拒绝或升级人工；只自动放行白名单类动作
2. **独立审查**：代审必须用独立提示词 + 独立会话（可用 profiles 配便宜稳当的模型专司审批）；严禁干活的 agent 自查自批
3. **留痕 + 可关闭**：每笔代审记录理由进审计；用户可复核追认；一键关闭托管模式

### 1.3 MCP 权限映射原则

每个接入的 MCP server 的工具权限级别（read/write/shell）**必须显式配置、默认从紧（read）**，
不得默认放行——否则一个 MCP server 就绕过全部权限门控。

---

## 2. 开发批次（按序开工，合计约 6~8 周）

> 每个任务卡含：目标 / 要点 / 验收。**验收不过不算完成**；改动遵守 §5 DoD。

### Batch 1｜工位参数 + 可发现性（约 1~1.5 周）— 用户痛点优先

**T1.1 工位级参数全套覆盖**（原 P0-2.3）
- 目标：压缩阈值等参数工位级可调，默认继承全局。
- 要点：
  1. `windows` 表加 `params_json` 覆盖列（NULL=全继承），走 `_MIGRATIONS`
  2. `AppConfig.effective_*(window)` 统一解析链：**工位覆盖 → 绑定 profile → active profile → 全局默认**
     （扩展现有 `effective_model()`，消除"两套覆盖机制"并存的坑；`WindowManager._compactor_for()`/`_execute()` 改用解析值）
  3. 覆盖字段：`max_context_tokens` / `compact_threshold` / `temperature` / `request_timeout` / `max_iterations` / `retry.*`
  4. UI：工位详情加「参数」标签页，每字段显示 `继承中（当前 xxx）` / `已覆盖` + "恢复继承"按钮；默认全继承
  5. 附带：工位上下文用量进度条 + 「立即压缩」按钮（API 已有）
- 验收：新建工位不填参数即可用；某工位上下文窗口改 8k 后仅该工位生效；有单测覆盖解析链四级优先。

**T1.2 能力清单 + 管理员引导**（原用户痛点②）
- 目标：新用户一眼能看到系统能干什么。
- 要点：
  1. 后端 `GET /api/capabilities`：从 `ToolRegistry.schemas()` + `RoleSpec` 自动生成工具卡（名称/说明/权限级/所属角色）+ 角色能力矩阵 + 调度格式/回报策略/权限模式速查
  2. 前端新增「能力」视图（卡片网格 + 角色矩阵）
  3. 管理员会话：首条消息带能力菜单；识别"你能做什么"类问题返回能力目录（复用同一数据源）
  4. 新增工具自动上架（禁止手写第二份清单——会漂移）
- 验收：注册新工具后能力页自动出现；QQ 问"你能做什么"返回能力目录。

**T1.3 UI"按钮不显示"复现**（原 P0-2.1，阻塞项）
- 按原 2.1 的 6 步排查清单在浏览器复现（Console 报错 / 304 缓存 / 登录遮罩 / 逐视图截图 / script 顺序 / 响应式隐藏）。
- 验收：修复前后截图 + Console 报错文本；`python scripts/audit_ui.py --strict` 通过。

**T1.4 前端工程化补强**（决策 2 的配套，可穿插）
- JSDoc 类型注解 + `tsc --noEmit` 检查 + ESLint；静态资源加 `?v=<__version__>` 指纹。
- 验收：tsc 零错误；升级后无需强刷即见新 UI。

**T1.5 文档一致性**（原 P2 小件，穿插）
- `docs/ARCHITECTURE.md` 的 aiosqlite→stdlib sqlite3、版本号统一为 0.3.1+。

### Batch 2｜SSE + 股东仪表盘（约 2 周）

**T2.1 SSE 事件通道**（原 P0-2.4）
- `GET /api/events`（SSE，鉴权头）：订阅进程内 EventBus，先推四类：工位状态 / 任务运行 / 限速 / 升级进度。
- 前端 EventSource + 指数退避重连。
- 验收：派活后 1 秒内看到 idle→running→idle，无需刷新。

**T2.2 通知最小集**（决策 4）
- 待决事项合并通知（"有 3 件事等您拍板"）+ 重大异常告警（连败暂停/服务故障/资源告警）；
  明确**不做**日报/周报。推送通道走 notify 插件（gateway→QQ）。
- 验收：产生 3 个待决事项只推 1 条合并通知；任务连败暂停必有告警。

**T2.3 待决事项队列**（股东模式核心）
- `pending_decisions` 表（类型：审批/提问/选择；状态：open/answered/declined/expired）+ 默认超时策略（如 24h 未答按配置默认走，不卡死任务）。
- 面板「待决」视图 + QQ 快捷回复入口；`ask_user` 改造为写队列（原 P1-3.4 的前半）。
- 验收：员工提问进入队列，QQ/面板答复后回填工位上下文；超时任务按默认策略继续。

**T2.4 账本页（用量/成本）**（原 P1-3.2 升格）
- `messages.tokens` 落库（`LLMResponse.usage` 已拿到）；账本视图：按工位/按天的 token 消耗、成本估算（可配单价）、任务产出、成功率。
- 验收：账本数字与 messages.tokens 求和一致；成本估算随单价配置变动。

**T2.5 流式输出**（原 P1-3.1）
- `llm/provider.py` 加 `stream_chat()` 端口；openai_compat 实现 `stream=True`；SSE 透传对话打字机（管理员对话 + 工位会话流）。
- 验收：控制台对话逐字出现；FakeLLM 契约测试 + 真实 provider 契约测试各一份。

**T2.6 工具调用显示工具名**（原 P0-2.2）
- messages DTO 带 `tool_name`/`tool_call_id`；`renderToolCard(name, args, result)` 显式接收；展示工具名徽章/参数/耗时/状态，可折叠。
- 验收：会话流工具气泡显示真实工具名（`window_create`/`shell`/…）。

**T2.7 run_now 落 run 记录**（原 P1-3.3）：手动触发与调度路径统一走"执行+落 job_runs"。
**T2.8 限速状态接通**（原 P1-3.9）：`RateLimitWaiting` 按接口抛出状态事件，接 SSE 通道，替换字符串匹配。

### Batch 3｜共享工作频道（群组协作）（约 2 周）— 兑现原设计承诺

**T3.1 群组数据模型**
- `groups(id, name, goal)` + `group_members(group_id, window_id, role)` + `messages` 加 `group_id`；
  迁移走 `_MIGRATIONS`；`windows.group_id` 预留字段启用。

**T3.2 群组任务流**
- `task_create(group_id=...)` → 组员按分工/轮序在**共享上下文**接力执行 → 中间产出进群组频道 → 指定组长收尾汇总。
- 会话组装 = 共享频道上下文 + 组员私有记忆；压缩按共享上下文整体做（阈值用 T1.1 的组级参数）。
- **权限：取组员中最严模式**（安全优先）。

**T3.3 群组 UI**
- 群组视图：成员卡（各员工状态）+ 共享频道时间线 + 组任务 + 「汇总报告」输出区。

**T3.4 并发治理**
- `drop_messages` 竞争修复（原 P2）：组内多成员并行写共享上下文，改为按 id 游标软删除或加锁。

- 验收：建组→派组任务→组员接力产出→组长汇总报告，全程控制台可演示；组任务的审批按最严模式拦截有效。

### Batch 4｜审批回路 + 托管代审（约 1.5 周）

**T4.1 高危命令审批流**（原 P1-3.4 后半）
- 危险命令 → 写待决队列（T2.3）→ QQ 推送"批不批" → 批准/拒绝 → 批准入放行清单 + 审计。
- 验收：审批闭环全程可演示；拒绝后任务安全降级（不执行该动作）。

**T4.2 ELEVATED 信任级**
- 权限模式增加 ELEVATED：经逐条批准的动作进入**工位级放行清单**（带次数/有效期），夜间定时任务可跑高危动作。
- 验收：批准过的动作再次执行不再询问（或按次数衰减），清单面板可见可撤。

**T4.3 托管代审**（决策 7，三道保险见 §1.2）
- 托管模式开关（per 用户）；独立审查 agent（独立提示词+独立会话+可配专用 profile）；保守默认策略；升级人工通道。
- 验收：托管下代审全程留痕可追认；不可逆动作必被拦或升级；一键关闭生效。

**T4.4 审计强化**（原 P2）
- 审计视图补 actor/action/时间过滤 + 导出 + 工位完整执行时间线；代审记录高亮。

### Batch 5｜MCP 接入（约 1 周）

**T5.1 MCP bridge**：按 Tool 协议适配 MCP client（stdio + SSE 传输），`registry.register()` 挂载；管理员/员工可分别配置启用。
**T5.2 权限映射**：每个 MCP server/工具的权限级别显式配置，**默认 read 从紧**（§1.3）。
**T5.3 能力上架**：MCP 工具自动进 T1.2 能力清单。
- 验收：接一个真实 MCP server（如 filesystem）工具可用；权限按配置拦截有效；能力清单自动出现。

### Batch 6｜纠错与架构加固（✅ 已完成，按方案 A 全做）

> **完成情况（2026-10-14，v0.5.0）**：A1 + C1~C5 全部落地，新增 23 个专项测试，全量 217 绿、零回归。
>
> | 项 | 落地位置 | 说明 |
> |---|---|---|
> | **A1 错误分类** | `dogbuddy/errors.py`（新） | 四类 `Transient/Permanent/PolicyDenied/ToolFailure` × 四处置 `RETRY/STOP/ASK/FEEDBACK`；`classify()` 永不自爆；`unwrap()` 把 `RetryExhausted` 剥到根因；`llm.retry.is_retryable()` 改为委托，**"什么算可重试"只剩一个定义** |
> | **C1 瞬态续跑** | `agents/core/loop.py: _chat_with_continuation` | 瞬态错误最多续跑 `transient_retries`（默认 3）× `transient_delay`（默认 10s）；**永久错误（认证/4xx）立即停**；provider 内部重试与 loop 层续跑是两层，各自有界 |
> | **C2 死循环熔断** | `agents/core/loop.py: _run_tool` + `_signature` | 同签名（工具名+参数 sha256）连续失败达阈值（默认 3）即拒执行，注入"换一种做法"提示；成功即清零；**参数不同不合并计数** |
> | **C3 坏参数容错** | `tools/registry.py: _validate_args` | 不是对象 / 缺必填 / 未知参数 → 回传"错在哪 + 正确 schema"，让模型自改；工具内部崩溃也变 `ToolResult.failure`（带分类与处置建议），不再抛 |
> | **C4 上限降级** | `agents/core/loop.py: _degradation_summary` | 到 `max_iterations` 产出"做到哪了 / 最近调了哪些工具 / 卡在哪 / 下次怎么接"，落进会话与任务记录（`summary`） |
> | **C5 孤儿清扫** | `db.py: sweep_orphan_runs` + `main.py: start()` | 启动时把卡 `running` 的 `job_runs` 标 failed+原因、`windows` 复位 idle；幂等、不误伤已结束记录 |
>
> **未做（有意）**：全库剩余裸 `except Exception` 集中在"绝不能打断主流程"的边界（审计、压缩、MCP 启动、SSE 扇出、审批留痕），这些是**刻意的防御性吞异常**，各自位置已写明理由；把它们改成分类处理只会增加打断主流程的风险，故保留。已收敛的是两条热路径（LLM 调用、工具执行）。

#### 原始任务卡（保留，供核对）

> 2026-10-14 立项。现状：纠错是"回传式"（工具失败文本给 LLM 看）+ LLM 层 RetryPolicy，缺兜底层。
> **范围待用户拍板**：A = 全做（A1+C1~C5）/ B = 只做 A1+C1+C2 / C = 自定。先做 A1（其余的地基）。

**A1 错误分类体系（地基，1 天）**：建 `dogbuddy/errors.py` 统一异常分类
（Transient 瞬态 / Permanent 永久 / PolicyDenied 策略拒绝 / ToolFailure 工具失败），每类映射标准处置
（重试 / 停 / 问人 / 降级）；收敛全库 20+ 处裸 `except Exception`；`llm/retry.RetryPolicy` 复用到
工具执行层（仅幂等工具重试）。
- 验收：分类有测试；LLM 契约测试（tests/test_llm_provider_contract.py）不回归。

**C1 LLM 瞬态失败续跑（0.5 天）**：loop 层 LLM 调用失败不再直接 `break`——瞬态错误
（超时/5xx/连接）按 RetryPolicy 续跑 N 次，永久错误（认证/4xx）才停。
**C2 死循环检测（0.5 天）**：同签名 tool_call（name+args 哈希）连续失败 3 次 → 拒执行并注入
"该路径已失败 3 次，换策略"提示，防烧 token 到 max_iterations。
**C3 坏参数容错（0.5 天）**：LLM 传的工具参数 JSON/schema 校验失败 → 错误回传让模型自改
（与工具失败同路），不直接抛。
**C4 max_iterations 降级（0.5 天）**：到顶不再硬截断，自动总结"做到哪了+卡在哪"存入任务记录
（AgentResult.stop_reason 已有该分支，补总结产出）。
**C5 孤儿任务清扫（0.5 天）**：服务启动时把 in-flight 悬挂任务（卡 running）标 failed+原因，
防崩溃重启后无人收尸。
- 验收（C1~C5）：每项配专项测试（LLM 瞬态 stub、重复调用注入、坏参数注入、超限触发、
  崩溃恢复模拟）；187+ 测试全绿不回归。
- **明确不做**：多步任务补偿回滚（sagas）——个人服务器场景副作用轻，不做。

### Batch 7｜工作区划分重构：项目层（✅ 已完成，2026-10-14，v0.6.0）

> **完成情况**：T7.1~T7.6 全部落地，新增 29 个专项测试（T7.4 的 15 个 + 项目层 14 个），**263 测试全绿、零回归**。
>
> | 项 | 落地位置 | 说明 |
> |---|---|---|
> | **T7.1 projects 表** | `db.py: _migrate_v6`（**迁移 v6**，v5 被模型表占用） | `projects(id,name,root_path,params_json)` + `windows.project_id/subdir` + `groups.project_id`；**老库自动一目录一项目**（把已有 `workspace_path` 包成项目并挂上工位），老数据零人工处理；删项目只解挂、绝不删目录/工位 |
> | **T7.2 挂载解析 + 四级参数链** | `window_manager.effective_workspace/project_for/params_for`、`config.effective_params(param_sources)`、`session.workspace_override` | 参数优先级 **工位 > 项目 > 绑定 profile > 全局**（`retry` 逐键合并，项目调 `max_retries`、工位调 `base_interval` 能叠加）；生效工作区 = `项目根/(工位子目录)`，**已存在时自动创建**、子目录越出项目根直接拒绝；该工作区同时写进 `ToolContext.workspace`（文件工具与 shell 越界守卫的唯一边界）**和提示词的 Workstation 块**（否则模型会按老路径找文件） |
> | **T7.3 群组共享工作区** | `group_manager.run` | 群组挂了项目 → **全体成员跑项目根**（组员看到同一批文件、组长汇总的产物就在同目录），没挂项目才退回各自工作区 |
> | **T7.4 shell 逃逸缝** | `tools/shell_guard.py`（v0.5.1 已完成） | 见下 |
> | **T7.5 文件管理器边界** | `workspace_manager.list/create_project/mount` | 可见根按**项目**对齐：已挂项目的工位不再列出被取代的 `workspace_path`；未挂项目的保留老行为 |
> | **T7.6 项目 UI** | `web/routes/projects.py`、`views-projects.js`、`tools/wire_projects_view.py` | 项目卡片（根目录/默认参数/已挂工位带子目录/关联群组）、新建/编辑/删除、挂载工位（可选子目录）、**参数继承链可视化**（`GET /api/projects/{id}/params-chain` 摊开每层的值与来源 window/project/inherited）、工位 DTO 增加 `project_id/subdir/project_name/effective_workspace` |

#### T7.4 补 shell 逃逸缝（✅ v0.5.1 已完成）
>
> **T7.4 D1 补 shell 逃逸缝（✅ 已完成）**：新增 `dogbuddy/tools/shell_guard.py`，
> `shell` 执行前先分析越界（`cd` 出工作区 / 重定向与写命令的绝对路径出界 /
> 写命令用 `..` 爬出），默认拒绝并给可行动提示；工位信任级
> `params.trust = "elevated"` 时放行，**但无论放行还是拒绝都写审计 `shell:boundary`**。
> 刻意**不拦工作区外的读**（`uname`/`df`/`which`/`cat /etc/os-release`）——那归
> PermissionGate 的危险命令黑名单管，一刀切禁读会废掉正常运维。
> 边界取 `ctx.workspace`，因此 Batch 7 把 workspace 改成项目根后无需返工。
> 新增 `tests/test_shell_guard.py`(11) + `tests/test_t74_trust_switch.py`(4)，
> 其中一条专门验证"放行开关真的可达"（否则拦住却放不开=死锁）。

> 拍板结论：采用**项目层**方案（A）。现状"工位=目录"一对一硬隔离的三个缺口：
> ①群组无共享工作区（协作靠消息抄）②无"项目"概念（多工位干同一活被迫割裂）
> ③shell 有逃逸缝（文件工具锁死，但 shell `cd /` 就绕过）。
> 目标划分：**项目=根目录+默认参数**，工位挂载项目（可带子目录），群组挂项目。

**T7.1 projects 表 + 迁移 v6**：`projects(id, name, root_path, params_json, created_at)`；
`windows` 加 `project_id`（+ `subdir` 子目录，可空）；`groups` 加 `project_id`。
兼容旧库：迁移时把已有 `workspace_path` 自动包一层项目（一目录一项目），数据无痛。
**T7.2 挂载解析与参数链**：`ToolContext.workspace` = 项目根 + 工位 `subdir`；文件工具边界
改项目根（工位带 subdir 时锁 subdir）；工位参数解析链延长为
**项目 params → 工位 params → 绑定 profile → active profile → 全局**（`effective_params` 加项目层，
`param_sources` 同步显示来源）。
**T7.3 群组共享工作区**：群组任务跑群组所挂项目的根（成员共享上下文+共享文件）；接力可直接
引用相对路径；权限仍取最严（沿用 T3.2）。
**T7.4 D1 补 shell 逃逸缝（必做）**：shell 命令强制锁在工作区内执行（检测 `cd` 越界/绝对路径
写出界），越界访问必须走 ELEVATED 信任级 + 审批留痕才放行。
**T7.5 D2 文件管理器边界对齐**：Web 文件管理器浏览根改为**项目维度**（按项目/挂载展示），
与工具侧边界口径一致（当前浏览根=全局+全部工位目录，越权可见）。
**T7.6 项目管理 UI**：项目视图（创建项目/挂载工位/挂群组）+ 参数继承链可视化
（项目→工位→profile，复用 T1.1 的"继承中/已覆盖"展示）。
- 验收：两工位挂同一项目可互读文件；旧库迁移无痛（迁移测试）；shell 越界被拦且有专项测试；
  文件管理器只见授权范围；参数继承链四级来源正确；187+ 测试全绿不回归。

### Batch 8｜自动化调度与项目迭代（进行中：组三已完成）

> 拍板：迭代自动档 = **方案 c（三档预设 + 手动覆盖并存）**；追加需求——
> **大模型限速非常慢时的智能退避**。三组能力，咬合 Batch 3（群组）/ Batch 7（项目层）。
> 现状底座：`cron/jobs.py` 4 种排程 + 60s tick + `job_runs` + 连续失败 auto_paused；
> `llm/retry.py` 指数退避（10s→20→…cap 300s，5 次，**不读 Retry-After、无历史记忆**）。

> **组二 · 能力画像与项目迭代（✅ 已完成，v0.8.0）**：新增 29 个专项测试，全量 332 绿、零回归。

> **组一 · 任务自动化（✅ 已完成，v0.7.1）**：新增 19 个专项测试，全量 303 绿、零回归。
>
> | 项 | 落地 | 说明 |
> |---|---|---|
> | **T8.4 员工画像** | `roles.RoleSpec` + `capabilities` 路由 | `RoleSpec` 新增 `domains`（擅长域）/ `can_deliver_alone`（能否独立交付）/ `needs_approval`（是否需审批）/ `iteration_speed`（推进节奏档）/ `note`（画像备注）；`tool_breadth`（工具集广度）**由工具数推导**（narrow<8≤standard<18≤broad）。内置：程序员=可独立交付/标准节奏，管理员=运维域/保守节奏。**模型能力表部分已在 v0.5.2 的模型管理页提前完成** |
> | **T8.5 迭代计划** | `services/iteration_service.py` + **迁移 v7**（`iterations` / `iteration_rounds`） | 项目级 `{目标, 参与工位, 档位}`。**方案 c：三档预设 + 手动覆盖并存**——预设（保守 240min/20 轮/2 轮无进展；标准 60min/50 轮/3 轮；激进 15min/100 轮/5 轮）给默认值，`speed_minutes`/`max_rounds`/`no_progress_limit` 一旦显式填写就以填的为准。**节奏按画像自动推**：参与工位里最慢的节奏档（木桶原理）× 当前模型能力表的速度档 × T8.8 限速严重度（banned ×6），模型越慢/工位越保守/正在被限速 → 迭代越慢（避免排队雪崩）。`effective_plan().derived` 逐项写明**每个数字来自哪一层**，控制台能解释"为什么它 4 小时才跑一轮"。停止条件：**达成目标 / 无进展 N 轮 / 次数上限**，预算上限是可选字段（0=不限） |
> | **T8.6 迭代执行器** | `services/iteration_executor.py` + `report_iteration` 工具 + `/api/iterations` + 项目页迭代块 | 到点把项目团队拉进**共享工作区**跑一轮（优先用项目上已有的群组，天然共享工作区+频道记录；没有群组则逐个派发，仍在同一共享目录）。轮次 +1、记 `iteration_rounds`；达停止条件即停、发 `iteration.stopped` 并写明原因；每轮发 `iteration.round` 汇报进展与下次时间。**进展判定**靠 `report_iteration`（工位自己汇报 progress/note/goal_achieved）——**没汇报也算一次"无进展"**，否则会永远转下去；progress 与上轮持平也算无进展，有进展则清零。单轮崩溃只记一次无进展，不会停掉整个迭代 |
>
> | 项 | 落地 | 说明 |
> |---|---|---|
> | **T8.1 自助排期** | `tools/automation_ops.py: ScheduleTool` + `registry.build_registry(jobs=…)` | 工位可用 `schedule` 工具建/改/删/暂停/恢复/立即执行/列出定时任务。**权限从紧**：只能管**本工位**与**同项目**工位的任务，跨项目一律拒绝（`_allowed_windows` + 逐操作校验），所有写操作过 PermissionGate 并审计。排期表达式写错会**回传正确语法让模型自改**。注意 `ADMIN_TOOLS` 必须恰好等于 `agents/admin/tools.py` 的集合（管理员用独立注册表），所以这三件工具只进员工工具箱——这个约束被不变量测试守着 |
> | **T8.2 条件任务** | `NotifyOwnerTool` + `JobOutcome.notify*` + `scheduler` 分支 + `JOB_QUIET` 事件 | `report_strategy=conditional` 时**只有工位调用过 `notify_owner` 才真的通知**；没调用就静默（发 `job.quiet`，不发 `job.completed`），砍掉"没事也报一遍"。有事时报告用**工位写给人的那段话**（`notify_text`）而不是整段执行输出。`brief` 任务行为不变 |
> | **T8.3 自调度** | `RescheduleTool` + `JobStore.mark_success(next_run_at=…)` + `scheduler` | 工位在本轮里用 `reschedule`（`after='2h'` 或 `at=ISO`）自决下次运行时间，**覆盖 cron 默认**；带 60s 下限（防疯狂空转）与 90 天上限；一次性任务自调度后**保持启用**（工位意图是"过会儿再来"，不是跑完即止）；任务失败时**不安排**下次运行（失败有失败的处理） |
>
> 数据流：工具把意图写进 `ctx.extra` → `PromptRunner` 提取成 `RunnerResult.next_run_at/notify*` → `WindowManager._to_outcome` 映射到 `JobOutcome` → `CronScheduler` 消费。中间任何一跳丢失都会让工位的意图失效，所以每一跳都有测试。

> **组三 · 限速自适应退避（✅ 已完成，v0.7.0）**：新增 20 个专项测试，全量 284 绿、零回归。
>
> | 项 | 落地 | 说明 |
> |---|---|---|
> | **T8.7①读 Retry-After** | `llm/retry.py: parse_retry_after / retry_after_from` | 秒数与 HTTP-date 两种格式都支持（过去时间归零）；能穿透 `RetryExhausted` 等包装异常挖到响应头。有服务端提示就**精确等待**，不再瞎猜指数 |
> | **T8.7②AIMD** | `RetryPolicy._bump_interval / delay_for` | 连续限速**先乘性放缓再等**（10→20→40→80…），连续成功**加性恢复**（每次只回收半个基准，不会一步回到基准）。修掉一个真实缺陷：原实现是"先等完再放缓"，等于第一次限速只等基准时长 |
> | **T8.7③每 profile 记忆** | `RetryMemory`（`data_dir/retry_memory.json`） | 记住"上次退避多久才成功"，新 policy 直接从该量级起跳；按 profile 隔离；文件损坏一律当空、写失败只记日志——记忆坏掉绝不影响调用 |
> | **T8.7④动态预算** | `effective_max_retries()` | 重档 +5 次、封禁档 +10 次，避免"5 次×5 分钟就放弃"；服务端给的量级可以突破 `max_interval`（否则 1 小时被砍成 5 分钟） |
> | **T8.8 分档** | `RateLimitSeverity / severity_for / RateLimitPaused` | 轻（<60s）原地等 / 重（<30min）等待+通知 / 封禁（≥30min）**抛 `RateLimitPaused` 暂停**——不原地睡小时级 |
> | **T8.8 联动** | `main._on_llm_severity`、`runners.PromptRunner`、`loop.AgentLoop` | 发 `rate_limit.paused` 事件；`AgentLoop` 把暂停映射成 `stop_reason=rate_limited`，`PromptRunner` 映射成状态 `rate_limited`，调度器因此**只标暂停并通知、不计入连续失败**（区别于 fail 暂停）。`_last_rate_limit_wait` 留给 T8.5 算"模型越慢→迭代越慢" |
>
> 顺带修掉一个真实缺陷：封禁级限速原本会**先按普通瞬态续跑 3 轮退避（30 秒）才暂停**；
> 现已改为立即暂停（有专门的回归断言 `transient_retries == 0`），测试耗时也从 31s 降到 0.7s。

**组一 · 任务自动化（原定 W1/W2/W4）**
- **T8.1 Agent 自助排期（W1，1 天）**：新增员工可用的 schedule 工具（权限从紧——只能给本工位/
  本项目排期）；对话里"这事每天做"→自动落成 job。
- **T8.2 条件任务（W2，1 天）**：job 跑完由 Agent 判定"有事才通知 / 才动作"（条件分支），
  砍掉无用通知。
- **T8.3 自调度（W4，0.5 天）**：任务可返回"下次运行时间"覆盖默认 cron，Agent 自决节奏。

**组二 · 能力画像与项目迭代（新增，管理员视角）**
- **T8.4 能力画像层（1 天）**：`RoleSpec` 扩展**员工画像**（擅长域 / 工具集广度 / 能否独立交付 /
  是否需审批）；新增**模型能力表**（速度档 / 上下文窗口 / 是否支持工具调用 / 成本档 / 擅长推理or代码），
  内置常见模型（GPT/Claude/Qwen/DeepSeek/Ollama 本地）+ 管理员可改。
- **T8.5 项目迭代计划（1.5 天）**：项目级配置 `{目标, 参与工位, 迭代模式(保守/标准/激进)}`；
  系统据能力画像（T8.4）自动推**迭代速度**与**迭代次数**，**三档预设 + 手动覆盖并存**（方案 c）。
  停止条件：达成目标 / 无进展 N 轮 / 次数上限（**预算上限预留为可选字段**）。
- **T8.6 迭代执行器（项目调度器，1 天）**：到点把项目团队拉进**共享工作区**跑一轮协作
  （吃 T3 群组 + T7 项目），轮次 +1、记进展；达停止条件即停并通知；每轮结束由 Agent 汇报进展
  （复用 T8.3 自调度）。

**组三 · 限速自适应退避（新增，针对"限速非常慢"）**
- **T8.7 智能退避（1 天）**：`RetryPolicy` 升级——①优先读服务端 `Retry-After` 精确等待
  （不再瞎猜指数）；②**AIMD 自适应**（连续限速乘性放缓 / 连续成功加性恢复）；③**每 profile 记忆**
  "上次退避多久才成功"，下次直接从该量级起跳。重度限速允许 `max_interval`/`max_retries` 动态扩展
  （避免 5 次×5 分钟就放弃）。
- **T8.8 限速严重度分档 + 联动（0.5 天）**：轻（秒级）原地等 / 重（分钟级）等待 + 放慢迭代节奏 /
  封禁级（小时级）暂停任务并通知（**区别于 fail 暂停**，沿用 `rate_limit.status` 事件通道）；
  把"当前限速严重度"喂给 T8.5 迭代速度计算（**模型越慢 → 迭代越慢**，避免排队雪崩）。
- 验收：限速 stub 测试（读 Retry-After、AIMD 收敛、记忆起跳）；重度限速下项目迭代自动降速；
  187+ 测试全绿不回归。

### Batch 9｜角色体系：三层指挥链（✅ 已完成，v0.9.0）

> 落地 `ROLES.md`（角色职能）+ `ARCHITECTURE-v2-ROLES.md`（架构 v2）。
> 核心：**主管 = 装了管理工具的工位（scope=项目）**；管理工具统一化；汇报逐级收敛。
>
> **两处文档矛盾的拍板见 `docs/DECISIONS-指挥链口径.md`**（主管工具集 = 员工工具 + scoped 管理工具；
> 多管理员先做 owner/admin 最小可用、配置文件 token 视作 owner 永不失联）。
> **完成情况**：T9.1-T9.6 全部落地，新增 26 个专项测试，全量 357 绿、零回归。
>
> | 项 | 落地 | 说明 |
> |---|---|---|
> | **T9.1 底座** | `agents/core/scope.py`（新）、`permissions.check_scope`、`roles.RoleSpec.scope_level` | `Scope{level,project_ids}` 值对象 + `ScopeResolver`（**由角色推导、不接受自报**；主管按 owner 字段 ∪ 项目内 `role=supervisor` 工位兜底）。Gate 新增作用域维度并**在模式矩阵之前**裁决：未声明/判不出目标/越界 → **一律拒绝（fail-closed）** |
> | **T9.2 工具统一化** | `agents/admin/tools.py` → `agents/management/tools.py`（admin 变兼容再导出） | 每个工具加 `required_scope`；`SYSTEM_TOOL_NAMES`（10 个）vs `PROJECT_TOOLS`（22 个）；**未归类的新工具默认 system**（不下放主管）。管理员 = 全部 32 个工具，**行为逐位不变** |
> | **T9.3 主管装配** | `agents/supervisor/tools.py`、`SUPERVISOR_TOOLS`、`SUPERVISOR_SYSTEM_PROMPT` | 主管 = 员工工具 + 项目级管理工具 + `project_memory_write`（共 42），**零 system 级**。主管工位 workspace = 项目根（吃 T7），提示词要求多项目时按 project 分段 |
> | **T9.4 指挥链服务** | `services/supervision_service.py` | 四向事件 `supervisor.command` / `worker.report` / `supervisor.digest` / `project.iteration.step`；寻址 `project_id → owner_supervisor_id → 主管工位`；**无主管/主管工位不存在 → 退化直连管理员**（v1 行为零回归）。`report_up()` 按角色自动分流 |
> | **T9.5 数据与 API** | 迁移 v8（`projects.owner_supervisor_id` + `admins`）、**迁移 v9（`conversations`）**、`tools/project_memory_ops.py`、`/api/projects/{id}/chain` | 项目主管字段是寻址枢纽；**v2 最终口径：只有一个管理员**（多管理员 CRUD 已移除，`admins` 表保留但不再用于鉴权/CRUD）；**用户可新建多个对话**（各自独立上下文，见 `docs/DECISIONS-指挥链口径.md`）；项目记忆 `memory/projects/<id>.md`（**主管可写、员工只读**）；`/chain` 摊开 管理员→主管→员工 与健康度 |
> | **T9.6 既有批次修正** | `loop._approval_flow` + `SupervisionService.approval_target` | 审批改**逐级**：不可逆动作永远升级管理员（第一道保险）；普通高危先投主管；**投出去后本轮仍然暂停**（不能算批准）。无主管 → 管理员 |
>
> **验收达成**：主管能派活给本项目员工并收回报 ✅；**主管改他人工位失败** ✅（`test_主管改他人工位必须失败`）；管理员功能零回归 ✅（工具集合与 32 个工具逐位一致）；无主管退化直连 ✅；作用域 fail-closed ✅。

**T9.1 角色与作用域底座（0.5 天）**
- `agents/core/roles.py`：新增 `supervisor` RoleSpec；RoleSpec 增 `scope_level` / `capability` 字段。
- `agents/core/scope.py`（新）：`Scope` 值对象 + `ScopeResolver`（角色/工位 → Scope）。
- `agents/core/permissions.py`：Gate 增**作用域维度**，**fail-closed**（未声明/越界一律拒绝）。

**T9.2 管理工具统一化（0.5 天，重构）**
- `agents/admin/tools.py` → `agents/management/tools.py`，每工具声明 `required_scope`（project|system）。
- `admin` 角色改为「引用 management 工具集 + system 装配」——**管理员行为逐位不变**（回归保障）。
- 验收：管理员功能零回归；主管装配出的工具集**不含** system 级工具（config/self_update/账号）。

**T9.3 主管角色装配（0.5 天）**
- `agents/supervisor/`（新）：主管工具集 = 员工工具 + scoped 管理工具（window_/task_/group_/context_ 限本项目）。
- 主管工位 `workspace` = 项目根；管多项目时指令按 `project` 分段防止上下文串味。

**T9.4 指挥链服务 supervision_service（1 天）**
- 四向事件：`supervisor.command`（管理员→主管）/ `worker.report`（员工→主管）/
  `supervisor.digest`（主管→管理员）/ `project.iteration.step`。
- 寻址：`project_id → projects.owner_supervisor_id → 主管工位`；**无主管时退化直连管理员**。
- `report_service` 改造为逐级路由（无 project_id 事件走原直连路径，行为不变）。

**T9.5 数据与 API（0.5 天）**
- 迁移：`projects` / `windows.project_id` / `groups.project_id` / `admins`（多管理员实体化）。
- L1：`routes/projects.py`、`routes/iterations.py`；`auth` 支持多管理员账号（可选）。
- 项目记忆 `memory/projects/<id>.md`（主管读写，员工只读）。

**T9.6 对既有批次的修正（同步生效）**
- Batch 4 审批：路由改**逐级**（员工→主管→管理员；不可逆动作升级管理员）。
- Batch 7 项目：owner = **主管**（非管理员）。
- Batch 8 迭代：执行者 = **主管**。

- 验收：主管能派活给本项目员工并收回报；**主管改他人工位必须失败**（越权专项测试）；
  管理员功能零回归；无主管场景退化直连正常；187+ 测试全绿不回归。

### 后置 / 暂不做（明确不做，避免接手者发散）

| 项 | 原因 |
|---|---|
| skills 技能系统（原 P1-3.5） | 决策 6：等有 3 个以上真实复用场景再定格式，不做空壳 |
| 多用户/多租户（原 P1-3.8） | 决策 5：暂不做；`whitelist.role` 字段保留 |
| 多模态输入（原 P1-3.10） | 后置；等 vision 模型成为刚需 |
| 前端框架迁移 | 决策 2：不做；tsc 检查足够 |
| 主从编排 / 自由多智能体会话 | 决策 10：共享频道先行，看效果再议 |
| 日报/周报类内容汇报 | 决策 4：明确不做（通知最小集除外） |

### 误报澄清（原缺陷清单中不成立的项）

- **原 P1-3.6 "员工工具包是空壳"：误报。** `agents/worker_programmer/tools.py` 是刻意的薄装配层：
  工具全量注册于 `tools/registry.build_registry()`，角色工具集经 `RoleSpec.tool_names` 在 AgentLoop
  的 schema 层过滤。非缺陷；顺手把 `spec` 变量用起来或删掉未使用警告即可。

---

## 3. 原缺陷清单 → 批次映射（保底：不丢任何欠账）

| 原编号 | 内容 | 归宿 |
|---|---|---|
| P0-2.1 | UI 按钮不显示 | Batch 1 · T1.3 |
| P0-2.2 | 工具名不显示 | Batch 2 · T2.6 |
| P0-2.3 | 工位参数调不了 | Batch 1 · T1.1 |
| P0-2.4 | 状态不实时 | Batch 2 · T2.1 |
| P1-3.1 | 无流式输出 | Batch 2 · T2.5 |
| P1-3.2 | 无 token/成本统计 | Batch 2 · T2.4 |
| P1-3.3 | run_now 不落记录 | Batch 2 · T2.7 |
| P1-3.4 | ask_user 不闭环 | Batch 2 · T2.3（队列）+ Batch 4 · T4.1（审批） |
| P1-3.5 | skills 空壳 | 后置 |
| P1-3.6 | 员工工具包空壳 | 误报（见上） |
| P1-3.7 | MCP 未实现 | Batch 5 |
| P1-3.8 | 单用户模型 | 暂不做 |
| P1-3.9 | RateLimitWaiting 死码 | Batch 2 · T2.8 |
| P1-3.10 | 多模态 | 后置 |
| P2 前端构建化 | → 决策 2 改为 tsc 补强 | Batch 1 · T1.4 |
| P2 文档不一致 | | Batch 1 · T1.5 |
| P2 测试可移植性 | | 各 Batch DoD 顺带 |
| P2 drop_messages 竞争 | | Batch 3 · T3.4 |
| P2 审计可观测 | | Batch 4 · T4.4 |

---

## 4. 现状：已经做完的事（不要重复修）

以下缺陷在原基线里存在，**均已修复并有测试覆盖**，接手时不要"重新发现"一遍：

| 已修复 | 位置 | 说明 |
|---|---|---|
| 配置永不落盘 | `dogbuddy/config.py: save_config()`、`main.py: persist_config()` | 控制台/管理员改的设置以前只改内存，重启全丢。现在原子写 + 0600 + `.bak` + env 注入的密钥不写回文件 |
| 无任何自更新能力 | `manage.py: cmd_update()`、`dogbuddy/updater.py` | 锁 / 脏树检查 / 配置备份 / 切代码 / 装依赖 / 重启 / **`/healthz` 断言版本** / 失败自动回滚 |
| 无 schema 迁移 | `dogbuddy/db.py:_MIGRATIONS` + `schema_version` 表 | 幂等有序迁移 |
| 控制台无法与管理员对话 | `dogbuddy/web/routes/chat.py` + 前端「对话」视图 | `/api/admin/chat{,/admins,/messages,/reset}` |
| `settings.py` 未导入 `HTTPException` | `web/routes/settings.py` | 无效 profile 名以前 500 |
| 工位提问进不了管理员上下文 | `main.py: _on_worker_question` | 订阅 `worker.question` / `confirm.request` |
| 工位 model 字段是装饰性的 | `llm/profile_registry.py`、`services/window_manager.py` | 以前填了不起作用；现在「工位指定 > 配置套 > 活动配置套 > 全局默认」，provider 缓存按 `(profile, model)` 建键 |
| **真实 LLM 一调就崩**（三处） | `main.py: llm_factory`、`llm/provider.py: chat()`、`llm/openai_compat.py: _usage_dict()` | ① 工厂接线错返回 lambda；② 只实现 `text_chat` 而调用方用 `chat`；③ `int(dict)` 解析嵌套 usage 崩溃。**测试全用 FakeLLM，所以以前完全没暴露** |
| `cron` 更新丢字段 | `cron/jobs.py: JobStore.update` | `report_channel` / `llm_profile` 以前被静默忽略 |

**教训（写测试时必须注意）**：现有测试大量注入 `FakeLLM`，它满足的契约和真实 provider **不一致**，
上面的三个致命 bug 就是这样漏掉的。**任何 LLM 相关改动都必须补"对着真实 provider 契约"的测试**，
见 `tests/test_llm_provider_contract.py`。

---

## 5. 全局验收标准（Definition of Done，适用于所有提交）

1. `python -m pytest -q` 全绿；新增功能必须有测试（LLM 相关必须对着真实 provider 契约测）
2. 配置改动走 `persist_config()`；DB 改动走 `_MIGRATIONS + SCHEMA_VERSION`
3. 自更新：`manage.py update` 服务器实跑一次，并故意做坏版本验证回滚
4. UI 改动：手工过 **总览 / 对话 / 工位 / 任务 / 文件管理器 / 日志审计 / 设置** 七个视图（Batch 1 后加「能力」视图）
5. 提交信息写清"改了什么+为什么"，并 bump `dogbuddy/__init__.py: __version__`
6. LLM 相关：FakeLLM 测试与真实契约测试**成对**提交

---

## 6. 已知坑（影响执行，开工前读完）

| 坑 | 说明 |
|---|---|
| FakeLLM 与真实契约不一致 | 曾致 3 个致命 bug 漏测；见 §4 教训 |
| PRoot 无 systemd | 面板 restart 只认 Cookie 不认 Agent 令牌（401）；进程由面板常驻应用托管 |
| 离线 wheels 陷阱 | 目标机无 PyPI 通路，`wheels/` 必须随包 |
| 升级期间进程重启 | 长任务需幂等；自更新走独立进程（45s 延迟） |
| `save_config` 不写 env 密钥 | 有意设计（密钥不落盘），改配置时别"修复"它 |
| `Window.model` 与 `llm_profile` 双机制 | Batch 1 的 T1.1 必须统一进 `effective_*` 解析链，别再加第三套 |
| GitHub 匿名 HTTPS 不可靠 | 用 SSH deploy key（GnuTLS recv error (-110)） |
| 升级后前端缓存 | T1.4 的 `?v=` 指纹解决；未解决前 Ctrl+Shift+R |
| 新增工具必须同步角色白名单 | `ADMIN_TOOLS` / `WORKER_PROGRAMMER_TOOLS`，有不变量测试 `test_self_upgrade_tool.py` 守着 |
| 本仓库公开 | 凭据绝不入库；面板地址/密码/令牌由 owner 私下提供 |
| **别用 PowerShell 的 `Invoke-RestMethod -Body <字符串>` 传中文** | WinPS 5.1 按 Latin-1 发送，中文全变 `?` 且服务端"看起来成功"→ 数据静默损坏（实测模型备注被写成 `??????;429 ????,???? 2;???`）。改用：本地 UTF-8 文件 + `-InFile`，或 base64 落地后 `curl --data-binary @file`。**验证**：读回字段打印 `ascii(v)`，能见 `\u5de5\u5177` 才对；见到真 `?` 就是被截断。产品自身 API 用 UTF-8 完全正常 |
| **也别用 PowerShell 调 `curl.exe -d "<JSON>"`** | WinPS 5.1 把参数交给原生程序时**不转义内嵌双引号**，`{"name":"x"}` 会变成 `{name:x}`，服务端报 `json_invalid / Expecting property name enclosed in double quotes`（实测创建群组时踩到）。**推荐姿势**：把验证脚本整段通过面板 `bash` 执行（那里 `-d '{...}'` 由 bash 解析，一切正常），或先把 body 写进文件再 `curl --data-binary @file`（`-F` 上传文件同理，`Invoke-RestMethod -Form` 在 5.1 不存在） |
| `manage.py update` 不为前端做构建 | 改了 `web/static/**` 必须本地跑 `tools/rebuild-bundles.py` 再提交，否则 bundle/指纹过期（有 `--check` 守卫） |
| **升级"看起来成功"但其实回滚了** | 2026-10-14 实测踩到：面板托管的应用被 kill 后，**旧进程仍占着 :8100 在应答**，新进程起不来；`manage.py update` 的健康检查读到的是旧版本号（`version=0.7.0` 而非 `0.7.1`），于是**自动回滚代码**——日志里只有一句 `已完成: version=...` 且 exit=0，很容易看漏。**判断方法**：`curl /healthz` 看 `version` 与 `build` 是否等于刚推的 commit；**处理**：确认旧进程已退出（`ps aux | grep dogbuddy`）后**再跑一次** `manage.py update` 即可（第二次会成功）。这是回滚保护在起作用，不是 bug，但会造成"我明明升级了却没变" |
