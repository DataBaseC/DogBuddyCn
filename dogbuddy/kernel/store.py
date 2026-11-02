# DogBuddyCn — durable task-kernel repository
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""SQLite repository for triggers, work items, outbox and the durable queue.

Why a separate class: `db.py` is already large and owns the connection. This
class reuses that connection (never opens a second one) but keeps the new
kernel tables out of the legacy DAO surface.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import _now_iso, new_id


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return json.dumps(str(value), ensure_ascii=False)


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


class KernelStore:
    def __init__(self, db: Any):
        self.db = db
        self._conn = db._conn
        self._lock = db._lock

    # ------------------------------------------------------------- threads
    def ensure_thread(self, *, source: str, external_id: str, kind: str = "web",
                      title: str = "") -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM threads WHERE source=? AND external_id=?",
                (source, external_id)).fetchone()
            if row is not None:
                return dict(row)
            tid = f"th-{new_id()[:10]}"
            self._conn.execute(
                "INSERT INTO threads(id,kind,source,external_id,title,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (tid, kind, source, external_id, title[:200], "open", now, now))
            self._conn.commit()
        return {"id": tid, "kind": kind, "source": source,
                "external_id": external_id, "title": title[:200],
                "status": "open", "created_at": now, "updated_at": now}

    # ------------------------------------------------------------- triggers
    def record_trigger(self, *, source: str, external_id: str, thread_id: str,
                       kind: str, payload: dict[str, Any], dedupe_key: str,
                       status: str = "candidate") -> tuple[dict[str, Any], bool]:
        """Return (trigger, created). `dedupe_key` is UNIQUE.

        Duplicate delivery must be a no-op, not a second trigger.
        """
        now = _now_iso()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM triggers WHERE dedupe_key=?", (dedupe_key,)).fetchone()
            if existing is not None:
                return dict(existing), False
            tid = f"tr-{new_id()[:10]}"
            self._conn.execute(
                "INSERT INTO triggers(id,source,external_id,thread_id,kind,"
                "payload_json,dedupe_key,status,received_at,processed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (tid, source, external_id, thread_id, kind, _json(payload),
                 dedupe_key, status, now, ""))
            self._conn.commit()
        return {"id": tid, "source": source, "external_id": external_id,
                "thread_id": thread_id, "kind": kind, "payload": payload,
                "dedupe_key": dedupe_key, "status": status,
                "received_at": now, "processed_at": ""}, True

    def mark_trigger(self, trigger_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE triggers SET status=?, processed_at=? WHERE id=?",
                (status, _now_iso(), trigger_id))
            self._conn.commit()

    def count_triggers(self, status: str = "") -> int:
        q = "SELECT COUNT(*) FROM triggers"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        with self._lock:
            row = self._conn.execute(q, args).fetchone()
        return int(row[0] if row else 0)

    # ----------------------------------------------------------- work items
    def create_work_item(self, *, thread_id: str, title: str, description: str = "",
                         project_id: str = "", window_id: str = "",
                         created_by: str = "web") -> dict[str, Any]:
        now = _now_iso()
        wid = f"wi-{new_id()[:10]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO work_items(id,thread_id,title,description,status,"
                "project_id,window_id,current_run_id,created_by,created_at,"
                "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (wid, thread_id, title[:200], description[:4000], "open",
                 project_id, window_id, "", created_by, now, now))
            self._conn.commit()
        return self.get_work_item(wid) or {}

    def get_work_item(self, work_item_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM work_items WHERE id=?", (work_item_id,)).fetchone()
        return dict(row) if row else None

    def list_work_items(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM work_items ORDER BY updated_at DESC LIMIT ?",
                (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- mandates
    def create_mandate(self, *, work_item_id: str, scope: str = "web",
                       allowed_actions: list[str] | None = None,
                       budget: dict[str, Any] | None = None,
                       actor_id: str = "owner", expires_at: str = "") -> dict[str, Any]:
        mid = f"md-{new_id()[:10]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO mandates(id,work_item_id,policy_version,scope,"
                "allowed_actions_json,budget_json,actor_id,expires_at,status) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (mid, work_item_id, "0.19", scope,
                 _json(allowed_actions or []), _json(budget or {}),
                 actor_id, expires_at, "active"))
            self._conn.commit()
        return {"id": mid, "work_item_id": work_item_id, "scope": scope,
                "allowed_actions": allowed_actions or [], "budget": budget or {},
                "actor_id": actor_id, "expires_at": expires_at, "status": "active"}

    def subscribe(self, *, work_item_id: str, thread_id: str,
                  created_by: str = "web") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO subscriptions(work_item_id,thread_id,"
                "created_at,created_by) VALUES(?,?,?,?)",
                (work_item_id, thread_id, _now_iso(), created_by))
            self._conn.commit()

    # --------------------------------------------------------------- outbox
    def enqueue_outbox(self, topic: str, payload: dict[str, Any],
                       *, ref_id: str = "", event_id: str = "") -> str:
        oid = event_id or f"ob-{new_id()[:10]}"
        now = _now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO outbox_events(id,topic,ref_id,payload_json,"
                "status,attempts,next_at,created_at,sent_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (oid, topic, ref_id, _json(payload), "pending", 0, now, now, ""))
            self._conn.commit()
        return oid

    def has_outbox(self, event_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM outbox_events WHERE id=?", (event_id,)).fetchone()
        return row is not None

    def pending_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        now = _now_iso()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outbox_events WHERE status='pending' AND "
                "(next_at='' OR next_at<=?) ORDER BY created_at LIMIT ?",
                (now, int(limit))).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = _loads(d.get("payload_json"), {})
            out.append(d)
        return out

    def mark_outbox_sent(self, event_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE outbox_events SET status='sent', sent_at=? WHERE id=?",
                (_now_iso(), event_id))
            self._conn.commit()

    def mark_outbox_failed(self, event_id: str, delay_s: float = 30.0) -> int:
        nxt = (datetime.now(timezone.utc) + timedelta(seconds=max(1.0, delay_s))).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE outbox_events SET attempts=attempts+1, next_at=? WHERE id=?",
                (nxt, event_id))
            self._conn.commit()
            row = self._conn.execute(
                "SELECT attempts FROM outbox_events WHERE id=?", (event_id,)).fetchone()
        return int(row[0] if row else 0)

    def count_pending_outbox(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE status='pending'").fetchone()
        return int(row[0] if row else 0)

    # ----------------------------------------------------------- task queue
    def enqueue_task(self, task: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO task_queue(id,window_id,kind,prompt,job_id,"
                "urgent,llm_profile,meta_json,priority,dedupe_key,work_item_id,"
                "status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task.id, task.window_id, task.kind, task.prompt, task.job_id or "",
                 int(bool(task.urgent)), task.llm_profile, _json(task.meta),
                 int(getattr(task, "priority", 0) or 0),
                 getattr(task, "dedupe_key", "") or "",
                 getattr(task, "work_item_id", "") or "", "queued",
                 _now_iso(), _now_iso()))
            self._conn.commit()

    def mark_task_running(self, task_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE task_queue SET status='running', updated_at=? WHERE id=?",
                (_now_iso(), task_id))
            self._conn.commit()

    def finish_task(self, task_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM task_queue WHERE id=?", (task_id,))
            self._conn.commit()

    def queued_tasks(self, window_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        q = "SELECT * FROM task_queue WHERE status='queued'"
        args: list[Any] = []
        if window_id:
            q += " AND window_id=?"
            args.append(window_id)
        q += " ORDER BY priority DESC, created_at LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["meta"] = _loads(d.get("meta_json"), {})
            out.append(d)
        return out

    def count_queued(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM task_queue WHERE status='queued'").fetchone()
        return int(row[0] if row else 0)

    # --------------------------------------------------------------- health
    def health(self) -> dict[str, Any]:
        return {"triggers": self.count_triggers(),
                "triggers_candidate": self.count_triggers("candidate"),
                "work_items": len(self.list_work_items(limit=1000)),
                "queued_tasks": self.count_queued(),
                "outbox_pending": self.count_pending_outbox()}
