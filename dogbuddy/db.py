# DogBuddyCn — SQLite storage (L5 Infrastructure)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""SQLite (WAL, single-writer) DAO.

Imports the ports from ports.py and provides the concrete `Database`.
Synchronous DAO methods guarded by a re-entrant write lock — SQLite calls on a
local file are sub-millisecond; the event loop only awaits around true IO
(LLM, shell, network). Tests can swap in any object satisfying the ports.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("dogbuddy.db")

from .ports import (AuditEntry, Compaction, Group, GroupMember, Job, JobRun,
                    Message, PendingDecision, Project, Todo, Window)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS windows(
  id TEXT PRIMARY KEY, name TEXT, role TEXT, workspace_path TEXT, model TEXT,
  mode TEXT DEFAULT 'BUILD', status TEXT DEFAULT 'idle', group_id TEXT,
  llm_profile TEXT DEFAULT '', params_json TEXT DEFAULT '{}',
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, window_id TEXT, role TEXT, content TEXT,
  tokens INTEGER DEFAULT 0, created_at TEXT, meta_json TEXT DEFAULT '{}',
  group_id TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_messages_window ON messages(window_id, id);
CREATE TABLE IF NOT EXISTS compactions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, window_id TEXT, summary_json TEXT,
  replaced_count INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS todos(
  id INTEGER PRIMARY KEY AUTOINCREMENT, window_id TEXT, content TEXT,
  done INTEGER DEFAULT 0, sort INTEGER DEFAULT 0, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_todos_window ON todos(window_id, sort);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, window_id TEXT, name TEXT, schedule_type TEXT,
  schedule_raw TEXT, schedule_json TEXT, prompt TEXT,
  report_strategy TEXT DEFAULT 'brief', kind TEXT DEFAULT 'prompt',
  report_channel TEXT DEFAULT '', llm_profile TEXT DEFAULT '', group_id TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1, last_run_at TEXT, next_run_at TEXT,
  fail_count INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS job_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, window_id TEXT,
  started_at TEXT, finished_at TEXT, status TEXT, summary TEXT, log_path TEXT);
CREATE INDEX IF NOT EXISTS idx_runs_job ON job_runs(job_id, id);
CREATE TABLE IF NOT EXISTS pending_decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, window_id TEXT, question TEXT,
  options_json TEXT DEFAULT '[]', answer TEXT DEFAULT '',
  status TEXT DEFAULT 'pending', source TEXT DEFAULT 'ask_user',
  created_at TEXT, answered_at TEXT, expires_at TEXT);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_decisions(status, id);
CREATE TABLE IF NOT EXISTS groups(
  id TEXT PRIMARY KEY, name TEXT UNIQUE, description TEXT DEFAULT '',
  lead_window_id TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS group_members(
  group_id TEXT, window_id TEXT, role TEXT DEFAULT 'member',
  joined_at TEXT, PRIMARY KEY(group_id, window_id));
CREATE INDEX IF NOT EXISTS idx_group_members ON group_members(group_id);
CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS whitelist(qq_id TEXT PRIMARY KEY, name TEXT,
  role TEXT DEFAULT 'admin');
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT, detail TEXT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS schema_version(
  version INTEGER PRIMARY KEY, applied_at TEXT);
"""

# Bump this and add an entry to _MIGRATIONS in the same commit whenever the
# schema changes. `manage.py update` refuses to roll forward when a migration
# is missing; the version is recorded in the DB so an old binary started
# against a newer DB can detect the mismatch.
SCHEMA_VERSION = 22

# columns added after v1.2 — applied to existing databases at startup
_COLUMN_MIGRATIONS = {
    "windows": [("llm_profile", "TEXT DEFAULT ''")],
    "jobs": [("llm_profile", "TEXT DEFAULT ''"), ("report_channel", "TEXT DEFAULT ''")],
    "whitelist": [("role", "TEXT DEFAULT 'admin'")],
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Idempotent add-column migrations for databases created by older versions."""
    for table, cols in _COLUMN_MIGRATIONS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in cols:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """Per-window parameter overrides (ROADMAP T1.1): windows.params_json."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(windows)").fetchall()}
    if "params_json" not in existing:
        conn.execute("ALTER TABLE windows ADD COLUMN params_json TEXT DEFAULT '{}'")
    conn.commit()


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """Pending-decision queue (ROADMAP T2.3): the durable ask_user inbox."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS pending_decisions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, window_id TEXT, question TEXT,
      options_json TEXT DEFAULT '[]', answer TEXT DEFAULT '',
      status TEXT DEFAULT 'pending', source TEXT DEFAULT 'ask_user',
      created_at TEXT, answered_at TEXT, expires_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_decisions(status, id);
    """)
    conn.commit()


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """Shared-work-channel groups (ROADMAP T3.1): groups + members + messages.group_id."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS groups(
      id TEXT PRIMARY KEY, name TEXT UNIQUE, description TEXT DEFAULT '',
      lead_window_id TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS group_members(
      group_id TEXT, window_id TEXT, role TEXT DEFAULT 'member',
      joined_at TEXT, PRIMARY KEY(group_id, window_id));
    CREATE INDEX IF NOT EXISTS idx_group_members ON group_members(group_id);
    """)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "group_id" not in existing:
        conn.execute("ALTER TABLE messages ADD COLUMN group_id TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id, id)")
    conn.commit()


def _migrate_v5(conn: sqlite3.Connection) -> None:
    """Model catalog with operator notes (模型管理页).

    `note` 是核心字段：光有模型名没用，管理员要能记下"这个模型擅长什么、
    什么不能干、限速多凶"，选型才有依据。
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS models(
      id TEXT PRIMARY KEY, provider TEXT DEFAULT '', model TEXT NOT NULL,
      base_url TEXT DEFAULT '', display_name TEXT DEFAULT '',
      api_key TEXT DEFAULT '',
      note TEXT DEFAULT '', capability TEXT DEFAULT '',
      context_window INTEGER DEFAULT 0, speed TEXT DEFAULT '', cost TEXT DEFAULT '',
      enabled INTEGER DEFAULT 1, is_default INTEGER DEFAULT 0,
      source TEXT DEFAULT 'manual', created_at TEXT, updated_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_models_default ON models(is_default, enabled);
    """)
    conn.commit()


def _migrate_v6(conn: sqlite3.Connection) -> None:
    """项目层（ROADMAP T7.1）：projects 表 + windows.project_id/subdir + groups.project_id。

    兼容旧库：把已有 `workspace_path` **自动包一层项目**（一目录一项目），
    并把工位挂上去，这样老数据无需人工处理，行为与之前等价。
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS projects(
      id TEXT PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL,
      params_json TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_projects_root ON projects(root_path);
    """)
    wcols = {r[1] for r in conn.execute("PRAGMA table_info(windows)").fetchall()}
    if "project_id" not in wcols:
        conn.execute("ALTER TABLE windows ADD COLUMN project_id TEXT DEFAULT ''")
    if "subdir" not in wcols:
        conn.execute("ALTER TABLE windows ADD COLUMN subdir TEXT DEFAULT ''")
    gcols = {r[1] for r in conn.execute("PRAGMA table_info(groups)").fetchall()}
    if gcols and "project_id" not in gcols:
        conn.execute("ALTER TABLE groups ADD COLUMN project_id TEXT DEFAULT ''")
    conn.commit()

    # ---- 老数据回填：一目录一项目
    try:
        rows = conn.execute(
            "SELECT id, name, workspace_path FROM windows "
            "WHERE COALESCE(project_id,'')='' AND COALESCE(workspace_path,'')<>''").fetchall()
    except sqlite3.Error:  # windows 表结构异常时不阻塞启动
        rows = []
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        win_id, win_name, root = row[0], row[1], row[2]
        existing = conn.execute(
            "SELECT id FROM projects WHERE root_path=?", (root,)).fetchone()
        if existing:
            pid = existing[0]
        else:
            base = (root or "").rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1] or "project"
            pid = f"legacy-{uuid.uuid4().hex[:8]}"
            conn.execute(
                "INSERT INTO projects(id,name,root_path,params_json,created_at,updated_at)"
                " VALUES(?,?,?,'{}',?,?)",
                (pid, f"{base}（自动创建）", root, now, now))
        conn.execute("UPDATE windows SET project_id=? WHERE id=?", (pid, win_id))
    conn.commit()


def _migrate_v7(conn: sqlite3.Connection) -> None:
    """项目迭代计划（ROADMAP T8.5/T8.6）。

    一个项目一份"迭代计划"：目标 + 参与工位 + 档位（保守/标准/激进）+ 停止条件。
    轮次明细单独一张表，便于控制台看"每轮做了什么、有没有进展"。
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS iterations(
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT '', goal TEXT DEFAULT '',
      mode TEXT DEFAULT 'standard', window_ids_json TEXT DEFAULT '[]',
      speed_minutes INTEGER DEFAULT 0, max_rounds INTEGER DEFAULT 0,
      no_progress_limit INTEGER DEFAULT 0, budget_tokens INTEGER DEFAULT 0,
      status TEXT DEFAULT 'active', round INTEGER DEFAULT 0,
      stop_reason TEXT DEFAULT '', last_progress TEXT DEFAULT '',
      no_progress_streak INTEGER DEFAULT 0, next_run_at TEXT DEFAULT '',
      created_at TEXT, updated_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_iter_status ON iterations(status, next_run_at);
    CREATE INDEX IF NOT EXISTS idx_iter_project ON iterations(project_id, status);
    CREATE TABLE IF NOT EXISTS iteration_rounds(
      id INTEGER PRIMARY KEY AUTOINCREMENT, iteration_id TEXT, round INTEGER,
      started_at TEXT, finished_at TEXT, status TEXT DEFAULT 'running',
      summary TEXT DEFAULT '', progress INTEGER DEFAULT 0, goal_achieved INTEGER DEFAULT 0,
      window_count INTEGER DEFAULT 0);
    CREATE INDEX IF NOT EXISTS idx_iter_rounds ON iteration_rounds(iteration_id, round);
    """)
    conn.commit()


def _migrate_v8(conn: sqlite3.Connection) -> None:
    """三层指挥链（Batch 9）：projects.owner_supervisor_id + admins（多管理员实体化）。

    `owner_supervisor_id` 是**寻址枢纽**：员工上报 / 管理员下指令都靠
    `project_id → owner_supervisor_id → 主管工位` 找到该找谁；
    为空 = 该项目没有主管，事件退化直连管理员（兼容单管理员部署）。
    """
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "owner_supervisor_id" not in pcols:
        conn.execute("ALTER TABLE projects ADD COLUMN owner_supervisor_id TEXT DEFAULT ''")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS admins(
      id TEXT PRIMARY KEY, name TEXT DEFAULT '', role_level TEXT DEFAULT 'admin',
      token_hash TEXT DEFAULT '', note TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
      created_at TEXT, updated_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_admins_enabled ON admins(enabled, role_level);
    """)
    conn.commit()


def _migrate_v9(conn: sqlite3.Connection) -> None:
    """对话（conversations）——v2 口径：**只有一个管理员**与用户对接，
    但用户可以有**多个对话**（各自独立的上下文，互不串味）。

    一行 = 一个对话；`id` 就是会话键（`WindowSession` 挂在合成窗口 `admin:<id>` 下），
    消息仍存在 `messages` 表里，所以这里只维护标题/时间这类元数据。
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS conversations(
      id TEXT PRIMARY KEY, title TEXT DEFAULT '', archived INTEGER DEFAULT 0,
      created_at TEXT, updated_at TEXT, last_message_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);
    """)
    conn.commit()


def _migrate_v10(conn: sqlite3.Connection) -> None:
    """`iteration_rounds(iteration_id, round)` 唯一约束（审查报告 P1-8）。

    原来表上只有一条普通索引，于是"手动推一轮绕过并发锁"时同一个 round 会插两行
    （报告实测：两次 `run_one` 并发都进了 round 1，`iteration_rounds` 出现两行
    round=1，而 `iterations.round` 仍是 1）。

    执行器层的并发锁已经修掉；这里是**数据库层的兜底**——先清掉历史重复行
    （保留 id 最小的那条），再建唯一索引。
    """
    conn.execute(
        "DELETE FROM iteration_rounds WHERE id NOT IN "
        "(SELECT MIN(id) FROM iteration_rounds GROUP BY iteration_id, round)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_iter_round "
                 "ON iteration_rounds(iteration_id, round)")
    conn.commit()


def _migrate_v11(conn: sqlite3.Connection) -> None:
    """`job_runs.owner_pid`（审查报告 P1-13）。

    启动清扫原来把所有 `running` 且未结束的运行记录一律标失败——但如果那是
    **另一个活着的进程**（`manage.py update` 期间新旧进程并存）正在跑的任务，
    这就是"杀活任务"。有了属主 pid，清扫只收"属主已死"的记录。
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(job_runs)").fetchall()}
    if "owner_pid" not in existing:
        conn.execute("ALTER TABLE job_runs ADD COLUMN owner_pid INTEGER DEFAULT 0")
    conn.commit()


def _migrate_v12(conn: sqlite3.Connection) -> None:
    """`models.api_key`（2026-10-15）：模型条目**自带密钥**。

    以前目录条目只有 provider/base_url，密钥始终取全局 `llm.api_key`：
    于是"设为当前"换到**另一家服务商**的模型时，用的还是上一家的 key（401），
    用户只能再回设置页手改——这就是"新增模型太不方便"的根因。
    条目自带 key 后，切模型 = 连密钥一起切。

    注意：密钥只落在本地库（0600），**任何 API 响应都只回掩码**（见 ModelService）。
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(models)").fetchall()}
    if "api_key" not in existing:
        conn.execute("ALTER TABLE models ADD COLUMN api_key TEXT DEFAULT ''")
    conn.commit()


def _migrate_v13(conn: sqlite3.Connection) -> None:
    """`runs` 表（2026-10-23，P0）：把"一次运行"做成一等实体。

    起因（owner 实测三连）："你在哪跑呢，我咋没看见"、"我能看到项目，但前端根本没显示
    工位被挂在"、"他好像卡住了"。根因是运行状态散在四个地方
    （`windows.status` / `groups.last_run` / `job_runs` / 内存里的 asyncio 任务），
    没有一个"**此刻谁在跑什么**"的唯一真相；而且运行的存活挂在调用方的 HTTP 连接上。

    `job_runs` 是**定时任务账本**，语义不同，保持不动；`runs` 覆盖全部运行
    （人工派单 / 群组 / 群组步骤 / 组长派给组员），并记录父子关系以便级联取消。
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS runs(
      id TEXT PRIMARY KEY,
      kind TEXT DEFAULT 'manual',
      parent_run_id TEXT DEFAULT '',
      window_id TEXT DEFAULT '', group_id TEXT DEFAULT '',
      project_id TEXT DEFAULT '', job_id TEXT DEFAULT '',
      status TEXT DEFAULT 'queued', phase TEXT DEFAULT '',
      step_no INTEGER DEFAULT 0, steps_total INTEGER DEFAULT 0,
      prompt_chars INTEGER DEFAULT 0, recap_chars INTEGER DEFAULT 0,
      iterations INTEGER DEFAULT 0,
      pause_count INTEGER DEFAULT 0, paused_seconds REAL DEFAULT 0,
      cancel_requested INTEGER DEFAULT 0,
      started_at TEXT DEFAULT '', updated_at TEXT DEFAULT '', finished_at TEXT DEFAULT '',
      error_code TEXT DEFAULT '', summary TEXT DEFAULT '',
      prompt TEXT DEFAULT '');
    CREATE INDEX IF NOT EXISTS idx_runs_active ON runs(status, started_at);
    CREATE INDEX IF NOT EXISTS idx_runs_window ON runs(window_id, started_at);
    CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id, started_at);
    """)
    conn.commit()


def _migrate_v14(conn: sqlite3.Connection) -> None:
    """`runs.prompt`（2026-10-20）：**把原始任务文本落库**，让"唤醒"真的可用。

    起因（owner）："要确保因为异常导致的循环停止后，管理员能够唤醒未完成的任务"。
    查明的硬缺口：`/api/runs/{id}/retry` 从 `windows._manual_last`（**内存**）取
    原始 prompt —— 进程重启后内存清空，retry 直接 400（"请重新派单"）。而
    "异常导致循环停止"最常见的场景恰恰就是进程重启/崩溃。所以 prompt 必须**落库**：
    重启后管理员仍能从 runs 台账找回任务原文并重试。
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "prompt" not in existing:
        conn.execute("ALTER TABLE runs ADD COLUMN prompt TEXT DEFAULT ''")
    conn.commit()


def _migrate_v15(conn: sqlite3.Connection) -> None:
    """`jobs.group_id`（2026-10-20）：定时任务可以**跑群组**了。

    起因（owner）："继续让其完成前端模板任务，每隔 1 小时触发一次"——他要的是
    那条「方向/程序员/审查」流水线按小时自动跑。以前 job 只能指向单个工位，
    群组运行没有定时入口（管理员只能在对话里手动 `group_run`）。
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "group_id" not in existing:
        conn.execute("ALTER TABLE jobs ADD COLUMN group_id TEXT DEFAULT ''")
    conn.commit()


# version → migration callable. Applied in ascending order for any version
# greater than what the database reports. Must be idempotent and must not
# depend on the application layer.
def _migrate_v16(conn: sqlite3.Connection) -> None:
    """成本账本（2026-10-25，0.11.0）：把"钱"变成可查的数字。

    起因为什么不是更好的调度算法：4 天 94 次压缩 / 19% 降级 / 缓存命中一度 11.5%，
    而系统里**只有 token 求和、没有单价、没有逐任务归集**——成本不可观测时，
    任何"检测成本智能调度"都是拍脑袋。详见 docs/工业自动化设计.md。

    三张表：
      * `cost_model`   单价表（可按 provider+model 覆盖；历史行保留当时单价）
      * `usage_ledger` 账本（一次外部调用一行；`idem_key` 幂等，防重放重复计费）
      * `budgets`      三级预算（global / group / job）；0.11.0 只读写，0.11.x 才执行
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS cost_model(
      id TEXT PRIMARY KEY,
      provider TEXT DEFAULT '', model TEXT NOT NULL,
      unit TEXT DEFAULT 'token',
      in_price REAL DEFAULT 0, out_price REAL DEFAULT 0, cache_price REAL DEFAULT 0,
      currency TEXT DEFAULT 'CNY',
      effective_from TEXT DEFAULT '', note TEXT DEFAULT '',
      enabled INTEGER DEFAULT 1, updated_at TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS usage_ledger(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts TEXT NOT NULL,
      kind TEXT DEFAULT 'llm',
      run_id TEXT DEFAULT '', job_id TEXT DEFAULT '', group_id TEXT DEFAULT '',
      window_id TEXT DEFAULT '',
      provider TEXT DEFAULT '', model TEXT DEFAULT '',
      in_tokens INTEGER DEFAULT 0, out_tokens INTEGER DEFAULT 0,
      cache_hit_tokens INTEGER DEFAULT 0, images INTEGER DEFAULT 0,
      unit_price REAL DEFAULT 0, amount REAL DEFAULT 0,
      idem_key TEXT DEFAULT '', meta_json TEXT DEFAULT ''
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_ledger_idem ON usage_ledger(idem_key)
      WHERE idem_key <> '';
    CREATE INDEX IF NOT EXISTS ix_ledger_ts ON usage_ledger(ts);
    CREATE INDEX IF NOT EXISTS ix_ledger_scope ON usage_ledger(window_id, run_id);

    CREATE TABLE IF NOT EXISTS budgets(
      id TEXT PRIMARY KEY,
      scope_type TEXT NOT NULL, scope_id TEXT DEFAULT '',
      period TEXT DEFAULT 'day', limit_amount REAL DEFAULT 0,
      strategy TEXT DEFAULT 'downgrade',
      enabled INTEGER DEFAULT 1, updated_at TEXT DEFAULT ''
    );
    """)
    conn.commit()


