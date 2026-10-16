# DogBuddyCn 开发路线图 / 外包交接文档

> 面向接手本项目的开发 Agent。本文档 = **决策定稿** + **批次任务卡** + 已确认缺陷（带代码位置）+ 验收标准。
> 最后更新：2026-10-14，对应版本 `0.10.3`，状态：**Batch 1-9 完成 + 独立审查报告的 32 项缺陷已修**。
> **453 个测试通过（4 个跳过）**。
>
> ⚠️ **审查结论修正**：文档此前多处写"完成"，但独立审查（`审查/REPORT.md`）证明
> Batch 8/9 存在"零件在、没接线"的缺陷（指挥链四向只有审批一通、参数页点了没反应、
> 主管角色控制台造不出来）。这些**已在本版修复**，详见本文档 §6 下方"审查修复批次"。
>
> ⚠️ **v2 最终口径（用户 2026-10-14 拍板）**：只有**一个管理员**，它是用户唯一的对接窗口
> （员工不直接找用户，逐级上报）；用户**可以新建对话**（每个对话独立上下文）。
> 据此**移除了多管理员 CRUD**（`/api/admins` → 404；`admins` 表保留但不再用于鉴权/CRUD）。
> 详见 `docs/DECISIONS-指挥链口径.md`。各批次完成情况见下方各批次小节。
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
tests/               457 个用例（453 passed / 4 skipped，环境相关），pytest 全绿
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
  （2026-10-14 已达成：`--strict` 输出 **0 个未定义 CSS class、0 个缺失图标、exit 0**，
  可以真正挂 CI。此前它恒 exit=1 —— 报的 10 个 class 逐个核对后 0 个是真缺失，
  详见"审查修复批次"P2-8。）

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
> | **T9.2 工具统一化** | `agents/admin/tools.py` → `agents/management/tools.py`（admin 变兼容再导出） | 每个工具加 `required_scope`；`SYSTEM_TOOL_NAMES`（11 个）vs `PROJECT_TOOLS`（24 个）；**未归类的新工具默认 system**（不下放主管）。管理员 = 全部 35 个工具，**行为逐位不变**（T9.2 时为 32；T9.4 起 +`supervisor_command`/`approve_decision`/`decision_list`） |
> | **T9.3 主管装配** | `agents/supervisor/tools.py`、`SUPERVISOR_TOOLS`、`SUPERVISOR_SYSTEM_PROMPT` | 主管 = 员工工具 + 项目级管理工具 + `project_memory_write`（共 48，随工具集增长，以 `len(SUPERVISOR_TOOLS)` 为准），**零 system 级**。主管工位 workspace = 项目根（吃 T7），提示词要求多项目时按 project 分段 |
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
| 无 schema 迁移 | `dogbuddy/db.py:_MIGRATIONS` + `schema_version` 表 | 幂等有序迁移；**当前 SCHEMA_VERSION = 11**。旧代码打开"未来库"会 fail-fast（`SchemaTooNewError`），`manage.py update` 升级前也会拒绝降级到更旧的 schema |
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
| `manage.py update` 不为前端做构建 | 改了 `web/static/**` 必须本地跑 `tools/rebuild-bundles.py` 再提交，否则 bundle/指纹过期（有 `--check` 守卫；⚠️ 仓库里**没有 `.github/`**，`--check` 目前只在本地/手工流程里生效，尚未挂 CI） |
| **升级"看起来成功"但其实回滚了** | 2026-10-14 实测踩到：面板托管的应用被 kill 后，**旧进程仍占着 :8100 在应答**，新进程起不来；`manage.py update` 的健康检查读到的是旧版本号（`version=0.7.0` 而非 `0.7.1`），于是**自动回滚代码**——日志里只有一句 `已完成: version=...` 且 exit=0，很容易看漏。**判断方法**：`curl /healthz` 看 `version` 与 `build` 是否等于刚推的 commit；**处理**：确认旧进程已退出（`ps aux | grep dogbuddy`）后**再跑一次** `manage.py update` 即可（第二次会成功）。这是回滚保护在起作用，不是 bug，但会造成"我明明升级了却没变"。**⚠️ 0.10.6 更正**：其中一半其实是**诊断本身错了**——"重启机制不可用"被写成"新版本起不来"，从而回滚了一份正确代码；而游离旧进程冒充新版本那次是**假 success**。现已改为 `_probe()` 记录现场 + **pid 身份断言**（同一 pid → `restart-noop`，不回滚）+ 终态必写，详见 §8 |

---

## 7. 审查修复批次（2026-10-14，独立审查报告落地）

背景：`docs/REVIEW-PROMPT.md` 交独立审查者跑了一轮，产出 `审查/REPORT.md`
（P0×4 / P1×16 / P2×12）。**报告的大部分结论经过我方独立复现**——
包括它最致命的一条：把 `registry.py` 的 `required_scope` 转发删掉、或把迭代
执行器的工具白名单换掉，**375 条测试全绿**（`scripts/verify_mutation_catch.sh` 现在
会红 5 条 / 1 条，见下）。修复按九个批次落地：

| 批次 | 覆盖 | 关键点 |
|---|---|---|
| B1 | P0-1、P0-2 | `server_exec` 声明 `required_scope="system"`（员工/主管 fail-closed）；`shell_guard` 口径收紧——工作区外的**读写一律拦**，含无法静态判定的构造（`$VAR`/`$( )`/`sh -c`/`sed -i`/`python -c`…）；面板路径保护扩到 shell 通道 |
| B2 | P0-3 | 删对话不再连带删同名真实工位的消息；对话 id 白名单；`system` 保留频道不可被对话接口操作 |
| B3 | P0-4、P2-1、P2-4 | 解包改用 `Path.is_relative_to` + 不提取链接成员；未来库 fail-fast；`/tmp` pid 加 cmdline 校验 |
| B4 | P1-7、P0-2降级 | `schemas(only=[])` 不再等于"不过滤"；删除 `except TypeError` fail-open 回退；未知角色不给工具 |
| B5 | P1-4、P1-5 | 新增 `project_read` 读级（员工只读项目记忆）+ `window_memory_*`（本工位记忆）；五条装配路径全部注入 `ctx.scope` |
| B6 | P1-1 | **指挥链四向事件的生产者**：`ReportService` 逐级收敛 + `supervisor_command` 入口；应用级接线测试（真实 `DogBuddyApp` + 真实总线） |
| B7 | P1-6 | 审批升级**落 `pending_decisions` 并可回传**（主管工具 `approve_decision`）；超时/未答一律 fail-closed |
| B8 | P1-8、P1-10、P2-6、P2-7 | 手动推轮与自动 tick 共用并发锁 + `UNIQUE(iteration_id, round)`；迭代停摆推送停因；重启收尸含轮次；长任务超时不再计失败 |
| B9 | P1-9、P1-11、P1-12、P1-13、P1-15 | profile 落盘；MCP 默认 system 作用域；cron 按时区比较 + 跨进程锁；`job_runs.owner_pid` 只收死属主；后台进程表统一 |
| B10 | P1-2、P1-3、P1-16 | 角色下拉加 `supervisor` 去掉 `admin`；项目表单加主管字段；指挥链接口接上前端；参数页补分支 + 分发加 `else`；**前端不变量加强**（三种绕过手法均被抓住） |
| B11 | P2-2/3/5/8/9/10/12 | 历史清理 + WAL 收回 + 快照 prune；允许根按需重算；日志先装后建；`audit_ui --strict` 真正 exit 0；project_id 校验 |

### 测试可信度（审查报告第六节的直接回答）

报告问"我不小心把作用域检查关掉，测试能不能拦住我？"——**当时不能**（v1 与基线
逐字节相同的失败集）。现在有两条**验证过的**回归守卫：

```bash
bash scripts/verify_mutation_catch.sh    # v1 / v4 变异各自被新测试抓住
bash scripts/verify_frontend_guards.sh   # 前端三种绕过手法各自被抓住
```

判据：新测试在变异下**必须变红**，恢复后**必须全绿**。两个脚本都会自证这一点。

---

## 8. 第二轮修复批次（2026-10-15，`0.10.6`）

背景：B1~B11 上线后现场又暴露四类问题（其中两类是 owner 亲自报的）。本轮**不依赖模型额度**
（额度 429 仍卡着所有"真跑一轮"的验证），因此全部可本地复现 + 单测钉死。

