# DogBuddyCn 项目审查提示词

> 用途：把这份文档整段交给一个**审查 Agent**（或人），让它找出项目里真正的问题。
> 写这份文档的人（下称"作者"）实现了 Batch 6-9，**下面列的每一条都是作者自己的怀疑点**，
> 不是抽象检查表——作者知道哪里埋了坑，也知道哪些地方"看起来没问题但没验证"。
>
> 使用方式：把本文件全文作为第一条消息发给审查 Agent，必要时追加"只看 P0/P1"。
> 最后附**环境操作手册**（含本项目特有的坑，跳过它会浪费大量时间）。

---

## 0. 你的角色与硬性规则

你是这个项目的**独立审查者**。目标不是"跑通测试就签字"，而是**找出会导致以下后果的缺陷**：
数据丢失 / 越权与逃逸 / 静默失效（错误被吞、功能没接线）/ 生产不可恢复。

**硬性规则（违反其一，审查结论作废）：**

1. **证据优先**。每条结论必须给出：文件+行号、可复现命令、实际输出。没有复现步骤的"我觉得"一律不要写。
2. **不信任文档**。`docs/ROADMAP.md` 里写着"已完成"的地方**必须看代码 + 跑一次**。
   历史教训：外包交付 v0.4.0 声称 Batch 1-5 全部完成，实际有三个视图"写了但没接线"
   （点进去空白/进不去/点了没反应），文档却是绿的。
3. **不信任测试通过数**。本项目 375 个测试大量由实现者本人编写，存在**与实现耦合**的风险
   （例如断言实现者自己写的错误字符串）。请专门评估"这些测试能不能抓住回归"，
   并**至少手写 3 个独立验证**（不引用现有测试的断言）。
4. **不改代码，先报告**。可以在临时分支/临时文件里做实验，但不要修改仓库源码；
   把你的补丁作为"建议修法"附在结论里。
5. **不许碰生产**。`192.168.1.2` 是作者正在使用的线上实例。
   只允许**只读**验证（curl / 面板 exec 读命令 / 查库 SELECT）。禁止：写库、重启服务、
   删除文件、`manage.py update`。
6. **区分三态**：`已确认缺陷` / `可疑但未验证` / `确认无问题`。第三类也要写（它能为后续审查省时间）。

---

## 1. 项目速览（够你定位，不够你偷懒）

**是什么**：DogBuddyCn —— 一个多智能体协作平台。用户只和**一个管理员**对话（自然语言），
管理员把活拆分派给**主管**，主管再派给**员工工位**；工位有持久会话、工作区、模型、定时任务。

**五层架构（依赖只许向下）**：
```
L1 web/routes/*.py          HTTP 接口（只做参数校验 + 调服务）
L2 services/*.py            业务服务（窗口/任务/项目/迭代/指挥链/模型目录）
L3 agents/core/*.py         Agent 循环、角色、作用域、权限门（业务规则在这里）
L4 tools/*.py               能力单元（文件/shell/搜索/自动化/记忆）
L5 infra                    db.py(SQLite) / config.py / llm/*.py
```

**关键不变量（破坏它们就是缺陷）**：
- `AgentLoop` 用 `tool_names=role_spec.tool_names` **过滤**工具；注册表全量 ≠ 模型能用
- 角色白名单与注册表必须一致（有不变量测试守着，见 `tests/test_self_upgrade_tool.py`）
- 管理员工具集（`ADMIN_TOOLS`）必须**恰好等于** `agents/management/tools.py` 注册的 32 个
- 作用域（`Scope`）**由角色推导，不接受模型自报**；`PermissionGate.check_scope` **fail-closed**
- 写入配置的唯一路径是 `persist_config()`（落盘 + 失效 provider 缓存）

**双环境（重要）**：开发机是 **Windows**（`D:\Ai\DBA\MutiAgent\DogBuddyCn`），
生产是 **Ubuntu 24.04 + PRoot + aarch64**（`/opt/dogbuddy`），无 systemd，
由 AegisServerPanel 托管。**同一份代码两边都跑**，所以平台差异是真风险来源。

**数据库**：SQLite（WAL），`schema_version` 目前 **9**，迁移在 `dogbuddy/db.py::_MIGRATIONS`。
线上库路径 `/var/lib/dogbuddy/dogbuddy.db`。

---

## 2. 高危可疑点清单（作者自述，按严重度排序）

> 阅读方式：`已确认` = 作者已经看到代码/现象；`待验证` = 作者怀疑但没验证。
> **请你独立复核每一条**，包括标"已确认"的（作者也可能看错）。

