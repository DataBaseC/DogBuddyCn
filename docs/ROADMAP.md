# DogBuddyCn 开发路线图 / 外包交接文档

> 面向接手本项目的开发 Agent。本文档 = 现状 + 已确认缺陷（带代码位置）+ 待办优先级 + 验收标准。
> 最后更新：2026-10-13，对应版本 `0.3.1`。

---

## 0. 交接须知（先读这一节）

### 0.1 这是什么

常驻 Ubuntu 服务器的**多 Agent 数字团队**：管理员 Agent 经 QQ / Web 控制台对接用户，程序员员工 Agent 在
持久"工位（window）"里被定时任务持续驱动干活，Web 控制台管理一切。

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
docs/                设计文档（ARCHITECTURE / DESIGN / DEV_PROMPT / EXTENSIBILITY）+ 本文档
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

## 1. 现状：已经做完的事（不要重复修）

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

## 2. P0 — 用户明确反馈的问题（优先修）

### 2.1 【UI】"按钮不显示" —— 需要视觉复现（静态样式/图标缺失已被排除）

**先说结论：我没有复现出来，原因不在静态标记或 CSS。**

已做的静态审计（脚本已入库，可随时重跑）：

```bash
python scripts/audit_ui.py            # 报告
python scripts/audit_ui.py --strict   # 有发现则非零退出，可挂 CI
```

结果：

```
[CSS] 静态使用 244 个 class，其中 3 个没有定义
    ★ .chat-time   ★ .jobs-table   ★ .modal-x
[图标] 引用 21 个，#ic- 定义 44 个，缺失 0 个
```

那 3 个未定义的类**经人工核对都是冗余钩子，不会造成任何元素不可见**：

| class | 依赖的已有样式 | 影响 |
|---|---|---|
| `modal-x`（`js/ui.js:124/158/192` 弹窗关闭按钮） | `.icon-btn`(`css/base.css:253` 尺寸/边框/悬停齐全) + `.modal-head`(`base.css:478` 已是 `flex` + `space-between`) | 无 |
| `jobs-table`（`index.html:207` 任务表） | `.table`(`base.css:383`) + `.th-actions`(`base.css:399`) | 无 |
| `chat-time`（`js/views-chat.js` 时间戳） | `.chat-meta` | 无 |

图标引用 21 个、定义 44 个、**缺失 0** → 所有图标按钮都能渲染。

**因此请按下面的清单在浏览器里复现并定位**（这是外包接手的第一件事）：

1. 打开页面 → `F12` → **Console**：任何视图抛 JS 异常，都会导致该视图后续 DOM 不再渲染。
   这是最可能的原因，请把报错原文记下来。
2. **Network**：确认 `/assets/js/*.js`、`/assets/css/*.css` 返回 200 而不是 304 缓存了旧版本
   （升级后前端文件同名，浏览器缓存很容易导致"新版后端 + 旧版前端"）。**先 Ctrl+Shift+R 强刷一次**。
3. 确认已登录：未登录时是 `login-mask` 覆盖层，页面元素看起来像"消失"。
4. **逐视图截图**：总览 / 对话 / 工位 / 任务 / 文件管理器 / 日志审计 / 设置 —— 记录哪几个视图正常、哪几个异常。
5. 若某视图整体空白：检查 `js/app.js` 的 `renderView()` 里对应 `case` 分支，
   以及 `global.DogBuddyViews.X` 是否存在（`index.html` 底部的 `<script>` 顺序错了会让 `X` 为 `undefined`，
   新增视图模块必须排在 `app.js` **之前**）。
6. 窄窗口下是否被 `css/views.css` 末尾的响应式规则隐藏。

**验收**：修复前后各一张截图 + Console 报错文本；`python scripts/audit_ui.py --strict` 通过。

> 顺带建议：控制台的静态资源没有版本指纹（`/assets/js/app.js` 永远是同一个 URL）。
> 建议加 `?v=<__version__>` 或改成长缓存 + hash 文件名，从根上消除"升级后 UI 看着没变"的假象。

### 2.2 【UI】工具调用不显示工具名

**现象**：工位会话流里，工具调用气泡永远显示"工具调用 tool"，看不到实际调用了哪个工具。