| 组 | 问题 | 修法 |
|---|---|---|
| **上下文口径** | `messages.tokens` 一个列被两种语义共用：账本要"累计花费"、压缩器要"当前占用"。`loop.py` 两处 `append("assistant", …)` **都不传 tokens** → 恒 0 → 用量条恒 0、**压缩永不触发**（比显示为 0 严重得多） | 新增 `WindowSession.last_prompt_tokens` / `note_usage()` / `context_tokens()`；assistant 消息记 `total_tokens`（账本+显示），压缩器与用量条改用**最近一次调用的 `prompt_tokens`**（拿不到则按字符粗估并标注"粗估"），**绝不回落 `SUM(tokens)`** |
| **管理员用量入口** | 管理员会话挂在**内存里的合成窗口** `admin:<cid>`（不写 windows 表），唯一用量端点 `GET /api/windows/{id}/usage` 对它必然 404 → 管理员侧界面上零元素 | 新增 `GET /api/admin/chat/usage?conversation=`（合成会话 + `db.sum_tokens("admin:<id>")`，**不依赖 windows 表**）；`usageHTML()` 抽到 `ui.js` 由工位/对话共用；`views-chat.js` 在加载/发送/切换对话后刷新 |
| **一次触发两本账** | cron 路径 `scheduler.py` 开行A、运行时 `window_manager.py` 又开行B，两行互不知情 → 历史两条相邻记录、仪表盘次数翻倍、成功率被那条**永远不可能变 timeout** 的行拉低 | cron 路径标记 `run_owned_by_caller`，运行时不再开行（调度器仍负责 ok/rate_limited/**timeout**/failed）；手动/`run_now` 路径维持"运行时开行" |
| **`_run_status` 认 `status`** | 真实 runner 返回 `RunnerResult`（无 `stop_reason`/`ok`，只有 `status`）→ 两条分支都命中不了 → **每次执行都记成 failed**（假失败） | 先认 `result.status ∈ {ok, failed, rate_limited}`，保留原 `stop_reason`/`ok` 分支（老测试全绿） |
| **`task_timeout` 无效** | 不在 `AppConfig.WINDOW_PARAM_KEYS` → `effective_params()` 静默丢弃 → `_wait_timeout()` 永远返回 1800s；控制台参数面板也没有这个字段（"看着设了、实际无效"） | 加进白名单 + 基础默认值 1800.0 + `views-params.js` FIELDS 入口（单轮等待上限） |
| **升级诊断不诚实** | ① 缺 `DOGBUDDY_PANEL_PASSWORD` 时"重启机制不可用"被当成"新版本有毛病"→ **回滚掉正确代码**；② 游离进程占端口 → 健康检查读到旧进程却报 `success`（假成功）；③ 一堆提前 return 让结果文件永远停在 `running` | `_panel_restart()` 返回 `(ok, reason)`；新增 `_probe()`（不过滤版本）与 `_observed()` 记录现场；**重启前后 pid 相同即判 `restart-noop`（不回滚、不写 VERSION 戳）**；失败时区分"无人应答"与"旧进程还在应答"；`_finalize_result()` 保证任何退出路径落终态（`failed`/`noop`）；`self_version` 对 `success` 且版本未变追加告警 |

测试：新增 `tests/test_context_usage.py`、`test_run_bookkeeping.py`、`test_update_diagnostics.py`
（18 条），**总基线 476 passed / 4 skipped**；压缩器口径与 assistant 记账两处变异各自被新测试抓住
（把 `compactor._used_tokens` 换回 `token_usage()`、或去掉 `tokens=` → 立刻红）。

未做（留给下一任，理由见 `HANDOVER.md §11`）：模型额度 429（owner 侧）、worker 限速可见化、
退避记忆把 600s 首等持久化、部署通道"不依赖外部守护"的正解（面板密码注入 vs agent-token 路线）。

---

## 9. 第三轮：模型接入"一条表单"（2026-10-15，`0.10.7`，schema 12）

背景：owner 报"我怎么新增 llm 模型，太不方便了吧"。实测痛点有两条，第二条是**真缺陷**：

| 问题 | 说明 |
|---|---|
| 目录条目**没有密钥** | `models` 表只有 provider/base_url，密钥永远取全局 `llm.api_key`。于是"设为当前"换到**另一家服务商**的模型时，带着上一家的 key 去请求（401），只能再回设置页手改——切模型 ≠ 切成一套可用配置 |
| 表单太重 | 新增模型要一次填 9 个字段（provider 还要手打），没有"填地址+密钥→拉列表选一个→测通→保存"的直线路径；拉取导入也不接受密钥 |

修法（**schema v12，新增 `models.api_key`**）：

| 层 | 改动 |
|---|---|
| L5 | `SCHEMA_VERSION 11 → 12`；`_migrate_v12` 给 `models` 补 `api_key`（老库自动升级，幂等）；`_MODEL_FIELDS` 加该列 |
| L2 | `ModelService.upsert/update` 接受密钥（**留空或回传掩码 = 保持原值**）；`activate()` 把条目自带密钥一起写进运行时配置并落盘，返回 `api_key_switched`；`catalog()` 一律回 `mask_key()`；`import_fetched` 把"用哪把 key 拉的列表"记在新条目上；新增 `PROVIDER_PRESETS`（8 个常见服务商，只预填地址） |
| L1 | `ModelIn/ModelPatch` 加 `api_key`；`GET /api/models/options` 回 `providers` 预设；`POST /api/settings/test_llm` 支持 `model_ref`（按目录条目自带密钥测连接） |
| L4/前端 | 模型页表单重做：服务商预设一键带入 → 模型名（可拉取列表选）→ 地址 → **API Key**（密码框）→「拉取模型列表 / 测试连接」→ 保存并可**勾选"保存后立即设为当前"**；备注与高级字段折叠；列表加"自带密钥 / 用全局密钥"徽标；导入弹窗也能带密钥 |

**安全口径**：密钥只落本地库（与 config.yaml 同级 0600），**任何接口只回掩码**（`********` + 末 4 位，
且长度 < 12 的短 key 连末 4 位都不给）；`PATCH` 回传掩码会被识别为"未修改"，不会把掩码存成真密钥；
审计日志只记"自带/全局"，不记密钥值。

测试：`tests/test_model_catalog_keys.py`（9 条）覆盖 v11→v12 迁移、掩码不回传、掩码回传不覆盖、
`activate` 连密钥一起切且落盘、HTTP 层不回明文、预设枚举、前端接入要素。**基线 485 passed / 4 skipped**。

⚠️ **运维后果**：生产库一旦升到 schema 12，**回滚到 0.10.5/0.10.6 会 `SchemaTooNewError` 拒绝启动**
（这是 fail-fast 的设计，不是 bug）。部署 0.10.7 前请先确认不需要回滚，或先备份 `/var/lib/dogbuddy/dogbuddy.db`。

### 9.1 同日发现并修掉的事故：`pytest` 会覆盖**真实用户配置**

本地部署试用时发现 `%LOCALAPPDATA%\dogbuddy\config.yaml` 的内容是测试垃圾
（`base_url: http://invalid.local/v1`、`model: fake`、`data_dir: <pytest 临时目录>`）。
根因：`tests/test_web_api.py::test_settings_profiles_crud` 打
`/api/settings/profiles/cheap` → `persist()` → `save_config()`，而它**没有隔离
`DOGBUDDY_CONFIG`** → 写到平台默认配置路径，把用户真实的 LLM 配置（含密钥）静默覆盖。

修法：`tests/conftest.py` 加 autouse fixture，把 `DOGBUDDY_CONFIG`（Windows 下连带
`LOCALAPPDATA`，因为 `default_data_dir()` 读它）钉到每个测试自己的 `tmp_path`；
新增 `tests/test_config_isolation.py`（3 条）复现事故路径并断言落点在 tmp 内、
真实配置字节不变。**自证**：去掉 fixture 后这 3 条立刻变红（实测），恢复后全绿。
基线 **488 passed / 4 skipped**。

教训写进这里是因为它满足"静默 + 数据损坏"两个条件：跑一次测试就毁掉用户凭据，
而且只有对比文件内容才发现。

---

## 10. 首次真正上生产（2026-10-15 当天，0.10.7 → 0.10.8 → 0.10.9）