### P0-1 `server_exec` 绕过了工作区边界（**已确认**，作者认为这是最严重的遗留）

`dogbuddy/tools/shell_ops.py` 里 `shell` 工具在执行前调用了
`dogbuddy/tools/shell_guard.py::analyze_command`：`cd /etc`、`> /etc/x`、`rm /var/log/x`
这类越界会被拒绝（需 `params.trust="elevated"` 才放行）。

但 `dogbuddy/tools/server_exec.py`（`ServerExecTool`）**完全没有这层检查**——它把命令直接
交给 AegisServerPanel 的 `/api/terminal/exec` 在**服务器上**执行，只依赖权限门的
"危险命令黑名单"（`rm -rf /`、`mkfs`、`shutdown` 之类），而
`cd /etc && cat /etc/shadow`、`cat ~/.ssh/id_rsa`、`rm -rf /var/log/*` 都**不在黑名单**里。

而 `server_exec` 就躺在员工工具箱里（`roles.py` 的 `WORKER_PROGRAMMER_TOOLS`）。

**验证方法**：
1. 以员工工位身份（或直接构造 `ToolContext`）对 `shell` 传 `cd /etc && ls` → 应被拒（`boundary=True`）
2. 对 `server_exec` 传同一条命令 → 观察是否放行
3. 断言两者行为应当一致（要么都拦，要么都放行并有等价审批）；不一致就是缺陷

**作者的建议修法方向**（不要照抄，请自己判断）：让 `server_exec` 复用 `shell_guard`，
或给它单独的、更严格的 elevation 要求（它本来就在工作区之外运行，语义上更危险）。

---

### P0-2 `registry.execute` 的 `TypeError` 回退可能**静默跳过作用域检查**（代码已确认，可利用性待验证）

`dogbuddy/tools/registry.py` 约 148-152 行：

```python
required = getattr(tool, "required_scope", "") or ""
try:
    decision = self.gate.check(name, tool.permission, args, ctx, required_scope=required)
except TypeError:
    decision = self.gate.check(name, tool.permission, args, ctx)   # ← 这里没有 required_scope
```

作者加这个回退是为了兼容"只接受 4 个参数的旧 gate"，但 **`TypeError` 也可能来自 gate 内部**。
如果第一次调用在**作用域检查之后**抛 `TypeError`，第二次调用会**完全跳过作用域检查**，
于是"越权调用"变成"正常执行"。

**验证方法**：写一个测试替身 gate，它的 `check()` 在收到 `required_scope` 参数时先
（或后）抛 `TypeError`，第二次调用正常返回 `GateDecision(True)`；
断言 `registry.execute()` 是否真的执行了工具。若执行了 → **fail-open 确认**。

**作者建议修法方向**：用 `inspect.signature` 判断签名，或把作用域检查挪到 `try` 之外；
退一万步也要在回退路径上**补做**作用域检查（不能默认放行）。

---

### P0-3 迭代执行器用 `tool_names=None`，**绕过角色白名单**（**已确认**）

`dogbuddy/services/iteration_executor.py` 约 204 行：`AgentLoop(..., tool_names=None, ...)`。

`AgentLoop` 里 `schemas = self.registry.schemas(only=self.tool_names)`，
`only=None` = **不过滤** = 把注册表里**所有**工具都交给模型。

后果：
- 员工工位在迭代轮次里会拿到 `project_memory_write`（它的角色白名单里**没有**这个工具，
  作者在 `tests/test_batch9_supervision.py` 里专门断言过"员工不给写项目记忆"——
  装配层挡了，**这条路径绕过了装配层**）
- 若启用了 MCP 桥，还会拿到任意外部 MCP 工具（无 `required_scope`）
- 该路径**也没有注入 `ctx.scope`**（`scope=` 全仓库只出现在
  `window_manager.py:455`），所以这些调用在作用域上"无身份"

**验证方法**：构造一个员工角色、走 `IterationExecutor._default_round` 的真实路径，
把交给模型的 schema 抓下来，检查里面有没有 `project_memory_write` / `server_exec` / MCP 工具。

**同时对比** `group_manager.run`：它走 `_strict_tools(order)`，None 时回退到
`role_spec.tool_names`。请确认这条回退**真的生效**（漏了就同样全量放行）。

---

### P0-4 `Database.delete_conversation` 可能误删真实工位的消息（**待验证**）

`db.py` 的 `delete_conversation`：

```python
self._conn.execute("DELETE FROM messages WHERE window_id IN (?,?)",
                   (f"admin:{conv_id}", conv_id))
```

