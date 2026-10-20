# DogBuddyCn — 运行台账 RunRegistry（P0，2026-10-23）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""把"一次运行"做成一等实体：**此刻谁在跑什么**只有一个真相。

起因（owner 实测三连）：
* "**你在哪跑呢，我咋没看见**" —— 用 API/脚本触发的群组运行在界面上完全不存在；
* "我能看到项目，但前端根本没显示工位被挂在"（项目页只统计直接挂载，已单独修）；
* "**他好像卡住了**" —— 人工派单被限速暂停后没人叫醒（已单独修）。

设计（与《方案-长任务可见性与可控性》一致，先做**最小可用**的一半）：
* 内存 `_active` 供毫秒级读取与取消；DB `runs` 表做历史与"重启后仍知道发生过什么"；
* 状态机：`queued → running → (paused) → done | failed | cancelled | timeout | interrupted`；
* 父子关系（`parent_run_id`）：群组 → 成员步骤 / 组长派给组员的活，取消可级联；
* 每次状态变化发 `run.updated`（前端只订阅一个事件即可）；
* **启动时把上个进程遗留的 running/paused 标成 `interrupted`**（否则重启后界面上
  永远挂着一堆"运行中"的僵尸行 —— 今天为部署重启 3 次就制造过这种情况）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..db import _now_iso, new_id

logger = logging.getLogger(__name__)

#: 还没结束的状态（`interrupted` 之外的都算"活着"）
LIVE_STATUSES = ("queued", "running", "paused", "cancelling")
# v0.10.38：`partial` = **有步骤没完成**的群组/多步运行终态。以前只要收尾就写
# `done`，哪怕 4/4 名成员全被限速暂停、零产出——上级探测（progress_report /
# 顶栏/runs API）看到的是"done"，而真相藏在 summary 的一行字里
# （实测：迭代实验 4/4 步暂停、status=done、零交付物）。现在"完成了几步"
# 是一等信号，不再让人从摘要里猜。
TERMINAL_STATUSES = ("done", "partial", "failed", "cancelled", "timeout", "interrupted")


@dataclass
class Run:
    id: str
    kind: str = "manual"
    parent_run_id: str = ""
    window_id: str = ""
    group_id: str = ""
    project_id: str = ""
    job_id: str = ""
    status: str = "queued"
    phase: str = ""
    step_no: int = 0
    steps_total: int = 0
    prompt_chars: int = 0
    recap_chars: int = 0
    iterations: int = 0
    pause_count: int = 0
    paused_seconds: float = 0.0
    cancel_requested: int = 0
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    error_code: str = ""
    summary: str = ""
    #: v0.10.40：**原始任务文本**（落库）。异常/重启导致的停止之后，管理员靠它
    #: "唤醒"未完成的任务——以前 retry 从内存 `_manual_last` 取，重启即丢。
    prompt: str = ""
    #: 只活在内存里：让"取消"能真的打断正在跑的那件事
    task: Any = field(default=None, repr=False, compare=False)
    #: 展示用（不入库）：工位名 / 群组名
    window_name: str = ""
    group_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "parent_run_id": self.parent_run_id,
            "window_id": self.window_id, "window_name": self.window_name,
            "group_id": self.group_id, "group_name": self.group_name,
            "project_id": self.project_id, "job_id": self.job_id,
            "status": self.status, "phase": self.phase,
            "step_no": self.step_no, "steps_total": self.steps_total,
            "prompt_chars": self.prompt_chars, "recap_chars": self.recap_chars,
            "iterations": self.iterations,
            "pause_count": self.pause_count,
            "paused_seconds": round(self.paused_seconds, 1),
            "cancel_requested": bool(self.cancel_requested),
            "started_at": self.started_at, "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "error_code": self.error_code, "summary": self.summary,
            # v0.10.41：DTO 里**截断**任务原文（DB 存全文供 run_retry 用）。
            # 不截断的话 /api/runs（50 条）与每个 SSE run.* 事件都会带上整段
            # 任务文本——15 秒轮询一次，白白扛几百 KB。
            "prompt": (self.prompt or "")[:500],
            "live": self.status in LIVE_STATUSES,
        }


