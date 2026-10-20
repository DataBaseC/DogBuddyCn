# DogBuddyCn — agent tool loop (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""AgentLoop: context assembly → LLM → tool execution → repeat until done.

Platform-agnostic — the same loop serves IM chat, scheduled runs and panel
dispatch. All capabilities (LLM, tools, session, compactor) are injected.

Batch 6 加固（原来这里是"LLM 一次失败就 break"）：
  C1 瞬态续跑   — 超时/5xx/连接错误不再直接结束这一轮，按 N 次续跑；永久错误才停
  C2 死循环熔断 — 同一调用（name+args）连续失败 3 次就拒执行并注入"换策略"提示
  C4 上限降级   — 到 max_iterations 不再只是一句 "(stopped)"，而是产出
                  "做到哪了 / 卡在哪" 的总结，落进任务记录
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ... import errors
from ...errors import Disposition, ErrorClass
from ...llm.retry import RateLimitPaused
from ...tools.registry import ToolContext, ToolRegistry, ToolResult
from ...windows.context import ContextBuilder
from ...windows.session import WindowSession

logger = logging.getLogger("dogbuddy.agents.loop")

# 同一调用连续失败达到该次数 → 熔断（C2）
DEFAULT_GUARD_THRESHOLD = 3

#: 认可（会真的重放那次调用）的答复
_APPROVE_WORDS = ("批准", "同意", "放行", "可以", "approve", "approved", "allow", "yes", "y", "ok")
#: 拒绝
_DENY_WORDS = ("拒绝", "不同意", "不行", "deny", "denied", "reject", "rejected", "no", "n")


def _approval_verdict(answer: str) -> bool | None:
    """把一条审批答复判成 批准/拒绝/未决（None）。

    未决（含超时/取消的 `[timeout]`/`[cancelled]`）必须走 fail-closed——
    绝不因为"没答上来"就当成批准。
    """
    t = str(answer or "").strip().lower()
    if not t or t.startswith("["):
        return None
    if t.startswith(_DENY_WORDS) or t in _DENY_WORDS:
        return False
    if t.startswith(_APPROVE_WORDS) or t in _APPROVE_WORDS:
        return True
    return None


def _usage_ints(resp: Any) -> dict[str, int]:
    """LLM 响应里的 usage → 三个整数（缺项/非数字一律补 0）。

    真实网关会塞 `prompt_tokens_details` 这类嵌套对象，所以不能假设都是数字。
    """
    raw = getattr(resp, "usage", None) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            val = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            val = 0
        out[key] = val
    return out


def _response_tokens(resp: Any) -> int:
    """本轮调用的**账本 token**：优先 total_tokens，缺了就用 prompt+completion。"""
    u = _usage_ints(resp)
    total = u.get("total_tokens") or (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))
    return max(0, int(total))


def _cache_ints(resp: Any) -> dict[str, int]:
    """本轮**缓存命中/未命中**（v0.10.43）。

    provider 层（`openai_compat._usage_dict`）已把两种形状规范化成
    `cache_hit_tokens` / `cache_miss_tokens`；这里再兜底认一次原始形状
    （DeepSeek 顶层 / OpenAI 嵌套），让每条助手消息都带上本轮缓存数字 ——
    前端才能在气泡下面显示"缓存 92%"，用量条才能算**平均命中**（而不是
    只看最后一次调用，那个可能恰好是 0%）。
    """
    raw = getattr(resp, "usage", None) or {}
    if not isinstance(raw, dict):
        return {}

    def num(src: Any, *keys: str) -> int | None:
        if not isinstance(src, dict):
            return None
        for k in keys:
            v = src.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                return int(v)
        return None

    hit = num(raw, "cache_hit_tokens", "prompt_cache_hit_tokens")
    miss = num(raw, "cache_miss_tokens", "prompt_cache_miss_tokens")
    if hit is None:
        hit = num(raw.get("prompt_tokens_details"), "cached_tokens")
    if hit is None:
        return {}
    if miss is None:
        prompt = num(raw, "prompt_tokens")
        miss = max(0, prompt - hit) if prompt is not None else 0
    return {"cache_hit": int(hit), "cache_miss": int(miss or 0)}


