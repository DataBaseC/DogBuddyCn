# DogBuddyCn 扩展性设计（v1.3）

> 每个模块"怎么加新的"都落在一个注册点上。本文是扩展点全景图。

## 1. LLM 配置套（profiles）——多套模型配置并存

- **配置**：`config.yaml` 的 `llm:` 是默认套，`llm_profiles: {name: {...}}` 是命名套，
  `active_llm_profile` 指定新工位默认用哪套。运行时可通过 API/QQ 随时增删改。
- **绑定**：工位 `llm_profile` 字段绑定套名；任务可再覆盖（`jobs.llm_profile`，空=继承工位）。
  解析链：任务 → 工位 → active → 默认 `llm` 套。
- **代码**：`llm/profile_registry.py` 的 `LLMProfileRegistry`（get/factory/invalidate），
  配置改动后 `invalidate()` 即换血。加新 provider 类型 = 在 registry `_build` 加一个分支。
- **管理入口**：Web 设置页（配置套卡片）、QQ 管理员工具 `profile_list / profile_save / profile_remove`、
  REST `/api/settings/profiles*`。

## 2. 管理员不唯一——多管理员会话

- 每个授权用户（白名单成员）拥有**独立的持久管理员会话**（`admin:<user_id>`），上下文互不污染；
  全局记忆共享。新 IM 平台传自己的 sender id 即自动获得新会话。
- 白名单带 `role` 字段（`owner | admin | viewer`），为权限分级预留（V1 所有角色同权）。
- **代码**：`services/admin_service.py` 的 `AdminService.session_for(sender)`。

## 3. 任务执行——TaskRunner 注册制

- `Task.kind` 决定谁来执行：`prompt`（AgentLoop 全工具循环）、`script`（无 Agent 直接跑 shell 命令）。
- **加新任务类型**：
  ```python
  class DeployRunner:
      kind = "deploy"
      async def run(self, task, env: TaskEnv) -> RunnerResult: ...
  window_manager.runners.register(DeployRunner())
  ```
  之后 `task_create(kind="deploy")` 即用新执行器，调度/队列/回报全链路复用。
- **代码**：`agents/core/runners.py`（TaskRunner 协议 / TaskEnv / RunnerResult / RunnerRegistry）。
  未知 kind 会显式失败（不会静默跑错执行器）。

## 4. 回报推送——Notifier 通道插件

- 通道类型：`console`（日志）/ `gateway`（QQ 推送）/ `webhook`（任意 HTTP）。
- 配置 `notify.channels:` 列表，每通道可命名；任务 `report_channel` 填通道名=只发该通道，
  留空=广播全部。单通道故障不影响其他通道。
- **加新通道**：实现 `Notifier` 协议（`name` + `async send(text)`）并加入 `notify/base.build_notifiers`。
- **代码**：`dogbuddy/notify/`。

## 5. IM 平台——BaseAdapter 注册

- `gateway/adapters/base.py` 定义 `BaseAdapter`（recv/send/push + MessageEvent）；
  新平台（官方 QQ / 微信 / 企微）实现该类并在 `main.py` 挂入即可，多适配器并存已预留
  （`DogBuddyApp.adapter` 可扩展为列表 + 各自 Router）。白名单/鉴权在适配器层完成。

## 6. 工具箱——ToolRegistry

- 新工具 = name + JSON schema + permission + `async execute(args, ctx)`，`registry.register()` 即暴露给角色；
  权限门控（BUILD/PLAN/AUTO + 危险命令黑名单）由注册中心统一裁决。MCP bridge 可在此层接入。
- 角色的工具集由 `RoleSpec.tool_names` 挑选；新员工角色用 `register_role(RoleSpec(...))` 注册
  （`window_create(role=...)` 直接可用）。

## 7. 其他留口

| 扩展 | 位置 |
|------|------|
| 群聊公共工作区 | `windows.group_id` 字段 + 会话多参与者概念（V1 不实现） |
| 技能系统 Skills | `dogbuddy/skills.py` 钩子 + `<data_dir>/skills/` 目录约定 |
| 记忆后端 | `memory/service.py` 文件式，可替换为向量库（接口不变） |
| 配置存储 | `db.config` KV + YAML 双层；可挂远程配置中心 |

## 数据库演进

新字段一律走 `db.py` 的 `_COLUMN_MIGRATIONS`（启动时幂等 `ALTER TABLE ADD COLUMN`），
旧库无痛升级，无需迁移工具。
