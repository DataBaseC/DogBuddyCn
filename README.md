# DogBuddyCn

常驻 Ubuntu 服务器的**多 Agent 数字团队**：管理员 Agent 经 QQ 统一对接用户，程序员员工 Agent 在
持久"工位（window）"里被定时任务持续驱动完成编程开发工作，Web 控制面板管理一切。

- **许可证**：AGPL-3.0（见 `LICENSE`；吸收 AstrBot/Hermes 等开源代码，出处与改动见 `NOTICE`）
- **技术栈**：Python 3.11+ · FastAPI · SQLite(WAL) · YAML 配置 · 原生 HTML/CSS/JS 面板（零构建）
- **LLM**：OpenAI 兼容 API（任意 base_url/model/key）或本地 Ollama

## 核心概念

| 概念 | 说明 |
|------|------|
| **工位 window** | 员工工位 = 角色 + 工作区 + 模型 + 持久会话 + 待办清单 + 挂载的定时任务集。任务**回到同一工位的持久会话**继续执行，一个项目一直做下去 |
| **管理员 Agent** | 唯一人机对话面（QQ/面板）。管理工位/任务/配置/记忆，汇总裁量员工产出（silent/brief/detailed 回报策略） |
| **员工 Agent** | 程序员角色：文件读写编辑、shell、搜索、web、待办、检查点回滚、只读调研子代理、提问、服务器命令 |
| **权限模式** | `BUILD` 工作区内自由写/工作区外拦截；`PLAN` 全写拦截只读规划；`AUTO` 全放行（**定时无人值守禁用**） |
| **抗损压缩** | 会话 token 达 80% 窗口自动压缩：只丢对话措辞，文件改动/待办/决策/教训结构化保留 + 沉淀到工位记忆 |
| **限速重试** | 429/529/超时/连接错误按指数退避重试（10→20→…封顶 300s，默认 5 次）；**同一 Key 原地等待，不切换 Key**；等待不计任务失败 |

## 架构（五层，依赖只许向下）

```
L1 接入层    gateway/adapters(OneBot v11 反向WS) · web/routes · cli/manage.py
L2 应用层    services(admin/report/window/job/session/workspace/file/config) · cron · events/bus
L3 领域层    agents(core: AgentLoop/roles/permissions · admin 工具包 · worker 工具装配)
             windows(session/queue/context/compactor) · memory
L4 能力层    tools(18 个工具，ToolRegistry 统一权限裁决) · llm(provider/openai_compat/ollama/retry)
L5 基础层    db(SQLite DAO) · config(YAML/pydantic) · external/panel_client(AegisServerPanel)
```

扩展点（V1 留口）：`register_role` 新角色 · BaseAdapter 新 IM 平台 · ToolRegistry 新工具/MCP ·
skills/ 目录钩子 · TaskRunner 新任务类型 · 群聊工作区 group 字段。
**v1.3 起支持**：多套 LLM 配置（命名 profiles，工位/任务级绑定）、多管理员会话（每用户独立）、
多回报通道（console/webhook/gateway，任务级路由）——扩展点全景见 `docs/EXTENSIBILITY.md`。

## 安装

```bash
git clone <this repo> dogbuddy && cd dogbuddy
sudo ./install.sh          # venv + 依赖 + /etc/dogbuddy/ + systemd 单元
sudo vim /etc/dogbuddy/config.yaml   # 填 LLM key/base_url/model、面板 token、白名单
sudo systemctl enable --now dogbuddy # 或 python manage.py start
```

数据目录（默认 `/etc/dogbuddy/`）：`config.yaml` · `dogbuddy.db` · `memory/` · `logs/` · `checkpoints/` · `workspaces/`。

`manage.py start|stop|status|logs|backup|restore` 负责进程与运维自查。

### Windows 安装（能跑版）

Windows 上不装服务，直接以普通进程运行（Python 3.11+）：

```powershell
cd dogbuddy
powershell -ExecutionPolicy Bypass -File .\install.ps1   # venv + 依赖 + 数据目录
notepad $env:LOCALAPPDATA\dogbuddy\config.yaml        # 填 LLM key/base_url/model
python manage.py start                                    # 启动（后台进程）
python manage.py logs                                     # 看日志
python manage.py status                                   # 自查
```

- 数据目录默认 `%LOCALAPPDATA%\dogbuddy`（可用 `DOGBUDDY_DATA_DIR` 覆盖）；PID/输出文件在 `%TEMP%`
- 命令行能力跨平台自适应：shell 工具超时杀进程树（Windows 用 `taskkill /F /T`）、
  `manage.py stop/logs` 不依赖 POSIX 命令
