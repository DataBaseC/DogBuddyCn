# DogBuddyCn — 压缩降成本：切片重试 / 节流 / 成本可见（v0.10.55）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""成本遥测（2026-10-22～24）：

* 4 天里三个工位压缩 **94 次**（40/31/23），相邻间隔 p50 **76–154 分钟**，最快 **3 分钟**；
* 99 次里有 **19 次降级**（全是 TimeoutError）→ 摘要变成原文截断；
* 一次压缩 = 一次摘要 LLM 调用（旧代码固定塞 60000 字符）+ 重写上下文前缀（缓存全废）；
* 同期缓存命中一度只有 11.5%。

这里钉住四件事：① 失败换更短切片重试，不要立刻降级；② 输入从 60k 降到 30k；
③ 同一工位 30 分钟内不重复压（除非用量 ≥95%）；④ 每次压缩的 token/字符数进审计与进程计数。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.windows import compactor as comp  # noqa: E402


class _Repo:
    def __init__(self):
        self.audit_rows = []
        self.compactions = []

    def audit(self, actor, action, detail):
        self.audit_rows.append((actor, action, detail))

    def add_compaction(self, window_id, summary, replaced_count=0):
        self.compactions.append((window_id, summary, replaced_count))
        return SimpleNamespace(id=len(self.compactions), window_id=window_id, summary=summary)


class _Resp:
    def __init__(self, content, tokens=1234):
        self.content = content
        self.usage = {"total_tokens": tokens}


class _LLM:
    """前 N 次调用超时/失败，之后正常返回 JSON。"""

    def __init__(self, fail_times: int, content: str = '{"narrative": "ok"}'):
        self.fail_times = fail_times
        self.calls = []
        self.content = content

    async def chat(self, messages):
        self.calls.append(messages[0]["content"])
        if len(self.calls) <= self.fail_times:
            raise TimeoutError("slow")
        return _Resp(self.content)


def _mk(llm, **kw):
    repo = _Repo()
    c = comp.Compactor(llm, repo, None, **kw)
    return c, repo


def test_first_attempt_uses_30k_slice_not_60k():
    llm = _LLM(0)
    c, repo = _mk(llm)
    summary = asyncio.new_event_loop().run_until_complete(
        c._summarize("x" * 100000, "w1"))
    assert summary.get("_degraded") is None
    assert summary["_in_chars"] == 30000, "输入切片应降到 30k（以前固定 60k）"
    assert summary["_tokens"] == 1234
    assert any(a == "compaction:ok" and "tokens=1234" in d for _, a, d in repo.audit_rows)


def test_timeout_retries_with_shorter_slice_before_degrading():
    llm = _LLM(1)                      # 第一次超时，第二次成功
    c, repo = _mk(llm)
    summary = asyncio.new_event_loop().run_until_complete(
        c._summarize("y" * 50000, "w2"))
    assert summary.get("_degraded") is None, "第二次（更短切片）成功后不该降级"
    assert len(llm.calls) == 2
    assert summary["_in_chars"] == 12000
    assert comp.compaction_stats()["ok"] >= 1


def test_two_failures_still_degrade_and_are_counted():
    llm = _LLM(99)
    c, repo = _mk(llm)
    before = comp.compaction_stats()["degraded"]
    summary = asyncio.new_event_loop().run_until_complete(
        c._summarize("z" * 50000, "w3"))
    assert summary["_degraded"] is True
    assert len(llm.calls) == 2, "只重试一次，不无限烧 token"
    assert comp.compaction_stats()["degraded"] == before + 1
    assert any(a == "compaction:degraded" for _, a, _ in repo.audit_rows)


def _session(window_id="w1", n_messages=60, used=100_000):
    msgs = [SimpleNamespace(id=i + 1, role="user", content="hi", created_at="",
                            tokens=0, meta=None) for i in range(n_messages)]
    return SimpleNamespace(
        window=SimpleNamespace(id=window_id),
        recent=lambda limit=400: msgs[-limit:],
        drop_messages=lambda _id: None,
        context_tokens=lambda: used,
        token_usage=lambda: used,
        append=lambda *a, **k: None,
    )


def test_same_window_is_not_compacted_twice_within_the_window():
    llm = _LLM(0)
    c, _ = _mk(llm, min_interval_s=1800)
    sess = _session(used=110_000)
    loop = asyncio.new_event_loop()
    first = loop.run_until_complete(c.maybe_compact(sess))
    assert first is not None, "第一次该压"
    calls_after_first = len(llm.calls)
    second = loop.run_until_complete(c.maybe_compact(sess))
    assert second is None, "30 分钟内不该再压（生产里出现过 3 分钟连压两次）"
    assert len(llm.calls) == calls_after_first, "跳过的压缩不能烧 token"


def test_urgent_usage_overrides_the_throttle():
    llm = _LLM(0)
    c, _ = _mk(llm, min_interval_s=1800, max_context_tokens=100_000)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(c.maybe_compact(_session(used=99_000)))
    n = len(llm.calls)
    again = loop.run_until_complete(c.maybe_compact(_session(used=99_000)))  # 99% 占用
    assert again is not None, "上下文快满时必须允许立刻再压"
    assert len(llm.calls) > n


def test_defaults_are_the_new_cost_profile():
    c, _ = _mk(_LLM(0))
    assert c.max_messages == 96, "消息数阈值 48→96（少压一半次数）"
    assert c.summarize_timeout == 90.0 and c.retry_timeout == 45.0
    assert c.slices == (30000, 12000)
