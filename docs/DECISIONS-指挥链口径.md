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

## 决策 2：多管理员模型

**空白**：原设计只说"多管理员实体化"，没有定义 `admins.role_level` 取值、
"分管不同域"怎么表达、多个 admin 是否共享 system scope。

**拍板（最小可用，先实体化不复杂化）**：
- `role_level ∈ {owner, admin}`；**首位管理员为 `owner`**，其余为 `admin`
- **暂不做按域分管**：所有管理员共享 `system` 作用域（`ScopeResolver.for_admin()` 直接返回
  `Scope(level="system")`），字段留好，日后要分域再收紧
- **配置文件里的 `web.auth_token` 始终有效，视作 owner** —— 这样即便把 `admins` 表清空
  也不会把自己锁在门外（避免"改错一条记录导致再也进不去"）
- 令牌**只存 sha256，绝不回显**（API DTO 只给 `has_token` 布尔）

**落地**：`admins` 表（迁移 v8）+ `web/routes/admins.py`（GET/POST/PATCH/DELETE `/api/admins`）。

**测试**：`test_T9_5_多管理员_CRUD_不回显令牌`（含"库里只有哈希""不回显""重名/非法档位被拒"）。

## 附：数据与寻址口径

| 概念 | 落地 | 说明 |
|---|---|---|
| 项目主管 | `projects.owner_supervisor_id` | 指挥链寻址枢纽；空 = 无主管 |
| 作用域推导 | `ScopeResolver`（角色 → Scope） | **不接受自报**；主管按"owner 字段 ∪ 项目内 role=supervisor 的工位"兜底 |
| 越权裁决 | `PermissionGate.check_scope` | **fail-closed**：未声明/判不出目标/越界 → 拒绝 |
| 逐级上报 | `supervision_service` | `worker.report` → 主管；无主管 → **直连管理员**（v1 行为零回归） |
| 审批路由 | `SupervisionService.approval_target` | 不可逆 → 管理员；普通高危 → 先主管；无主管 → 管理员 |
| 项目记忆 | `memory/projects/<id>.md` | 主管可写、**员工只读**（防一个员工改掉全项目规矩） |
