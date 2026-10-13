# DogBuddyCn

> 📋 **待办与外包交接文档见 [docs/ROADMAP.md](docs/ROADMAP.md)** —— 现状、已确认缺陷（带代码位置）、
> 优先级、验收标准、以及和同类软件的能力差距对照。

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
sudo bash install.sh         # venv + 依赖 + /etc/dogbuddy/config.yaml + /var/lib/dogbuddy/
sudo vim /etc/dogbuddy/config.yaml   # 填 LLM key/base_url/model、面板 token、白名单
sudo systemctl enable --now dogbuddy # 有 systemd；容器/PRoot 见下
```

无 systemd 的环境（容器 / PRoot / Termux）把本软件注册为 **AegisServerPanel 常驻应用**
即可获得崩溃拉起、日志轮转、开机自启：

```bash
python3 scripts/register_panel_app.py \
  --panel http://127.0.0.1:8787 --password '<面板密码>' \
  --app-dir "$PWD" --config /etc/dogbuddy/config.yaml
```

安装脚本输出里也能看到同样的指引。离线安装（目标机无 PyPI 通路）在执行 `install.sh`
之前放一个 `wheels/` 目录即可，pip 会自动切 `--no-index`。

## 自我更新 / 升级

升级链与 AegisServerPanel 的 `serverpanel --update` 同构：**锁 → 脏树检查 → 备份配置 →
切换代码 → 装依赖 → 重启 → `/healthz` 断言新版本号 → 失败自动回滚**。

```bash
python manage.py version                    # 当前运行 / 磁盘 / git 版本
python manage.py update --check             # 只看更新源，不改任何东西
python manage.py update                     # git：跟随 config update.ref（默认 origin/main）
python manage.py update --from-bundle x.tar.gz   # 离线包（推荐在无 GitHub 通路的机器上）
python manage.py update --from-url https://…/dogbuddy-x.tar.gz
```

关键行为：

- **旧服务全程不中断**，直到新版本通过健康检查；检查不过会自动回退代码、重装依赖、重启并复查
- 配置在代码目录之外（`/etc/dogbuddy/config.yaml`），升级永不触碰；升级前仍会额外备份一份
- 版本权威是 `dogbuddy/__init__.py` 的 `__version__`；`VERSION` 文件只是部署来源标记
- 数据库升级走 `schema_version` 表 + `dogbuddy/db.py` 的 `_MIGRATIONS`，启动时幂等执行
- 重启方式按优先级自动选择：`DOGBUDDY_RESTART_CMD` → 面板会话 API → systemd → 终止被托管的
  进程并等外部守护拉起 → 本地 stop/start
- 有本地 git 改动时默认中止（`--force` 会先 `git stash`）；`update.lock` 自身不算改动

打包一个可离线安装的发布包（在开发机上执行）：

```bash
python scripts/make_bundle.py --with-wheels \
  --platform manylinux2014_aarch64 --python-version 3.12 --abi cp312
# → dist/dogbuddy-<version>.tar.gz（代码 + wheels/，目标机无网也能装）
```

### 让 DBC 自己升级自己

管理员 Agent 有两个工具，可以直接对它说「升级你自己」：

| 工具 | 作用 |
|---|---|
| `self_version` | 当前版本、代码来源（git 还是 bundle）、上次升级结果；`check_remote=true` 时顺带问一次远端有没有新提交 |
| `self_update` | 在**后台独立进程**里跑 `manage.py update`，随后重启服务 |

为什么必须是独立进程：自升级会替换代码并重启服务，**进程内做不到**。所以
`self_update` 立刻返回、延迟 45 秒再动手（留时间让当前这轮回复落盘），升级结果写进
`<data_dir>/update_result.json`，下一轮对话用 `self_version` 就能回答「升级好了吗」。
`/api/status` 也会带上 `last_update`。

### 首次接入 GitHub（一次性）

```bash
# 1) 服务器上生成专用部署密钥
ssh-keygen -t ed25519 -N "" -f /root/.ssh/dogbuddy_deploy
cat /root/.ssh/dogbuddy_deploy.pub
# 2) 把公钥加到 GitHub: 仓库 → Settings → Deploy keys → Add deploy key，勾选 Allow write access
# 3) 让 git@github.com 使用这把密钥
cat >> /root/.ssh/config <<'EOF'
Host github.com
  User git
  IdentityFile /root/.ssh/dogbuddy_deploy
  IdentitiesOnly yes
EOF
chmod 600 /root/.ssh/config

# 4) 把部署目录变成 git 检出并推送
cd /opt/dogbuddy && bash scripts/bootstrap_git.sh
```

之后：**拉取走匿名 HTTPS，完全不需要凭据**（公开仓库），推送才需要那把密钥。
`git remote -v` 会显示 fetch 是 `https://…`、push 是 `git@…`，这是刻意的。


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