_DB_COLUMNS = ("id", "kind", "parent_run_id", "window_id", "group_id", "project_id",
               "job_id", "status", "phase", "step_no", "steps_total", "prompt_chars",
               "recap_chars", "iterations", "pause_count", "paused_seconds",
               "cancel_requested", "started_at", "updated_at", "finished_at",
               "error_code", "summary", "prompt")


class RunRegistry:
    """运行台账（L2）。线程/协程内共享，写库失败**不影响运行本身**。"""

    def __init__(self, db: Any = None, bus: Any = None, *, on_change: Any = None):
        self.db = db
        self.bus = bus
        self.on_change = on_change          # 可选：给测试/前端桥用
        self._active: dict[str, Run] = {}

    # ------------------------------------------------------------------ 写
    def start(self, *, kind: str = "manual", window_id: str = "", group_id: str = "",
              project_id: str = "", job_id: str = "", parent_run_id: str = "",
              phase: str = "", steps_total: int = 0, prompt_chars: int = 0,
              window_name: str = "", group_name: str = "", status: str = "running",
              run_id: str = "", prompt: str = "") -> Run:
        run = Run(id=run_id or f"r-{new_id()[:8]}", kind=kind, window_id=window_id,
                  group_id=group_id, project_id=project_id, job_id=job_id,
                  parent_run_id=parent_run_id, phase=phase, steps_total=steps_total,
                  # v0.10.40：没显式给 prompt_chars 时按 prompt 实际长度记
                  prompt_chars=prompt_chars or len(prompt or ""),
                  window_name=window_name,
                  group_name=group_name, status=status, prompt=prompt or "",
                  started_at=_now_iso(), updated_at=_now_iso())
        self._active[run.id] = run
        self._persist(run)
        self._emit("run.started", run)
        logger.info("run start %s kind=%s window=%s group=%s status=%s",
                    run.id, kind, window_id or "-", group_id or "-", status)
        return run

    def update(self, run_id: str, **fields: Any) -> Run | None:
        run = self._active.get(run_id)
        if run is None:
            run = self._load(run_id)
            if run is None:
                return None
            self._active[run_id] = run
        for k, v in fields.items():
            if hasattr(run, k) and k not in ("id", "task"):
                setattr(run, k, v)
        run.updated_at = _now_iso()
        self._persist(run)
        self._emit("run.updated", run)
        return run

    def finish(self, run_id: str, status: str, summary: str = "",
               error_code: str = "") -> Run | None:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"不是终态：{status}")
        run = self.update(run_id, status=status, summary=(summary or "")[:2000],
                          error_code=error_code or "", finished_at=_now_iso())
        if run is not None:
            self._active.pop(run_id, None)
            self._emit("run.finished", run)
            logger.info("run finish %s status=%s %s", run.id, status,
                        (summary or "")[:120])
        return run

    def attach_task(self, run_id: str, task: Any) -> None:
        """绑定 asyncio 任务 —— 取消时要真的把它打断（而不只是置个标志）。"""
        run = self._active.get(run_id)
        if run is not None:
            run.task = task

    def cancel(self, run_id: str, *, cascade: bool = True) -> dict[str, Any]:
        """取消一次运行（幂等）。返回给人看的结果。

        * 排队中（还没起跑）→ 直接终态 `cancelled`；
        * 在跑 → 置 `cancel_requested` + `task.cancel()`（asyncio 取消会贯穿进
          httpx / 工具等待），状态先 `cancelling`，由运行方收尾成 `cancelled`；
        * `cascade=True` 连子运行一起取消（群组 → 成员）。
        """
        run = self._active.get(run_id) or self._load(run_id)
        if run is None:
            return {"ok": False, "reason": f"没有这个运行：{run_id}"}
        if run.status in TERMINAL_STATUSES:
            return {"ok": True, "already": True, "status": run.status, "run_id": run.id}
        children = [c for c in self._active.values()
                    if c.parent_run_id == run.id and c.status in LIVE_STATUSES]
        cancelled_children: list[str] = []
        if cascade and children:
            for c in children:
                res = self.cancel(c.id, cascade=True)
                if res.get("ok") and not res.get("already"):
                    cancelled_children.append(c.id)
        run.cancel_requested = 1
        if run.task is not None and not getattr(run.task, "done", lambda: True)():
            run.status = "cancelling"
            self.update(run.id, status="cancelling", cancel_requested=1)
            try:
                run.task.cancel()
            except Exception:  # noqa: BLE001 — 取消失败也要给人一个明确答复
                logger.warning("cancel task failed for %s", run.id, exc_info=True)
        else:
            if run.id in self._active:
                self.finish(run.id, "cancelled", summary="已取消（尚未开始执行）")
            else:
                self.update(run.id, status="cancelled", finished_at=_now_iso(),
                            summary="已取消（进程重启后清理）")
        return {"ok": True, "run_id": run.id, "status": run.status,
                "children_cancelled": cancelled_children}

    def note_pause(self, run_id: str, seconds: float) -> None:
        run = self._active.get(run_id)
        if run is None:
            return
        self.update(run_id, status="paused", pause_count=run.pause_count + 1,
                    paused_seconds=run.paused_seconds + max(0.0, float(seconds)))

    def resume(self, run_id: str) -> None:
        run = self._active.get(run_id)
        if run is not None and run.status == "paused":
            self.update(run_id, status="running")

    # ------------------------------------------------------------------ 读
    def get(self, run_id: str) -> Run | None:
        return self._active.get(run_id) or self._load(run_id)

    def active(self) -> list[Run]:
        return sorted(self._active.values(), key=lambda r: r.started_at)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        names = self._name_maps()
        for r in self.active():
            rows.append(r.as_dict())
            seen.add(r.id)
        if self.db is not None:
            try:
                cur = self.db._conn.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?",
                    (max(1, int(limit)),))
                for row in cur.fetchall():
                    d = {k: row[k] for k in row.keys() if k in _DB_COLUMNS}
                    if d["id"] in seen:
                        continue
                    d["live"] = d.get("status") in LIVE_STATUSES
                    d["cancel_requested"] = bool(d.get("cancel_requested"))
                    rows.append(d)
            except Exception:  # noqa: BLE001 — 历史读不到不影响活跃列表
                logger.debug("runs history read failed", exc_info=True)
        for d in rows:
            # 名字不入库（会随改名变旧），读的时候补上
            d.setdefault("window_name", names[0].get(d.get("window_id", ""), ""))
            d.setdefault("group_name", names[1].get(d.get("group_id", ""), ""))
        return rows[: max(1, int(limit))]

    def recent_for_window(self, window_id: str, limit: int = 3) -> list[dict[str, Any]]:
        """某个工位**最近几次**运行（v0.10.41）。

        为什么不复用 `recent(30)` 再筛：忙碌时该工位的运行可能已被全局更新的
        记录挤出前 30 条，window_status 就会显示"没有最近运行"（假阴性）。
        """
        if not window_id or self.db is None:
            return []
        rows: list[dict[str, Any]] = []
        try:
            cur = self.db._conn.execute(
                "SELECT * FROM runs WHERE window_id=? "
                "ORDER BY started_at DESC, id DESC LIMIT ?",
                (window_id, max(1, int(limit))))
            for row in cur.fetchall():
                d = {k: row[k] for k in row.keys() if k in _DB_COLUMNS}
                d["prompt"] = (d.get("prompt") or "")[:500]
                d["live"] = d.get("status") in LIVE_STATUSES
                d["cancel_requested"] = bool(d.get("cancel_requested"))
                rows.append(d)
        except Exception:  # noqa: BLE001 — 台账读失败不影响调用方
            logger.debug("recent_for_window failed", exc_info=True)
        return rows

    def _name_maps(self) -> tuple[dict[str, str], dict[str, str]]:
        win: dict[str, str] = {}
        grp: dict[str, str] = {}
        if self.db is None:
            return win, grp
        try:
            win = {w.id: w.name for w in self.db.list_windows()}
        except Exception:  # noqa: BLE001
            pass
        try:
            grp = {g.id: g.name for g in self.db.list_groups()}
        except Exception:  # noqa: BLE001
            pass
        return win, grp

    def summary(self) -> dict[str, Any]:
        live = self.active()
        return {
            "running": sum(1 for r in live if r.status == "running"),
            "queued": sum(1 for r in live if r.status == "queued"),
            "paused": sum(1 for r in live if r.status == "paused"),
            "cancelling": sum(1 for r in live if r.status == "cancelling"),
            "total_live": len(live),
        }

    # ------------------------------------------------------------- 启动清理
    def mark_orphans_interrupted(self) -> int:
        """启动时把上个进程遗留的活着状态标成 `interrupted`。

        v0.10.37：没有这一步，重启（部署/自升级）会在界面上留下**永远"运行中"的僵尸行**，
        而任务其实早没了 —— 这本身就是"看不见真相"的一部分。返回清理条数。
        """
        n = 0
        if self.db is None:
            return n
        try:
            cur = self.db._conn.execute(
                "SELECT id FROM runs WHERE status IN ({})".format(
                    ",".join("?" * len(LIVE_STATUSES))), LIVE_STATUSES)
            ids = [r[0] for r in cur.fetchall()]
            for rid in ids:
                self.db._conn.execute(
                    "UPDATE runs SET status='interrupted', finished_at=?, updated_at=?, "
                    "summary=CASE WHEN summary='' THEN ? ELSE summary END WHERE id=?",
                    (_now_iso(), _now_iso(),
                     "进程重启导致中断（任务未完成；可用 /retry 重放）", rid))
                n += 1
            self.db._conn.commit()
            # 内存里也清掉（不然同一进程内再读会看到"还活着"的假象）
            for rid in ids:
                self._active.pop(rid, None)
        except Exception:  # noqa: BLE001 — 清理失败不能拦住启动
            logger.warning("mark_orphans_interrupted failed", exc_info=True)
        if n:
            logger.warning("启动清理：%d 条运行被标记为 interrupted", n)
        return n

    # ------------------------------------------------------------------ 内部
    def _persist(self, run: Run) -> None:
        if self.db is None:
            return
        vals = [getattr(run, c) for c in _DB_COLUMNS]
        try:
            self.db._conn.execute(
                "INSERT OR REPLACE INTO runs({}) VALUES({})".format(
                    ",".join(_DB_COLUMNS), ",".join("?" * len(_DB_COLUMNS))), vals)
            self.db._conn.commit()
        except Exception:  # noqa: BLE001 — 台账写失败不能影响运行
            logger.warning("run persist failed (%s)", run.id, exc_info=True)

    def _load(self, run_id: str) -> Run | None:
        if self.db is None:
            return None
        try:
            row = self.db._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        except Exception:  # noqa: BLE001
            return None
        if row is None:
            return None
        run = Run(id=run_id)
        for c in _DB_COLUMNS:
            if c in row.keys():
                setattr(run, c, row[c])
        return run

    def _emit(self, etype: str, run: Run) -> None:
        payload = run.as_dict()
        if self.bus is not None:
            try:
                from ..events.bus import Event
                self.bus.emit(Event(type=etype, payload=payload))
            except Exception:  # noqa: BLE001 — 推送失败不影响运行
                logger.debug("run event emit failed", exc_info=True)
        if self.on_change is not None:
            try:
                self.on_change(etype, payload)
            except Exception:  # noqa: BLE001
                logger.debug("run on_change failed", exc_info=True)

    #: 兼容：把 `asyncio.CancelledError` 转成"取消"的终态（供运行方调用）
    @staticmethod
    def cancellation_status(exc: BaseException) -> str:
        return "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