前两轮修复一直卡在"没有凭据、服务停着"。当天拿到**面板 Agent Token** 后，
本地三次改动全部上线并逐条验证。生产现状：`/opt/dogbuddy` = GitHub `main` = 本地，
`schema_version=12`，单个面板托管进程。

### 10.1 部署通道（实测有效，已写进 `运维/README.md`）

面板的 Agent API 是真正的入口——**它等价于服务器 root**：

```bash
POST http://192.168.1.2:8787/api/terminal/exec
X-Agent-Token: <面板 Agent Token>
{"command":"id","cwd":"/","timeout":60}   →  {"code":0,"stdout":"uid=0(root)…"}
```

流程：`git diff --binary` → gzip+base64（55KB 补丁 → 8KB）→ 分片 `printf` 追加到
`/tmp/dbc.b64` → `sha256sum -c` → `git apply` → `compileall` → `git commit` →
`git push origin HEAD:refs/heads/main`（服务器有 SSH deploy key，**GitHub 顺带同步**）→
`git rev-parse HEAD > VERSION` → **kill 进程让面板 `restart=always` 拉起** →
轮询 `/healthz` 断言版本号与 `schema_version`。

三个坑（都真踩过，见 `运维/README.md` §3）：
**① 别用 `serverpanel --apps restart` 重启**（外部进程启动会把应用变成游离进程，
面板再拉一个 → `[Errno 98] address already in use` → 启动/退出码3/再启动 死循环，
日志看起来像"升级失败"）；**② 长命令别塞 argv**（Windows 侧 `Argument list too long`），
命令写文件走 stdin；**③ SIGTERM 曾经不退**（见 10.3）。

### 10.2 `0.10.8`：面板 Agent Token 明文泄露（部署当天顺手撞见的 P0）

`GET /api/settings` 里 `"panel": cfg.panel.model_dump()` → 面板 `agent_token` **明文**返回，
前端还把它渲染进 `<input value=...>`。而那个 token 能在服务器上以 root 执行任意命令
（10.1 就是用它）。即"任何拿到控制台 web token 的人 = 拿到服务器 root"。

同时发现第二个真缺陷：`PUT /api/settings` 对 `api_key`/`keys` **没有掩码判断**，
前端把 `********abcd` 原样回传时会把**真密钥覆盖掉**（实测复现）→ LLM 静默认证失败；
配置套 `_apply_llm` 同样中招。

修法：`config.py` 提供唯一掩码口径 `mask_secret()` / `is_masked_secret()`；
`_panel_dump()` 掩码 agent token；`PUT` 全面"空或掩码 = 保持不变"；
前端 Agent Token 改密码框 + "留空保持不变"（不再预填）。新增
`tests/test_secret_masking.py`（5 条）。

### 10.3 `0.10.9`：关停无界 → SIGTERM 卡死 → 面板重启循环

实测：`kill`（SIGTERM）后进程一直不退（`State=S`，`epoll_wait`），必须 `kill -9`；
它不退就一直占着 `:8100`，守护拉起的新进程 `[Errno 98]` 秒退 → 面板"启动/退出码3/再启动"循环。

修法：`DogBuddyApp.stop()` 每一步有 `SHUTDOWN_STEP_TIMEOUT=8s` 上限（超时只记日志、继续清理；
WAL 库不怕强退）＋ 抽出 `build_uvicorn_config()` 加 `timeout_graceful_shutdown=10`（第二道保险）。
新增 `tests/test_shutdown_bounded.py`（4 条）。**生产实测**：SIGTERM 后 **1s 释放端口、11s 进程退出**，
面板随后干净拉起单个新进程（此前是"永不退出"）。

### 10.4 未修（留给下一任）

- SSE 的 `GET /api/events?token=<web token>`（EventSource 不能带自定义头）会让 **web token 进访问日志**；
  要么日志层对该路由 query 脱敏，要么改成一次性短票换 Cookie。
- 模型额度 `429 429003` 仍在——所有"真跑一轮"的验证依然做不了（本轮验证的都是
  启动/接口/进程/迁移这些**不需要模型**的部分）。

测试基线：**497 passed / 4 skipped**。





---

## 11. 第四轮：拉取到的模型列表"只能选第一个"（2026-10-15 晚，`0.10.11`）

owner 报："拉取列表无法选择，只能选第一个。"

- **API 是好的**：SiliconFlow 一次返回 **99** 个模型（`GET /api/settings/models`）。
- **根因在前端**：拉取成功后代码把**第一个模型自动填进了输入框**，而 `<datalist>` 的候选是
  **按输入框里已有的文字过滤**的——输入框里已经是 `BAAI/bge-large-en-v1.5`，下拉里就只剩它自己。
  （同源问题：输入框里本来就有旧模型名时，下拉同样会"看起来拉不到东西"。）
- **修法**：拉取结果放进真正的 `<select>`（全量、不过滤），不预填输入框；选中后写回"模型名"。
  共用实现放在 `ui.js: fillModelSelect()`，四个能拉取模型的地方全部接上：
  模型管理页新增/编辑、设置页「LLM 接入」卡片、首次设置向导、工位编辑弹窗、LLM 配置套弹窗；
  向导里那行 `state.model = state.models[0]` 删掉。
- **顺带修**：条目**自带密钥**时，"拉取模型列表"原来会拿全局密钥去请求（401）——
  现在拉取也支持 `model_ref`（服务端取该条目的地址与密钥），与 `test_llm` 同口径。
- 测试：`tests/test_model_picker.py`（4 条）——`model_ref` 参数口径、未知条目 404、
  "不许自动填第一个"、四个弹窗都要有真下拉。

---

## 12. 第五轮：模型上下文 / 建员工时的压缩阈值 / 管理员的模型（2026-10-15 晚，`0.10.12`）

owner 的三条口径：

> - 模型拉取应该采用服务商默认的上下文
> - 然后管理员建立员工时会指定压缩阈值
> - 我还想设置管理员的模型

落地成这样：

| 诉求 | 实现 |
|---|---|
| **上下文跟服务商走** | `llm/models.py` 新增 `fetch_model_meta()`：从各家网关的 `context_length` / `context_window` / `max_model_len` / `max_input_tokens`、以及嵌套的 `top_provider` / `meta` 里读上下文；`/api/settings/models` 多返回 `details: [{id, context_window}]`。**服务商没给就留 `0`（= 继承），绝不替它编一个数**——实测 SiliconFlow 的 `/v1/models` 只回 `id/object/created/owned_by`。拉取后在下拉里选中模型会自动把上下文填进表单；导入（`/api/models/import`）只补"还没有值"的条目，**用户手填过的一律不覆盖**；`activate()` 只在该条目有值时改运行时上下文 |
| **建员工时指定压缩阈值** | 控制台「新建工位」加 `压缩阈值 0.3~0.95` + `上下文上限`（留空=继承）；`POST /api/windows` 已有 `params`；**校验放进 L3**（`WindowManager._check_params`，越界 → 400，避免 `compact_threshold: 5` 让它"永不压缩"却看不出来）；管理员用对话建员工时 `window_create` / `window_update` 也能带这个参数（更新是**合并**已有覆盖，不是整包替换） |
| **管理员的模型** | 管理员没有工位 → 它的模型就是运行时全局配置，"设为当前"就是给它换模型。所以：`/api/admin/chat/usage` 增加 `model` / `base_url` / `provider`；**对话页顶部显示「模型：xxx」+「换模型」按钮**；模型管理页把「当前生效」改成「**管理员当前模型**」、按钮改成「**设为管理员模型**」，并写明"工位没单独指定模型时继承它"；设置页的兜底卡片只留地址/密钥/参数 + "去模型管理页换模型" |

测试：`tests/test_context_window_and_threshold.py`（10 条）覆盖上下文字段解析、导入不编数/不覆盖、
`activate` 保留全局、建工位带阈值（API + agent 工具 + 越界报错）、管理员模型可见可换（含 SSE/前端接线）。
基线 **513 passed / 4 skipped**。

---

## 13. 第六轮：汇报到用户眼前 + 管理员模型一致性（2026-10-15 晚，`0.10.14`）

owner 报了两件事：

> - 两个员工测试完成后，管理员没有向用户做简短汇报
> - 管理员好像调用了模型列表里不存在的模型

### 13.1 员工干完活 → 用户眼前（三处缺口）