**根因（已定位）**：
- 后端**已经存了**工具名：`dogbuddy/agents/core/loop.py:87-89` 写入 `role="tool"` 的消息，
  `meta={"tool_call_id": tc.id, "tool_name": tc.name}`（`messages.meta_json` 字段）。
- 前端**没有读 meta**：`dogbuddy/web/static/js/views-main.js` 的 `renderMessages()` 里
  `Windows.renderToolCard(m.content)` 只传了 `content`（工具输出文本），
  而 `renderToolCard` 只能用 `JSON.parse(content)` 去"猜"工具名——真实工具返回的是纯文本
  （例如 `(todo list is empty)`、`ls` 输出），解析失败 → 永远回落到默认值 `'tool'`。

**修法**：
1. `GET /api/windows/{id}/messages`（`web/routes/windows.py`）的 DTO 增加 `tool_name` / `tool_call_id`
   （从 `Message.meta` 读）。
2. `renderToolCard(name, args, result)` 改为显式接收工具名与参数，不再靠 JSON 猜。
3. 建议同时展示：工具名徽章、参数（截断）、耗时、成功/失败状态。可折叠。

**验收**：会话流里每个工具气泡显示真实工具名（如 `window_create`、`shell`、`todo_add`）。

### 2.3 【功能】工位级参数调不了（"工位调不了上下文"）

**现状**：工位详情只有三个动作按钮——`压缩` / `注入` / `重置`
（`views-main.js:392-394`，对应 `POST /api/windows/{id}/context/compress|inject|reset`）。
**没有任何地方能设置这个工位自己的参数。**

**缺口**：`Window` 实体（`dogbuddy/ports.py`）只有
`id / name / role / workspace_path / model / mode / status / llm_profile / group_id`，
而以下**全是全局配置**（`config.py: LLMConfig`），工位级无法覆盖：

| 参数 | 现在的位置 | 需要的语义 |
|---|---|---|
| `max_context_tokens` 上下文窗口 | `config.llm.max_context_tokens` | 工位可覆盖 |
| `compact_threshold` 压缩阈值 | `config.llm.compact_threshold` | 工位可覆盖 |
| `temperature` | `config.llm.temperature` | 工位可覆盖 |
| `request_timeout` | `config.llm.request_timeout` | 工位可覆盖 |
| `max_iterations` 单轮工具调用上限 | `WindowManager(max_iterations=25)` 全局 | 工位可覆盖 |
| 限速重试策略 `retry.*` | `config.llm.retry` | 工位/任务可覆盖（可选） |

**修法（建议）**：
1. `Window` 增加可选覆盖字段（用 `""`/`None` 表示继承，与 `model` 的语义保持一致）。
2. 在 `AppConfig` 增加 `effective_*(window)` 系列的解析函数，**保持"单一解析规则"**
   （参考已有的 `AppConfig.effective_model()`，不要在前端另写一套）。
3. `WindowManager` 的 `_compactor_for()` / `_execute()` 改用解析后的值。
4. 前端：工位详情的「模型 / 模式」弹窗扩成「工位设置」标签页，每个字段显示
   `继承中（当前 xxx）` 或 `已覆盖`，并提供"恢复继承"按钮。**默认必须是继承**，
   不要让用户为了跑起来而逐个填。
5. DB 迁移：加列走 `dogbuddy/db.py` 的 `_MIGRATIONS` + `SCHEMA_VERSION`，不要手改表。

**验收**：新建工位不填任何参数即可正常工作；把某个工位的上下文窗口改成 8k 并触发压缩后，
只有该工位按 8k 生效，其它工位不受影响；有对应单测。

### 2.4 【体验】控制台状态不实时

**现状**：无 WebSocket / SSE。前端靠轮询（`app.js: refreshStatus`），工位运行状态、
限速徽章、任务进度都有延迟。`window.status` 等事件只在进程内 `EventBus` 流转，不推给浏览器。

**修法**：加一条 `WS /api/events`（或 SSE），把 `EventBus` 的事件推给控制台；
先做"工位状态 + 任务运行 + 限速 + 升级进度"四类即可。

**验收**：派活后控制台 1 秒内看到工位从 idle → running → idle，无需刷新。

---

## 3. P1 — 核心能力缺口

