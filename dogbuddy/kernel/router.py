# DogBuddyCn — Kernel Router (Fast Path, v0.23.1)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Classify Web messages and (only in explicit/auto) dispatch explicit commands.

`shadow` records decisions only. `explicit` handles `/task`, `/continue`,
`/cancel`, `/status`; ordinary chat still goes to the old AdminService path.
No Router LLM call in this version.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .domain import RouterDecision

logger = logging.getLogger("dogbuddy.kernel.router")


class KernelRouter:
    MODES = ("off", "shadow", "explicit", "auto")

    def __init__(self, store: Any, intake: Any = None, mode: str = "off",
                 windows: Any = None, route_conversations: list[str] | None = None,
                 llm_factory: Any = None, auto_confidence: float = 0.75):
        self.store = store
        self.intake = intake
        self.mode = str(mode or "off").lower()
        if self.mode not in self.MODES:
            self.mode = "off"
        self.windows = windows
        self.route_conversations = {str(x) for x in (route_conversations or [])}
        self.llm_factory = llm_factory
        self.auto_confidence = max(0.5, min(float(auto_confidence), 0.99))

    def enabled_for(self, conversation: str) -> bool:
        if self.mode == "off":
            return False
        if self.mode == "shadow":
            return True
        return (not self.route_conversations
                or str(conversation) in self.route_conversations)

    def route_web(self, *, conversation: str, text: str,
                  sender: str = "owner") -> dict[str, Any] | None:
        if not self.enabled_for(conversation) or self.store is None or self.intake is None:
            return None
        trigger = self.intake.accept_web(
            conversation=conversation, text=text, sender=sender)
        if not trigger:
            return None
        return self.route_trigger(trigger, text=text, sender=sender,
                                  conversation=conversation)

    def route_trigger(self, trigger: dict[str, Any], *, text: str,
                      sender: str = "owner", conversation: str = "") -> dict[str, Any]:
        trigger_id = str(trigger.get("id") or "")
        existing = self.store.get_router_decision(trigger_id)
        if existing is not None:
            return self._existing_result(existing)
        return self._apply_decision(
            trigger, self.decide(text=text, thread_id=str(trigger.get("thread_id") or ""),
                                 sender=sender),
            text=text, sender=sender)

    async def route_web_async(self, *, conversation: str, text: str,
                              sender: str = "owner") -> dict[str, Any] | None:
        """auto 模式：Fast Path 判为 chat 时，才允许调用一次 Router LLM。"""
        if self.mode != "auto":
            return self.route_web(conversation=conversation, text=text, sender=sender)
        if not self.enabled_for(conversation) or self.store is None or self.intake is None:
            return None
        trigger = self.intake.accept_web(
            conversation=conversation, text=text, sender=sender)
        if not trigger:
            return None
        trigger_id = str(trigger.get("id") or "")
        existing = self.store.get_router_decision(trigger_id)
        if existing is not None:
            return self._existing_result(existing)
        decision = self.decide(text=text, thread_id=str(trigger.get("thread_id") or ""),
                               sender=sender)
        if decision.action == "chat" and self.llm_factory is not None:
            try:
                decision = await self._classify(text, decision)
            except Exception:  # noqa: BLE001 — 分类失败退回聊天，绝不乱建任务
                logger.warning("router auto classify failed", exc_info=True)
                decision = RouterDecision(action="chat", reason="router llm failed")
        return self._apply_decision(trigger, decision, text=text, sender=sender)

    async def _classify(self, text: str, fallback: RouterDecision) -> RouterDecision:
        llm = self.llm_factory()
        prompt = (
            "你是任务路由器。只输出一个 JSON 对象，不要解释。\n"
            "action 只能是 create_task / resume_task / chat / ask。\n"
            "只有当用户明确要求做一项需要员工执行、会改文件或跑命令的任务时，"
            "才用 create_task；问问题、闲聊、让你解释，一律 chat。\n"
            "信息不足（目标/范围不清）用 ask。\n"
            '格式: {"action":"chat|create_task|resume_task|ask","confidence":0.0,'
            '"prompt":"完整任务目标","title":"短标题","reason":"一句话"}\n'
            f"用户消息：{text[:4000]}")
        resp = await llm.chat([{"role": "user", "content": prompt}])
        raw = str(getattr(resp, "content", "") or "")
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return fallback
        obj = json.loads(raw[start:end + 1])
        action = str(obj.get("action") or "chat")
        conf = float(obj.get("confidence") or 0)
        if action == "create_task":
            if conf < self.auto_confidence:
                return RouterDecision(action="ask", reason="low confidence task",
                                      prompt="你是想让我创建一个执行任务吗？请确认目标。",
                                      confidence=conf)
            goal = str(obj.get("prompt") or text).strip()
            return RouterDecision(action="create", reason=str(obj.get("reason") or "auto"),
                                  prompt=goal, title=str(obj.get("title") or goal[:80]),
                                  mandate_scope="workspace", confidence=conf)
        if action == "resume_task":
            # 自然语言恢复容易指错任务；第一版只认 /continue。
            return RouterDecision(action="ask", reason="resume needs explicit /continue",
                                  prompt="请用 /continue 指定要恢复的任务。", confidence=conf)
        if action == "ask":
            return RouterDecision(action="ask", reason=str(obj.get("reason") or "need info"),
                                  prompt=str(obj.get("prompt") or "请补充任务目标。"),
                                  confidence=conf)
        return RouterDecision(action="chat", reason=str(obj.get("reason") or "chat"),
                              confidence=conf)

    def _apply_decision(self, trigger: dict[str, Any], decision: RouterDecision,
                        *, text: str, sender: str) -> dict[str, Any]:
        trigger_id = str(trigger.get("id") or "")
        thread_id = str(trigger.get("thread_id") or "")
        result: dict[str, Any] = {"handled": False, "action": decision.action,
                                  "reason": decision.reason}
        if decision.action == "create":
            result = self._create_task(decision, thread_id, sender)
        elif decision.action == "resume":
            result = self._resume_task(decision, text, sender)
        elif decision.action == "cancel":
            result = self._cancel_task(text)
        elif decision.action == "status":
            result = self._status_task(text)
        elif decision.action == "ask":
            result = {"handled": self.mode in ("explicit", "auto"),
                      "action": "ask", "reason": decision.reason,
                      "reply": decision.prompt or "请补充任务信息。"}

        decision.work_item_id = str(result.get("work_item_id") or decision.work_item_id)
        decision.window_id = str(result.get("window_id") or decision.window_id)
        body = dict(decision.__dict__)
        body["result"] = result
        rec, _created = self.store.record_router_decision(
            trigger_id=trigger_id, thread_id=thread_id,
            action=result.get("action") or decision.action,
            decision=body, reason=result.get("reason") or decision.reason,
            work_item_id=decision.work_item_id, window_id=decision.window_id)
        result["id"] = rec.get("id")
        result["trigger_id"] = trigger_id
        return result

    def _existing_result(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            data = json.loads(row.get("decision_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        return {**result, "id": row.get("id"),
                "handled": bool(result.get("handled")),
                "action": row.get("action") or result.get("action", ""),
                "duplicate": True}

    def decide(self, *, text: str, thread_id: str, sender: str = "owner") -> RouterDecision:
        body = str(text or "").strip()
        if not body:
            return RouterDecision(action="ignore", reason="empty message")
        low = body.lower()
        if low in ("/cancel", "/stop") or low.startswith(("/cancel ", "/stop ")):
            return RouterDecision(action="cancel", reason="explicit cancel command")
        if low == "/status" or low.startswith(("/status ", "/runs")):
            return RouterDecision(action="status", reason="explicit status command")
        if body.startswith("/task"):
            prompt = body[len("/task"):].strip()
            if not prompt:
                return RouterDecision(action="ask", reason="/task without a goal",
                                      prompt="请补充任务目标。")
            return RouterDecision(action="create", reason="explicit /task command",
                                  prompt=prompt, title=prompt[:80],
                                  mandate_scope="workspace", confidence=1.0)
        if body.startswith("/continue"):
            extra = body[len("/continue"):].strip()
            # 只有显式 `wi-...` 才当 ID；否则 trailing 文本是追加说明。
            target = extra if extra.startswith(("wi-", "rd-")) else ""
            wi = (self.store.get_work_item(target) if target
                  else self.store.find_open_work_item(thread_id, include_shadow=False))
            if wi is None or str(wi.get("origin") or "") == "shadow":
                return RouterDecision(action="ask", reason="no open non-shadow WorkItem",
                                      prompt="没有可继续的任务。")
            return RouterDecision(action="resume", reason="explicit /continue command",
                                  work_item_id=str(wi.get("id") or ""),
                                  title=str(wi.get("title") or ""), confidence=1.0)
        # 自然语言暂不建任务；explicit 阶段也只认命令。
        return RouterDecision(action="chat", reason="default chat path", confidence=0.5)

    # ------------------------------------------------------------- execution
    def _create_task(self, d: RouterDecision, thread_id: str,
                     sender: str) -> dict[str, Any]:
        w = self._select_window()
        if w is None and self.mode != "shadow":
            return {"handled": True, "action": "ask",
                    "reason": "no worker window available",
                    "reply": "没有可用的员工工位，请先在控制台创建或选择一个工位。"}
        # Shadow 只记录候选：没有工位也保留 WorkItem，供后续接管前观察。
        wi = self.store.create_work_item(
            thread_id=thread_id, title=d.title or d.prompt[:80],
            description=d.prompt,
            project_id=str(getattr(w, "project_id", "") or "") if w else "",
            window_id=str(w.id) if w else "", created_by=sender, origin="router",
            review_required=True)
        wid = str(wi.get("id") or "")
        self.store.create_mandate(
            work_item_id=wid, scope=d.mandate_scope or "workspace",
            allowed_actions=["read", "write", "exec"], actor_id=sender)
        self.store.subscribe(work_item_id=wid, thread_id=thread_id, created_by=sender)
        if self.mode == "shadow":
            return {"handled": False, "action": "create", "work_item_id": wid,
                    "window_id": str(w.id) if w else "",
                    "reason": "shadow only"}
        try:
            handle = self.windows.dispatch_run(
                window_id=w.id, prompt=d.prompt, origin="router", kind="prompt",
                urgent=True, sender=sender, work_item_id=wid)
        except Exception as e:  # noqa: BLE001
            self.store.update_work_item(wid, status="failed")
            return {"handled": True, "action": "create", "work_item_id": wid,
                    "window_id": w.id, "reply": f"任务创建失败：{e}"}
        self.store.update_work_item(wid, status="working",
                                    current_run_id=handle.run_id)
        return {"handled": True, "action": "create", "work_item_id": wid,
                "window_id": w.id, "run_id": handle.run_id,
                "reply": f"已创建任务 {wid}，由「{w.name}」执行（Run {handle.run_id}）。"}

    def _resume_task(self, d: RouterDecision, text: str,
                     sender: str) -> dict[str, Any]:
        wi = (self.store.get_work_item(d.work_item_id) if d.work_item_id
              else None)
        if wi is None:
            return {"handled": self.mode in ("explicit", "auto"), "action": "ask",
                    "reply": "没有可继续的任务。"}
        wid = str(wi.get("id") or "")
        window_id = str(wi.get("window_id") or "")
        if not window_id or self.windows.get(window_id) is None:
            return {"handled": True, "action": "resume", "work_item_id": wid,
                    "reply": "任务原来绑定的工位已不存在，请重新创建任务。"}
        extra = str(text or "").split("/continue", 1)[-1].strip()
        prompt = f"继续任务：{wi.get('description') or wi.get('title') or ''}"
        if extra:
            prompt += f"\n\n用户追加：{extra}"
        if self.mode == "shadow":
            return {"handled": False, "action": "resume", "work_item_id": wid,
                    "window_id": window_id, "reason": "shadow only"}
        try:
            handle = self.windows.dispatch_run(
                window_id=window_id, prompt=prompt, origin="router", kind="prompt",
                urgent=True, sender=sender, work_item_id=wid)
        except Exception as e:  # noqa: BLE001
            return {"handled": True, "action": "resume", "work_item_id": wid,
                    "reply": f"续跑失败：{e}"}
        self.store.update_work_item(wid, status="working",
                                    current_run_id=handle.run_id)
        return {"handled": True, "action": "resume", "work_item_id": wid,
                "window_id": window_id, "run_id": handle.run_id,
                "reply": f"已继续任务 {wid}（Run {handle.run_id}）。"}

    def _cancel_task(self, text: str) -> dict[str, Any]:
        if self.windows is None:
            return {"handled": True, "action": "cancel", "reply": "运行台账不可用。"}
        parts = str(text or "").split(maxsplit=1)
        target = parts[1].strip() if len(parts) > 1 else ""
        run_id = target
        if target and self.store.get_work_item(target) is not None:
            run_id = str(self.store.get_work_item(target).get("current_run_id") or "")
        if not run_id:
            return {"handled": True, "action": "cancel", "reply": "没有指定可取消的 Run。"}
        out = self.windows.cancel_run(run_id)
        return {"handled": True, "action": "cancel", "run_id": run_id,
                "reply": "已取消。" if out.get("ok") else f"取消失败：{out.get('reason', '')}"}

    def _status_task(self, text: str) -> dict[str, Any]:
        parts = str(text or "").split(maxsplit=1)
        target = parts[1].strip() if len(parts) > 1 else ""
        wi = self.store.get_work_item(target) if target else None
        if wi is not None:
            run_id = str(wi.get("current_run_id") or "")
            runs = getattr(self.windows, "runs", None) if self.windows else None
            run = runs.get(run_id) if (runs is not None and run_id) else None
            status = getattr(run, "status", "") if run is not None else ""
            return {"handled": True, "action": "status", "work_item_id": target,
                    "run_id": run_id,
                    "reply": f"任务 {target}：{wi.get('status')}"
                             + (f"，Run {run_id}：{status}" if run_id else "，尚无 Run")}
        return {"handled": True, "action": "status",
                "reply": "用法：/status <work_item_id>。"}

    def _select_window(self) -> Any:
        if self.windows is None:
            return None
        try:
            candidates = [w for w in self.windows.list()
                          if w.role not in ("admin", "supervisor", "worker_chat")
                          and str(getattr(w, "status", "") or "") not in
                          ("stopped", "terminated", "disabled")]
        except Exception:  # noqa: BLE001
            return None
        if not candidates:
            return None

        def score(w: Any) -> tuple[int, int]:
            rt = getattr(self.windows, "_runtime", {}).get(w.id)
            pending = len(rt.queue.pending()) if rt is not None else 0
            role_rank = 0 if w.role == "worker_programmer" else 1
            return role_rank, pending
        return sorted(candidates, key=score)[0]

    def health(self) -> dict[str, Any]:
        return {"mode": self.mode,
                "decisions": (self.store.count_router_decisions()
                              if self.store is not None else 0),
                "shadow_only": self.mode == "shadow",
                "route_conversations": sorted(self.route_conversations)}