| 缺口 | 证据 | 修法 |
|---|---|---|
| 人工/对话派单跑完**不发任何事件** | `JOB_COMPLETED` 只有 `cron/scheduler.py` 发；"让两个员工做测试"是对话派单，没有 `job_id` | `WindowManager._report_manual_run()`：无 `job_id` 的 prompt 运行结束时发 `job.completed`（失败发 `job.failed`），`report_strategy="brief"`；**带 job_id 的不发**（调度器已发，避免用户收到两条） |
| console 渠道**只写日志** | `ConsoleNotifier.send()` 只 `logger.info`，用户不会去看日志 | 新增 `notify/chat.py: ChatNotifier`：日志 + **写进用户对话流**；`build_notifiers(..., chat_sink=...)` 注入，名字仍叫 `console`（按名路由不变） |
| 用户对话流需要即时更新 | 之前只有整页刷新才看得到 | `AdminService.notify_user()` 落库到**最近活跃的对话**并推 SSE `admin.notice`；`views-chat.js` 订阅后即时追加气泡（别的对话则 toast 提示） |

### 13.2 "管理员调用了列表里不存在的模型"

真相：模型**是存在的**（实测：`zai-org/GLM-5.3-Flash`、`Qwen/Qwen3.5-9B` 都在 SiliconFlow 返回的
99 个里），缺的是**控制台的目录没登记它** —— 模型管理页只有 5 条旧的 Sensenova/DeepSeek 条目，
于是页面显示"未纳入目录"，看起来就像"用了一个列表里没有的模型"。

修法（两条都给）：
- `POST /api/models/adopt-current`：**一键把正在运行的模型收进目录**（密钥不复制，继续用全局那把）；
  页面在"未纳入目录"时直接给按钮。
- `GET /api/models/verify-current`：**向服务商核对**当前模型在不在它的列表里（拼错/下线会在
  调用时才 400，界面上看不出来）；不在时给出相近候选。页面给「校验它在服务商列表里」按钮。

测试：`tests/test_user_reporting.py`（7 条）+ `tests/test_model_catalog_keys.py` 扩 3 条。
基线 **531 passed / 4 skipped**。

---

## 14. 第七轮：流式不闪 / 工位选模型 / 掉线要有终态（2026-10-15 深夜，`0.10.15`）

owner 的三条反馈：

> 1. 流式输出时，输出窗口会闪动
> 2. 工位点击修改模型，应该从模型列表里直接选配置好的模型
> 3. 刚才工位模型掉线了，显示的还是正在输入（上方提示限速等待中），但是确实是掉线了，也没有错误代码

| 反馈 | 根因 | 修法 |
|---|---|---|
| **闪动** | 每个 delta 都用 `outerHTML` 替换整块气泡 → 整棵子树重建 + 重排重绘 | 气泡只建一次，之后只更新 `<span class="stream-text">` 的 **textContent**（换行交给 CSS `white-space: pre-wrap`）；并且**只有用户本来就贴在底部时**才自动滚动（`stickToBottom`，gap>80px 就不抢滚动条） |
| **工位选模型** | 下拉只有点了「拉取模型」才出现，而拉取问的是服务商**全量**列表；选中的只是名字，地址/密钥还来自 profile → 换服务商必然 401/404 | ①弹窗打开即从**模型目录**（`GET /api/models`）填下拉，空选项显示"跟随管理员模型（当前 xxx）"，并标出"管理员当前"；「从服务商拉取更多」保留为次要入口。②`LLMProfileRegistry.model_lookup`（注入 `db.get_model_by_name`）：条目自带 provider/base_url/密钥时**用它建 provider**，空字段退回 profile（绝不拿空串覆盖） |
| **掉线还显示"正在输入"** | ①LLM 失败落库的是 **system** 消息，而前端只认新的 **assistant** 消息 → 死等到 180s 超时；②限速徽标只有"亮"没有"灭" | ①`AgentResult.error_code/error_detail` → `RunnerResult.error_code` → `window_manager._emit_run_finished()` 发 `window.run_finished`（终态+错误码+错误文本），前端收到**立刻**收起气泡并按错误码弹提示；②轮询兜底也认终态 system（`[LLM error/`、`[限速暂停]`），但**瞬态重试的 `[LLM 瞬态失败…继续重试]` 不算**（否则重试期间气泡就没了）；③`main._on_run_finished` 清掉 `_rate_limit` 并广播 `rate_limit.status{waiting:false}` |

测试：`tests/test_stream_and_model_binding.py`（9 条）+ 前端结构断言。基线 **540 passed / 4 skipped**。

### 14.1 补一刀：人工派单**失败**也要汇报（`0.10.16`）

0.10.14 让"员工干完活"汇报给用户，但**失败那一路被静默了**：`ReportService._on_job_failed`
只在"自动暂停"或"连败 ≥3 次"时才触达人，单次失败只写日志（对定时任务是降噪，
对**用户正等着结果的人工派单**就是"掉线了没人告诉我"）。
现在：`payload.manual=True` 的失败**立即按 brief 汇报**（模板里带 `[错误码]` + 人话），
定时任务仍按连败降噪；给主管看的原文也带上错误码。

---

## 15. 第八轮：`tool` 配对 400 的真正原因 + 模型只从目录里选（2026-10-15 深夜，`0.10.17`）

owner 报：

> 我配置模型是不是没配好？
> `[LLM error/permanent] ... Messages with role 'tool' must be a response to a
>  preceding message with 'tool_calls'`
> 要不不搞默认模型了，全从模型目录里自己配得了。管理员不能自己拉取模型，
> 只能从模型页里的模型列表选。

### 15.1 那个 400 **不是他配错了**，是我们重建请求时形状错了

`ContextBuilder.build()` 把持久化消息映射成 OpenAI 形状时是**逐条独立**映射的：
一条 `tool` 结果只要有 `tool_call_id` 就原样发出。可是历史一旦被
`recent_limit=60` 裁剪、被压缩、或被 `context_reset` 清掉一半，就会出现
**"有 tool 结果、它的 assistant.tool_calls 已被裁掉"** —— DeepSeek 对这条极严格，
整轮 400，而用户看到的是一个像"配置问题"的报错。

生产数据里的实锤（本机脚本扫的）：

```
tester-2: 全量 119 条 | limit= 60 段首=tool ← 孤儿开头=True 内部断裂=1
tester-1: 全量  58 条 | limit= 60 段首=user 孤儿开头=False 内部断裂=0
```

修法（`windows/context.py`，L3 域内）：
- **孤儿 tool 结果 → 降级成 `user` 文本**（信息不丢，形状合法）；
- **assistant 声明了 tool_calls 却没有结果 → 在下一条非 tool 消息前补占位 tool 结果**；
- 用与服务商同规则的校验器写测试（`tests/test_tool_call_pairing.py`，5 条），
  并用 **tester-2 的真实历史**验证：裁剪到 60 条后构建 **61 条消息、0 处形状问题**。

### 15.2 模型只从目录里选（owner 的决定）

- 管理员新增两个工具：`model_catalog`（只读列表，标出"管理员当前"）与
  `model_select`（只选目录里的条目：给自己换，或 `window_id` 指定某个工位）。
- `window_create` / `window_update` 的 `model` **必须命中目录**，否则失败并**列出候选**
  （以前随手写也能过 → 请求打到 profile 端点上，报错还像"模型没配好"）。
- 管理员**没有任何拉取服务商模型列表的工具**（本就没有，加测试钉住）；
  `ADMIN_TOOLS` 同步补上两个新名字（不变量测试会拦）。
- 向导保存凭据后自动 `adopt-current`：填的模型立刻进目录 ——"配置里的模型"和
  "模型页里的模型"不再有两套说法。

---

## 16. 第九轮：函数定义非法导致的 400（切到 DeepSeek 才暴露）（`0.10.18`）

owner 报：

> `[LLM error/permanent] ... Invalid schema for function 'approve_decision':
>  true is not of type "array"` —— 你这 deepseek 预设不对啊

**不是预设不对，是我们发出去的函数定义非法。** `_p()` 把 `required` 当**布尔**塞进属性里：

```json
"properties": {"decision_id": {"type": "integer", "description": "...", "required": true}}
```

JSON Schema 里 `required` 只能是**字符串数组**。SiliconFlow / Sensenova 不校验，
所以一直没暴露；切到 **DeepSeek**（严格校验整轮 `tools`）立刻 400，而报错指向工具名，
看起来就像"模型配置不对"。

