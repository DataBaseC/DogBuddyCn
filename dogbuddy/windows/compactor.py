# DogBuddyCn — loss-resistant context compaction (L3 Domain)
# ContextCompressor protocol & threshold-trigger idea derived from AstrBot
# (https://github.com/AstrBotDevs/AstrBot) — Copyright (c) AstrBotDevs and contributors, AGPL-3.0.
# Modifications (2026-10-12, DogBuddyCn contributors):
#   - added loss-resistant structured fact extraction (file_changes/todos/decisions/lessons)
#     persisted to compactions + window memory (only conversational wording is dropped)
#   - persistence via injected repos (ports), no SQL here
"""When a window's token usage crosses the threshold (default 80% of model window),
older messages are compacted: an LLM summary + structured facts replace them."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

#: v0.10.55：压缩降级的进程级计数，挂在 `/healthz.compactions` 上。
#: 以前降级只在审计里留一行，看不见"最近是不是一直在降级"。
_STATS: dict[str, Any] = {"ok": 0, "degraded": 0, "last_degraded_at": "",
                          "last_tokens": 0, "last_chars": 0, "skipped": 0}


def compaction_stats() -> dict[str, Any]:
    return dict(_STATS)

from ..ports import Compaction

logger = logging.getLogger("dogbuddy.windows.compactor")

_KEEP_RECENT = 12  # messages kept verbatim after compaction

_EXTRACT_PROMPT = """You are compressing a long-running work session. Produce a JSON object:
{{
  "narrative": "a faithful summary of what happened and where things stand",
  "file_changes": [{{"path": "...", "action": "created|modified|deleted", "note": "..."}}],
  "todos": ["still-open todo items"],
  "decisions": ["important decisions made"],
  "lessons": ["mistakes/learnings worth remembering"]
}}
Rules: keep facts, drop pleasantries; never invent file paths; answer with ONLY the JSON.