- 危险命令黑名单含 Windows 项（`format`/`diskpart`/`bcdedit`/删盘根/注册表关键项/系统进程等），
  权限门控与 Linux 同等生效
- 注意：文件路径大小写不敏感、`rm -rf` 类命令在 cmd 下无效属正常现象；OneBot(NapCat) 
  在 Windows 上运行 NapCat 连到本服务即可（网关是纯 WebSocket 服务端）

## 配置要点

**推荐：不用手改配置。** 启动后直接进控制台 `http://<server>:8100` ——检测到 LLM 未配置时会弹出
**设置向导**：选 provider → 填 base_url（内置 OpenAI/DeepSeek 快捷项）和 api_key → 点「拉取模型」
**自动列出该服务可用的全部模型**（OpenAI 兼容走 `/models`，Ollama 走 `/api/tags`）→ 下拉选中 →
「测试连接」（延迟/模型数/聊天往返）→ 保存即用。设置页可随时修改，模型列表也可在改模型时随时重拉。

`config.example.yaml` 有全量注释，仅在需要网关/面板 token 等进项时手改：

```yaml
llm:
  base_url: https://api.openai.com/v1   # 任意 OpenAI 兼容中转
  model: gpt-4o-mini
  api_key: ""                           # 或环境变量 DOGBUDDY_LLM_API_KEY
  retry: {base_interval: 10, max_interval: 300, max_retries: 5, backoff: exp}
gateway:                                 # OneBot v11 反向 WS（NapCat/Lagrange 连入）
  port: 8095
  access_token: ""
  whitelist: [123456789]                 # 只有白名单 QQ 能对话
  push_target: {type: private, id: 123456789}
web: {host: 127.0.0.1, port: 8100, auth_token: ""}
panel:                                   # AegisServerPanel（只对接不修改）
  base_url: http://127.0.0.1:8080
  agent_token: ""
```

## QQ 对接（NapCat）

1. NapCat 配置反向 WebSocket：`ws://<server>:8095/`，access_token 与 config 一致
2. 把你的 QQ 号加进 `gateway.whitelist`
3. 对机器人说：

> 每天早8点在 /data/projects/myapp 这个工位推进待办，完成后简要汇报

管理员会自动建工位/建任务/配置回报策略，到点任务回同一工位执行。

## Web 控制面板

`http://<server>:8100`（鉴权 `web.auth_token`）：总览（工位卡片/任务/最近产出）· 工位管理
（会话流/暂停恢复/改模型模式/手动派活/看待办）· 任务管理（CRUD/启停/立即执行/运行历史）·
设置（LLM/限速重试/面板/白名单/回报策略）· **文件管理器**（目录树+面包屑+预览/编辑/上传下载）·
日志审计。

## 安全模型

- IM 白名单（QQ 号）+ OneBot Access Token + 面板 X-Agent-Token
- 员工三模式门控 + 危险命令黑名单（`rm -rf /`、`mkfs`、`reboot/shutdown`、`systemctl` 关键操作、
  改面板配置等）→ 拒绝或需用户 IM 确认后放行；定时无人值守禁用 AUTO
- 改文件前自动单文件快照 + 全量检查点，支持一键回滚
- 所有写/shell 工具调用进审计日志（面板可查）

## 测试

```bash
python -m pytest tests/ -q     # 43 tests：调度解析/压缩/权限门控/队列/路由/工具/Web API/M1 闭环
```

## 项目结构

```
dogbuddy/
├── LICENSE  NOTICE  README.md  install.sh  manage.py  config.example.yaml  requirements.txt
├── dogbuddy/
│   ├── main.py config.py db.py ports.py       # 装配根 / L5
│   ├── gateway/    adapters/base.py onebot_v11.py · router.py
│   ├── web/        app.py routes/ static/(原生 JS 面板)
│   ├── services/   admin/report/window/job/session/workspace/file/config
│   ├── cron/       jobs.py scheduler.py        # 抄 Hermes（MIT）
│   ├── events/     bus.py
│   ├── agents/     core/(loop roles permissions) admin/ worker_programmer/
│   ├── windows/    session.py queue.py context.py compactor.py
│   ├── memory/     service.py
│   ├── tools/      registry + file/shell/search/todo/checkpoint/subagent/ask_user/server_exec
│   ├── llm/        provider.py openai_compat.py ollama.py retry.py
│   └── external/   panel_client.py
├── systemd/dogbuddy.service
└── tests/
```