修法：
- `_p()` 改用内部标记 `_required`，由 `_props()` 收走并生成顶层字符串数组；线上定义里
  再也不会出现属性级布尔 `required`。
- 新增 `dogbuddy/tools/schema_check.py`：`schema_problems()` 递归校验（顶层 object、
  `required` 是数组且已声明、属性不许带布尔 `required`/内部标记、array 必须有 items、
  enum 是数组、type 合法…）。**注册时**每个工具都会自检并记 error 日志（不抛，避免起不来）。
- `tests/test_tool_schemas_valid.py`：把**所有注册表**（admin/management/supervisor/
  worker/generic，共 194 个定义）过一遍校验器，并专门钉住这次的具体形状；
  另有一条测试证明校验器能复现原故障（免得测试变成永远为真的摆设）。

教训：**别把"服务商特定的严格性"当成"用户配置问题"** —— 报错里的工具名和
`true is not of type "array"` 已经指得很清楚了。

---

## 17. 第十轮：圆桌测试复盘 —— 群组工具 / 误拦收敛 / 任务挂死 / token 优化（2026-10-15 夜，`0.10.20`）

owner 报："我前端无法看到群组，还有他们的沟通"。复盘圆桌测试（qagroup 三工位）
的生产数据 + 代码现状，四条根因全部坐实，本轮一次修完；token 优化参考
`github.com/T-Auto/dsh-ops`（DSH 的底层操作插件），**只移植适用 Linux 服务端的思想**
（Rust 原生工具与独立 Windows Shell 不适用）。

### 17.1 群组系统：管理员一个 group 工具都没有（最优先）

| 现象 | 根因（生产数据实证） |
|---|---|
| 控制台群组页永远空白；owner 让管理员"建个群组让他们自己聊" | `groups`/`group_members`/频道消息 **0 行**；`ADMIN_TOOLS` 里没有任何 `group_*`。管理员在对话里明说只能退化成"共享工作区 + 多窗口圆桌"土办法（msg id 474）——它**没有能力**走 Group 功能 |
| "他们的沟通"看不见 | 群频道（`messages.group_id`）没有任何生产者；工人发言散在各窗口会话 + 工作区文件 |

修法：
- `GroupManager.post()`：群频道唯一生产入口（写 `messages.group_id` + 审计）。
- 管理工具包新增 4 个工具：`group_create` / `group_list` / `group_assign` /
  `group_channel_post`（`agents/management/tools.py`），登记进 `ADMIN_TOOLS`
  与 `PROJECT_TOOLS`（project 级 = 主管也能编排自己项目里的群组；运行时 gate 按
  group/window 归属 fail-closed，跨项目拒绝）。
- `AdminServices`/`AdminService`/`main.py` 注入 `GroupManager`。
- 窗口 assign 进组后 `WindowSession.append` 自动带 `group_id` 进频道（T3.1 既有
  机制被激活）。
- 前端群组详情成员行加「会话」入口（跳到该工位会话流）；部署后把 qagroup 三工位
  建成真群组，此后他们的对话自动进共享频道。

### 17.2 边界守卫误拦（圆桌实测：9 次 shell 4 次被拦，每次 5 行长文）

qa-env 因此烧掉大量轮次、最终撞 `max iterations reached`（25 轮/43 次工具调用，
`job_runs` id 20 的 summary 实证）。三条 owner 交接书点名的误拦：

| 误拦 | 修法 |
|---|---|
| `grep -oE '"/api/[a-z]+'`、`curl "http://h$c"` 里的 `/api`、URL 被当路径 | 词法级引号掩码（`_mask_quoted`）：任一引号 region 脱引号后**像纯路径**才参与判定（`rm -rf '/var/log'`、`cat "/etc/passwd"` 依然拦）；URL（`://`）、正则/glob 图案、普通文本一律掩码。重定向与 `cd` 目标照旧在原文上判定（`echo x >"/etc/x"`、`cd "/etc"` 仍拦） |
| `for c in /api /docs; do …` 的词表被当路径 | `for/while/until/if` 构造本身不拦（词表与测试条件不是路径引用）；**命令体逐段照常判**（`do cp $f /etc/` 仍拦）。循环体内的不可判定构造按交接书"放行+记录"（audit `shell:dynamic-allowed`），不再拒绝 |
| `python -c` 一律拒 | 他语言内联代码（python/perl/node/ruby/php）从拒绝降为**放行+审计**：内容从来不可静态分析，且写进 .py 文件执行本就放行——拦它不改变真实攻击面，只制造噪音与绕行动机（qa-env 正是被逼去写脚本文件，随后读到了 `/etc/dogbuddy/config.yaml` 明文密钥）。真正绕不过 shell 分析的构造（`sh -c`/`eval`/`sudo`/`xargs`/`find -exec`/`$( )`/反引号/`${ }`/`$VAR`）仍一律拒 |

拒绝消息**单行化 + 错误码**（`E_BOUNDARY_OUTSIDE_PATH` / `E_BOUNDARY_UNEVALUABLE`），
同一轮对话同一错误码只完整说明一次（`ctx.extra["boundary_codes_shown"]`）。
新增只读工具 `run_status`（已用 token、上下文上限与阈值、轮次/等待上限、信任级、
工作区可写性、待办、定时/人工）——替代 worker 去撞 `env`/`/proc`（qa-ops 查不到
预算只能瞎跑，正是撞上限的推手）。

### 17.3 任务 park 到 2030："启用中"但永不再跑

生产 6 个任务里 5 个 `next_run_at=2030-01-01`、`enabled=1`、`last_run_at=null`
（qa-ops 原话："启用 ≠ 会跑"）。根因：`JobManager.run_now` 只派单，
**从不更新任务生命周期**——一次性任务被 park 到远期后靠手动 run_now 触发，
跑完依然显示启用中。修法：once 任务派单即 consumed（禁用 + 清 next_run，
留审计）；`task_create`/`task_list` 对 > 30 天的下次运行时间给 ⚠ 警告；
前端任务页同名标记。

### 17.4 token 优化（移植 dsh-ops 思想）

- **有界结果引用（PTC 思想）**：`tools/bound_output.py`——shell/web_fetch 的
  20000 字符硬截（留巨大输出的头部，圆桌实测：PyPI simple 索引全量、GitHub API
  root、360KB HTML，每次 ~5-7k token 噪音）改为 head+tail（默认 8000，3:1 分配），
  中间标注省略量与翻页办法（`sed -n`/`grep`/`head`/`tail` 或 `max_output_chars`）；
  grep 截断现在也告诉模型"停在 200 条"。
- **缓存命中可见化**（交接书 §2.2）：`_usage_dict()` 规范化两种形状
  （DeepSeek 顶层 `prompt_cache_hit/miss_tokens`；OpenAI 风格
  `prompt_tokens_details.cached_tokens`，miss 由 prompt 推）→ `WindowSession
  .note_usage()/cache_stats()` → `/api/admin/chat/usage` 与
  `/api/windows/{id}/usage` 新增 `cache_hit_tokens/cache_miss_tokens/
  cache_hit_ratio` → 前端用量条「缓存命中 x% (h/m)」（全 0 时不显示，不造假数字）。
  **验收**：连发两条相同前缀的请求 → 第二条 `cache_hit_tokens > 0`（用 DeepSeek 验）。
- **渐进式披露（按需加载工具 schema）**：改的是工具调用契约，风险高——
  列为待 owner 决策的后继项，本轮不做。

### 17.5 顺带

- `progress_report` 的 NULL summary 崩溃（`0.10.19` 已修）随本轮部署生效。
- 测试基线 559 → **601 passed / 4 skipped**（新增 42 条：群组工具 9 / 守卫误拦 8 /
  run_status 4 / 任务挂死 5 / 有界输出 7 / 缓存命中 9）。`audit_ui --strict` exit 0；
  五条变异（v1/v4/前端 A/B/C）全部被抓住（本机无 bash，守卫脚本的 PowerShell
  复刻在 `scripts/verify_guards.ps1`）。

**已知未修（需 owner 决策）**：沙箱 `.py` 逃逸 P0（qa-tools 实证：脚本文件可读
`/etc/dogbuddy/config.yaml` 明文密钥 + worker env 内 `DOGBUDDY_PANEL_TOKEN`
可 root 执行）——静态检查器的固有局限，真修需要容器/只读挂载级方案。


---

