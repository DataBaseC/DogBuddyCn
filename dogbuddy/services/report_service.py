# DogBuddyCn — report service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""ReportService: task results/employee questions → admin discretion → notify channels.

Strategy enum silent/brief/detailed (default brief). The admin LLM merges and
decides what actually reaches the user ("看管理员心情"), with a deterministic
template fallback when the LLM is unavailable. Critical events (auto-pause,
confirm requests) always surface.

Delivery is channel-based (L4 ``notify`` package): the report goes to the
channel named in ``event.payload["report_channel"]`` when set (falling back to
all channels if that name does not exist), otherwise to every configured
channel. A failing channel never blocks the others.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Callable

from ..events.bus import CONFIRM_REQUEST, JOB_COMPLETED, JOB_FAILED, RATE_LIMIT_STATUS, WORKER_QUESTION, Event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..notify.base import Notifier

logger = logging.getLogger("dogbuddy.services.report")

_DISCRETION_PROMPT = """You are the admin agent deciding whether to push a task report to the user.
Event:
{event}

Report strategy for this item: {strategy} (silent = never push, brief = one or two sentences, \
detailed = full detail). You may merge context if similar items arrived recently, and you may \
downgrade detail if the event is trivial. Answer ONLY JSON: {{"forward": true|false, "text": "..."}} \
(text in the user's language, Chinese by default; empty text when forward=false)."""


class _CallbackNotifier:
    """Deprecated shim: wraps a legacy ``push(text)`` callback as a channel.

    Keeps old call sites (``ReportService(..., push=cb)``) working while the
    channel list becomes the primary delivery interface. Do not use in new
    code — pass ``notifiers=build_notifiers(...)`` instead.
    """

    def __init__(self, callback: Callable[[str], Any]) -> None:
        self.name = "push"
        self._callback = callback

    async def send(self, text: str) -> None:
        await self._callback(text)