def _migrate_v17(conn: sqlite3.Connection) -> None:
    """模型分级路由的样本台账（2026-10-25，0.12.0）。

    级联决策要回答"**这个模型在这个任务类型上失败过几次**"，而现有数据给不出：
    `usage_ledger` 只有用量（没有成败），`runs` 有状态但没记"当时用的哪个模型"
    （窗口的模型会被改）。所以单独记一张小表，按 `任务类型 × 模型` 聚合：

      * `attempts / ok / failed` —— 算成功率，Beta 平滑后当 `p_fail`
      * `escalated` —— 降档后翻车、被迫升级的次数（判断"省这点钱值不值"）

    刻意**不存逐次明细**（那是账本的事），只存聚合计数——否则又是一张无限长的表。
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS model_route_stats(
      id TEXT PRIMARY KEY,
      task_class TEXT DEFAULT '',
      model TEXT DEFAULT '',
      attempts INTEGER DEFAULT 0,
      ok INTEGER DEFAULT 0,
      failed INTEGER DEFAULT 0,
      escalated INTEGER DEFAULT 0,
      last_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_route_class ON model_route_stats(task_class);
    """)
    conn.commit()


def _migrate_v18(conn: sqlite3.Connection) -> None:
    """流水线编排（2026-10-25，0.12.1）：把"多步、有依赖、可续跑"变成一等公民。

    现在的 `runs` 是"一次员工回合"，没有步骤概念：中途挂掉只能整轮重跑，
    重跑又会重复花钱、重复改文件。工业场景里（取图→判定→复核→报告）
    必须是**步骤级**的：哪步成了、产物在哪、下次从哪继续。

    三张表：
      * `pipelines`      定义（steps 的 DAG，JSON 存 spec）
      * `pipeline_runs`  一次执行（输入/输出/状态）
      * `pipeline_steps` 步骤实例（**主键 = run_id|step_id**，带 `idem_key` 与 `attempt`）
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS pipelines(
      id TEXT PRIMARY KEY,
      name TEXT DEFAULT '', description TEXT DEFAULT '',
      spec_json TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
      created_at TEXT DEFAULT '', updated_at TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS pipeline_runs(
      id TEXT PRIMARY KEY,
      pipeline_id TEXT DEFAULT '', status TEXT DEFAULT 'running',
      input_json TEXT DEFAULT '', output_json TEXT DEFAULT '',
      started_at TEXT DEFAULT '', finished_at TEXT DEFAULT '', error TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_pruns_pipe ON pipeline_runs(pipeline_id, started_at);

    CREATE TABLE IF NOT EXISTS pipeline_steps(
      id TEXT PRIMARY KEY,
      run_id TEXT DEFAULT '', step_id TEXT DEFAULT '', action TEXT DEFAULT '',
      status TEXT DEFAULT 'pending',
      attempt INTEGER DEFAULT 0, idem_key TEXT DEFAULT '',
      params_json TEXT DEFAULT '', output_json TEXT DEFAULT '', error TEXT DEFAULT '',
      started_at TEXT DEFAULT '', finished_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_psteps_run ON pipeline_steps(run_id);
    """)
    conn.commit()


def _migrate_v19(conn: sqlite3.Connection) -> None:
    """抽检与复核（2026-10-25，0.12.2）。

    "检测成本智能调度"的落点：**不是每件都花大钱复判，而是按风险与预算抽样**，
    再用真实漏检反馈修正下一批。要能算，就得把"批"和"复核结论"记下来。

      * `qc_batches`  一个批次：总量、抽样数、发现缺陷、漏检估计、策略
      * `qc_reviews`  抽样样本的复核结论（逐条，用于回流与算准确率）
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS qc_batches(
      id TEXT PRIMARY KEY,
      pipeline_run_id TEXT DEFAULT '', line TEXT DEFAULT '',
      total INTEGER DEFAULT 0, sampled INTEGER DEFAULT 0,
      defects_found INTEGER DEFAULT 0,
      defect_rate REAL DEFAULT 0, miss_estimate REAL DEFAULT 0,
      cost_estimate REAL DEFAULT 0, policy_json TEXT DEFAULT '',
      status TEXT DEFAULT 'open',
      created_at TEXT DEFAULT '', closed_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_qc_created ON qc_batches(created_at);

    CREATE TABLE IF NOT EXISTS qc_reviews(
      id TEXT PRIMARY KEY,
      batch_id TEXT DEFAULT '', item_ref TEXT DEFAULT '',
      model_label TEXT DEFAULT '', confidence REAL DEFAULT 0,
      reviewer TEXT DEFAULT '', verdict TEXT DEFAULT '',
      created_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_qc_rev_batch ON qc_reviews(batch_id);
    """)
    conn.commit()


def _migrate_v20(conn: sqlite3.Connection) -> None:
    """工业信号接入（2026-10-25，0.13.0）。

    产线的"现状"是一串数字：产量、节拍、设备温度、缺陷率、探针可用性。
    这一版只做**接收与取用**（push 模型）：

      * 边缘只推**指标**（`metrics` 一行一个采样），原图/原始数据留厂内；
      * 流水线步骤 `sensor_read` 取最新值（可设 `max_age_s` 防用陈旧数据做决策）；
      * 判定走本地视觉服务（`cv_detect`），DogBuddy 不碰像素。

    刻意不建"设备表"：设备是外部系统的资产，我们只关心它报出来的数字。
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS metrics(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      value REAL DEFAULT 0,
      unit TEXT DEFAULT '',
      source TEXT DEFAULT '',
      ts TEXT NOT NULL,
      meta_json TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_metrics_name_ts ON metrics(name, ts);
    CREATE INDEX IF NOT EXISTS ix_metrics_ts ON metrics(ts);
    """)
    conn.commit()