## 18. 第十一轮：群组真机验收 + 模型配置三处硬伤（2026-10-16，`0.10.21`）

owner 交付了 3 个新工位（worker-1 deepseek-v4-flash / worker-2 sensenova-6.8-flash-lite /
worker-3 继承），让我在**真机 + 真限速**下验收群组，并提了三条模型配置反馈。
本节把验收证据与随之修掉的三处硬伤记下来。

### 18.1 群组真机验收（通过）

`能力测试 [9174e4b248cc]`：管理员**自己**用 `group_create`/`group_assign` 建的群
（12.2s，未触发限速）→ `POST /groups/{id}/run` 一轮真实任务 → 三名成员全部 `ok`：

| 证据 | 值 |
|---|---|
| 产物 | `ws1/hello.txt`=`hello-from-worker-1`、`ws2/…-2`、`ws3/hello-from-worker` |
| 频道 | 20 条消息（worker-1/2 各 6 条、worker-3 8 条），组长产出 3 人汇总 |
| 总耗时 | 400.3s（HTTP 200） |
| 限速 | `rate_limit.status {attempt:1, delay:340.0}`，severity=**heavy** → 原地等 340s 后**成功续跑**，未记失败 |
| 缓存命中 | worker-1 `6144/608 = 91.0%`、worker-2 `6144/616 = 90.9%`、worker-3(SiliconFlow) `0/7307 = 0%` |

缓存命中数据在真实 provider 上拿到了非零值（SenseNova 报 cache 字段，SiliconFlow 不报）
—— §17.4 那条"换成会报缓存的 provider 才看得见"得到实证。

### 18.2 模型配置三处硬伤

| 现象（owner 原话） | 根因 | 修法 |
|---|---|---|
| "我好像新增的会覆盖掉原来的" | 目录条目 id 只由 `provider+model` 决定（`make_model_id`），**不含端点**：同名模型换地址必然撞 id，第二条把第一条的 `base_url` 静默改掉，而 key 没跟着换 → 之后请求带着上一家的密钥打到新端点 → **401**（圆桌实测 worker-1/worker-2 的 401 正是这条） | `ModelService._resolve_id()`：同名条目已指向另一个端点时另存 `...@host`，两条共存；`upsert` 回报 `endpoint_conflict`；`_public()` 出 `endpoint/endpoint_host/key_source`；前端列表给同名条目打「同名 · 端点」标签、新增时明确提示"已另存" |
| "拉取模型有时候拉取过多" | `import_fetched` 无任何过滤，SiliconFlow 一次 99 条全入库 | `POST /models/import` 新增 `q`（关键词）与 `only`（只导勾选的）；前端改成**两步**：先「拉取列表（只看不写）」→ 勾选 → 「只导入勾选的」（`mdl-pick` 复选框 + 过滤框 + 全选/清空） |
| "把默认 key 的模型也拉取了" | 导入接口留空即静默回落到"当前生效"的端点与密钥，用户看不到用了谁 | 响应回报 `used_base_url`/`key_source`/`filtered_from`/`filtered`/`conflicts`；前端顶部明说"这次用的端点 + 密钥来源"；**并且不再改写已存在条目的 `base_url`/`provider`**（只在空值时补） |
| "本地 lmstudio 部署的模型不识别" | 实测**不是应用问题**：服务器只有 `192.168.1.2/24`（路由表仅 `192.168.1.0/24`），而 LM Studio 在 `192.168.3.78:1234` → ping 100% 丢包、curl 连接超时。界面却把"连不上"呈现成"模型不存在" | 新增 `fetch_error_hint()`：连接类错误一律附人话（同网段 / Serve on Local Network / 防火墙 / 先在服务器 `curl <base_url>/models` 自检），并回报 `used_endpoint` |

### 18.3 顺带修掉：限速徽标"等完不灭"

群组实测暴露：退避 340s 早已结束、任务跑完、HTTP 已返回 200，`/api/status` 里
`rate_limit.waiting` **仍是 true**。根因：清徽标的钩子挂在 `window.run_finished`
事件上，而**群组运行、管理员对话、工具调用都不发这个事件**（owner 之前报的
"上方提示限速等待中，但是确实掉线了"同源）。

修法：`RetryPolicy` 新增 `on_success`——等待过至少一次之后一旦调用成功就回调，
`LLMProfileRegistry(on_resume_hook=…)` → `main._on_llm_resumed()` 灭徽标并发
`rate_limit.status{waiting:false, reason:"resumed"}`。这条覆盖**所有**调用路径，
不再依赖某个事件恰好被人发出。

### 18.4 基线

614 passed / 4 skipped（+13：身份/导入 8、限速恢复 5）；`audit_ui --strict` exit 0；
bundle 已重建。

---

## 19. 第十二轮：群组频道 recap 的 token 优化 + 验证按钮真调用 + 统一模型实验（2026-10-16，`0.10.22`）

owner 四条反馈：①"将群聊记录重新全发一遍会浪费很多的 token"；②"4.1 有但是无法用"；
③"统一后会非常慢，你可以试一下"；④ LM Studio 不整了。逐条落地。

### 19.1 recap 的 token 浪费：根因是**自指嵌套**（最优先）

生产实测（群组 `9174e4b248cc` 一次 3 人任务）：

| 证据 | 值 |
|---|---|
| 频道消息 | 20 条，其中 **6 条（30%）是编排提示词本身** |
| 提示词长度 | 385 → 915 → 1403 → 1910 → 2471 → **3028** 字符（逐条膨胀） |
| 嵌套深度 | 最后一条里出现 10 次同样的提示词签名 —— 提示词里有 recap、recap 又读回提示词 |
| 按旧口径模拟 | recap 每次注入 402 / 958 / 1462 / 2018 / 2579 / 3152 字符 |
| 一轮合计 | **≈10571 字符 ≈ 3523 tokens** 纯粹的重复 |

旧实现 `group_manager._channel_recap()`：每次给"最近 30 条 × 每条 400 字符"的全量，
而且把编排提示词也算成频道内容；`WindowSession.append` 又把每条提示词写进频道
（T3.1 的"工会话进频道"没有区分**输入**与**产出**）。

修法（四条）：
1. **提示词不进频道**：`WindowSession.append` 认 `session.channel_mute = {marker,left}`，
   只静音"恰好那一条带签名的编排提示词"（不劫持正常 user 输入）；recap 侧同样过滤签名。
2. **增量**：`run()` 开始时取频道最新 id 作水位，每个成员只读"本轮此前成员的产出"；
   跨轮次的上下文由一条**有界历史尾部**（预算减半）承载。
3. **按字符预算裁**（÷3 ≈ token），不是按条数：默认 3600 字符 / 尾部 1800；超出丢最旧的
   （保留最后 3 条），并给出"更早的 N 条已省略"的可行动提示。
4. **可见性**：每一步回报 `recap_chars/recap_tokens/recap_lines/recap_dropped/prompt_chars`，
   审计写 `group:run` 汇总 —— 谁烧了多少 token 不再黑盒。

### 19.2 「验证」按钮：只查"在不在列表"不够（ds4.1 实测）

`deepseek-v4.1-flash` 在 SenseNova 的 `/v1/models` 里，但这把 key 一调就是
`403 permission_denied_error: model is not available in the current token plan`（code 7）——
**列表校验显示绿灯，真跑任务才炸**。现在 `/models/verify-current?probe=1` 会再发一个
1-token 真调用（复用 `test_connection`，`max_retries=0` 快速失败），并在"在列表里但调用被拒"
时给出 warning + 原因；前端「验证」按钮走 `probe=1`，默认路径不花这次调用。

### 19.3 统一模型实验：不只是"慢"，是**级联退避**

owner 说"统一后会非常慢，你可以试一下"。实测（三个工位全部指向 `deepseek-v4-flash`，
同一把 key，干净群组，任务与混合模型那次逐字相同）：

* 运行 **9 分钟**后，第一名成员**连第一条产出都还没出来**（频道里只有那条编排提示词）；
* `/api/status` = `{waiting: true, severity: "heavy", delay: 600.0}`；
* `/var/lib/dogbuddy/retry_memory.json` = `{"": {"last_interval": 600.0, "last_severity": "heavy", "waits": 7, "successes": 6}}`
  —— 退避基准被抬到 **600 秒**并持续生效；
* 对照：混合模型那次（3 个不同 provider）**400.3s 跑完三人**，其中包含一次 340s 退避。