| # | 缺口 | 位置 / 现状 | 建议 |
|---|---|---|---|
| 3.1 | **无流式输出** | `llm/openai_compat.py: text_chat()` 是非流式的（V1 设计如此）；`AgentLoop` 一次性拿完整回复 | 控制台聊天体验差的主因。加 `stream=True` + SSE，把 delta 推给前端；`llm/provider.py` 加 `stream_chat()` 端口 |
| 3.2 | **无 token / 成本统计** | `messages.tokens` 字段恒为 0；`LLMResponse.usage` 拿到了但没人落库 | 把 usage 写入 `messages.tokens`，控制台加"本工位/全局消耗"面板 |
| 3.3 | **手动 run_now 不写 job_runs** | `services/job_manager.py` 的 `run_now` 直接投递队列；只有 `cron/scheduler.py` 的调度路径写 `job_runs` | 统一走同一条"执行 + 落 run 记录"的路径，否则"任务运行记录"里看不到手动触发的执行 |
| 3.4 | **`ask_user` 不阻塞、确认回路缺失** | `tools/ask_user.py:41` 恒返回 `answered=False`，靠人工 `context_inject` 回填 | 需要：请求 → 事件 → 用户答复 → 回填同一轮对话；含超时与拒绝语义；危险命令的 IM 确认要走同一条链路 |
| 3.5 | **`skills/` 是空壳** | `dogbuddy/skills.py: load_skills()` 永远 `return []` | 实现目录扫描 + 加载钩子（`/var/lib/dogbuddy/skills/`） |
| 3.6 | **员工工具包是空壳** | `agents/worker_programmer/tools.py` 只有 20 行，`spec` 变量未使用 | 真正装配程序员角色的工具集（当前只是转调 `build_registry()`） |
| 3.7 | **MCP 未实现** | 设计文档留了接口，代码没有 | 按 `ToolRegistry` 的 Tool 协议接 MCP server；管理员/员工分别配置 |
| 3.8 | **单用户模型** | `whitelist` 表有 `role` 字段但无实际权限分级 | 多用户 / 多角色（owner/admin/viewer）、工位归属与隔离 |
| 3.9 | **`RateLimitWaiting` 是死码** | `llm/retry.py:28` 定义后无人抛出；前端只能靠字符串匹配 `"rate limited"` 判断限速 | 按接口设计向上抛状态事件，接 3.1 的事件通道 |
| 3.10 | **无图片/文件多模态输入** | `MessageEvent.attachments` 字段存在但无人处理 | IM 与控制台都应能发图给模型（需 provider 侧支持 vision） |

---

## 4. P2 — 工程与体验

- **前端是零构建的原生 JS**（`web/static/js/*.js` 共 ~150KB，全局 `DogBuddyViews`/`UI`/`API` 三个命名空间）。
  当前能跑，但状态管理、组件复用、类型安全都缺。**建议迁移到构建化前端**（Vite + 任一框架），
  或者至少引入模块化 + 类型检查，否则 UI 类缺陷会持续出现。
- **文档与实现不一致**：`docs/ARCHITECTURE.md` 仍写着 `aiosqlite`（实际用 stdlib `sqlite3`）、
  版本号 v1.0，而代码是 0.3.1。请统一。
- **测试可移植性**：已修 `tests/test_tools.py` 的 POSIX `sleep`；`pytest.ini` 建议补 `asyncio_mode`
  并按需清理 `asyncio.get_event_loop()` 弃用告警。
- **`windows/session.py` 的 `drop_messages` 语义**：`reset()` 依赖"删除到最后一条"，
  在并发派活时可能与新消息竞争，建议加锁或改为软删除。
- **审计与可观测性**：`audit_log` 有表有写，但控制台只做了基础展示；建议补
  按 actor/action/时间过滤、导出、以及"每个工位的完整执行时间线"。

---

## 5. 同类软件能力对照（用户反馈"差得很远"）

下面是**能力维度**对照，不是具体产品的功能声明。左侧是 DogBuddyCn 现状：

