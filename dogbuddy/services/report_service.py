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
        # T9.4 指挥链（审查报告 P1-1）：注入后，员工的结果/提问/失败会**先给主管**
        # （逐级收敛），没有主管时才走原来的直连推送（v1 零回归）。
        self.supervision = None
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
        # P1-10（审查报告）：迭代**停摆**是火警。初版 `iteration.stopped` 全仓库只有
        # `web/sse.py` 的 `subscribe("*")` 一个订阅者——即"当时开着浏览器标签的人"，
        # 没走 notify。而设计是"用户长时间不在场、数据自取"，于是项目停了没人知道、
        # 也不知道停因。ROADMAP T8.6 写的是"发事件并写明原因"——**发事件 ≠ 触达人**。
        try:
            from .iteration_executor import ITERATION_STOPPED
            bus.subscribe(ITERATION_STOPPED, self._on_iteration_stopped)
        except ImportError:  # pragma: no cover
            pass

    # -------------------------------------------------------------- handlers
    async def _on_job_completed(self, event: Event) -> None:
        wid = str(event.payload.get("window_id") or event.payload.get("job_id") or "")
        self._fail_streak.pop(wid, None)  # success breaks the failure streak
        # T9.4（审查报告 P1-1）：有主管 → 逐级收敛，先给主管，不再直达用户
        if self._route_up(event, wid):
            return
        strategy = event.payload.get("report_strategy") or self.default_strategy
        await self._deliver(event, strategy, critical=False)

    async def _on_job_failed(self, event: Event) -> None:
        wid = str(event.payload.get("window_id") or event.payload.get("job_id") or "")
        self._fail_streak[wid] = self._fail_streak.get(wid, 0) + 1
        streak = self._fail_streak[wid]
        critical = bool(event.payload.get("auto_paused")) or streak >= self.alert_fail_streak
        # 火警（自动暂停 / 连败到阈值）必须触达人——不因逐级收敛而静默
        if critical:
            self._fail_streak[wid] = 0
            await self._deliver(event, "detailed", critical=True)
            return
        # 普通失败：有主管就交给主管（他要能重派/兜底）；没主管才闭嘴（原行为）
        if self._route_up(event, wid):
            return
        logger.info("job failed (streak %d, no alert yet): %s", streak, event.payload)

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
        wid = str(event.payload.get("window_id") or "")
        if self._route_up(event, wid):
            return
        await self._deliver(event, "detailed", critical=True)

    # ------------------------------------------------- 逐级收敛（T9.4 / P1-1）
    def _hierarchical(self, event: Event) -> bool:
        """这个事件类型是否需要逐级路由（`HIERARCHICAL_EVENTS` 是权威口径）。"""
        try:
            from .supervision_service import HIERARCHICAL_EVENTS
        except ImportError:  # pragma: no cover
            return False
        return event.type in HIERARCHICAL_EVENTS

    def _route_up(self, event: Event, window_id: str) -> bool:
        """把事件交给指挥链。True = 已交给主管，调用方**不要**再直连推送。

        这是 `worker.report` / `supervisor.digest` 在生产路径上的生产者
        ——初版这两个事件**没有任何投递者**，处理器写得对却永远不会被调用，
        于是"逐级收敛"从未发生（审查报告 P1-1）。
        """
        sup = self.supervision
        if sup is None or not window_id or not self._hierarchical(event):
            return False
        try:
            return bool(sup.route_report(window_id, self._raw_text(event),
                                         level="warn" if event.type == JOB_FAILED else "info"))
        except Exception:  # noqa: BLE001 — 指挥链出问题不能吞掉通知本身
            logger.exception("hierarchical routing failed; falling back to direct push")
            return False

    @staticmethod
    def _raw_text(event: Event) -> str:
        """给主管看的**原始**信息——不做"看管理员心情"的裁量。

        裁量是管理员层的事：员工先把结果给主管，主管合并后才是给管理员的汇总。
        """
        p = event.payload
        name = p.get("name") or p.get("window_name") or p.get("window_id", "?")
        if event.type == WORKER_QUESTION:
            return f"员工提问：{p.get('question', '')}"
        if event.type == JOB_FAILED:
            return f"任务「{name}」失败：{str(p.get('summary', ''))[:500]}"
        if event.type == JOB_COMPLETED:
            return f"任务「{name}」完成：{str(p.get('summary', ''))[:500]}"
        return f"{event.type}: {str(p)[:300]}"

    async def _on_confirm_request(self, event: Event) -> None:
        await self._deliver(event, "detailed", critical=True)

    async def _on_iteration_stopped(self, event: Event) -> None:
        """迭代停摆 → 必须触达人（P1-10）。

        这是"火警"：迭代因连续无进展 / 到上限 / 连续崩溃而停下，如果不通知，
        用户按设计"长时间不在场、数据自取"就只会看到一个静默停住的项目。
        停因必须一起报出去（否则用户不知道该改什么）。
        """
        p = event.payload or {}
        text = (f"🛑 项目迭代已停止（第 {p.get('round', '?')} 轮）\n"
                f"原因：{p.get('reason') or '(未说明)'}\n"
                f"目标：{str(p.get('goal') or '')[:200]}\n"
                f"最后进展：{str(p.get('summary') or '')[:400]}")
        self._remember(text)
        for notifier in self._targets(event):
            try:
                await notifier.send(text)
            except Exception:  # noqa: BLE001 — 一个通道失败不能挡住其余
                logger.exception("notifier %s send failed", getattr(notifier, "name", "?"))

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