**结论：不要统一。** 多 provider 分散的不只是能力差异，更是限速预算；同一个 key 上串行
三个成员会把退避推高到分钟级以上。实验跑完前我停掉了客户端（服务端没有取消入口）。

顺带修掉一个由此暴露的运行风险：**群组运行没有总时间上限**（限速退避是"无限耐心"设计，
heavy 档还追加重试预算），一次运行可以挂几小时。现在每个成员的步骤用已有的
`task_timeout`（默认 1800s）由 `asyncio.wait_for` 封顶，超时如实记 `timeout: true`
并告诉用户"要放宽就调该工位的 task_timeout"，不再无限等。

### 19.4 基线

624 passed / 4 skipped（+10：recap 预算 6、验证探测 4）＋群组预算 3；`audit_ui --strict` exit 0。

---

## 20. 第十三轮：新增模型**真的**不再覆盖同名（2026-10-23，`0.10.23`）

owner 第二次报同一个问题："创建新模型还是会覆盖同名模型啊"。复现坐实：
0.10.21 只修了"同名但**不同**端点"（另存 `...@host`），**同名同端点**仍然落到
`upsert` 的更新语义上 —— 生产实测（修复前）：

```
create#1 -> 200 openai_compat___lead_tmp_dup_test  note='第一条：原始备注'
create#2 -> 200 openai_compat___lead_tmp_dup_test  note='第二条：新备注'
目录里同名条数: 1        ← 第一条被吃掉，HTTP 200，界面无任何提示
```

**修法**：
* `ModelService.create()`（新增专用入口）：同名同端点 ⇒ 抛 `ModelExistsError`
  （路由 **409** + 人话：告诉你原条目 id/端点/备注，并指路"点那一行的备注编辑"或
  "改名字/换端点另存一条"）；同名不同端点 ⇒ 仍另存。
* `POST /api/models` 改走 `create()`；空模型名等输入错误仍是 400。
* 改配置仍走 `PATCH /models/{id}`（页面「备注」按钮），语义不变；`upsert()` 作为
  低层写入者保留更新语义（`import_fetched` / `adopt_current` 依赖它）。
* 前端：**提交前**就用与后端同口径的 `endpointHostOf()` 判断会不会撞上，
  撞上直接红字提示并拦下（不再"提交了才发现"）。

基线：632 passed / 4 skipped（+5 服务层 +1 路由层）。

---

## 21. 第十四轮：目录条目身份 = **模型名 + 端点 + 密钥**（2026-10-23，`0.10.24`）

owner 第三次纠正口径："我的意思是**两个服务商一样、模型一样，但是 key 不同**，我还是不能创建两个。"

前两轮把身份定义错了：
* 0.10.21：身份 = 名 + `provider`（换个地址就覆盖）；
* 0.10.23：身份 = 名 + 端点（**同端点换 key 仍被当成同一条** → 就是这次报的问题）。

**定稿口径：身份 = 模型名 + 端点 + 密钥指纹**
| 场景 | 行为 |
|---|---|
| 同名 + 同端点 + **同一把 key** | 409（完全同身份，新增不覆盖；要改用「备注」按钮） |
| 同名 + 同端点 + **不同 key**（owner 的诉求） | **各存一条**，新条目 id `...-k<key 尾 4 位>` |
| 同名 + 不同端点 | 各存一条，id `...@<host>`；两者都不同 → `...@host-k<尾4>` |
| 条目**没写 key**（用全局）与另一把 key | 视为不同身份 → 可各存一条 |
| **导入**（`import_fetched`） | 走**弱匹配**（同名+端点，不管 key）：同步服务商列表不该因 key 不同造重复条目，也不挤掉手写备注 |

实现要点：
* `key_signature()`（后 4 位，可读，界面本来就以掩码显示）与
  `key_identity()`（sha1 前 12 位，用来判断"是不是同一把 key"，不泄露密钥）。
* 分隔符**必须路径安全**：`#` 在 URL 里会被当 fragment 截断（`PATCH /api/models/{id}` 直接 404），
  所以用 `@`（pchar 合法）与 `-k`。踩过一次：初版用 `#`，`_safe_id()` 把它替换成了 `_`。
* `adopt_current()` 按身份找，且"条目没写 key = 用全局那把 key"也算同身份，避免每次收编都造重复。
* 前端提交前按同口径拦（同名+同端点+同 key 尾才拦；换 key 放行）。

基线：`tests/test_model_create_no_overwrite.py` 9 条 + 路由 1 条；全套 642 passed / 4 skipped。

---

## 22. 第十五轮：设置页全局密钥停用，密钥归模型条目（2026-10-23，`0.10.25`）

owner："把设置页里全局 llm key 这个给禁用了，我感觉一点用都没有，只留模型页里的列表。"

**口径**：密钥归属**模型条目**（`activate()` 本来就会把条目的密钥切到运行时），
设置页不再提供全局密钥的编辑入口。

| 位置 | 变化 |
|---|---|
| 设置页 · LLM 卡片 | API Key 输入框改为 **disabled**，只读显示当前生效密钥的掩码 + 说明"密钥统一在模型管理页按条目配置"；保存时**不再读取/提交**它 |
| `PUT /api/settings` | 收到一把真密钥（非掩码）→ **400 + 人话**（"请到模型管理页把密钥配到具体条目"），不再默默写入、也不默默忽略；掩码/留空照旧放行 |
| 模型管理页 | "用全局密钥"徽标改为**黄色警告**并注明"设置页已停用"；概览新增一栏"用全局密钥（设置页已停用）：N"，一眼看出哪些条目还依赖它 |
| 条目自带密钥 | 完全不受影响：新增/编辑条目填 API Key →「设为管理员模型」连密钥一起切换（有回归钉住） |

注意：**停用的是"设置页那个框"，不是"全局密钥"这个机制** —— 运行时 `cfg.llm.api_key`
仍然存在（条目切换会写它、没有自带密钥的条目仍会用它），所以已有配置不会因为这次改动而断掉。

基线：新增 4 条回归；全套 642 passed / 4 skipped；`audit_ui --strict` exit 0。

### 22.1 补丁（`0.10.26`）：向导不能绕过停用

`0.10.25` 上线后发现一个自己造的坑：**首次设置向导**（7 步）最后一步仍然
`PUT /api/settings {llm:{…, api_key}}` —— 全局密钥停用后这条路会被 400 拒掉，
**新装用户会卡在向导最后一步**。

修法：向导保存分支改成与全局口径一致的两步 ——
① `POST /api/models` 建一条**带密钥**的条目（409 = 同身份已存在 → 列表定位那条 → `PATCH` 补密钥）；
② `POST /api/models/{id}/activate` 设为当前（密钥随之切换）。
没选模型时退回"只存兜底地址"并提示去模型管理页新增条目。
另：`endpointHost()` 提到 `ui.js`（模型页与向导共用同一口径），并加两条静态守卫测试钉住
"向导不得再往设置页塞密钥"。

---

## 23. 第十六轮：删掉设置页的「LLM 兜底配置」与「LLM 配置套」两张卡（2026-10-23，`0.10.27`）

owner："所以设置里的兜底配置和 LLM 配置套可以删了。"

**模型只有一个来源**：模型管理页的目录（密钥、地址、上下文都在条目上，`activate()` 写运行时）。
设置页从此只剩与模型无关的运维设置。

| 删除 | 说明 |
|---|---|
| `Settings.cardLLM`（LLM 兜底配置） | 连同 `#set-provider` / `#set-base-url` / 已停用的 `#set-api-key` / 高级参数（temperature、max_context_tokens、compact_threshold、request_timeout）/ 「测试兜底配置」按钮 / 三个绑定监听（高级折叠、打开模型管理、测试兜底）一并删除 |
| `Profiles.cardHTML()`（LLM 配置套） | 设置页不再渲染其编辑卡片 |

**保留**（重要）：
* `views-profiles.js` **模块保留** —— 工位/任务表单里的"配置套"下拉仍然由
  `ProfilesMod.fillSelect` 提供；后端 `/api/settings/profiles` 与 `llm_profile` 字段不动。
  只是不再有设置页那个编辑入口（实验阶段没人用配置套：所有工位 `llm_profile=""`）。
* `PUT /api/settings` 的 `llm` 段保留（限速重试卡片仍走它；向导"没选模型"时也用它存地址），
  但写全局密钥仍然 **400**（上一轮的停用规则不放松）。
