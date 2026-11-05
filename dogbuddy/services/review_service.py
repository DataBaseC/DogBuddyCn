# DogBuddyCn — AI review shadow service (0.24.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Run.finished → ReviewRequest → optional reviewer run.

Shadow mode never blocks delivery: the WorkItem keeps its status and only
`review_status` changes. Missing reviewer / parse errors become
`waiting_human` / `error`, never an implicit PASS.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..events.bus import Event

logger = logging.getLogger("dogbuddy.services.review")


def parse_verdict(text: str) -> dict[str, Any] | None:
    """Extract the first valid JSON object with verdict PASS/FAIL."""
    raw = str(text or "")
    dec = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(raw[i:])
        except (ValueError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        verdict = str(obj.get("verdict") or "").upper()
        if verdict not in ("PASS", "FAIL"):
            continue
        findings = obj.get("findings") if isinstance(obj.get("findings"), list) else []
        if verdict == "FAIL" and not findings:
            # FAIL without evidence is not actionable; treat as malformed.
            continue
        obj["verdict"] = verdict
        obj["findings"] = findings
        return obj
    return None


class ReviewService:
    def __init__(self, store: Any, windows: Any, bus: Any,
                 mode: str = "off", window_id: str = "",
                 reviewer_model: str = "", db: Any = None):
        self.store = store
        self.windows = windows
        self.bus = bus
        self.db = db
        self.mode = str(mode or "off").lower()
        self.window_id = str(window_id or "")
        self.reviewer_model = str(reviewer_model or "")
        self._subscribed = False

    @property
    def enabled(self) -> bool:
        return self.mode in ("shadow", "enforce") and self.store is not None

    def start(self) -> None:
        if self.enabled and self.bus is not None and not self._subscribed:
            self.bus.subscribe("run.finished", self.on_run_finished)
            self._subscribed = True

    async def on_run_finished(self, event: Event) -> None:
        if not self.enabled:
            return
        p = dict(event.payload or {})
        if str(p.get("kind") or "") == "review":
            self._finish_review(p)
            return
        self._maybe_request(p)

    def _maybe_request(self, p: dict[str, Any]) -> None:
        status = str(p.get("status") or "")
        wid = str(p.get("work_item_id") or "")
        if status not in ("done", "partial") or not wid:
            return
        if str(p.get("parent_run_id") or ""):
            return
        if str(p.get("kind") or "") in ("review", "group_step"):
            return
        wi = self.store.get_work_item(wid)
        if not wi or not int(wi.get("review_required") or 0):
            return
        run_id = str(p.get("id") or "")
        req, created = self.store.create_review_request(
            work_item_id=wid, run_id=run_id,
            reviewer_window_id=self.window_id)
        if not created:
            return
        self.store.update_work_item(wid, review_status="queued")
        self._audit(f"review requested: {req['id']} run={run_id} wi={wid}")
        self._emit("review.requested", {"review_id": req["id"], "work_item_id": wid,
                                        "run_id": run_id})
        if not self.window_id:
            self._waiting(req["id"], "no reviewer window configured")
            return
        reviewer = self.windows.get(self.window_id) if self.windows else None
        if reviewer is None:
            self._waiting(req["id"], f"reviewer window missing: {self.window_id}")
            return
        prompt = self._review_prompt(wi, p)
        try:
            handle = self.windows.dispatch_run(
                window_id=reviewer.id, prompt=prompt, origin="review",
                kind="prompt", urgent=True, sender="reviewer",
                work_item_id=wid)
        except Exception as e:  # noqa: BLE001 — review failure cannot block delivery
            logger.warning("review dispatch failed", exc_info=True)
            self.store.update_review_request(
                req["id"], status="error", summary=f"dispatch failed: {e}")
            self.store.update_work_item(wid, review_status="error")
            return
        self.store.update_review_request(
            req["id"], status="running", reviewer_run_id=handle.run_id,
            reviewer_model=self.reviewer_model)
        self.store.update_work_item(wid, review_status="running")
        self._audit(f"review running: {req['id']} reviewer_run={handle.run_id}")

    def _finish_review(self, p: dict[str, Any]) -> None:
        run_id = str(p.get("id") or "")
        req = self.store.get_review_by_run(run_id)
        if req is None:
            return
        parsed = parse_verdict(str(p.get("summary") or ""))
        if parsed is None:
            self.store.update_review_request(
                req["id"], status="error", verdict="",
                summary=str(p.get("summary") or "")[:2000],
                finished_at=str(p.get("finished_at") or ""))
            self.store.update_work_item(req["work_item_id"], review_status="error")
            self._audit(f"review parse error: {req['id']} run={run_id}")
            return
        verdict = parsed["verdict"]
        status = "passed" if verdict == "PASS" else "failed"
        findings = parsed.get("findings") or []
        self.store.update_review_request(
            req["id"], status=status, verdict=verdict,
            summary=str(parsed.get("summary") or "")[:2000],
            findings_json=json.dumps(findings, ensure_ascii=False)[:50_000],
            finished_at=str(p.get("finished_at") or ""))
        self.store.add_review_findings(req["id"], findings)
        # Shadow: only review_status changes; delivery status stays untouched.
        self.store.update_work_item(req["work_item_id"], review_status=status)
        self._audit(f"review {status}: {req['id']} run={run_id}")
        self._emit("review.finished", {"review_id": req["id"], "verdict": verdict,
                                       "work_item_id": req["work_item_id"]})

    def _waiting(self, review_id: str, reason: str) -> None:
        self.store.update_review_request(
            review_id, status="waiting_human", summary=reason)
        req = self.store.get_review_request(review_id)
        if req:
            self.store.update_work_item(req["work_item_id"], review_status="waiting_human")
        self._audit(f"review waiting human: {review_id}: {reason}")

    def _review_prompt(self, wi: dict[str, Any], p: dict[str, Any]) -> str:
        return (
            "你是独立代码审查员，只读，不要改任何文件。\n"
            f"WorkItem: {wi.get('id')}\n标题: {wi.get('title')}\n"
            f"任务: {wi.get('description')}\n"
            f"执行摘要: {str(p.get('summary') or '')[:2000]}\n\n"
            "请检查工作区实际改动/测试结果（不要只信执行者总结），"
            "只输出一个 JSON：\n"
            '{"verdict":"PASS|FAIL","summary":"...","tests":[{"command":"...",'
            '"exit_code":0}],"findings":[{"severity":"blocking|nit","file":"...",'
            '"line":1,"issue":"...","why":"..."}]}\n'
            "FAIL 至少给一条 blocking finding；无法验证时请说明并 FAIL。")

    def _audit(self, detail: str) -> None:
        try:
            if self.db is not None:
                self.db.audit("system", "review", detail[:1000])
        except Exception:  # noqa: BLE001
            logger.debug("review audit failed", exc_info=True)

    def _emit(self, topic: str, payload: dict[str, Any]) -> None:
        if self.bus is None:
            return
        try:
            self.bus.emit(Event(type=topic, payload=payload))
        except Exception:  # noqa: BLE001
            logger.debug("review event emit failed", exc_info=True)

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode, "window_id": self.window_id,
                "requests": (self.store.count_reviews()
                             if self.store is not None else 0),
                "waiting": (self.store.count_reviews("waiting_human")
                            if self.store is not None else 0)}