第二个占位符用 `conv_id` 本身，本意是兼容"老的 IM 会话直接用 <id> 当 window_id"。
但**对话 id 没有任何校验**（`create_conversation` 只是 `c-<hex>` 或用户传入的 title——
id 由后端生成，见 `AdminService.create_conversation`；但 `insert_conversation(conv_id, ...)`
是公开 DAO，`/api/admin/chat` 的 `conversation` 参数也能指定任意字符串）。

**验证方法**：`POST /api/admin/chat/conversations` 只能生成 `c-*`，但试
`GET /api/admin/chat/messages?conversation=<某个真实工位 id>` → 会为该 id 建对话行吗？
如果会，再 `DELETE` 该对话 → 是否把该工位的消息删了？→ **数据丢失确认**。

**作者建议修法方向**：对话 id 加前缀白名单/校验，或删除时只认 `admin:<id>` 一种窗口。

---

### P1-1 迭代「手动推一轮」绕过并发锁（**已确认**）

`iteration_executor.py` 用 `self._inflight` 防止同一个迭代被定时器重复触发，
但 `web/routes/iterations.py:114` 的 `run_now` **直接调用 `ex.run_one(...)`**，不经过 `_inflight`。
于是"手动推一轮"与"定时到点"可以并发跑同一个迭代：轮次号可能重复、同一个迭代被跑两遍、
停止条件判定基于脏数据。

**验证方法**：并发调用 `run_now` 两次（或 `run_now` + 手动制造 `tick`），
检查 `iteration_rounds` 是否出现相同的 `round`、`round` 计数是否跳号。

---

### P1-2 作用域只在一条路径注入（**已确认**）

`ctx.scope` 只在 `WindowManager._execute` 注入。以下路径的 `ToolContext` **没有 scope**：
- `group_manager.run`（群组协作轮）
- `iteration_executor._default_round`（迭代逐个派发）
- `admin_service._run_turn`（管理员会话——它用 `_AdminGate`，本身不做作用域检查，**要确认这是有意的**）
- `tools/subagent.py`（子代理注册表）

方向上是"更安全"（无 scope → 管理工具被 fail-closed 拒绝），但会产生**功能死角**：
主管在群组/迭代路径里**根本无法使用自己本该能用的管理工具**。请判定：
这是可接受的设计，还是"主管在迭代里干不了活"的功能缺陷。

---

### P1-3 `conversations` 与 IM 会话共用键空间（**待验证**）

v2 口径是"**只有一个管理员**与用户对接"。但 `AdminService._touch_conversation` 会对
**任何** sender 建对话行，而 QQ 网关用 `sender=<用户 id>` 调 `handle_user_message`。
→ IM 用户会话会出现在控制台对话列表里。

请判定是否符合 v2 的意图（"用户"是否包含 IM 用户），以及：
`admin:system`（系统通知）被显式排除，是否还有别的"非用户会话"会漏进去。

---

### P1-4 项目记忆的路径消毒（**待验证**）

`tools/project_memory_ops.py`：`safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" ...)`。
**`.` 在允许集合内**，所以 `..` 会原样保留。作者认为拼上 `.md` 后无法逃出目录，
但请实际验证 `project_id = "../.."`、`"....//....//etc/passwd"`、含 NUL/换行 等输入，
并确认不会写到 `memory/projects/` 之外。

---

### P1-5 shell 边界本身的绕过面（**部分待验证**）

`shell_guard.py` 的作者注释已声明"这不是沙箱"。请量化它实际的绕过面，至少覆盖：
- `python -c "open('/etc/x','w')"`（作者已知会绕过）
- `base64 -d | sh`、`echo <cmd> | sh`、`$IFS`、变量拼接、`$(...)` 嵌套
- `sudo`、`env`、`find -exec`、`xargs`、`tar --to-command`、`rsync -e`
- 只读越界（作者**有意**放行 `cat /etc/os-release`、`uname`），确认这与文档一致

结论要落到"这对本项目意味着什么"（员工是被信任的吗？）而不是罗列 payload。

---

### P2-1 无界增长（**已确认无清理**）

以下表/文件只增不减，没有清理策略：`messages`、`job_runs`、`pending_decisions`、
`iteration_rounds`、`conversations`、`data/retry_memory.json`、`logs/`。
请评估在长期运行（若干月、每 15 分钟一轮迭代）下的实际影响与推荐策略。

### P2-2 `schema_version` 比代码高时的行为（**待验证**）