class ReportService:
    def __init__(
        self,
        bus: Any,
        notifiers: list["Notifier"] | None = None,
        llm_factory: Callable[[], Any] | None = None,
        default_strategy: str = "brief",
        *,
        push: Callable[[str], Any] | None = None,  # deprecated: legacy single-target callback
    ):
        self.bus = bus
        self.notifiers: list[Notifier] = list(notifiers or [])
        if push is not None:  # backward compat for pre-channel wiring (main.py)
            self.notifiers.append(_CallbackNotifier(push))
        self.llm_factory = llm_factory
        self.default_strategy = default_strategy
        self._recent: list[str] = []  # recent pushed headlines (merge context)
        # T2.2 notification minimum set: merged decision asks + rare alerts
        self.decisions = None  # set below / injected (DecisionService)
        self._ask_debounce: dict[str, Any] = {}
        self._fail_streak: dict[str, int] = {}
        self._ask_delay = 20.0       # merge questions arriving within this window
        self.alert_fail_streak = 3   # consecutive failures before an alert

        bus.subscribe(JOB_COMPLETED, self._on_job_completed)
        bus.subscribe(JOB_FAILED, self._on_job_failed)
        bus.subscribe(WORKER_QUESTION, self._on_worker_question)
        bus.subscribe(CONFIRM_REQUEST, self._on_confirm_request)
        bus.subscribe(RATE_LIMIT_STATUS, self._on_rate_limit)
        try:
            from .decisions import DECISION_CREATED
            bus.subscribe(DECISION_CREATED, self._on_decision_created)
        except ImportError:  # pragma: no cover — standalone wiring in tests
            pass

    # -------------------------------------------------------------- handlers
    async def _on_job_completed(self, event: Event) -> None:
        wid = str(event.payload.get("window_id") or event.payload.get("job_id") or "")
        self._fail_streak.pop(wid, None)  # success breaks the failure streak
        strategy = event.payload.get("report_strategy") or self.default_strategy
        await self._deliver(event, strategy, critical=False)

    async def _on_job_failed(self, event: Event) -> None:
        wid = str(event.payload.get("window_id") or event.payload.get("job_id") or "")
        self._fail_streak[wid] = self._fail_streak.get(wid, 0) + 1
        streak = self._fail_streak[wid]
        critical = bool(event.payload.get("auto_paused")) or streak >= self.alert_fail_streak
        # T2.2: ordinary single failures stay in the UI — only real trouble pushes
        if not critical:
            logger.info("job failed (streak %d, no alert yet): %s", streak, event.payload)
            return
        self._fail_streak[wid] = 0
        strategy = "detailed"
        await self._deliver(event, strategy, critical=True)

    async def _on_decision_created(self, event: Event) -> None:
        """T2.2: merge rapid-fire questions from one window into one push.

        An agent asking 3 questions in a row must not ring the owner's phone
        3 times — debounce per window, then push the whole open set at once.
        """
        import asyncio
        wid = str(event.payload.get("window_id") or "")
        old = self._ask_debounce.pop(wid, None)
        if old is not None:
            old.cancel()
        self._ask_debounce[wid] = asyncio.get_event_loop().create_task(
            self._push_pending_asks(wid))

    async def _push_pending_asks(self, window_id: str) -> None:
        import asyncio
        try:
            await asyncio.sleep(self._ask_delay)
        except asyncio.CancelledError:
            return  # a newer question reset the timer
        self._ask_debounce.pop(window_id, None)
        items: list = []
        if self.decisions is not None:
            try:
                items = [d for d in self.decisions.list(status="pending", limit=50)
                         if d.window_id == window_id]
            except Exception:  # noqa: BLE001 — notification never crashes a run
                logger.exception("pending decision lookup failed")
        if not items:
            return
        w_name = window_id
        lines = [f"【需要你决策】工位有 {len(items)} 个问题待决："]
        for i, d in enumerate(items[:5], 1):
            lines.append(f"{i}. {d.question[:120]}")
        if len(items) > 5:
            lines.append(f"…共 {len(items)} 条")
        lines.append("（打开控制台「待决事项」回答，或等超时自动继续）")
        text = "\n".join(lines)
        self._remember(text)
        for notifier in self._targets(None):
            try:
                await notifier.send(text)
            except Exception:  # noqa: BLE001
                logger.exception("notifier %s failed", getattr(notifier, "name", "?"))

    async def _on_worker_question(self, event: Event) -> None:
        await self._deliver(event, "detailed", critical=True)

    async def _on_confirm_request(self, event: Event) -> None:
        await self._deliver(event, "detailed", critical=True)

    async def _on_rate_limit(self, event: Event) -> None:
        # visible in UI status; no push spam
        logger.info("rate limit waiting: %s", event.payload)

    # -------------------------------------------------------------- delivery
    async def _deliver(self, event: Event, strategy: str, critical: bool) -> None:
        if strategy == "silent" and not critical:
            return
        text = await self._discretion(event, strategy)
        if not text:
            return
        self._remember(text)
        for notifier in self._targets(event):
            try:
                await notifier.send(text)
            except Exception:  # noqa: BLE001 — one channel failure must not block the rest
                logger.exception("notifier %s send failed", getattr(notifier, "name", "?"))

    def _targets(self, event: Event) -> list["Notifier"]:
        """Channels for this event: `payload.report_channel` name, or all."""
        want = str(event.payload.get("report_channel") or "").strip()
        if not want:
            return list(self.notifiers)
        matched = [n for n in self.notifiers if getattr(n, "name", "") == want]
        if not matched:
            logger.warning("report channel %r not found; delivering to all channels", want)
            return list(self.notifiers)
        return matched

    def _remember(self, text: str) -> None:
        self._recent.append(text[:100])
        self._recent = self._recent[-10:]

    async def _discretion(self, event: Event, strategy: str) -> str:
        """Admin LLM decides forward/text; falls back to template."""
        if self.llm_factory is not None:
            try:
                llm = self.llm_factory()
                prompt = _DISCRETION_PROMPT.format(
                    event=json.dumps(event.payload, ensure_ascii=False)[:4000], strategy=strategy)
                resp = await llm.chat([{"role": "user", "content": prompt}])
                m = re.search(r"\{.*\}", (resp.content or ""), re.DOTALL)
                if m:
                    data = json.loads(m.group(0))
                    if data.get("forward") is False:
                        return ""
                    text = str(data.get("text", "")).strip()
                    if text:
                        return text
            except Exception:  # noqa: BLE001 — fall back to template
                logger.warning("report discretion LLM failed; using template", exc_info=True)
        return self._template(event, strategy)

    @staticmethod
    def _template(event: Event, strategy: str) -> str:
        p = event.payload
        kind = event.type
        name = p.get("name") or p.get("window_name") or p.get("window_id", "?")
        if kind == WORKER_QUESTION:
            return f"❓ 员工 {name} 提问：{p.get('question', '')}"
        if kind == CONFIRM_REQUEST:
            return f"⚠️ 需要确认（{name}）：{p.get('reason', '')}。回复“确认放行”可继续。"
        if kind == JOB_FAILED:
            tail = f"（已连续失败 {p.get('fail_count')} 次，任务自动暂停）" if p.get("auto_paused") else ""
            return f"❌ 任务「{name}」失败{tail}：{str(p.get('summary', ''))[:200]}"
        if kind == JOB_COMPLETED:
            if strategy == "brief":
                return f"✅ 任务「{name}」完成：{str(p.get('summary', ''))[:120]}"
            return f"✅ 任务「{name}」完成：\n{str(p.get('summary', ''))[:1500]}"
        return f"ℹ️ {kind}: {str(p)[:200]}"
