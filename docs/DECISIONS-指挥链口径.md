# 拍板记录：三层指挥链的两处文档矛盾（Batch 9）

> 背景：`docs/ROLES.md` 与 `docs/ARCHITECTURE-v2-ROLES.md` 对两件事的表述不一致，
> 而这两件事直接决定 `roles.py` / `db.py` / 权限门怎么写。实现 Batch 9 时按下面的
> 口径收敛，**代码与测试按此落地**。此文档是权威口径。

## 决策 1：主管的工具集

**矛盾**：
- `ROLES.md` §2/§4：「主管……必要时可下场干一段」「主管工具集 = 员工工具 + 作用域受限的管理工具」
- `ARCHITECTURE-v2-ROLES.md` §3.2 的工具表：把 `window_*`/`task_*`/`config_*` 列为主管可用，
  但没有提"主管有没有 read_file/shell"；文档别处又出现"主管只有管理工具"的措辞
- `ROADMAP.md` T9.3：**主管工具集 = 员工工具 + scoped 管理工具**

**拍板**：按 `ROADMAP.md` T9.3 —— **员工工具 + 本项目的 scoped 管理工具**。

**理由**：只给管理工具的话，"必要时亲自下场"这句话在实现上无法成立（没有
`read_file`/`write_file`/`shell` 就无法下场）。而给上员工工具并不降低安全性：
越权由 `PermissionGate.check_scope`（fail-closed）裁决，与装配层给不给无关。

**落地**：`SUPERVISOR_TOOLS = 员工工具 ∪ PROJECT_TOOLS ∪ {project_memory_write}`；
**不含任何 system 级工具**（`config_*`/`profile_*`/`self_update`/全局 `memory_*`）。
装配层过滤 + 运行时裁决**双层保证**。

**测试**：`tests/test_batch9_supervision.py::test_T9_3_主管工具集_员工工具加项目级管理`
（断言含员工工具、含项目级管理工具、**不含**任何 system 级）。

## 决策 2：**只有一个管理员**（v2 最终口径，2026-10-14 用户拍板）

**背景**：ROADMAP T9.5 原写「`admins`（多管理员实体化）」，我先按此实现了多管理员 CRUD。
**用户随后拍板：以最新的 v2 为准 —— 只有一个管理员，用于和用户对接；用户可以选择新建对话。**

**拍板**：
- **管理员唯一**：它是用户唯一的对接窗口（员工不直接找用户，逐级上报）。
  据此**移除了多管理员 CRUD API**（`routes/admins.py` 已删，`/api/admins` 返回 404），
  并加了测试 `test_v2_只有一个管理员_且没有多管理员接口` 防止它被加回来。
- `admins` 表**保留**（迁移 v8 已在生产执行，删表风险大于收益），但**不再用于任何鉴权或 CRUD**；
  离线部署的鉴权仍用 `web.auth_token`（单一凭据）。
- **对话可以有多个**：这是用户明确要的能力——换一件事就开一个对话，各自独立上下文、互不串味。
  见下方"对话模型"。

## 附：对话模型（v9）

| 概念 | 落地 | 说明 |
|---|---|---|
| 对话 | `conversations(id,title,archived,created_at,updated_at,last_message_at)` | 一行 = 一个对话 |
| 会话 | `WindowSession` 挂在合成窗口 `admin:<对话id>` | 消息仍存 `messages` 表，无新存储层 |
| 新建对话 | `POST /api/admin/chat/conversations` | 标题留空自动编号「对话 N」 |
| 自动命名 | `AdminService._touch_conversation` | **首条用户消息前 40 字当标题**；用户手改过就不再覆盖 |
| 切换 | `GET /api/admin/chat/messages?conversation=<id>` | 前端 `#chat-sender` 下拉即对话列表 |
| 重命名 / 删除 | `PATCH` / `DELETE /api/admin/chat/conversations/{id}` | 删除**连消息一起删**；删光了自动补一个默认对话（不留空白） |
| 清空 | `POST /api/admin/chat/reset` | 只清当前对话，**不波及其他对话** |
| 兼容 | 老字段 `sender` 与新字段 `conversation` 都认 | 同一键空间，老客户端不改也能用 |


## 附：数据与寻址口径

| 概念 | 落地 | 说明 |
|---|---|---|
| 项目主管 | `projects.owner_supervisor_id` | 指挥链寻址枢纽；空 = 无主管 |
| 作用域推导 | `ScopeResolver`（角色 → Scope） | **不接受自报**；主管按"owner 字段 ∪ 项目内 role=supervisor 的工位"兜底 |
| 越权裁决 | `PermissionGate.check_scope` | **fail-closed**：未声明/判不出目标/越界 → 拒绝 |
| 逐级上报 | `supervision_service` | `worker.report` → 主管；无主管 → **直连管理员**（v1 行为零回归） |
| 审批路由 | `SupervisionService.approval_target` | 不可逆 → 管理员；普通高危 → 先主管；无主管 → 管理员 |
| 项目记忆 | `memory/projects/<id>.md` | 主管可写、**员工只读**（防一个员工改掉全项目规矩） |