`Database.__init__` 只做"从当前版本往上迁移"。如果**降级**运行（老代码读新库），
`_to_window`/`_to_project` 用 `keys` 判断列是否存在来容错——请验证：
①降级启动是否静默丢数据；②是否应该拒绝启动。同理 `_migrate_v6` 的"老库自动包项目"
在 `windows` 表不存在时是否会崩。

### P2-3 SSE 在面板代理下的缓冲（**未验证**）

作者只确认了响应头（`content-type: text/event-stream`、`x-accel-buffering: no`、
`retry: 5000`）与首帧 `event: hello`。**没有验证**事件能否在真实浏览器里连续到达
（面板反代 + 长连接是被点名过的部署风险）。请用 `curl -N` 持续观察并统计到达延迟。

### P2-4 启动时序（**待验证**）

`main.start()` 的顺序是：`sweep_orphan_runs()` → `mcp.start()` → `windows.start()`
→ 起 bus/scheduler/iteration_executor。
请检查 `sweep_orphan_runs` 把 `windows.status` 置 idle 是否与 `windows.start()` 的 boot 竞态。

### P2-5 MCP 启用后的边界（**待验证**）

`mcp.enabled` 默认 False（作者有意保持关闭）。若启用：bridged 工具注册进**每个**工位注册表，
且它们**没有 `required_scope`**、没有边界检查。请评估"启用即全量暴露"的风险与最低防线。

---

## 3. 也别只看上面：这些维度作者建议你独立扫一遍

1. **静默失效**：全仓库搜 `except Exception` / `except BaseException`，对每一处判断
   "吞掉这个异常，用户会不会在不知情的情况下得到错误结果"。作者已收敛了两条热路径
   （LLM 调用、工具执行），其余是刻意的防御性边界——**请独立判断哪些其实该报错**。
2. **降级路径**：LLM 没配置、面板不可达、SQLite 锁、目录只读、时钟偏移、磁盘满。
   每一条都问："会怎样？会静默吗？"
3. **平台差异**：Windows 开发 / Linux 生产。找那些"本地过、线上错"的写法
   （路径分隔符、大小写、`/proc`、`signal`、编码、`chmod`、行尾）。
4. **文档与实现一致性**：随机抽 `docs/` 里的 10 条"已完成/行为"陈述，逐条对代码。
5. **测试质量**：找"断言了实现细节而非行为"的测试；找"改了实现仍会通过"的测试
   （可以故意破坏一处实现，看有没有测试失败——这是最有效的办法）。
6. **前端接线**（有历史前科）：新增视图是否**四处同步**——导航项、`<section id="view-x">`、
   `app.js` 的 `TITLES` 与 `renderView` 的 `case`、`tools/bundle-order.json`。
   项目有不变量测试守这四处，请确认它**没有漏洞**（例如动态 class、条件渲染绕过）。

---

## 4. 输出格式（请严格照此交付）

```markdown
# 审查报告

## 结论摘要
- P0（安全/数据丢失）：N 条
- P1（功能失效/逻辑错误）：N 条
- P2（健壮性/运维）：N 条
- 确认无问题（值得记录）：N 条

## P0-1 <一句话标题>
- **位置**：<文件:行号>
- **状态**：已确认缺陷 / 可疑未验证
- **现象**：<实际观察>
- **复现**：
  ```bash
  <可直接粘贴执行的命令>
  ```
- **证据**：<实际输出，不要转述>
- **影响**：<最坏情况；谁会受害>
- **建议修法**：<最小改动思路，附 diff 片段>
- **验证修好了**：<一条能证明修好的命令/测试>
```

最后必须有一节 **「我没验证的部分」**，列出你时间/权限不够而跳过的区域——
这比多报两条风格问题更有价值。

---

## 5. 环境操作手册（含本项目特有的坑，务必先读）

### 本地（Windows）
```powershell
cd D:\Ai\DBA\MutiAgent\DogBuddyCn
.\.venv\Scripts\python.exe -m pytest -q                 # 全量 375 条，约 15 秒
.\.venv\Scripts\python.exe -m pytest -q tests/test_batch9_supervision.py
.\.venv\Scripts\python.exe tools\rebuild-bundles.py --check   # 前端 bundle/指纹是否过期
.\.venv\Scripts\python.exe scripts\audit_ui.py           # 静态 UI 审计（缺 CSS/图标）
python -m compileall -q dogbuddy                         # 语法
```

### 生产（只读！）
服务：`http://192.168.1.2:8100`，健康检查 `/healthz` 返回
`{version, build, schema_version, pid}`。凭据请向 owner 索取（**不要写进报告或提交**）。