* temperature / compact_threshold / max_context_tokens / request_timeout 现在只在
  **工位参数页**按工位调（每个模型条目的 `context_window` 决定 `max_context_tokens`），
  全局默认值保持不动。

基线：644 passed / 4 skipped；`audit_ui --strict` exit 0；bundle 已重建。

---

## 24. 第十七轮：长限速"不再睡在请求里" + 退避记忆按 (端点,key) 分桶（2026-10-23，`0.10.28`）

owner："600 秒是不是太长了"。生产实测（SenseNova `Retry-After: 600` / `inference exceeds
tpm/rpm limit`）暴露的不是一个参数，而是四个放大器：

| # | 旧行为 | 后果 |
|---|---|---|
| A | 单次调用内 `await sleep(600)` | HTTP 请求挂着、工位串行队列堵着、界面零进展 |
| B | heavy 档重试预算按**次数**（5+5=10 次）× 每次 600s | 单次调用最坏 **100 分钟** |
| C | 恢复是**加性 5 秒**（600→595→…） | 回到基准要约 118 次成功，实测**几乎永不恢复** |
| D | 退避记忆按 **profile** 分桶（默认全是 `""`） | 管理员被限速把 600s **传染给所有工位** |

修法：

1. **P0 长等待改"暂停 + 调度"**（`llm/retry.py`）：新增 `inline_cap`（默认 90s）与
   `wait_budget`（默认 300s，可被工位 `params.retry` 覆盖，也可在设置页「限速重试」改）。
   超过任一阈值 → 抛 `RateLimitPaused`（**不 sleep**）。定时任务会自己排到 5 分钟后重试
   （`mark_rate_limited`），交互路径立刻拿到明确答复。短等待仍在原地重试。
2. **P1 分桶 + 比例恢复 + TTL**：记忆键改成 `端点|key尾4`（`_memory_key()`）；
   恢复改成"每 2 次成功减半"（600→300→150→…→10）；记忆加 900s TTL 与 schema 版本
   （旧文件直接丢弃，避免那个 600s 又回来）。
3. **P3.1 可解释**：`rate_limit.status` 现在带 `source`（server-retry-after / aimd）、
   `retry_after`、`key_tail`、`endpoint`、`interval`、`action`（slept/paused）、`reason`
   —— 以前一个 600s 的等待**无法归因**。
4. **群组运行如实记**：成员被限速时 `step.rate_limited=true`（不再混进"失败"），
   summary 前缀 `[限速暂停 N/M 名成员]`，审计记 `rate_limited=N`。

实测收益（同一场景）：服务端要 600s 时，调用**在 1 秒内**返回"已暂停"，而不是挂 600s；
`inline_cap=90` / `wait_budget=300` 都是可配的。

基线：655 passed / 4 skipped；`audit_ui --strict` exit 0。

---

## 25. 第十八轮：`server_exec` 装错了工具箱（2026-10-23，`0.10.29`）

owner："先让管理员整理线上工作区。" 结果暴露一个真 bug（管理员自己在对话里如实报告
"我这个管理接口**没有 server_exec 工具**"，而不是权限被拒）：

| 角色 | 旧状态 | 问题 |
|---|---|---|
| 员工（worker） | `WORKER_PROGRAMMER_TOOLS` **含** `server_exec` | 它声明 `required_scope="system"`，员工作用域永远过不了作用域裁决 → 每次调用必然被 fail-closed 拒绝（实测：管理员把"整理工作区"派给 worker-1 → 先被拒 server_exec、再被边界守卫拦绝对路径，任务根本做不到） |
| 主管（supervisor） | 经 `PROJECT_TOOLS` 继承？否 —— 同样没有 | 同上（project 作用域也过不了 system） |
| 管理员 | **没有**该工具 | 唯一合法调用者反而不能用 → 宿主机级操作（清工作区、看磁盘/日志、重启服务）无人可做，只能"派给做不了的员工" |

**修法**：
1. `build_admin_tools()` 末尾装上 `ServerExecTool()`（管理员的独立注册表里），
   `ADMIN_TOOLS` / `SYSTEM_TOOL_NAMES` 同步登记（不变量测试要求三者一致）；
2. `WORKER_PROGRAMMER_TOOLS` **移除** `server_exec` —— 挂上只是"白给一个必然被拒的工具"：
   白烧 schema token（TPM 紧张时尤其亏），还会把模型引向"被拒 → 换个方式绕"的死循环；
3. 两处系统提示词同步：员工提示词不再承诺 server_exec（并说明越界会被拦、有事找管理员），
   管理员提示词明确"**宿主机的事只有你能做**，别派给员工"。

基线：656 passed / 4 skipped；`audit_ui --strict` exit 0。

### 25.1 补丁（`0.10.30`）：管理员路径**没把面板客户端传进 ToolContext**

`0.10.29` 把 `server_exec` 挂给了管理员，但管理员一调就报
`server_exec unavailable: panel client not configured` —— 它自己在对话里如实回报：
"工具存在，但 AegisServerPanel 客户端未配置"。

根因：`AdminService._run_turn()` 构造 `ToolContext` 时**只传了 db/scope/llm_factory**，
漏了 `panel=`；而工位派发路径（`WindowManager` / `GroupManager`）一直是传的。
于是"工具挂了、依赖没接" —— 与 0.10.29 是同一类装配漏洞的两半。

修法：`AdminService(panel=…)` 新增参数并存入 `self.panel`，`_run_turn` 的
ToolContext 带上它；`main.py` 用既有的 `self.panel`（`_build_panel()` 建的面板客户端）注入。
新增回归：装配层面钉住"管理员的 ToolContext 必须带 panel 且 scope 是 system"。

### 25.2 补丁（`0.10.31`）：把等待时间整体缩短（owner："太折磨了"）

| 参数 | 旧 | 新 | 理由 |
|---|---|---|---|
| `base_interval` | 10s | **5s** | 起跳更短 |
| `max_interval` | 300s | **120s** | AIMD 封顶，别一路涨到 5 分钟 |
| `inline_cap`（在线等待上限） | 90s | **20s** | 超过 20 秒就暂停重排，不让人盯着转圈 |
| `wait_budget`（单次等待预算） | 300s | **60s** | 一次调用最多原地等 1 分钟 |
| 限速暂停后的重排间隔（`mark_rate_limited`） | 5 分钟 | **60 秒** | v0.10.28 之后"长等待改暂停"，那 5 分钟就成了新的等待成本 |

记忆量级也会被 `max_interval` 夹住（240s 的记忆回来是 120s），避免历史惩罚把等待又拉长。
设置页「限速重试」卡片可以直接改这几项。

---

## 26. 第十九轮：限速暂停后**人工派单没人叫醒**（2026-10-23，`0.10.32`）

owner："他好像卡住了。" 生产事实：

```
14:03:10  worker-1  [限速暂停] 服务端要求等待 600 秒（heavy 档）；本轮暂停，不是失败，
                    稍后会自动重试。        ← 这句话对人工派单是**假的**
14:03 → 14:34  worker-1 一直 idle，没有任何后续动作
```

**根因**：v0.10.28 把长限速从"睡 600s"改成"立刻暂停"，但**只有定时任务有调度器重排**
（`mark_rate_limited` 会把 `next_run_at` 排到 60s 后）。`dispatch_manual` 没有调度器
—— 被暂停后就没人叫醒它。改动本身是对的（不再占着调用栈），只是**少了一半**：
"暂停"必须配"自动续跑"。

**修法**：
1. `WindowManager.dispatch_manual()` 记住最后一次人工派单（prompt + 续跑次数）；
2. 新增 `requeue_after_rate_limit(window_id, delay=60)`：排一个 60s 后的任务再次派单，
   审计记 `window:requeue_rate_limited`；次数用尽（`RATE_LIMIT_REQUEUE_MAX=15`）→
   记 `window:requeue_giveup` 并**明确放弃**，提示"换模型/换 key，或稍后手动重发"；
3. `main._on_run_finished()`：`status == "rate_limited" and manual` 时调用它，
   并 `admin.notify_user()` 如实告诉用户"已自动续跑第 N 次 / 已停止自动续跑"；
4. 群组运行仍按原样（成员被限速如实标 `rate_limited`，由用户决定是否重跑）。

基线：662 passed / 4 skipped；`audit_ui --strict` exit 0。
