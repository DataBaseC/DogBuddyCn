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
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ports import (AuditEntry, Compaction, Group, GroupMember, Job, JobRun,
                    Message, PendingDecision, Todo, Window)


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
  report_channel TEXT DEFAULT '', llm_profile TEXT DEFAULT '',
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
SCHEMA_VERSION = 4

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


# version → migration callable. Applied in ascending order for any version
# greater than what the database reports. Must be idempotent and must not
# depend on the application layer.
_MIGRATIONS: dict[int, Any] = {
    1: _ensure_columns,
    2: _migrate_v2,
    3: _migrate_v3,
    4: _migrate_v4,
}


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
                "INSERT INTO windows(id,name,role,workspace_path,model,mode,status,group_id,llm_profile,params_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (w.id, w.name, w.role, w.workspace_path, w.model, w.mode, w.status,
                 w.group_id, w.llm_profile, json.dumps(w.params or {}, ensure_ascii=False),
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
                   "llm_profile", "params"}
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
                   report_strategy,kind,report_channel,llm_profile,enabled,last_run_at,next_run_at,fail_count,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     window_id=excluded.window_id, name=excluded.name,
                     schedule_type=excluded.schedule_type, schedule_raw=excluded.schedule_raw,
                     schedule_json=excluded.schedule_json, prompt=excluded.prompt,
                     report_strategy=excluded.report_strategy, kind=excluded.kind,
                     report_channel=excluded.report_channel, llm_profile=excluded.llm_profile,
                     enabled=excluded.enabled, last_run_at=excluded.last_run_at,
                     next_run_at=excluded.next_run_at, fail_count=excluded.fail_count,
                     updated_at=excluded.updated_at""",
                (job.id, job.window_id, job.name, job.schedule_type, job.schedule_raw,
                 json.dumps(job.schedule, ensure_ascii=False), job.prompt,
                 job.report_strategy, job.kind, job.report_channel, job.llm_profile,
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
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=?",
                (now_iso,)).fetchall()
        return [self._to_job(r) for r in rows]

    @staticmethod
    def _to_job(r: sqlite3.Row) -> Job:
        keys = r.keys()
        return Job(id=r["id"], window_id=r["window_id"], name=r["name"],
                   schedule_type=r["schedule_type"], schedule_raw=r["schedule_raw"],
                   schedule=json.loads(r["schedule_json"] or "{}"), prompt=r["prompt"],
                   report_strategy=r["report_strategy"], kind=r["kind"],
                   report_channel=r["report_channel"] if "report_channel" in keys else "",
                   llm_profile=r["llm_profile"] if "llm_profile" in keys else "",
                   enabled=bool(r["enabled"]), last_run_at=r["last_run_at"],
                   next_run_at=r["next_run_at"], fail_count=r["fail_count"],
                   created_at=r["created_at"], updated_at=r["updated_at"])

    # ------------------------------------------------------------ job_runs
    def start_run(self, job_id: str, window_id: str) -> JobRun:
        ts = _now_iso()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO job_runs(job_id,window_id,started_at,status) VALUES(?,?,?,?)",
                (job_id, window_id, ts, "running"))
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
        """
        with self._lock:
            runs = self._conn.execute(
                "UPDATE job_runs SET status='failed', finished_at=?, "
                "summary=CASE WHEN COALESCE(summary,'')='' THEN ? ELSE summary END "
                "WHERE status='running' AND finished_at IS NULL",
                (_now_iso(), reason)).rowcount
            windows = self._conn.execute(
                "UPDATE windows SET status='idle', updated_at=? WHERE status='running'",
                (_now_iso(),)).rowcount
            self._conn.commit()
        return {"runs": int(runs or 0), "windows": int(windows or 0)}

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
        return Group(id=r["id"], name=r["name"],
                     description=r["description"] or "",
                     lead_window_id=r["lead_window_id"] or "",
                     created_at=r["created_at"], updated_at=r["updated_at"])

    def insert_group(self, g: Group) -> Group:
        g.created_at = g.created_at or _now_iso()
        g.updated_at = _now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO groups(id,name,description,lead_window_id,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (g.id, g.name, g.description, g.lead_window_id,
                 g.created_at, g.updated_at))
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
        allowed = {"name", "description", "lead_window_id"}
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

    def list_audit(self, limit: int = 200) -> list[AuditEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [AuditEntry(id=r["id"], actor=r["actor"], action=r["action"],
                           detail=r["detail"], created_at=r["created_at"]) for r in rows]


def new_id() -> str:
    return uuid.uuid4().hex[:12]