面板 exec（在服务器上执行只读命令）：
```bash
# 注意：用 base64 传脚本，避免中文/引号在传输层被破坏（见下方坑 1、2）
CMD=$(printf '%s' "$SCRIPT" | base64 -w0)
curl -sS -X POST http://192.168.1.2:8787/api/terminal/exec \
  -H "X-Agent-Token: <TOKEN>" -H "Content-Type: application/json" \
  -d "{\"command\":\"echo $CMD | base64 -d | bash\",\"timeout\":120}"
```

查库（**只读**）：
```bash
/opt/dogbuddy/.venv/bin/python - <<'PY'
import sqlite3
c = sqlite3.connect('file:/var/lib/dogbuddy/dogbuddy.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
print([dict(r) for r in c.execute("select max(version) from schema_version")])
PY
```
（`status` 字段是英文/ASCII，读出来不会乱码；中文内容请用 `ascii()` 打印。）

### 本项目特有的坑（作者踩过，别重踩）

1. **PowerShell 5.1 的 `Invoke-RestMethod -Body <字符串>` 按 Latin-1 发送**：
   中文会变成真正的 `?`（0x3F）而服务端"看起来成功"→ **数据静默损坏**。
   用 `-InFile`（UTF-8 文件）或 base64。
   **验证字节完整性**：读回字段并 `ascii(v)`，能看到 `\u5de5\u5177` 才对；看到真 `?` 就是被截断。
2. **PowerShell 5.1 调 `curl.exe -d "<JSON>"` 不转义内嵌双引号**：
   `{"name":"x"}` 会变成 `{name:x}` → 服务端报 `json_invalid`。
   要么把脚本整段交给服务器上的 `bash` 执行，要么写文件再 `--data-binary @file`。
3. **面板输出是 GBK 控制台**：中文显示成乱码 ≠ 数据坏了。判断数据是否有问题，
   要用 `ascii()` / 字节长度 / 数据库直读。
4. **本机不能 push GitHub**（hosts 把 github.com 指向 127.0.0.1）：
   推送是从服务器走 SSH deploy key 完成的。你不需要推送，请勿尝试。
5. **`.NET` 静态方法不会跟随 PowerShell 的 `cd`**：`[System.IO.File]::ReadAllText("相对路径")`
   会相对**进程**工作目录解析而失败。在 pwsh 里请用绝对路径。
6. **不要用 `$pid` / `$home` / `$host` 当变量名**（PowerShell 只读自动变量）。
7. **`manage.py update` 的"看起来成功但其实回滚"**：面板 kill 后旧进程仍占 8100 时，
   健康检查读到旧版本号 → 自动回滚；日志只有一句"已完成"。判断以 `/healthz` 的
   `version`+`build` 是否等于目标 commit 为准。（你只做审查，不要触发升级。）
8. **改了 `web/static/**` 必须重建 bundle**（`tools/rebuild-bundles.py`），
   否则页面白屏或吃旧缓存；有 `--check` 守卫。

---

## 6. 作者已确认"不是问题"的清单（别浪费时间，但欢迎复核）

| 事项 | 作者的证据 |
|---|---|
| 模型页备注的中文存储 | 字节级验证：`bytes=107`、`含真问号=False`、`ascii()` 可见 `\u5de5\u5177` |
| 切换模型是否立即生效 | `main.persist_config()` 里调了 `self.profiles.invalidate()` |
| 管理员功能零回归 | `ADMIN_TOOLS` 与注册表**逐位一致**共 32 个；有不变量测试 |
| 作用域 fail-closed 的基本行为 | 线上实测：越权/无声明/判不出目标 → 全部 DENY |
| 限速退避是否真读 `Retry-After` | 线上实测等待 `[47.0]`（精确），AIMD `[20,40]`，记忆起跳 `240` |
| 老会话历史是否会在升级中丢失 | 线上实测：28 条历史被接管成对话 `console`，空占位被清掉 |
| 前端四处接线 | 不变量测试 `tests/test_frontend_wiring.py` 6 条（作者写的，请评估其覆盖是否足够） |

---

## 7. 一句话提醒

这个项目最大的风险**不是写错的代码，而是"看起来已经做完"的东西**：
作者的 6 轮批次都通过了全量测试、都做了线上实测，但仍然出现了
"功能写好却没人能调用"（T7.3 缺绑定入口）、"边界只修了一半"（P0-1 `server_exec`）、
"升级后历史看起来丢了"（老会话变孤儿）这类问题。
**请优先攻击"完成度声称"与"实际可达性"之间的缝隙。**