def _migrate_v21(conn: sqlite3.Connection) -> None:
    """数据闭环（2026-10-25，0.13.1）：让复核结果**回流**。

    0.12.2 已经有 `qc_reviews`（谁被驳回），但那些样本只是躺在库里：
    既没有进"难例池"，也没有反馈给抽检比例和模型路由。这一版补三处：

      * `hard_samples`  难例池（复核驳回的样本，可导出成训练清单）
      * `qc_reviews.model`  这次判定是**哪个模型**做的（路由要按模型算失败率）
      * 索引：按时间/批次查难例
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS hard_samples(
      id TEXT PRIMARY KEY,
      batch_id TEXT DEFAULT '',
      item_ref TEXT DEFAULT '',
      model_label TEXT DEFAULT '',
      model TEXT DEFAULT '',
      confidence REAL DEFAULT 0,
      verdict TEXT DEFAULT '',
      reviewer TEXT DEFAULT '',
      line TEXT DEFAULT '',
      task_class TEXT DEFAULT '',
      ts TEXT NOT NULL,
      meta_json TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_hard_batch ON hard_samples(batch_id);
    CREATE INDEX IF NOT EXISTS ix_hard_ts ON hard_samples(ts);
    """)
    # 老库里没有 model 列 —— SQLite 不支持 IF NOT EXISTS 的 ADD COLUMN，只能试
    try:
        conn.execute("ALTER TABLE qc_reviews ADD COLUMN model TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass                      # 已经有了
    conn.commit()


def _migrate_v22(conn: sqlite3.Connection) -> None:
    """运营回合（2026-10-26，0.14.0）：把"持续改进"变成可追溯的回合。

    工业线的闭环是"批次 → 复核 → 策略"，运营线是"目标 → 回合 → 日志 → 实验"。
    两套都复用同一底座（流水线执行、成本账本、指标）。

    三张表：
      * `ops_goals`      目标（KPI 名/方向/目标值/节奏/执行流水线）
      * `ops_experiments` 变体（一个假设一次实验；赢/输决定下一轮换不换动作）
      * `ops_journal`    回合日志（假设/动作/预期/实测/判定 + 耗时与产物）

    `metric_before/after` 允许为 NULL：没有指标时必须老实记"无法判定"，
    而不是拿 0 冒充数据。
    """
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ops_goals(
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT DEFAULT '',
      metric_name TEXT DEFAULT '',
      metric_unit TEXT DEFAULT '',
      direction TEXT DEFAULT 'up',
      baseline REAL,
      target REAL,
      cadence TEXT DEFAULT 'manual',
      pipeline_id TEXT DEFAULT '',
      enabled INTEGER DEFAULT 1,
      created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '',
      last_round_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_goals_updated ON ops_goals(updated_at);

    CREATE TABLE IF NOT EXISTS ops_experiments(
      id TEXT PRIMARY KEY,
      goal_id TEXT DEFAULT '',
      name TEXT DEFAULT '', hypothesis TEXT DEFAULT '', action TEXT DEFAULT '',
      variant INTEGER DEFAULT 1,
      status TEXT DEFAULT 'running',
      metric_before REAL, metric_after REAL,
      created_at TEXT DEFAULT '', closed_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_exp_goal ON ops_experiments(goal_id, created_at);

    CREATE TABLE IF NOT EXISTS ops_journal(
      id TEXT PRIMARY KEY,
      goal_id TEXT DEFAULT '', experiment_id TEXT DEFAULT '',
      round_no INTEGER DEFAULT 0,
      status TEXT DEFAULT 'running',
      hypothesis TEXT DEFAULT '', action TEXT DEFAULT '', expected TEXT DEFAULT '',
      observed TEXT DEFAULT '', verdict TEXT DEFAULT '',
      pipeline_run_id TEXT DEFAULT '',
      metric_before REAL, metric_after REAL, delta REAL,
      started_at TEXT DEFAULT '', finished_at TEXT DEFAULT '', notes TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS ix_journal_goal ON ops_journal(goal_id, started_at);
    CREATE INDEX IF NOT EXISTS ix_journal_status ON ops_journal(status);
    """)
    conn.commit()


# version → migration callable. Applied in ascending order for any version
# greater than what the database reports. Must be idempotent and must not
# depend on the application layer.
_MIGRATIONS: dict[int, Any] = {
    1: _ensure_columns,
    2: _migrate_v2,
    3: _migrate_v3,
    4: _migrate_v4,
    5: _migrate_v5,
    6: _migrate_v6,
    7: _migrate_v7,
    8: _migrate_v8,
    9: _migrate_v9,
    10: _migrate_v10,
    11: _migrate_v11,
    12: _migrate_v12,
    13: _migrate_v13,
    14: _migrate_v14,
    15: _migrate_v15,
    16: _migrate_v16,
    17: _migrate_v17,
    18: _migrate_v18,
    19: _migrate_v19,
    20: _migrate_v20,
    21: _migrate_v21,
    22: _migrate_v22,
}


def _as_utc(value: Any) -> datetime | None:
    """把库里的 ISO 时间解析成带时区的 UTC 时间；解析不了返回 None。

    审查报告 P1-12：`due_jobs` 原来用 SQL 的 TEXT 比较（`next_run_at <= ?`），
    而库里存的时间可能带**不同时区偏移**——`now="…+08:00"` 与
    `next_run_at="2026-10-14T15:00:00+00:00"`（实际还差 2.5 小时）会被字符串
    比出 True → 任务抢跑。现在一律解析后按时区比较。
    """
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _pid_alive(pid: Any) -> bool:
    """这个 pid 是否还活着。0/空 = 未知属主（老记录）→ 视为已死，可以被收掉。"""
    try:
        n = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    if n == os.getpid():
        return True
    try:
        os.kill(n, 0)
    except OSError:
        return False
    return True


class SchemaTooNewError(RuntimeError):
    """库的 schema 比当前代码更新 —— 拒绝以降级代码打开（审查报告 P2-1）。

    初版只把版本号写进 `schema_version`，注释声称"老二进制能据此检测出不匹配"，
    但**没有任何地方做这个检测**：用旧代码打开一个未来库不报错、不告警，
    照常写入 —— 结果是对不存在的列读字段静默丢失、随机 `OperationalError`。
    现在在打开时就 fail-fast：宁可起不来（触发自更新回滚），也不能带着错版本写数据。
    """


def _current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0



def _migrate(conn: sqlite3.Connection, target: int = SCHEMA_VERSION) -> list[int]:
    """Run pending schema migrations; returns the list of applied versions."""
    current = _current_schema_version(conn)
    applied: list[int] = []
    for version in sorted(_MIGRATIONS):
        if version > current and version <= target:
            _MIGRATIONS[version](conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version(version, applied_at) VALUES(?,?)",
                (version, _now_iso()))
            applied.append(version)
    conn.commit()
    return applied



class Database:
    """Concrete DAO satisfying WindowRepo/MessageRepo/TodoRepo/JobRepo/ConfigRepo/AuditSink."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            # P2-1: 未来库必须 fail-fast，不能"静默降级运行"
            found = _current_schema_version(self._conn)
            if found > SCHEMA_VERSION:
                self._conn.close()
                raise SchemaTooNewError(
                    f"数据库 schema_version={found} 比当前代码的 "
                    f"SCHEMA_VERSION={SCHEMA_VERSION} 更新：拒绝以降级代码打开"
                    f"（会静默丢失字段、写入随机失败）。"
                    f"请升级代码，或从备份恢复一个匹配的库。"
                    f"库文件：{self.path}")
            _migrate(self._conn)


    @property
    def schema_version(self) -> int:
        with self._lock:
            return _current_schema_version(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------- windows
    def insert_window(self, w: Window) -> Window:
        w.created_at = w.created_at or _now_iso()
        w.updated_at = _now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO windows(id,name,role,workspace_path,model,mode,status,group_id,"
                "llm_profile,params_json,project_id,subdir,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (w.id, w.name, w.role, w.workspace_path, w.model, w.mode, w.status,
                 w.group_id, w.llm_profile, json.dumps(w.params or {}, ensure_ascii=False),
                 getattr(w, "project_id", "") or "", getattr(w, "subdir", "") or "",
                 w.created_at, w.updated_at))
            self._conn.commit()
        return w

    def get_window(self, window_id: str) -> Window | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM windows WHERE id=?", (window_id,)).fetchone()
        return self._to_window(row) if row else None

    def get_window_by_name(self, name: str) -> Window | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM windows WHERE name=?", (name,)).fetchone()
        return self._to_window(row) if row else None

    def list_windows(self) -> list[Window]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM windows ORDER BY created_at").fetchall()
        return [self._to_window(r) for r in rows]

    def update_window(self, window_id: str, **fields: Any) -> Window | None:
        allowed = {"name", "role", "workspace_path", "model", "mode", "status", "group_id",
                   "llm_profile", "params", "project_id", "subdir"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return self.get_window(window_id)
        if "params" in fields:
            fields["params_json"] = json.dumps(fields.pop("params") or {}, ensure_ascii=False)
        fields["updated_at"] = _now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE windows SET {sets} WHERE id=?",
                               (*fields.values(), window_id))
            self._conn.commit()
        return self.get_window(window_id)

    def delete_window(self, window_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM windows WHERE id=?", (window_id,))
            self._conn.execute("DELETE FROM messages WHERE window_id=?", (window_id,))
            self._conn.execute("DELETE FROM todos WHERE window_id=?", (window_id,))
            self._conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def _to_window(r: sqlite3.Row) -> Window:
        keys = r.keys()
        try:
            params = json.loads(r["params_json"] or "{}") if "params_json" in keys else {}
        except (json.JSONDecodeError, TypeError):
            params = {}
        return Window(id=r["id"], name=r["name"], role=r["role"],
                      workspace_path=r["workspace_path"], model=r["model"],
                      mode=r["mode"], status=r["status"], group_id=r["group_id"],
                      llm_profile=r["llm_profile"] if "llm_profile" in keys else "",
                      params=params if isinstance(params, dict) else {},
                      project_id=(r["project_id"] or "") if "project_id" in keys else "",
                      subdir=(r["subdir"] or "") if "subdir" in keys else "",
                      created_at=r["created_at"], updated_at=r["updated_at"])

    # ------------------------------------------------------------ messages
    def add_message(self, window_id: str, role: str, content: str, tokens: int = 0,
                    meta: dict[str, Any] | None = None, group_id: str = "") -> Message:
        ts = _now_iso()
        meta = meta or {}
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(window_id,role,content,tokens,created_at,meta_json,group_id)"
                " VALUES(?,?,?,?,?,?,?)",
                (window_id, role, content, tokens, ts, json.dumps(meta, ensure_ascii=False),
                 group_id or ""))
            self._conn.commit()
            mid = cur.lastrowid
        return Message(id=mid, window_id=window_id, role=role, content=content,
                       tokens=tokens, created_at=ts, meta=meta, group_id=group_id or "")

    def list_messages(self, window_id: str, limit: int = 200) -> list[Message]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE window_id=? ORDER BY id DESC LIMIT ?",
                (window_id, limit)).fetchall()
        out = []
        for r in reversed(rows):
            try:
                meta = json.loads(r["meta_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            keys = r.keys()
            out.append(Message(id=r["id"], window_id=r["window_id"], role=r["role"],
                               content=r["content"], tokens=r["tokens"],
                               created_at=r["created_at"], meta=meta,
                               group_id=r["group_id"] if "group_id" in keys else ""))
        return out

    def sum_tokens(self, window_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(tokens),0) AS s FROM messages WHERE window_id=?",
                (window_id,)).fetchone()
        return int(row["s"])

    def cache_totals(self, window_id: str) -> tuple[int, int]:
        """累计**缓存命中/未命中** token（v0.10.43）。

        owner："工位有时还是会显示缓存命中 0%，请换成像 dsh 一样的**平均**缓存
        命中"——只看最后一次调用会被个别 0% 的调用带偏。每条助手消息的 meta 里
        记着当轮的 cache_hit/cache_miss（loop 写入），这里求和算平均。
        返回 `(hit, miss)`；没有数据时返回 (0, 0)。
        """
        hit = miss = 0
        with self._lock:
            rows = self._conn.execute(
                "SELECT meta_json FROM messages WHERE window_id=? AND meta_json LIKE '%cache_hit%'",
                (window_id,)).fetchall()
        for r in rows:
            try:
                meta = json.loads(r["meta_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            try:
                hit += int(meta.get("cache_hit") or 0)
                miss += int(meta.get("cache_miss") or 0)
            except (TypeError, ValueError):
                continue
        return hit, miss

    def ledger_summary(self, days: int = 30) -> dict[str, Any]:
        """Token usage grouped by window and by day (ROADMAP T2.4, 账本页)."""
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        with self._lock:
            by_window = self._conn.execute(
                "SELECT window_id, COUNT(*) AS messages, COALESCE(SUM(tokens),0) AS tokens "
                "FROM messages WHERE created_at >= ? GROUP BY window_id "
                "ORDER BY tokens DESC", (since,)).fetchall()
            by_day = self._conn.execute(
                "SELECT substr(created_at,1,10) AS day, COUNT(*) AS messages, "
                "COALESCE(SUM(tokens),0) AS tokens FROM messages "
                "WHERE created_at >= ? GROUP BY day ORDER BY day", (since,)).fetchall()
        return {
            "since": since,
            "by_window": [{"window_id": r["window_id"], "messages": r["messages"],
                           "tokens": r["tokens"]} for r in by_window],
            "by_day": [{"day": r["day"], "messages": r["messages"],
                        "tokens": r["tokens"]} for r in by_day],
            "total_tokens": sum(r["tokens"] for r in by_window),
            "total_messages": sum(r["messages"] for r in by_window),
        }

    def delete_messages(self, window_id: str, before_id: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM messages WHERE window_id=? AND id<=?", (window_id, before_id))
            self._conn.commit()
            return cur.rowcount

    # ----------------------------------------------------------- compactions
    def add_compaction(self, window_id: str, summary: dict[str, Any], replaced_count: int) -> Compaction:
        ts = _now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO compactions(window_id,summary_json,replaced_count,created_at) VALUES(?,?,?,?)",
                (window_id, json.dumps(summary, ensure_ascii=False), replaced_count, ts))
            self._conn.commit()
            cid = cur.lastrowid
        return Compaction(id=cid, window_id=window_id, summary=summary,
                          replaced_count=replaced_count, created_at=ts)

    def list_compactions(self, window_id: str) -> list[Compaction]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM compactions WHERE window_id=? ORDER BY id", (window_id,)).fetchall()
        return [Compaction(id=r["id"], window_id=r["window_id"],
                           summary=json.loads(r["summary_json"]),
                           replaced_count=r["replaced_count"], created_at=r["created_at"])
                for r in rows]

    # --------------------------------------------------------------- todos
    def add_todo(self, window_id: str, content: str, sort: int = 0) -> Todo:
        ts = _now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO todos(window_id,content,done,sort,created_at) VALUES(?,?,?,?,?)",
                (window_id, content, 0, sort, ts))
            self._conn.commit()
            tid = cur.lastrowid
        return Todo(id=tid, window_id=window_id, content=content, done=False, sort=sort)

    def list_todos(self, window_id: str) -> list[Todo]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM todos WHERE window_id=? ORDER BY done, sort, id",
                (window_id,)).fetchall()
        return [Todo(id=r["id"], window_id=r["window_id"], content=r["content"],
                     done=bool(r["done"]), sort=r["sort"]) for r in rows]

    def set_todo_done(self, todo_id: int, done: bool) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE todos SET done=? WHERE id=?", (1 if done else 0, todo_id))
            self._conn.commit()
            return cur.rowcount > 0

    def delete_todo(self, todo_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM todos WHERE id=?", (todo_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ---------------------------------------------------------------- jobs
    def upsert_job(self, job: Job) -> Job:
        job.updated_at = _now_iso()
        job.created_at = job.created_at or job.updated_at
        with self._lock:
            self._conn.execute(
                """INSERT INTO jobs(id,window_id,name,schedule_type,schedule_raw,schedule_json,prompt,
                   report_strategy,kind,report_channel,llm_profile,group_id,enabled,last_run_at,next_run_at,fail_count,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     window_id=excluded.window_id, name=excluded.name,
                     schedule_type=excluded.schedule_type, schedule_raw=excluded.schedule_raw,
                     schedule_json=excluded.schedule_json, prompt=excluded.prompt,
                     report_strategy=excluded.report_strategy, kind=excluded.kind,
                     report_channel=excluded.report_channel, llm_profile=excluded.llm_profile,
                     group_id=excluded.group_id,
                     enabled=excluded.enabled, last_run_at=excluded.last_run_at,
                     next_run_at=excluded.next_run_at, fail_count=excluded.fail_count,
                     updated_at=excluded.updated_at""",
                (job.id, job.window_id, job.name, job.schedule_type, job.schedule_raw,
                 json.dumps(job.schedule, ensure_ascii=False), job.prompt,
                 job.report_strategy, job.kind, job.report_channel, job.llm_profile,
                 getattr(job, "group_id", "") or "",
                 1 if job.enabled else 0,
                 job.last_run_at, job.next_run_at, job.fail_count,
                 job.created_at, job.updated_at))
            self._conn.commit()
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._to_job(r) if r else None

    def list_jobs(self, window_id: str | None = None) -> list[Job]:
        q = "SELECT * FROM jobs"
        args: tuple = ()
        if window_id:
            q += " WHERE window_id=?"
            args = (window_id,)
        q += " ORDER BY created_at"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [self._to_job(r) for r in rows]

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def due_jobs(self, now_iso: str) -> list[Job]:
        """到点的任务。**按时区解析后比较**，不用 SQL 的 TEXT 比较（P1-12）。"""
        now = _as_utc(now_iso)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE enabled=1 AND next_run_at IS NOT NULL").fetchall()
        out: list[Job] = []
        for r in rows:
            nxt = _as_utc(r["next_run_at"])
            if nxt is None:
                continue
            if now is None or nxt <= now:
                out.append(self._to_job(r))
        return out

    @staticmethod
    def _to_job(r: sqlite3.Row) -> Job:
        keys = r.keys()
        return Job(id=r["id"], window_id=r["window_id"], name=r["name"],
                   schedule_type=r["schedule_type"], schedule_raw=r["schedule_raw"],
                   schedule=json.loads(r["schedule_json"] or "{}"), prompt=r["prompt"],
                   report_strategy=r["report_strategy"], kind=r["kind"],
                   report_channel=r["report_channel"] if "report_channel" in keys else "",
                   llm_profile=r["llm_profile"] if "llm_profile" in keys else "",
                   group_id=r["group_id"] if "group_id" in keys else "",
                   enabled=bool(r["enabled"]), last_run_at=r["last_run_at"],
                   next_run_at=r["next_run_at"], fail_count=r["fail_count"],
                   created_at=r["created_at"], updated_at=r["updated_at"])

    # ------------------------------------------------------------ job_runs
    def start_run(self, job_id: str, window_id: str) -> JobRun:
        ts = _now_iso()
        # P1-13：记下本次运行属于哪个进程，清扫时才能区分"崩溃留下的"与
        # "另一个活着的进程正在跑的"（自更新期间新旧进程会并存）。
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO job_runs(job_id,window_id,started_at,status,owner_pid)"
                " VALUES(?,?,?,?,?)",
                (job_id, window_id, ts, "running", os.getpid()))
            self._conn.commit()
            rid = cur.lastrowid
        return JobRun(id=rid, job_id=job_id, window_id=window_id, started_at=ts,
                      finished_at=None, status="running", summary="", log_path="")

    def finish_run(self, run_id: int, status: str, summary: str = "", log_path: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE job_runs SET finished_at=?, status=?, summary=?, log_path=? WHERE id=?",
                (_now_iso(), status, summary, log_path, run_id))
            self._conn.commit()

    def sweep_orphan_runs(self, reason: str = "服务重启，任务中断（孤儿清扫）") -> dict[str, int]:
        """启动时收尸（Batch 6 · C5）。

        进程崩溃/被重启时会有 `job_runs` 卡在 `running`（finished_at 为空），
        以及 `windows` 卡在 `running`。如果不处理，这些任务永远没有结论、
        面板上也一直显示"运行中"。启动时统一标失败并写明原因。

        审查报告 P2-6：初版**不碰 `iteration_rounds`** → 迭代轮次永久卡在
        `running`，控制台永远显示"运行中"（进程早就没了）。
        """
        with self._lock:
            # P1-13：只收"属主进程已经死了"的运行记录。自更新期间新旧进程并存，
            # 若无差别地把所有 running 标失败，就会**杀掉另一个进程正在跑的任务**。
            live_runs = self._conn.execute(
                "SELECT id, window_id, owner_pid FROM job_runs "
                "WHERE status='running' AND finished_at IS NULL").fetchall()
            dead = [r["id"] for r in live_runs if not _pid_alive(r["owner_pid"])]
            busy_windows = {r["window_id"] for r in live_runs
                            if _pid_alive(r["owner_pid"])}
            runs = 0
            for rid in dead:
                runs += self._conn.execute(
                    "UPDATE job_runs SET status='failed', finished_at=?, "
                    "summary=CASE WHEN COALESCE(summary,'')='' THEN ? ELSE summary END "
                    "WHERE id=? AND status='running'",
                    (_now_iso(), reason, rid)).rowcount
            windows = 0
            for r in self._conn.execute(
                    "SELECT id FROM windows WHERE status='running'").fetchall():
                if r["id"] in busy_windows:
                    continue  # 另一个活着的进程正在这个工位上跑任务
                windows += self._conn.execute(
                    "UPDATE windows SET status='idle', updated_at=? WHERE id=?",
                    (_now_iso(), r["id"])).rowcount
            rounds = self._conn.execute(
                "UPDATE iteration_rounds SET status='interrupted', finished_at=?, "
                "summary=CASE WHEN COALESCE(summary,'')='' THEN ? ELSE summary END "
                "WHERE status='running' AND finished_at IS NULL",
                (_now_iso(), reason)).rowcount
            self._conn.commit()
        return {"runs": int(runs or 0), "windows": int(windows or 0),
                "iteration_rounds": int(rounds or 0)}

    def list_runs(self, job_id: str | None = None, limit: int = 50) -> list[JobRun]:
        q = "SELECT * FROM job_runs"
        args: tuple = ()
        if job_id:
            q += " WHERE job_id=?"
            args = (job_id,)
        q += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(q, (*args, limit)).fetchall()
        return [JobRun(id=r["id"], job_id=r["job_id"], window_id=r["window_id"],
                       started_at=r["started_at"], finished_at=r["finished_at"],
                       status=r["status"], summary=r["summary"], log_path=r["log_path"])
                for r in rows]

    # ------------------------------------------------- pending_decisions (T2.3)
    @staticmethod
    def _to_decision(r: sqlite3.Row) -> PendingDecision:
        try:
            options = json.loads(r["options_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            options = []
        return PendingDecision(
            id=r["id"], window_id=r["window_id"], question=r["question"],
            options=options if isinstance(options, list) else [],
            answer=r["answer"] or "", status=r["status"], source=r["source"],
            created_at=r["created_at"], answered_at=r["answered_at"],
            expires_at=r["expires_at"])

    def add_pending_decision(self, window_id: str, question: str,
                             options: list | None = None,
                             source: str = "ask_user",
                             expires_at: str | None = None) -> PendingDecision:
        ts = _now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO pending_decisions(window_id,question,options_json,"
                "status,source,created_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (window_id, question, json.dumps(options or [], ensure_ascii=False),
                 "pending", source, ts, expires_at))
            self._conn.commit()
            rid = cur.lastrowid
        return PendingDecision(id=rid, window_id=window_id, question=question,
                               options=options or [], status="pending",
                               source=source, created_at=ts, expires_at=expires_at)

    def answer_pending_decision(self, decision_id: int, answer: str,
                                status: str = "answered") -> PendingDecision | None:
        with self._lock:
            self._conn.execute(
                "UPDATE pending_decisions SET answer=?, status=?, answered_at=? WHERE id=?",
                (answer, status, _now_iso(), decision_id))
            self._conn.commit()
        return self.get_pending_decision(decision_id)

    def cancel_pending_decisions(self, window_id: str) -> int:
        """A fresh ask_user supersedes open ones from the same window."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE pending_decisions SET status='cancelled', answered_at=? "
                "WHERE window_id=? AND status='pending'", (_now_iso(), window_id))
            self._conn.commit()
            return cur.rowcount

    def get_pending_decision(self, decision_id: int) -> PendingDecision | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM pending_decisions WHERE id=?",
                                   (decision_id,)).fetchone()
        return self._to_decision(r) if r is not None else None

    def list_pending_decisions(self, status: str | None = None,
                               limit: int = 100) -> list[PendingDecision]:
        q = "SELECT * FROM pending_decisions"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        q += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(q, (*args, limit)).fetchall()
        return [self._to_decision(r) for r in rows]

    def expire_pending_decisions(self, now: str | None = None) -> list[PendingDecision]:
        """Mark overdue pending decisions as timeout; returns what expired.

        Called by the sweep task — the timeout policy lives in the service layer.
        """
        now = now or _now_iso()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pending_decisions WHERE status='pending' "
                "AND expires_at IS NOT NULL AND expires_at < ?", (now,)).fetchall()
            expired = [self._to_decision(r) for r in rows]
            if expired:
                self._conn.execute(
                    "UPDATE pending_decisions SET status='timeout', answered_at=? "
                    "WHERE status='pending' AND expires_at IS NOT NULL AND expires_at < ?",
                    (now, now))
                self._conn.commit()
        return expired

    # ----------------------------------------------------- groups (T3.1)
    @staticmethod
    def _to_group(r: sqlite3.Row) -> Group:
        keys = r.keys()
        return Group(id=r["id"], name=r["name"],
                     description=r["description"] or "",
                     lead_window_id=r["lead_window_id"] or "",
                     project_id=(r["project_id"] or "") if "project_id" in keys else "",
                     created_at=r["created_at"], updated_at=r["updated_at"])

    def insert_group(self, g: Group) -> Group:
        g.created_at = g.created_at or _now_iso()
        g.updated_at = _now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO groups(id,name,description,lead_window_id,project_id,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (g.id, g.name, g.description, g.lead_window_id,
                 getattr(g, "project_id", "") or "", g.created_at, g.updated_at))
            self._conn.commit()
        return g

    def get_group(self, group_id: str) -> Group | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM groups WHERE id=? OR name=?",
                                   (group_id, group_id)).fetchone()
        return self._to_group(r) if r is not None else None

    def list_groups(self) -> list[Group]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM groups ORDER BY name").fetchall()
        return [self._to_group(r) for r in rows]

    def update_group(self, group_id: str, **fields: Any) -> Group | None:
        allowed = {"name", "description", "lead_window_id", "project_id"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            fields["updated_at"] = _now_iso()
            sets = ", ".join(f"{k}=?" for k in fields)
            with self._lock:
                self._conn.execute(f"UPDATE groups SET {sets} WHERE id=?",
                                   (*fields.values(), group_id))
                self._conn.commit()
        return self.get_group(group_id)

    def delete_group(self, group_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM groups WHERE id=?", (group_id,))
            self._conn.execute("DELETE FROM group_members WHERE group_id=?", (group_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def group_add_member(self, group_id: str, window_id: str,
                         role: str = "member") -> GroupMember:
        with self._lock:
            self._conn.execute(
                "INSERT INTO group_members(group_id,window_id,role,joined_at) VALUES(?,?,?,?) "
                "ON CONFLICT(group_id,window_id) DO UPDATE SET role=excluded.role",
                (group_id, window_id, role or "member", _now_iso()))
            self._conn.commit()
        return GroupMember(group_id=group_id, window_id=window_id, role=role)

    def group_remove_member(self, group_id: str, window_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM group_members WHERE group_id=? AND window_id=?",
                (group_id, window_id))
            self._conn.commit()
            return cur.rowcount > 0

    def list_group_members(self, group_id: str) -> list[GroupMember]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM group_members WHERE group_id=? ORDER BY joined_at",
                (group_id,)).fetchall()
        return [GroupMember(group_id=r["group_id"], window_id=r["window_id"],
                            role=r["role"], joined_at=r["joined_at"] or "")
                for r in rows]

    def list_group_messages(self, group_id: str, limit: int = 100) -> list[Message]:
        """共享频道的最新消息（跨所有成员窗口）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE group_id=? ORDER BY id DESC LIMIT ?",
                (group_id, limit)).fetchall()
        out = []
        for r in reversed(rows):
            try:
                meta = json.loads(r["meta_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                meta = {}
            keys = r.keys()
            out.append(Message(id=r["id"], window_id=r["window_id"], role=r["role"],
                               content=r["content"], tokens=r["tokens"],
                               created_at=r["created_at"], meta=meta,
                               group_id=r["group_id"] if "group_id" in keys else ""))
        return out

    # -------------------------------------------------------------- config
    def get_config(self, key: str, default: Any = None) -> Any:
        with self._lock:
            r = self._conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        if r is None:
            return default
        try:
            return json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            return r["value"]

    def set_config(self, key: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, payload))
            self._conn.commit()

    # -------------------------------------------------- 跨进程单例锁（P1-13）
    #: cron 单例锁的 config 键
    CRON_LOCK_KEY = "runtime.cron_lock"

    def try_cron_lock(self, ttl_seconds: float = 180.0) -> bool:
        """拿跨进程的 cron 单例锁。返回 True = 本进程可以跑这一轮 tick。

        审查报告 P1-13：`_inflight` 是**进程内**集合，`manage.py update` 期间新旧
        进程会同时 tick 同一批任务 → 同一个任务被跑两遍。这里用一个带属主 pid 与
        TTL 的 config 行做互斥：持有者还活着且未过期 → 让给它。
        """
        me = os.getpid()
        holder = self.get_config(self.CRON_LOCK_KEY) or {}
        if isinstance(holder, dict):
            pid = int(holder.get("pid") or 0)
            since = _as_utc(holder.get("at"))
            fresh = (since is not None and
                     (datetime.now(timezone.utc) - since).total_seconds() < ttl_seconds)
            if pid != me and fresh and _pid_alive(pid):
                return False
        self.set_config(self.CRON_LOCK_KEY, {"pid": me, "at": _now_iso()})
        return True

    def release_cron_lock(self) -> None:
        holder = self.get_config(self.CRON_LOCK_KEY) or {}
        if isinstance(holder, dict) and int(holder.get("pid") or 0) == os.getpid():
            self.set_config(self.CRON_LOCK_KEY, {})

    # ----------------------------------------------------------- whitelist
    def whitelist_add(self, qq_id: str, name: str = "", role: str = "admin") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO whitelist(qq_id,name,role) VALUES(?,?,?) "
                "ON CONFLICT(qq_id) DO UPDATE SET name=excluded.name, role=excluded.role",
                (str(qq_id), name, role or "admin"))
            self._conn.commit()

    def whitelist_remove(self, qq_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM whitelist WHERE qq_id=?", (str(qq_id),))
            self._conn.commit()
            return cur.rowcount > 0

    def whitelist_list(self) -> list[dict[str, str]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM whitelist ORDER BY qq_id").fetchall()
        return [{"qq_id": r["qq_id"], "name": r["name"],
                 "role": r["role"] if "role" in r.keys() else "admin"} for r in rows]

    def whitelist_check(self, qq_id: str) -> bool:
        with self._lock:
            r = self._conn.execute("SELECT 1 FROM whitelist WHERE qq_id=?", (str(qq_id),)).fetchone()
        return r is not None

    # ---------------------------------------------------------------- audit
    def audit(self, actor: str, action: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log(actor,action,detail,created_at) VALUES(?,?,?,?)",
                (actor, action, detail, _now_iso()))
            self._conn.commit()

    # ------------------------------------------------- projects (T7.1, v6)
    @staticmethod
    def _to_project(r: sqlite3.Row) -> Project:
        keys = r.keys()
        try:
            params = json.loads(r["params_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            params = {}
        return Project(id=r["id"], name=r["name"], root_path=r["root_path"],
                       params=params if isinstance(params, dict) else {},
                       owner_supervisor_id=((r["owner_supervisor_id"] or "")
                                            if "owner_supervisor_id" in keys else ""),
                       created_at=r["created_at"] or "", updated_at=r["updated_at"] or "")

    def insert_project(self, p: Project) -> Project:
        p.created_at = p.created_at or _now_iso()
        p.updated_at = _now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects(id,name,root_path,params_json,owner_supervisor_id,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (p.id, p.name, p.root_path,
                 json.dumps(p.params or {}, ensure_ascii=False),
                 getattr(p, "owner_supervisor_id", "") or "", p.created_at, p.updated_at))
            self._conn.commit()
        return p

    def get_project(self, project_id: str) -> Project | None:
        if not project_id:
            return None
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE id=? OR name=?",
                                     (project_id, project_id)).fetchone()
        return self._to_project(row) if row else None

    def list_projects(self) -> list[Project]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        return [self._to_project(r) for r in rows]

    def update_project(self, project_id: str, **fields: Any) -> Project | None:
        allowed = {"name", "root_path", "params", "owner_supervisor_id"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return self.get_project(project_id)
        if "params" in fields:
            fields["params_json"] = json.dumps(fields.pop("params") or {}, ensure_ascii=False)
        fields["updated_at"] = _now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE projects SET {sets} WHERE id=?",
                               (*fields.values(), project_id))
            self._conn.commit()
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        """删项目：**不删工位**，只把它们解挂（避免误删工作目录归属记录）。"""
        with self._lock:
            self._conn.execute("UPDATE windows SET project_id='' WHERE project_id=?",
                               (project_id,))
            self._conn.execute("UPDATE groups SET project_id='' WHERE project_id=?",
                               (project_id,))
            cur = self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            self._conn.commit()
        return bool(cur.rowcount)

    def project_summary(self) -> dict[str, int]:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS total FROM projects").fetchone()
            win = self._conn.execute(
                "SELECT COUNT(*) AS n FROM windows WHERE COALESCE(project_id,'')<>''").fetchone()
        return {"total": int(row["total"] or 0), "mounted_windows": int(win["n"] or 0)}

    # ------------------------------------------------- iterations (T8.5/T8.6, v7)
    _ITER_FIELDS = ("project_id", "goal", "mode", "window_ids_json", "speed_minutes",
                    "max_rounds", "no_progress_limit", "budget_tokens", "status",
                    "round", "stop_reason", "last_progress", "no_progress_streak",
                    "next_run_at")

    @staticmethod
    def _to_iteration(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        try:
            d["window_ids"] = json.loads(d.pop("window_ids_json", "[]") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["window_ids"] = []
        return d

    def insert_iteration(self, entry: dict[str, Any]) -> dict[str, Any]:
        now = _now_iso()
        cols = {"id": entry.get("id") or uuid.uuid4().hex[:12],
                "created_at": now, "updated_at": now}
        for f in self._ITER_FIELDS:
            if entry.get(f) is not None:
                cols[f] = entry[f]
        cols["window_ids_json"] = json.dumps(entry.get("window_ids") or [], ensure_ascii=False)
        cols.pop("window_ids", None)
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        with self._lock:
            self._conn.execute(f"INSERT INTO iterations({names}) VALUES({marks})",
                               tuple(cols.values()))
            self._conn.commit()
        return self.get_iteration(cols["id"]) or {}

    def get_iteration(self, iteration_id: str) -> dict[str, Any] | None:
        if not iteration_id:
            return None
        with self._lock:
            r = self._conn.execute("SELECT * FROM iterations WHERE id=?",
                                   (iteration_id,)).fetchone()
        return self._to_iteration(r) if r else None

    def list_iterations(self, project_id: str | None = None,
                        status: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM iterations"
        where, args = [], []
        if project_id:
            where.append("project_id=?"); args.append(project_id)
        if status:
            where.append("status=?"); args.append(status)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(q, tuple(args)).fetchall()
        return [self._to_iteration(r) for r in rows]

    def update_iteration(self, iteration_id: str, **fields: Any) -> dict[str, Any] | None:
        patch = {k: v for k, v in fields.items()
                 if k in self._ITER_FIELDS or k == "window_ids"}
        if not patch:
            return self.get_iteration(iteration_id)
        if "window_ids" in patch:
            patch["window_ids_json"] = json.dumps(patch.pop("window_ids") or [],
                                                  ensure_ascii=False)
        patch["updated_at"] = _now_iso()
        sets = ", ".join(f"{k}=?" for k in patch)
        with self._lock:
            self._conn.execute(f"UPDATE iterations SET {sets} WHERE id=?",
                               (*patch.values(), iteration_id))
            self._conn.commit()
        return self.get_iteration(iteration_id)

    def delete_iteration(self, iteration_id: str) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM iteration_rounds WHERE iteration_id=?",
                               (iteration_id,))
            cur = self._conn.execute("DELETE FROM iterations WHERE id=?", (iteration_id,))
            self._conn.commit()
        return bool(cur.rowcount)

    def due_iterations(self, now_iso: str) -> list[dict[str, Any]]:
        """到点的迭代。同样按时区解析后比较（P1-12）。"""
        now = _as_utc(now_iso)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM iterations WHERE status='active' "
                "AND COALESCE(next_run_at,'')<>''").fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            nxt = _as_utc(r["next_run_at"])
            if nxt is None:
                continue
            if now is None or nxt <= now:
                out.append(self._to_iteration(r))
        out.sort(key=lambda e: str(e.get("next_run_at") or ""))
        return out

    def start_iteration_round(self, iteration_id: str, round_no: int,
                              window_count: int = 0) -> int:
        """开一轮。`(iteration_id, round)` 有唯一约束（迁移 v10），重复开同一轮
        不会再插第二行——返回已存在那行的 id（幂等，P1-8 的数据库层兜底）。"""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO iteration_rounds"
                "(iteration_id,round,started_at,status,window_count)"
                " VALUES(?,?,?,'running',?)",
                (iteration_id, round_no, _now_iso(), window_count))
            self._conn.commit()
            if cur.rowcount:
                return int(cur.lastrowid or 0)
            row = self._conn.execute(
                "SELECT id FROM iteration_rounds WHERE iteration_id=? AND round=?",
                (iteration_id, round_no)).fetchone()
            return int(row["id"]) if row is not None else 0

    def finish_iteration_round(self, round_id: int, status: str, summary: str = "",
                               progress: int = 0, goal_achieved: bool = False) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE iteration_rounds SET finished_at=?, status=?, summary=?, progress=?,"
                " goal_achieved=? WHERE id=?",
                (_now_iso(), status, summary[:4000], int(progress), 1 if goal_achieved else 0,
                 round_id))
            self._conn.commit()

    def list_iteration_rounds(self, iteration_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM iteration_rounds WHERE iteration_id=? ORDER BY round DESC LIMIT ?",
                (iteration_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------- admins (T9.5, v8)
    _ADMIN_FIELDS = ("name", "role_level", "token_hash", "note", "enabled")

    def list_admins(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM admins"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY role_level='owner' DESC, created_at"
        with self._lock:
            rows = self._conn.execute(q).fetchall()
        return [dict(r) for r in rows]

    def get_admin(self, admin_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM admins WHERE id=? OR name=?",
                                   (admin_id, admin_id)).fetchone()
        return dict(r) if r else None

    def upsert_admin(self, entry: dict[str, Any]) -> dict[str, Any]:
        aid = str(entry.get("id") or "").strip() or uuid.uuid4().hex[:12]
        now = _now_iso()
        with self._lock:
            existing = self._conn.execute("SELECT * FROM admins WHERE id=?",
                                          (aid,)).fetchone()
            if existing is None:
                cols: dict[str, Any] = {"id": aid, "created_at": now, "updated_at": now}
                for f in self._ADMIN_FIELDS:
                    if entry.get(f) is not None:
                        cols[f] = 1 if (f == "enabled" and entry[f]) else (
                            0 if f == "enabled" else entry[f])
                names = ", ".join(cols)
                marks = ", ".join("?" for _ in cols)
                self._conn.execute(f"INSERT INTO admins({names}) VALUES({marks})",
                                   tuple(cols.values()))
            else:
                patch = {f: entry[f] for f in self._ADMIN_FIELDS
                         if f in entry and entry[f] is not None}
                if "enabled" in patch:
                    patch["enabled"] = 1 if patch["enabled"] else 0
                patch["updated_at"] = now
                sets = ", ".join(f"{k}=?" for k in patch)
                self._conn.execute(f"UPDATE admins SET {sets} WHERE id=?",
                                   (*patch.values(), aid))
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM admins WHERE id=?", (aid,)).fetchone()
        return dict(row) if row else {}

    def delete_admin(self, admin_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM admins WHERE id=? OR name=?",
                                     (admin_id, admin_id))
            self._conn.commit()
        return bool(cur.rowcount)

    # ------------------------------------------------- conversations (v9)
    def insert_conversation(self, conv_id: str, title: str = "") -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO conversations(id,title,archived,created_at,updated_at,"
                "last_message_at) VALUES(?,?,0,?,?,?)",
                (conv_id, title or "", now, now, ""))
            self._conn.commit()
        return self.get_conversation(conv_id) or {}

    def get_conversation(self, conv_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM conversations WHERE id=?",
                                   (conv_id,)).fetchone()
        return dict(r) if r else None

    def list_conversations(self, include_archived: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM conversations"
        if not include_archived:
            q += " WHERE COALESCE(archived,0)=0"
        q += " ORDER BY COALESCE(last_message_at, created_at) DESC, created_at DESC"
        with self._lock:
            rows = self._conn.execute(q).fetchall()
        return [dict(r) for r in rows]

    def update_conversation(self, conv_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {"title", "archived", "last_message_at"}
        patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not patch:
            return self.get_conversation(conv_id)
        patch["updated_at"] = _now_iso()
        if "archived" in patch:
            patch["archived"] = 1 if patch["archived"] else 0
        sets = ", ".join(f"{k}=?" for k in patch)
        with self._lock:
            self._conn.execute(f"UPDATE conversations SET {sets} WHERE id=?",
                               (*patch.values(), conv_id))
            self._conn.commit()
        return self.get_conversation(conv_id)

    def delete_conversation(self, conv_id: str, drop_messages: bool = True) -> bool:
        """删对话：连它的消息一起删（对话是用户可见的完整单位）。

        审查报告 P0-3：初版是 `window_id IN (?,?)` 传 `(f"admin:{conv_id}", conv_id)`，
        而 `conv_id` **零校验**——`DELETE /api/admin/chat/conversations/<真实工位id>`
        会把那个工位的 messages 全部删光（工位还在、历史空白、不可恢复）。
        现在裸 `<id>` 只在「它确实不是一个真实工位」时才连带删。
        """
        cid = str(conv_id or "")
        with self._lock:
            if drop_messages and cid:
                # 对话消息落在合成窗口 `admin:<id>` 下
                self._conn.execute("DELETE FROM messages WHERE window_id=?", (f"admin:{cid}",))
                # 兼容更老的形态（裸 `<id>` 当窗口），但绝不误伤真实工位
                if self.get_window(cid) is None:
                    self._conn.execute("DELETE FROM messages WHERE window_id=?", (cid,))
            cur = self._conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
            self._conn.commit()
        return bool(cur.rowcount)


    #: 历史保留天数（消息 / 任务运行记录 / 迭代轮次 / 已决待决事项 / 压缩摘要）
    RETENTION_DAYS_DEFAULT = 90

    def prune_history(self, keep_days: int | None = None) -> dict[str, int]:
        """清理无界增长的历史（审查报告 P2-2）。

        `messages` / `job_runs` / `pending_decisions` / `iteration_rounds` /
        `compactions` 原来**只增不减**，库里也没有任何 `VACUUM`/`wal_checkpoint`。
        报告按 15 分钟档外推 ≈ 132k 行 messages/月、1.6M 行/年。

        只删**已终结**的行（未结束的任务/仍未决的事项一律不动）。
        保留天数可用 `DOGBUDDY_RETENTION_DAYS` 覆盖。
        """
        days = int(keep_days or os.environ.get("DOGBUDDY_RETENTION_DAYS")
                   or self.RETENTION_DAYS_DEFAULT)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        out: dict[str, int] = {}
        with self._lock:
            out["messages"] = self._conn.execute(
                "DELETE FROM messages WHERE COALESCE(created_at,'')<>'' AND created_at < ?",
                (cutoff,)).rowcount
            out["job_runs"] = self._conn.execute(
                "DELETE FROM job_runs WHERE COALESCE(finished_at,'')<>'' AND started_at < ?",
                (cutoff,)).rowcount
            out["iteration_rounds"] = self._conn.execute(
                "DELETE FROM iteration_rounds WHERE COALESCE(finished_at,'')<>'' "
                "AND started_at < ?", (cutoff,)).rowcount
            out["pending_decisions"] = self._conn.execute(
                "DELETE FROM pending_decisions WHERE status<>'pending' AND created_at < ?",
                (cutoff,)).rowcount
            out["compactions"] = self._conn.execute(
                "DELETE FROM compactions WHERE COALESCE(created_at,'')<>'' AND created_at < ?",
                (cutoff,)).rowcount
            self._conn.commit()
        return {k: int(v or 0) for k, v in out.items()}

    def wal_checkpoint(self, truncate: bool = True) -> None:
        """把 WAL 收回主库（P2-2：长读者按住时 WAL 能涨到 55MB 且进程存活期不回落）。"""
        mode = "TRUNCATE" if truncate else "PASSIVE"
        with self._lock:
            try:
                self._conn.execute(f"PRAGMA wal_checkpoint({mode})")
            except sqlite3.Error:  # noqa: BLE001
                logger.warning("wal checkpoint failed", exc_info=True)

    def vacuum(self) -> None:
        """回收删除后留下的空闲页。只在 prune 之后调用（VACUUM 会重写整库）。"""
        with self._lock:
            try:
                self._conn.execute("VACUUM")
            except sqlite3.Error:  # noqa: BLE001
                logger.warning("vacuum failed", exc_info=True)

    def legacy_admin_windows(self) -> list[tuple[str, int]]:
        """老版本留下的管理员会话窗口（`admin:<key>` + 消息数，按消息多寡降序）。

        用途：v9 引入 `conversations` 表之前，控制台把会话记在 `admin:console` 之类
        的合成窗口下。升级后如果没有对话行，这些历史就会变成**孤儿**——
        用户打开对话页看到空白，会以为记录丢了。启动时用这个方法把它们接管回来。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT window_id, COUNT(*) AS n FROM messages "
                "WHERE window_id LIKE 'admin:%' GROUP BY window_id ORDER BY n DESC"
            ).fetchall()
        return [(r["window_id"], int(r["n"] or 0)) for r in rows]

    def list_audit(self, limit: int = 200) -> list[AuditEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [AuditEntry(id=r["id"], actor=r["actor"], action=r["action"],
                           detail=r["detail"], created_at=r["created_at"]) for r in rows]

    # ------------------------------------------------- models (模型管理页, v5)
    _MODEL_FIELDS = ("provider", "model", "base_url", "display_name", "api_key", "note",
                     "capability", "context_window", "speed", "cost",
                     "enabled", "is_default", "source")

    def list_models(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM models"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY is_default DESC, provider ASC, model ASC"
        with self._lock:
            rows = self._conn.execute(q).fetchall()
        return [dict(r) for r in rows]

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        return dict(row) if row else None

    def get_model_by_name(self, model: str) -> dict[str, Any] | None:
        """按**模型名**找目录条目（工位只存模型名，不带条目 id）。

        多条同名（不同 provider）时优先启用的、其次最近更新的——因为工位选的
        就是"这个名字的模型"，取一条能用的比报错重要。
        """
        name = (model or "").strip()
        if not name:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM models WHERE model=? "
                "ORDER BY COALESCE(enabled,1) DESC, updated_at DESC LIMIT 1", (name,)).fetchone()
        return dict(row) if row else None

    def upsert_model(self, entry: dict[str, Any]) -> dict[str, Any]:
        """按 id 新增或更新。**未提供的字段保持原值**（PATCH 语义）。"""
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            raise ValueError("model id 不能为空")
        now = _now_iso()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
            if existing is None:
                if not str(entry.get("model") or "").strip():
                    raise ValueError("新增模型必须给 model（API 里的模型名）")
                cols = {"id": model_id, "created_at": now, "updated_at": now}
                for f in self._MODEL_FIELDS:
                    if entry.get(f) is not None:
                        cols[f] = entry[f]
                cols.setdefault("model", model_id)
                names = ", ".join(cols)
                marks = ", ".join("?" for _ in cols)
                self._conn.execute(f"INSERT INTO models({names}) VALUES({marks})",
                                   tuple(cols.values()))
            else:
                updates = {}
                for f in self._MODEL_FIELDS:
                    if f in entry and entry[f] is not None:
                        updates[f] = entry[f]
                updates["updated_at"] = now
                sets = ", ".join(f"{k}=?" for k in updates)
                self._conn.execute(f"UPDATE models SET {sets} WHERE id=?",
                                   (*updates.values(), model_id))
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        return dict(row) if row else {}

    def delete_model(self, model_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM models WHERE id=?", (model_id,))
            self._conn.commit()
        return bool(cur.rowcount)

    def set_default_model(self, model_id: str) -> bool:
        """把某个模型标为全局默认（其它全部清掉）。"""
        with self._lock:
            if not self._conn.execute("SELECT 1 FROM models WHERE id=?", (model_id,)).fetchone():
                return False
            self._conn.execute("UPDATE models SET is_default=0")
            self._conn.execute("UPDATE models SET is_default=1, updated_at=? WHERE id=?",
                               (_now_iso(), model_id))
            self._conn.commit()
        return True

    def models_summary(self) -> dict[str, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) AS enabled, "
                "SUM(CASE WHEN is_default=1 THEN 1 ELSE 0 END) AS defaults, "
                "SUM(CASE WHEN COALESCE(note,'')<>'' THEN 1 ELSE 0 END) AS noted "
                "FROM models").fetchone()
        return {"total": int(row["total"] or 0), "enabled": int(row["enabled"] or 0),
                "defaults": int(row["defaults"] or 0), "noted": int(row["noted"] or 0)}

    # ------------------------------------------------- 成本账本（0.11.0，schema 16）
    # 只做「存」与「查」；换算规则（单价、缓存折扣、幂等键）在 CostService 里，
    # 因为那是业务口径，DAO 只负责落库（ARCHITECTURE §3 的分层约定）。

    _COST_MODEL_FIELDS = ("id", "provider", "model", "unit", "in_price", "out_price",
                          "cache_price", "currency", "effective_from", "note",
                          "enabled", "updated_at")

    def list_cost_models(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cost_model ORDER BY provider ASC, model ASC").fetchall()
        return [dict(r) for r in rows]

    def get_cost_model(self, model_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM cost_model WHERE id=?",
                                     (str(model_id),)).fetchone()
        return dict(row) if row else None

    def upsert_cost_model(self, entry: dict[str, Any]) -> dict[str, Any]:
        """按 id 新增或更新；未提供的字段保持原值（PATCH 语义，与模型目录一致）。"""
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            raise ValueError("cost_model id 不能为空")
        data = {k: v for k, v in entry.items() if k in self._COST_MODEL_FIELDS}
        data["id"] = model_id
        data["updated_at"] = _now_iso()
        if "enabled" in data:
            data["enabled"] = 1 if data["enabled"] else 0
        cols = [c for c in data if c != "id"]
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM cost_model WHERE id=?", (model_id,)).fetchone()
            if exists:
                self._conn.execute(
                    f"UPDATE cost_model SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?",
                    [data[c] for c in cols] + [model_id])
            else:
                data.setdefault("model", model_id)
                all_cols = [c for c in self._COST_MODEL_FIELDS if c in data]
                self._conn.execute(
                    f"INSERT INTO cost_model({', '.join(all_cols)}) "
                    f"VALUES({', '.join('?' for _ in all_cols)})",
                    [data[c] for c in all_cols])
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM cost_model WHERE id=?",
                                     (model_id,)).fetchone()
        return dict(row) if row else dict(data)

    def delete_cost_model(self, model_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM cost_model WHERE id=?", (str(model_id),))
            self._conn.commit()
        return bool(cur.rowcount)

    def record_usage(self, row: dict[str, Any]) -> bool:
        """写一条账本。返回 False = `idem_key` 命中（重放/重试），**不重复计费**。

        幂等不是洁癖：`run_retry` 与群组步骤续跑都会重放调用，没有去重就会把同一笔
        调用记两次账，成本页直接失真。
        """
        idem = str(row.get("idem_key") or "").strip()
        cols = ("ts", "kind", "run_id", "job_id", "group_id", "window_id", "provider",
                "model", "in_tokens", "out_tokens", "cache_hit_tokens", "images",
                "unit_price", "amount", "idem_key", "meta_json")
        data = {c: row.get(c, "" if c in ("ts", "kind", "run_id", "job_id", "group_id",
                                          "window_id", "provider", "model", "idem_key",
                                          "meta_json") else 0)
                for c in cols}
        data["ts"] = str(data.get("ts") or _now_iso())
        with self._lock:
            if idem:
                hit = self._conn.execute(
                    "SELECT 1 FROM usage_ledger WHERE idem_key=?", (idem,)).fetchone()
                if hit:
                    return False
            self._conn.execute(
                f"INSERT INTO usage_ledger({', '.join(cols)}) "
                f"VALUES({', '.join('?' for _ in cols)})", [data[c] for c in cols])
            self._conn.commit()
        return True

    def usage_rows(self, since: str = "", until: str = "",
                   limit: int = 200) -> list[dict[str, Any]]:
        q = "SELECT * FROM usage_ledger WHERE 1=1"
        args: list[Any] = []
        if since:
            q += " AND ts >= ?"
            args.append(since)
        if until:
            q += " AND ts < ?"
            args.append(until)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 2000)))
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    #: 分组维度白名单（列名直接进 SQL，绝不能让外部决定）
    USAGE_GROUP_COLUMNS = {"day": "substr(ts,1,10)", "window": "window_id",
                          "model": "model", "run": "run_id", "kind": "kind",
                          "provider": "provider"}

    def usage_summary(self, since: str = "", until: str = "",
                      group_by: str = "day") -> list[dict[str, Any]]:
        col = self.USAGE_GROUP_COLUMNS.get(str(group_by), "substr(ts,1,10)")
        q = (f"SELECT {col} AS bucket, COUNT(*) AS calls, "
             "SUM(in_tokens) AS in_tokens, SUM(out_tokens) AS out_tokens, "
             "SUM(cache_hit_tokens) AS cache_tokens, SUM(images) AS images, "
             "SUM(amount) AS amount FROM usage_ledger WHERE 1=1")
        args: list[Any] = []
        if since:
            q += " AND ts >= ?"
            args.append(since)
        if until:
            q += " AND ts < ?"
            args.append(until)
        q += " GROUP BY bucket ORDER BY bucket DESC"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def usage_totals(self, since: str = "") -> dict[str, Any]:
        q = ("SELECT COUNT(*) AS calls, COALESCE(SUM(in_tokens),0) AS in_tokens, "
             "COALESCE(SUM(out_tokens),0) AS out_tokens, "
             "COALESCE(SUM(cache_hit_tokens),0) AS cache_tokens, "
             "COALESCE(SUM(images),0) AS images, COALESCE(SUM(amount),0) AS amount "
             "FROM usage_ledger WHERE 1=1")
        args: list[Any] = []
        if since:
            q += " AND ts >= ?"
            args.append(since)
        with self._lock:
            row = self._conn.execute(q, args).fetchone()
        return dict(row) if row else {}

    _BUDGET_FIELDS = ("id", "scope_type", "scope_id", "period", "limit_amount",
                      "strategy", "enabled", "updated_at")

    def spend_since(self, ts: str, *, window_id: str = "", run_id: str = "",
                    job_id: str = "", group_id: str = "") -> dict[str, Any]:
        """某个时间点之后的花费（可叠加作用域过滤）。

        v0.11.1 预算闸门的唯一数据源：账本已经是逐调用一行，所以"花了多少"不该
        另存一份计数（两份数一定会对不上）。
        """
        q = ("SELECT COUNT(*) AS calls, COALESCE(SUM(amount), 0) AS amount,"
             " COALESCE(SUM(in_tokens), 0) AS in_tokens,"
             " COALESCE(SUM(out_tokens), 0) AS out_tokens"
             " FROM usage_ledger WHERE ts >= ?")
        args: list[Any] = [ts]
        for col, val in (("window_id", window_id), ("run_id", run_id),
                         ("job_id", job_id), ("group_id", group_id)):
            if val:
                q += f" AND {col}=?"
                args.append(val)
        with self._lock:
            row = self._conn.execute(q, args).fetchone()
        return dict(row) if row else {"calls": 0, "amount": 0.0,
                                      "in_tokens": 0, "out_tokens": 0}

    # ------------------------------------------------- 指标（0.13.0）
    def push_metric(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO metrics(name, value, unit, source, ts, meta_json) "
                "VALUES(?,?,?,?,?,?)",
                (str(row.get("name") or ""), float(row.get("value") or 0),
                 str(row.get("unit") or ""), str(row.get("source") or ""),
                 row.get("ts") or datetime.now(timezone.utc).isoformat(),
                 row.get("meta_json") or ""))
            self._conn.commit()
            mid = cur.lastrowid
        return {"id": mid, **{k: row.get(k) for k in ("name", "value", "unit", "source", "ts")}}

    def latest_metrics(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        """每个指标名取最新一条（控制台/流水线都用它）。"""
        if names:
            marks = ",".join("?" for _ in names)
            q = (f"SELECT m.* FROM metrics m JOIN (SELECT name, MAX(id) AS mx "
                 f"FROM metrics WHERE name IN ({marks}) GROUP BY name) t ON m.id = t.mx "
                 f"ORDER BY m.name")
            args: list[Any] = list(names)
        else:
            q = ("SELECT m.* FROM metrics m JOIN (SELECT name, MAX(id) AS mx "
                 "FROM metrics GROUP BY name) t ON m.id = t.mx ORDER BY m.name")
            args = []
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def metric_history(self, name: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM metrics WHERE name=? ORDER BY id DESC LIMIT ?",
                (str(name), int(limit))).fetchall()
        return [dict(r) for r in rows]

    def metric_names(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT name FROM metrics ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    # ------------------------------------------------- 运营（0.14.0）
    _GOAL_FIELDS = ("id", "name", "description", "metric_name", "metric_unit",
                    "direction", "baseline", "target", "cadence", "pipeline_id",
                    "enabled", "created_at", "updated_at", "last_round_at")

    def upsert_goal(self, row: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        gid = str(row.get("id") or "")
        with self._lock:
            self._conn.execute(
                """INSERT INTO ops_goals(id, name, description, metric_name, metric_unit,
                                         direction, baseline, target, cadence, pipeline_id,
                                         enabled, created_at, updated_at, last_round_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, description=excluded.description,
                     metric_name=excluded.metric_name, metric_unit=excluded.metric_unit,
                     direction=excluded.direction, baseline=excluded.baseline,
                     target=excluded.target, cadence=excluded.cadence,
                     pipeline_id=excluded.pipeline_id, enabled=excluded.enabled,
                     updated_at=excluded.updated_at""",
                (gid, row.get("name") or "", row.get("description") or "",
                 row.get("metric_name") or "", row.get("metric_unit") or "",
                 row.get("direction") or "up", row.get("baseline"), row.get("target"),
                 row.get("cadence") or "manual", row.get("pipeline_id") or "",
                 int(row.get("enabled", 1) or 0),
                 row.get("created_at") or now, now,
                 row.get("last_round_at") or ""))
            self._conn.commit()
        return self.get_goal(gid) or {}

    def list_goals(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM ops_goals"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY updated_at DESC"
        with self._lock:
            return [dict(r) for r in self._conn.execute(q).fetchall()]

    def get_goal(self, gid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM ops_goals WHERE id=?",
                                     (str(gid),)).fetchone()
        return dict(row) if row else None

    def delete_goal(self, gid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM ops_goals WHERE id=?", (str(gid),))
            self._conn.commit()
        return cur.rowcount > 0

    def touch_goal(self, gid: str, when: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE ops_goals SET last_round_at=?, updated_at=? WHERE id=?",
                (when, when, str(gid)))
            self._conn.commit()

    def create_experiment(self, row: dict[str, Any]) -> dict[str, Any]:
        eid = str(row.get("id") or "")
        with self._lock:
            self._conn.execute(
                """INSERT INTO ops_experiments(id, goal_id, name, hypothesis, action,
                                               variant, status, metric_before,
                                               metric_after, created_at, closed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (eid, row.get("goal_id") or "", row.get("name") or "",
                 row.get("hypothesis") or "", row.get("action") or "",
                 int(row.get("variant") or 1), row.get("status") or "running",
                 row.get("metric_before"), row.get("metric_after"),
                 row.get("created_at") or datetime.now(timezone.utc).isoformat(),
                 row.get("closed_at") or ""))
            self._conn.commit()
        return self.get_experiment(eid) or {}

    def get_experiment(self, eid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM ops_experiments WHERE id=?",
                                     (str(eid),)).fetchone()
        return dict(row) if row else None

    def update_experiment(self, eid: str, **fields: Any) -> None:
        allowed = ("status", "metric_after", "closed_at", "hypothesis", "action")
        sets, args = [], []
        for k in allowed:
            if k in fields:
                sets.append(f"{k}=?")
                args.append(fields[k])
        if not sets:
            return
        args.append(str(eid))
        with self._lock:
            self._conn.execute(
                f"UPDATE ops_experiments SET {', '.join(sets)} WHERE id=?", args)
            self._conn.commit()

    def list_experiments(self, goal_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        q = "SELECT * FROM ops_experiments"
        args: list[Any] = []
        if goal_id:
            q += " WHERE goal_id=?"
            args.append(goal_id)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def create_journal(self, row: dict[str, Any]) -> dict[str, Any]:
        jid = str(row.get("id") or "")
        with self._lock:
            self._conn.execute(
                """INSERT INTO ops_journal(id, goal_id, experiment_id, round_no,
                                           status, hypothesis, action, expected,
                                           observed, verdict, pipeline_run_id,
                                           metric_before, metric_after, delta,
                                           started_at, finished_at, notes)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (jid, row.get("goal_id") or "", row.get("experiment_id") or "",
                 int(row.get("round_no") or 0), row.get("status") or "running",
                 row.get("hypothesis") or "", row.get("action") or "",
                 row.get("expected") or "", row.get("observed") or "",
                 row.get("verdict") or "", row.get("pipeline_run_id") or "",
                 row.get("metric_before"), row.get("metric_after"),
                 row.get("delta"), row.get("started_at") or datetime.now(timezone.utc).isoformat(),
                 row.get("finished_at") or "", row.get("notes") or ""))
            self._conn.commit()
        return self.get_journal(jid) or {}

    def update_journal(self, jid: str, **fields: Any) -> None:
        allowed = ("status", "observed", "verdict", "pipeline_run_id",
                   "metric_after", "delta", "finished_at", "notes")
        sets, args = [], []
        for k in allowed:
            if k in fields:
                sets.append(f"{k}=?")
                args.append(fields[k])
        if not sets:
            return
        args.append(str(jid))
        with self._lock:
            self._conn.execute(f"UPDATE ops_journal SET {', '.join(sets)} WHERE id=?", args)
            self._conn.commit()

    def get_journal(self, jid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM ops_journal WHERE id=?",
                                     (str(jid),)).fetchone()
        return dict(row) if row else None

    def list_journal(self, goal_id: str = "", limit: int = 50,
                     status: str = "") -> list[dict[str, Any]]:
        q = "SELECT * FROM ops_journal"
        clause, args = [], []
        if goal_id:
            clause.append("goal_id=?")
            args.append(goal_id)
        if status:
            clause.append("status=?")
            args.append(status)
        if clause:
            q += " WHERE " + " AND ".join(clause)
        q += " ORDER BY started_at DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def count_journal(self, goal_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM ops_journal WHERE goal_id=?", (str(goal_id),)).fetchone()
        return int(row[0] if row else 0)

    # ------------------------------------------------- 抽检（0.12.2）
    def create_qc_batch(self, row: dict[str, Any]) -> dict[str, Any]:
        bid = str(row.get("id") or "")
        with self._lock:
            self._conn.execute(
                """INSERT INTO qc_batches(id, pipeline_run_id, line, total, sampled,
                                          defects_found, defect_rate, miss_estimate,
                                          cost_estimate, policy_json, status,
                                          created_at, closed_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (bid, row.get("pipeline_run_id") or "", row.get("line") or "",
                 int(row.get("total") or 0), int(row.get("sampled") or 0),
                 int(row.get("defects_found") or 0),
                 float(row.get("defect_rate") or 0), float(row.get("miss_estimate") or 0),
                 float(row.get("cost_estimate") or 0), row.get("policy_json") or "",
                 row.get("status") or "open",
                 row.get("created_at") or datetime.now(timezone.utc).isoformat(),
                 row.get("closed_at") or ""))
            self._conn.commit()
        return self.get_qc_batch(bid) or {}

    def get_qc_batch(self, bid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM qc_batches WHERE id=?",
                                     (str(bid),)).fetchone()
        return dict(row) if row else None

    def list_qc_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM qc_batches ORDER BY created_at DESC LIMIT ?",
                (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def update_qc_batch(self, bid: str, **fields: Any) -> None:
        allowed = ("sampled", "defects_found", "defect_rate", "miss_estimate",
                   "cost_estimate", "status", "closed_at")
        sets, args = [], []
        for k in allowed:
            if k in fields:
                sets.append(f"{k}=?")
                args.append(fields[k])
        if not sets:
            return
        args.append(str(bid))
        with self._lock:
            self._conn.execute(f"UPDATE qc_batches SET {', '.join(sets)} WHERE id=?", args)
            self._conn.commit()

    def add_qc_review(self, row: dict[str, Any]) -> dict[str, Any]:
        rid = str(row.get("id") or "")
        with self._lock:
            self._conn.execute(
                """INSERT INTO qc_reviews(id, batch_id, item_ref, model_label,
                                          model, confidence, reviewer, verdict, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (rid, row.get("batch_id") or "", row.get("item_ref") or "",
                 row.get("model_label") or "", row.get("model") or "",
                 float(row.get("confidence") or 0),
                 row.get("reviewer") or "", row.get("verdict") or "",
                 row.get("created_at") or datetime.now(timezone.utc).isoformat()))
            self._conn.commit()
        return row

    def list_qc_reviews(self, batch_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM qc_reviews WHERE batch_id=? ORDER BY created_at",
                (str(batch_id),)).fetchall()
        return [dict(r) for r in rows]

    def recent_qc_reviews(self, limit: int = 200) -> list[dict[str, Any]]:
        """跨批次取最近 N 条复核（漂移监控用）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM qc_reviews ORDER BY created_at DESC LIMIT ?",
                (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------- 难例池（0.13.1）
    def add_hard_sample(self, row: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """INSERT INTO hard_samples(id, batch_id, item_ref, model_label, model,
                                            confidence, verdict, reviewer, line,
                                            task_class, ts, meta_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(row.get("id") or ""), row.get("batch_id") or "",
                 row.get("item_ref") or "", row.get("model_label") or "",
                 row.get("model") or "", float(row.get("confidence") or 0),
                 row.get("verdict") or "", row.get("reviewer") or "",
                 row.get("line") or "", row.get("task_class") or "",
                 row.get("ts") or datetime.now(timezone.utc).isoformat(),
                 row.get("meta_json") or ""))
            self._conn.commit()
        return row

    def list_hard_samples(self, limit: int = 100, batch_id: str = "",
                          line: str = "") -> list[dict[str, Any]]:
        q = "SELECT * FROM hard_samples"
        clause, args = [], []
        if batch_id:
            clause.append("batch_id=?")
            args.append(batch_id)
        if line:
            clause.append("line=?")
            args.append(line)
        if clause:
            q += " WHERE " + " AND ".join(clause)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def count_hard_samples(self, since: str = "") -> int:
        if since:
            with self._lock:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM hard_samples WHERE ts >= ?", (since,)).fetchone()
        else:
            with self._lock:
                row = self._conn.execute("SELECT COUNT(*) FROM hard_samples").fetchone()
        return int(row[0] if row else 0)

    # ------------------------------------------------- 流水线（0.12.1）
    def upsert_pipeline(self, row: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        pid = str(row.get("id") or "")
        with self._lock:
            self._conn.execute(
                """INSERT INTO pipelines(id, name, description, spec_json, enabled,
                                         created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, description=excluded.description,
                     spec_json=excluded.spec_json, enabled=excluded.enabled,
                     updated_at=excluded.updated_at""",
                (pid, row.get("name") or "", row.get("description") or "",
                 row.get("spec_json") or "", int(row.get("enabled", 1) or 0),
                 row.get("created_at") or now, now))
            self._conn.commit()
        return self.get_pipeline(pid) or {}

    def list_pipelines(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM pipelines"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY updated_at DESC"
        with self._lock:
            return [dict(r) for r in self._conn.execute(q).fetchall()]

    def get_pipeline(self, pid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM pipelines WHERE id=?",
                                     (str(pid),)).fetchone()
        return dict(row) if row else None

    def delete_pipeline(self, pid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM pipelines WHERE id=?", (str(pid),))
            self._conn.commit()
        return cur.rowcount > 0

    def create_pipeline_run(self, row: dict[str, Any]) -> dict[str, Any]:
        rid = str(row.get("id") or "")
        with self._lock:
            self._conn.execute(
                """INSERT INTO pipeline_runs(id, pipeline_id, status, input_json,
                                             output_json, started_at, finished_at, error)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (rid, row.get("pipeline_id") or "", row.get("status") or "running",
                 row.get("input_json") or "", row.get("output_json") or "",
                 row.get("started_at") or datetime.now(timezone.utc).isoformat(),
                 row.get("finished_at") or "", row.get("error") or ""))
            self._conn.commit()
        return self.get_pipeline_run(rid) or {}

    def get_pipeline_run(self, rid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM pipeline_runs WHERE id=?",
                                     (str(rid),)).fetchone()
        return dict(row) if row else None

    def list_pipeline_runs(self, limit: int = 50, pipeline_id: str = "",
                           status: str = "") -> list[dict[str, Any]]:
        q = "SELECT * FROM pipeline_runs"
        clause, args = [], []
        if pipeline_id:
            clause.append("pipeline_id=?")
            args.append(pipeline_id)
        if status:
            clause.append("status=?")
            args.append(status)
        if clause:
            q += " WHERE " + " AND ".join(clause)
        q += " ORDER BY started_at DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def update_pipeline_run(self, rid: str, **fields: Any) -> None:
        allowed = ("status", "output_json", "finished_at", "error")
        sets, args = [], []
        for k in allowed:
            if k in fields:
                sets.append(f"{k}=?")
                args.append(fields[k])
        if not sets:
            return
        args.append(str(rid))
        with self._lock:
            self._conn.execute(f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE id=?", args)
            self._conn.commit()

    def upsert_pipeline_step(self, row: dict[str, Any]) -> dict[str, Any]:
        """步骤实例。主键 `run_id|step_id`；`attempt` 由调用方递增。"""
        pk = str(row.get("id") or "")
        with self._lock:
            self._conn.execute(
                """INSERT INTO pipeline_steps(id, run_id, step_id, action, status,
                                              attempt, idem_key, params_json,
                                              output_json, error, started_at, finished_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status, attempt=excluded.attempt,
                     idem_key=excluded.idem_key, output_json=excluded.output_json,
                     error=excluded.error, started_at=excluded.started_at,
                     finished_at=excluded.finished_at""",
                (pk, row.get("run_id") or "", row.get("step_id") or "",
                 row.get("action") or "", row.get("status") or "pending",
                 int(row.get("attempt") or 0), row.get("idem_key") or "",
                 row.get("params_json") or "", row.get("output_json") or "",
                 row.get("error") or "", row.get("started_at") or "",
                 row.get("finished_at") or ""))
            self._conn.commit()
        return self.get_pipeline_step(pk) or {}

    def get_pipeline_step(self, pk: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM pipeline_steps WHERE id=?",
                                     (str(pk),)).fetchone()
        return dict(row) if row else None

    def reset_pipeline_steps(self, run_id: str) -> int:
        """续跑前把失败/中断的步骤拉回 pending（已 done 的不动 —— 幂等跳过）。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE pipeline_steps SET status='pending', error='', finished_at='' "
                "WHERE run_id=? AND status IN ('failed','running')", (str(run_id),))
            self._conn.commit()
        return cur.rowcount

    def list_pipeline_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pipeline_steps WHERE run_id=? ORDER BY started_at, id",
                (str(run_id),)).fetchall()
        return [dict(r) for r in rows]

    def list_route_stats(self) -> list[dict[str, Any]]:
        """按 `任务类型 × 模型` 聚合的路由样本（0.12.0）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM model_route_stats ORDER BY task_class, model").fetchall()
        return [dict(r) for r in rows]

    def bump_route_stat(self, task_class: str, model: str, *,
                        ok: bool = True, escalated: bool = False) -> dict[str, Any]:
        """记一次路由结果。聚合计数，不存逐次明细。"""
        rid = f"{task_class or 'default'}|{model or ''}"
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """INSERT INTO model_route_stats
                     (id, task_class, model, attempts, ok, failed, escalated, last_at)
                   VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     attempts = attempts + 1,
                     ok = ok + excluded.ok,
                     failed = failed + excluded.failed,
                     escalated = escalated + excluded.escalated,
                     last_at = excluded.last_at""",
                (rid, task_class or "default", model or "",
                 1 if ok else 0, 0 if ok else 1,
                 1 if escalated else 0, now))
            self._conn.commit()
        return {"id": rid, "ok": ok, "escalated": escalated}

    def list_budgets(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM budgets ORDER BY scope_type ASC, scope_id ASC").fetchall()
        return [dict(r) for r in rows]

    def upsert_budget(self, entry: dict[str, Any]) -> dict[str, Any]:
        bid = str(entry.get("id") or "").strip()
        data = {k: v for k, v in entry.items() if k in self._BUDGET_FIELDS}
        if not bid:
            bid = f"{data.get('scope_type', 'global')}:{data.get('scope_id', '') or '*'}"
        data["id"] = bid
        data["updated_at"] = _now_iso()
        if "enabled" in data:
            data["enabled"] = 1 if data["enabled"] else 0
        cols = [c for c in data if c != "id"]
        with self._lock:
            exists = self._conn.execute("SELECT 1 FROM budgets WHERE id=?", (bid,)).fetchone()
            if exists:
                self._conn.execute(
                    f"UPDATE budgets SET {', '.join(f'{c}=?' for c in cols)} WHERE id=?",
                    [data[c] for c in cols] + [bid])
            else:
                data.setdefault("scope_type", "global")
                all_cols = [c for c in self._BUDGET_FIELDS if c in data]
                self._conn.execute(
                    f"INSERT INTO budgets({', '.join(all_cols)}) "
                    f"VALUES({', '.join('?' for _ in all_cols)})",
                    [data[c] for c in all_cols])
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM budgets WHERE id=?", (bid,)).fetchone()
        return dict(row) if row else dict(data)

    def delete_budget(self, bid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM budgets WHERE id=?", (str(bid),))
            self._conn.commit()
        return bool(cur.rowcount)


def new_id() -> str:
    return uuid.uuid4().hex[:12]