@dataclass
class AgentResult:
    content: str = ""
    stop_reason: str = "done"  # done | max_iterations | error
    iterations: int = 0
    tool_calls: int = 0
    pending_confirms: list[str] = field(default_factory=list)
    pending_questions: list[str] = field(default_factory=list)
    transient_retries: int = 0   # C1：本轮的瞬态续跑次数
    loop_guards: int = 0         # C2：本轮的熔断次数
    # v0.10.15：失败必须带**错误码**上路。原来只把 `[LLM error/xxx]` 写进会话文本，
    # 控制台既没有码也没有终态，用户看到的是"一直正在输入"（owner 实测）。
    error_code: str = ""         # errors.ErrorClass.value（如 network / auth / rate_limit）
    error_detail: str = ""       # 给人看的一句话


class AgentLoop:
    def __init__(
        self,
        llm: Any,
        registry: ToolRegistry,
        session: WindowSession,
        builder: ContextBuilder | None = None,
        compactor: Any = None,
        tool_names: list[str] | None = None,
        max_iterations: int = 25,
        transient_retries: int = 3,
        transient_delay: float = 10.0,
        guard_threshold: int = DEFAULT_GUARD_THRESHOLD,
    ):
        self.llm = llm
        self.registry = registry
        self.session = session
        self.builder = builder or ContextBuilder()
        self.compactor = compactor
        self.tool_names = tool_names
        self.max_iterations = max_iterations
        self.transient_retries = max(0, int(transient_retries))
        self.transient_delay = max(0.0, float(transient_delay))
        self.guard_threshold = max(1, int(guard_threshold))
        # 调用签名 → 连续失败次数（C2）
        self._fail_streak: dict[str, int] = {}

    # ------------------------------------------------------------------ run
    async def run(self, user_input: str, ctx: ToolContext,
                  on_delta: Callable[[str], None] | None = None) -> AgentResult:
        """Run one task. `on_delta` (optional, T2.5) receives text fragments as
        the model streams them — wired to the SSE feed for the typewriter UI."""
        result = AgentResult()
        if user_input:
            # v0.10.43：**这条是谁派的** —— 群组里派活的是组长，不是 owner。
            # 以前不记，前端只能把所有 user 消息显示成"我"（owner 实测反馈）。
            sender = str((ctx.extra or {}).get("sender") or "")
            self.session.append("user", user_input,
                                meta={"sender": sender} if sender else None)
        schemas = self.registry.schemas(only=self.tool_names)

        for step in range(1, self.max_iterations + 1):
            result.iterations = step
            messages = self.session.build_context(self.builder)
            try:
                # C1: transient failures get continuation attempts instead of
                # killing the whole run on the first timeout.
                resp = await self._chat_with_continuation(messages, schemas, on_delta, result)
            except RateLimitPaused as e:
                # T8.8 封禁级限速：**暂停**任务，不是失败。
                # stop_reason=rate_limited 会被 PromptRunner 映射成 rate_limited 状态，
                # 调度器据此把 job 标成 rate_limited（区别于 fail 暂停）并通知。
                logger.warning("任务因封禁级限速暂停：需等待 %.0fs", e.wait_seconds)
                self.session.append(
                    "system",
                    f"[限速暂停] 服务端要求等待 {e.wait_seconds:.0f} 秒"
                    f"（{getattr(e.severity, 'value', 'banned')} 档）；"
                    f"本轮暂停，不是失败，稍后会自动重试。")
                result.stop_reason = "rate_limited"
                result.error_code = "rate_limit_paused"
                result.error_detail = (f"服务端要求等待 {e.wait_seconds:.0f} 秒"
                                       f"（{getattr(e.severity, 'value', 'banned')} 档）")
                result.content = (f"限速暂停（非失败）：服务端要求等待约 "
                                  f"{e.wait_seconds / 60:.0f} 分钟，任务已挂起等待重试。")
                break
            except Exception as e:  # noqa: BLE001 — classified below, never swallowed
                # 这里是 **LLM 调用**失败，不是工具参数问题 → assume_tool_args=False。
                # 否则框架自己抛的 TypeError 会被归成 tool_failure，并把
                # "参数或调用方式有问题，请按报错修正后重新调用" 喂给模型。
                cls = errors.classify(e, assume_tool_args=False)
                logger.exception("LLM chat failed (%s)", cls.value)
                self.session.append("system", f"[LLM error/{cls.value}] {errors.describe(e)}")
                result.stop_reason = "error"
                result.error_code = str(getattr(cls, "value", cls))
                result.error_detail = errors.describe(e)
                result.content = (f"LLM 调用失败（{cls.value}）：{e}。"
                                  f"{errors.action_hint(e, assume_tool_args=False)}")
                break

            # v11：记账与"上下文占用"是两个口径，都在这里落地
            #   tokens       → 本轮 total_tokens，供账本页/用量条显示累计花费
            #   note_usage() → 本轮 prompt_tokens，供压缩器判断"上下文有多满"
            tokens = _response_tokens(resp)
            self._note_usage(resp)
            cache = _cache_ints(resp)  # v0.10.43：气泡下的"缓存 x%"

            if not resp.tool_calls:
                result.content = resp.content or ""
                self.session.append("assistant", result.content, tokens=tokens,
                                    meta=dict(cache) or None)
                result.stop_reason = "done"
                break

            # assistant turn with tool calls (persisted with meta for rebuild)
            llm_tool_calls = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in resp.tool_calls
            ]
            self.session.append("assistant", resp.content or "", tokens=tokens,
                                meta={"tool_calls": llm_tool_calls, **cache})
            for tc in resp.tool_calls:
                result.tool_calls += 1
                tool_result = await self._run_tool(tc, ctx, result)
                if tool_result.needs_confirm:
                    tool_result = await self._approval_flow(tc, tool_result, ctx)
                self.session.append(
                    "tool", tool_result.as_tool_text(),
                    meta={"tool_call_id": tc.id, "tool_name": tc.name})
                if tool_result.needs_confirm:
                    result.pending_confirms.append(tool_result.output)
                if tc.name == "ask_user" and not tool_result.data.get("answered", True):
                    q = tool_result.data.get("question", "")
                    if q:
                        result.pending_questions.append(q)
        else:
            # C4: hitting the ceiling must produce a usable hand-off summary
            result.stop_reason = "max_iterations"
            result.content = self._degradation_summary(result)
            self.session.append("system", f"[轮次上限] {result.content}")

        # post-run compaction (anti-loss); never breaks the run
        if self.compactor is not None:
            try:
                await self.compactor.maybe_compact(self.session)
            except Exception:  # noqa: BLE001
                logger.warning("compaction failed", exc_info=True)
        return result

    # ------------------------------------------------------- C1 瞬态续跑
    def _note_usage(self, resp: Any) -> None:
        """把本轮 usage 里的 `prompt_tokens` 记到会话上（= 上下文占用）。

        记账失败绝不能影响对话本身，所以整段吞异常。
        """
        note = getattr(self.session, "note_usage", None)
        if not callable(note):
            return
        try:
            note(getattr(resp, "usage", None))
        except Exception:  # noqa: BLE001 — 只影响用量显示，不能影响对话
            logger.debug("note_usage failed", exc_info=True)

    async def _chat(self, messages: list, schemas: list,
                    on_delta: Callable[[str], None] | None) -> Any:
        if on_delta is not None:
            return await self._stream_turn(messages, schemas, on_delta)
        return await self.llm.chat(messages, tools=schemas or None)

    async def _chat_with_continuation(self, messages: list, schemas: list,
                                      on_delta: Callable[[str], None] | None,
                                      result: AgentResult) -> Any:
        """LLM 调用的瞬态失败续跑（C1）。

        永久错误 / 策略拒绝立即抛出（外层停止）；瞬态错误最多续跑
        `transient_retries` 次，每次退避 `transient_delay` 秒。用尽后抛最后一个异常。
        """
        attempts = self.transient_retries + 1
        last: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await self._chat(messages, schemas, on_delta)
            except RateLimitPaused:
                # 封禁级限速：立即上报给外层暂停任务，
                # **不要**当普通瞬态续跑（否则先白等好几轮退避才暂停）
                raise
            except Exception as e:  # noqa: BLE001 — classified immediately
                cls = errors.classify(e, assume_tool_args=False)
                if cls is not ErrorClass.TRANSIENT:
                    raise
                last = e
                if attempt >= attempts:
                    break
                result.transient_retries += 1
                logger.warning("LLM 瞬态失败（%s），第 %d/%d 次续跑，%.0fs 后重试",
                               errors.describe(e), attempt, self.transient_retries, self.transient_delay)
                self.session.append(
                    "system",
                    f"[LLM 瞬态失败 {attempt}/{self.transient_retries}，继续重试] {errors.describe(e)}")
                if self.transient_delay > 0:
                    await asyncio.sleep(self.transient_delay)
        assert last is not None
        raise last

    # ------------------------------------------------------- C2 死循环熔断
    @staticmethod
    def _signature(name: str, args: Any) -> str:
        try:
            payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001 — 不可序列化也要能算出稳定签名
            payload = repr(args)
        digest = hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:12]
        return f"{name}#{digest}"

    async def _run_tool(self, tc: Any, ctx: ToolContext, result: AgentResult) -> ToolResult:
        """执行一次工具调用，带熔断（C2）。

        同一签名（工具名 + 参数）连续失败达到阈值后拒执行，改为把"该路径已失败，
        换策略"注入会话——防止一路烧 token 到 max_iterations。
        """
        sig = self._signature(tc.name, tc.arguments)
        streak = self._fail_streak.get(sig, 0)
        if streak >= self.guard_threshold:
            result.loop_guards += 1
            msg = (f"[LOOP-GUARD] {tc.name} 的同一调用已连续失败 {streak} 次，本次拒绝执行。"
                   f"请换一种做法（改参数、先检查前置条件，或换工具）；"
                   f"重复同样的调用不会有不同结果。")
            logger.warning("loop guard tripped: %s (streak=%d)", tc.name, streak)
            return ToolResult.failure(msg, loop_guard=True, tool_name=tc.name,
                                      fail_streak=streak)

        tool_result = await self.registry.execute(tc.name, tc.arguments, ctx)
        if tool_result.ok:
            self._fail_streak.pop(sig, None)
        else:
            self._fail_streak[sig] = streak + 1
        return tool_result

    # ------------------------------------------------------- C4 上限降级总结
    def _degradation_summary(self, result: AgentResult) -> str:
        """到轮次上限时产出的交接摘要：做到哪了 / 卡在哪 / 下一步怎么接。"""
        msgs = self.session.recent(80)
        tool_names: list[str] = []
        for m in msgs:
            if m.role == "tool":
                tool_names.append(str((m.meta or {}).get("tool_name") or "tool"))
        last_text = ""
        for m in reversed(msgs):
            if m.role == "assistant" and (m.content or "").strip():
                last_text = m.content.strip()
                break

        stuck = []
        for sig, count in self._fail_streak.items():
            if count >= 2:
                stuck.append(f"{sig.split('#', 1)[0]}×{count}")

        lines = [f"(stopped: max iterations reached — {result.iterations} 轮 / "
                 f"{result.tool_calls} 次工具调用)"]
        if last_text:
            lines.append(f"最后一轮模型输出：{last_text[:300]}")
        if tool_names:
            recent = " → ".join(tool_names[-8:])
            lines.append(f"最近调用的工具：{recent}")
        if stuck:
            lines.append(f"反复失败的调用：{', '.join(stuck)}（建议换策略或人工介入）")
        if result.loop_guards:
            lines.append(f"熔断次数：{result.loop_guards}")
        if result.transient_retries:
            lines.append(f"LLM 瞬态续跑次数：{result.transient_retries}")
        lines.append("下次继续时：先读上面的进展，不要再走已经失败的路径。")
        return "\n".join(lines)

    async def _stream_turn(self, messages: list, schemas: list,
                           on_delta: Callable[[str], None]) -> Any:
        """Consume provider stream chunks → one LLMResponse (T2.5).

        Identical output shape to `llm.chat` so the rest of the loop does not
        care which path ran; the only difference is `on_delta` seeing fragments.
        """
        from ...llm.provider import LLMResponse

        stream = getattr(self.llm, "stream_chat", None)
        if stream is None:  # third-party llm without streaming: degrade gracefully
            resp = await self.llm.chat(messages, tools=schemas or None)
            if resp.content:
                try:
                    on_delta(resp.content)
                except Exception:  # noqa: BLE001
                    logger.debug("on_delta callback failed", exc_info=True)
            return resp

        parts: list[str] = []
        tool_calls: list = []
        finish_reason = "stop"
        usage: dict = {}
        async for chunk in stream(messages, tools=schemas or None):
            if chunk.delta:
                parts.append(chunk.delta)
                try:
                    on_delta(chunk.delta)
                except Exception:  # noqa: BLE001 — UI feedback never breaks a run
                    logger.debug("on_delta callback failed", exc_info=True)
            if getattr(chunk, "done", False):
                tool_calls = list(chunk.tool_calls or [])
                finish_reason = chunk.finish_reason or "stop"
                usage = dict(chunk.usage or {})
        return LLMResponse(content="".join(parts) or None, tool_calls=tool_calls,
                           finish_reason=finish_reason, usage=usage)

    async def _approval_flow(self, tc: Any, tool_result: Any, ctx: ToolContext) -> Any:
        """T4.1/T4.3 — a dangerous call is PAUSED here until approved.

        三道保险 (three safeguards):
        1. irreversible commands are NEVER auto-approved — the owner decides;
        2. 托管代审: with an `approval_reviewer` injected (admin delegation mode),
           an independent LLM reviews the call; approve → replay, deny → refuse;
        3. every decision is audited (留痕) and turns into a pending decision so
           the owner sees it in the console/QQ either way.
        """
        from ...tools.registry import ToolResult

        reason = tool_result.output
        irreversible = bool((tool_result.data or {}).get("irreversible"))
        preview = json.dumps(tc.arguments, ensure_ascii=False)[:300]
        audit = getattr(ctx, "db", None)

        def _log(action: str) -> None:
            if audit is not None:
                try:
                    audit.audit("owner", f"approval:{action}",
                                f"tool={tc.name} irreversible={irreversible} args={preview}")
                except Exception:  # noqa: BLE001
                    logger.warning("approval audit failed", exc_info=True)

        question = (f"高危操作待审批：{tc.name}\n参数：{preview}\n风险：{reason}\n"
                    f"批准请回复「批准」，拒绝回复「拒绝」。")

        # safeguard 2 — 托管代审 (skipped entirely for irreversible commands)
        reviewer = ctx.extra.get("approval_reviewer")

        # T9.6：审批**逐级**路由（员工→主管→管理员）。不可逆动作直接升级管理员。
        #
        # 审查报告 P1-6：初版只给主管工位投一条文本就 `return failure`，**没有任何
        # 回传通道** —— 不落 `pending_decisions`，主管也没有工具能批准这条待批命令。
        # 结果是"配了主管反而更差"：无主管时用户能在控制台批准并重放（v1 行为），
        # 有主管时该操作**永久无法批准**（安全上 fail-closed，功能上是死路）。
        # 现在：落一条**指定给主管**的待决事项 → 投递带 id 的通知 → 等它被裁决
        # → 批准则重放、拒绝则拦截。没有 DecisionService 时退回"只提请、不执行"。
        router = ctx.extra.get("approval_router")
        if router is not None:
            try:
                route = router(ctx.window_id, irreversible) or {}
            except Exception:  # noqa: BLE001 — 路由失败按最保守处理（留给管理员）
                route = {"target": "admin"}
            if route.get("target") == "supervisor" and route.get("window_id"):
                sup_id = str(route["window_id"])
                decisions = ctx.extra.get("decisions")
                pending = None
                if decisions is not None:
                    try:
                        pending = decisions.ask(
                            sup_id,
                            f"【待你裁决 · 来自工位 {ctx.window_id}】\n"
                            f"工具：{tc.name}\n参数：{preview}\n风险：{reason}",
                            options=["批准", "拒绝"],
                            source="escalated_to_supervisor",
                            # 不取消主管自己正在问的问题——两件事只是同一个收件人
                            supersede=False)
                    except Exception:  # noqa: BLE001 — 落库失败不阻断"提请"本身
                        logger.warning("creating supervisor decision failed", exc_info=True)
                dispatch = ctx.extra.get("supervision_dispatch")
                if callable(dispatch):
                    hint = (f"\n（待决事项 #{pending.id}：请用 approve_decision 批准或拒绝）"
                            if pending is not None else "")
                    try:
                        dispatch(sup_id, f"【待你审批 · 来自工位 {ctx.window_id}】\n{question}{hint}")
                        _log(f"escalated_to_supervisor:{sup_id}")
                    except Exception:  # noqa: BLE001 — 投递失败就退化成找管理员
                        logger.warning("approval escalation to supervisor failed", exc_info=True)
                if pending is not None and decisions is not None:
                    try:
                        answer = await decisions.wait(pending.id)
                    except Exception:  # noqa: BLE001
                        logger.warning("waiting for supervisor decision failed", exc_info=True)
                        answer = "[cancelled]"
                    verdict = _approval_verdict(answer)
                    if verdict is True:
                        _log("approved_by_supervisor")
                        return await self.registry.execute(tc.name, tc.arguments, ctx,
                                                           approved=True)
                    if verdict is False:
                        _log("denied_by_supervisor")
                        return ToolResult.failure(
                            f"[主管拒绝] {reason}；操作已拦截，未执行。回答：{str(answer)[:200]}")
                    return ToolResult.failure(
                        f"[已提请主管审批] {reason}\n"
                        f"主管未在时限内裁决（{str(answer)[:80]}）；操作未执行。")
                return ToolResult.failure(
                    f"[已提请主管审批] {reason}\n"
                    f"申请已提交给你的主管（工位 {sup_id}）；在主管批准之前，操作不会执行。")

        if reviewer is not None and not irreversible:
            try:
                verdict = await reviewer(tc.name, tc.arguments, reason)
            except Exception as e:  # noqa: BLE001 — reviewer failure → ask the human
                logger.warning("approval reviewer failed: %s", e)
                verdict = None
            if verdict is not None:
                if verdict:
                    _log("auto_approved")
                    return await self.registry.execute(tc.name, tc.arguments, ctx,
                                                       approved=True)
                _log("auto_denied")
                return ToolResult.failure(
                    f"[托管代审拒绝] {reason}；操作已拦截并记录，未执行。")

        # safeguard 3 — ask the owner (durable decision queue when available)
        ask = getattr(ctx, "ask_user_fn", None)
        if ask is None:
            return tool_result  # no channel to ask — keep the old pending-confirm report
        answer = ask(question, options=["批准", "拒绝"])
        if hasattr(answer, "__await__"):
            answer = await answer
        text = str(answer or "")
        verdict = _approval_verdict(text)
        if verdict is True:
            _log("approved")
            return await self.registry.execute(tc.name, tc.arguments, ctx, approved=True)
        if verdict is False:
            _log("denied")
            return ToolResult.failure(f"[用户拒绝] {reason}；操作已拦截，未执行。回答：{text[:200]}")
        # 未答复 / 超时 / 取消 → fail-closed：不放行
        _log("no_answer")
        return ToolResult.failure(
            f"[未获批准] {reason}；操作未执行（答复：{text[:120]}）。"
            f"请在控制台「待决事项」明确批准后再重试该操作。")