Conversation:
{conversation}
"""


class Compactor:
    """Threshold-triggered LLM compaction with structured fact preservation."""

    def __init__(self, llm: Any, compaction_repo: Any, memory_service: Any = None,
                 threshold: float = 0.8, max_context_tokens: int = 128000,
                 max_messages: int = 96, summarize_timeout: float = 90.0,
                 retry_timeout: float = 45.0, min_interval_s: float = 1800.0,
                 cost: Any = None):
        self.llm = llm
        self.repo = compaction_repo
        self.memory = memory_service
        #: v0.11.0：压缩摘要也是钱（4 天 94 次调用），进同一本成本账。
        self.cost = cost
        self.threshold = threshold
        self.max_context_tokens = max_context_tokens
        #: 摘要调用的上限（v0.10.48）。压缩跑在**每轮 LLM 调用之前**，以前没有超时：
        #: provider 卡住时整轮就跟着卡（生产 10-21/10-22 各出现一次 `compaction LLM
        #: failed`）。超时后按降级处理（截断保留原文），绝不让压缩拖死主流程。
        #: v0.10.55：首轮放宽到 90s（60s 时 19/99 次超时降级），重试 45s。
        self.summarize_timeout = summarize_timeout
        self.retry_timeout = retry_timeout
        #: v0.10.55 降成本：同一工位两次压缩的**最小间隔**。生产实测相邻压缩间隔
        #: p50=76~154 分钟、最快 3 分钟 —— 压缩一次 = 一次摘要 LLM 调用 + 重写上下文
        #: 前缀（缓存全废）。除非上下文真的快满（>=95%），否则半小时内不重复压。
        self.min_interval_s = min_interval_s
        self._last_at: dict[str, float] = {}
        #: 摘要输入切片：先 30k 字符，失败再用 12k 重试一次（更短=更快更便宜）。
        #: 以前固定 60000 字符，一次压缩光输入就上万 token，超时了还白付。
        self.slices: tuple[int, ...] = (30000, 12000)
        #: 消息数触发线（v0.10.38）。**为什么需要它**：token 阈值在长上下文库上
        #: 永远不触发——工位 `max_context_tokens=1048576`、阈值 0.8 → 要 84 万 token
        #: 才压缩，而 `ContextBuilder.build()` 只取 `recent(60)`：会话一超过 60 条，
        #: 每轮请求的窗口就向前滑一条 → **消息列表头部位移 → 提供商缓存前缀匹配不上**，
        #: 超出的部分每次全价重付（实测：95 条会话的工位缓存命中只有 4096/25490=13.8%,
        #: 56 条会话的工位 97.5%）。在 60 之前压缩（保留 12 条），窗口再也不滑动，
        #: 前缀稳定 → 缓存恢复，上下文也封顶。
        self.max_messages = max_messages

    # AstrBot ContextCompactor protocol shape
    def should_compress(self, current_tokens: int, max_tokens: int | None = None) -> bool:
        max_tokens = max_tokens or self.max_context_tokens
        return current_tokens >= self.threshold * max_tokens

    def should_compress_by_count(self, session: Any) -> bool:
        """消息数超线就压（与 token 阈值并列的第二个触发维度）。"""
        try:
            return len(session.recent(400)) > self.max_messages
        except Exception:  # noqa: BLE001 — 数不了消息时不阻断压缩路径
            logger.debug("message count failed", exc_info=True)
            return False

    async def maybe_compact(self, session: Any) -> Compaction | None:
        """Compact when over threshold (tokens OR message count). Returns the
        Compaction record if one happened。

        判据必须用**上下文占用**（最近一次调用的 prompt_tokens），不能用累计花费
        （`session.token_usage()` 是 `SUM(tokens)`）：长寿命工位的累计花费只会一直涨，
        用它判压缩 = 会话越老越容易触发压缩，把上下文压碎。
        """
        used = self._used_tokens(session)
        if not (self.should_compress(used) or self.should_compress_by_count(session)):
            return None
        wid = str(getattr(getattr(session, "window", None), "id", ""))
        # v0.10.55：压缩节流。压缩=一次摘要调用 + 前缀重写（缓存全废），
        # 生产里出现过 3 分钟连压两次。除非用量逼近上限，否则 max(min_interval, ...) 内只压一次。
        urgent = used >= 0.95 * self.max_context_tokens
        last = self._last_at.get(wid, 0.0)
        if not urgent and last and (time.monotonic() - last) < self.min_interval_s:
            _STATS["skipped"] += 1
            logger.info("compaction skipped for %s: %.0fs since last (used=%d, urgent=False)",
                        wid or "?", time.monotonic() - last, used)
            return None
        rec = await self.compact_now(session)
        self._last_at[wid] = time.monotonic()
        return rec

    @staticmethod
    def _used_tokens(session: Any) -> int:
        """上下文占用的取值口径，带兜底：老 session 对象没有 `context_tokens()`。"""
        ctx = getattr(session, "context_tokens", None)
        if callable(ctx):
            try:
                return int(ctx())
            except Exception:  # noqa: BLE001 — 拿不到占用时退回累计值，绝不阻断压缩路径
                logger.debug("context_tokens() failed", exc_info=True)
        return int(session.token_usage())

    async def compact_now(self, session: Any) -> Compaction:
        messages = session.recent(400)
        keep_from = max(len(messages) - _KEEP_RECENT, 0)
        doomed = messages[:keep_from]
        if not doomed:
            # nothing to compress — just record a no-op so callers can proceed
            return self.repo.add_compaction(session.window.id, {"narrative": "", "file_changes": [],
                                                               "todos": [], "decisions": [], "lessons": []}, 0)

        conversation = self._render(doomed)
        summary = await self._summarize(conversation, getattr(session.window, "id", ""))

        compaction = self.repo.add_compaction(
            session.window.id, summary, replaced_count=len(doomed))

        # drop compacted messages; keep the tail verbatim
        session.drop_messages(doomed[-1].id)

        # precipitate durable facts into window memory (anti-loss)
        if self.memory is not None:
            try:
                facts = []
                for key in ("decisions", "lessons"):
                    for item in summary.get(key, [])[:10]:
                        facts.append(f"{key[:-1]}: {item}")
                for fc in summary.get("file_changes", [])[:20]:
                    if isinstance(fc, dict):
                        facts.append(f"file {fc.get('action', 'modified')}: {fc.get('path', '?')}")
                if facts:
                    self.memory.append_window(session.window.id, "compaction沉淀：" + "; ".join(facts))
            except Exception:  # noqa: BLE001 — memory precipitation must not break compaction
                logger.warning("memory precipitation failed", exc_info=True)
        return compaction

    async def _summarize(self, conversation: str, window_id: str = "") -> dict[str, Any]:
        """抽摘要：先 30k 字符，失败再用 12k 重试一次，两次都失败才降级。

        v0.10.55 降成本 + 降降级率：
          * 输入从固定 60000 字符降到 30000（prefill 少一半，超时自然少）；
          * 失败不立刻截断，而是**换更短的切片再试一次**（生产 19/99 次降级全是
            TimeoutError，短一点的输入通常就能过）；
          * 每次成功/失败都把 token 与字符数写进审计与进程计数，
            这样"压缩到底花了多少"第一次变得可查。
        """
        last_error: Exception | None = None
        for idx, chars in enumerate(self.slices):
            timeout = self.summarize_timeout if idx == 0 else self.retry_timeout
            slice_text = conversation[:chars]
            try:
                resp = await asyncio.wait_for(
                    self.llm.chat([{"role": "user",
                                    "content": _EXTRACT_PROMPT.format(conversation=slice_text)}]),
                    timeout=timeout)
                text = (resp.content or "").strip()
                if not text:
                    raise RuntimeError("empty summary")
                out = self._parse_json(text, conversation)
                usage = getattr(resp, "usage", None) or {}
                tokens = int(usage.get("total_tokens") or 0)
                out["_in_chars"] = len(slice_text)
                out["_tokens"] = tokens
                # v0.11.0：压缩调用的花费也入账（它曾经是"看不见的开销"）
                if getattr(self, "cost", None) is not None:
                    try:
                        self.cost.record(
                            kind="llm", window_id=window_id,
                            provider=str(getattr(resp, "provider", "") or ""),
                            model=str(getattr(resp, "model", "")
                                      or getattr(self.llm, "model", "") or ""),
                            in_tokens=int(usage.get("prompt_tokens")
                                          or usage.get("input_tokens") or 0),
                            out_tokens=int(usage.get("completion_tokens")
                                           or usage.get("output_tokens") or 0),
                            cache_hit_tokens=int(usage.get("cache_read_input_tokens")
                                                 or 0),
                            idem_key=f"compaction:{window_id}:{time.time_ns()}",
                            meta={"phase": "compaction", "chars": len(slice_text),
                                  "slice": idx + 1},
                        )
                    except Exception:  # noqa: BLE001 — 记账失败不影响压缩结果
                        logger.debug("compaction cost record failed", exc_info=True)
                _STATS["ok"] += 1
                _STATS["last_tokens"] = tokens
                _STATS["last_chars"] = len(slice_text)
                try:
                    self.repo.audit("system", "compaction:ok",
                                    f"{window_id} chars={len(slice_text)} tokens={tokens} "
                                    f"slice={idx + 1}/{len(self.slices)}")
                except Exception:  # noqa: BLE001
                    logger.debug("compaction audit failed", exc_info=True)
                return out
            except Exception as e:  # noqa: BLE001 — 换更短切片再试
                last_error = e
                logger.info("compaction attempt %d/%d failed (%s: %s) chars=%d",
                            idx + 1, len(self.slices), type(e).__name__, e, len(slice_text))

        e = last_error or RuntimeError("compaction failed")
        # v0.10.48：降级**必须留痕**。以前只有一行 WARNING，台账和审计里都看不出
        # 这次压缩是"LLM 总结"还是"原文截断"，出问题时无从判断上下文质量。
        _STATS["degraded"] += 1
        _STATS["last_degraded_at"] = datetime.now(timezone.utc).isoformat()
        logger.warning(
            "compaction LLM failed after %d attempts (%s: %s) — truncating, %d chars kept",
            len(self.slices), type(e).__name__, e, min(len(conversation), 2000))
        try:
            self.repo.audit("system", "compaction:degraded",
                            f"{window_id} {type(e).__name__} attempts={len(self.slices)} "
                            f"kept={min(len(conversation), 2000)}")
        except Exception:  # noqa: BLE001 — 审计写不进去也不能阻断压缩
            logger.debug("compaction audit failed", exc_info=True)
        return {"narrative": conversation[:2000], "file_changes": [], "todos": [],
                "decisions": [], "lessons": [],
                "_degraded": True, "_degraded_reason": type(e).__name__}

    @staticmethod
    def _parse_json(text: str, fallback_src: str) -> dict[str, Any]:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    data.setdefault("narrative", "")
                    for k in ("file_changes", "todos", "decisions", "lessons"):
                        data.setdefault(k, [])
                    return data
            except json.JSONDecodeError:
                pass
        return {"narrative": text[:2000] or fallback_src[:2000], "file_changes": [],
                "todos": [], "decisions": [], "lessons": []}

    @staticmethod
    def _render(messages: list[Any]) -> str:
        lines = []
        for m in messages:
            lines.append(f"[{m.role}] {m.content}")
        return "\n".join(lines)