| 维度 | DogBuddyCn | 成熟同类（Agent 面板 / 编排平台）通常具备 |
|---|---|---|
| 实时输出 | ❌ 非流式、轮询 | ✅ token 级流式 + SSE/WS |
| 工具调用可视化 | ⚠️ 有气泡但**不显示工具名**（见 2.2） | ✅ 工具名 / 参数 / 结果 / 耗时 / 折叠 / 重试 |
| 参数可调 | ⚠️ 多数参数只有全局 | ✅ 会话级、Agent 级、模型级多层覆盖 |
| 上下文管理 | ⚠️ 只有"压缩/注入/重置"三个按钮 | ✅ 窗口大小、阈值、摘要策略可视可调、token 用量实时显示 |
| 成本/用量 | ❌ 无 | ✅ 按会话/按模型统计 |
| 多 Agent 编排 | ⚠️ 固定两角色 + 单串行队列 | ✅ 自定义角色、并行、DAG/流程编排 |
| 插件生态 | ❌ 无（MCP/skills 未实现） | ✅ MCP / 插件市场 / 自定义工具热加载 |
| 多用户与权限 | ❌ 单用户 | ✅ 账号、角色、资源隔离 |
| 文件与工作区 | ⚠️ 有文件管理器，功能基础 | ✅ 在线编辑、diff、预览、版本对比 |
| 消息平台覆盖 | ⚠️ 只有 OneBot v11 | ✅ Telegram / Discord / 飞书 / 企微 / Web 多端 |
| 可观测与审计 | ⚠️ 有表，展示弱 | ✅ 全链路 trace、日志检索、告警 |

**结论**：骨架（五层架构、工位会话模型、抗损压缩、权限三模式、限速退避、自更新）是扎实的，
差距集中在 **实时交互层（流式 + 事件推送）** 和 **产品化层（参数可视化、用量、插件、多用户）**。

---

## 6. 验收标准（Definition of Done）

任何提交必须满足：

1. `python -m pytest -q` 全绿；新增功能必须有测试（**LLM 相关必须对着真实 provider 契约测，不要只测 FakeLLM**）。
2. 涉及配置/DB 的改动：改配置走 `persist_config()`；改表走 `_MIGRATIONS` + `SCHEMA_VERSION`。
3. 涉及自更新的改动：`manage.py update` 在服务器上实跑一次，确认「升级成功 → `/healthz` 版本正确」，
   并用一个**故意做坏的版本**验证自动回滚。
4. UI 改动：至少手工过一遍 `总览 / 对话 / 工位 / 任务 / 文件管理器 / 日志审计 / 设置` 七个视图，
   确认没有"元素存在但不可见/无样式"的情况（可用本文 2.1 的 class 差集脚本自查）。
5. 提交信息写清"改了什么 + 为什么"，并且 **bump `dogbuddy/__init__.py: __version__`**
   （自更新靠它断言版本）。

---

## 7. 已知坑（踩过的）

1. **`FakeLLM` 与真实 provider 契约不一致** → 三个致命 bug 曾长期潜伏，见第 1 节末。
2. **PRoot 无 systemd**：不要写 systemd-only 的部署逻辑；进程重启依赖面板托管或 `DOGBUDDY_RESTART_CMD`。
3. **面板的 `/api/panel/apps/{id}/restart` 只认会话 Cookie，不认 Agent 令牌**（会 401）。
   这也是 `manage.py` 里"终止被托管进程等守护拉起"这条路径存在的原因。
4. **离线 wheels 陷阱**：`wheels/` 存在时 pip 会走 `--no-index`；依赖升级后必须重新打包，
   或依赖 `_pip_install` 的联网回退（已实现）。
5. **升级期间进程会重启**：任何长任务（派活、LLM 调用）都可能被打断，设计时要有幂等与恢复。
6. **`save_config` 不会把 env 注入的密钥写回文件**（有意设计）；如果发现"设了 key 但文件里没有"，
   检查是不是 `DOGBUDDY_*` 环境变量在起作用。
7. **`Window.model` 与 `Window.llm_profile` 是两套覆盖机制**，解析顺序见
   `AppConfig.effective_model()`，不要在前端另写一套判断。

---

## 8. 建议的迭代顺序

1. **P0 全部**（2.1 样式缺失 → 2.2 工具名 → 2.3 工位参数 → 2.4 事件推送）
2. **P1 的 3.1 流式 + 3.2 用量统计 + 3.3 run 记录**（这三项直接决定"好不好用"）
3. **P1 的 3.4 阻塞式问答/确认回路**（决定"能不能放心无人值守"）
4. **前端构建化重构**（建议在 1~3 之后做，避免边改边重构）
5. **P1 的 3.5/3.6/3.7 扩展生态**、**3.8 多用户**（产品化阶段）
