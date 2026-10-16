# DogBuddyCn — 限速长等待"不再睡在请求里"（v0.10.28，P0/P1）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""生产实测（2026-10-16）：
* SenseNova 返回 `Retry-After: 600` / `inference exceeds tpm/rpm limit`；
* 我们的策略**真的睡了 600s** —— HTTP 请求挂着、工位串行队列堵着、界面零进展；
* heavy 档 `max_retries+5` = 10 次 × 600s → 单次调用最坏 **100 分钟**；
* 恢复是**加性 5 秒**：600→595→590… 要约 118 次成功，实际几乎永不恢复；
* 退避记忆按 profile 分桶（默认全是 `""`）→ 管理员被限速把 600s 传染给所有工位。

本文件钉住三件事：**长等待改暂停**、**预算按时间不按次数**、**按 (端点,key) 分桶 + 比例恢复 + TTL**。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.errors import TransientError  # noqa: E402
from dogbuddy.llm.retry import (RateLimitPaused, RetryMemory, RetryPolicy,  # noqa: E402
                                severity_for)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class Fake429(TransientError):
    """带 `Retry-After` 的 429（`retry_after_from` 从 response.headers 里读）。"""

    def __init__(self, retry_after: float | None = None):
        super().__init__("429")
        headers = {} if retry_after is None else {"Retry-After": str(retry_after)}

        class _Resp:
            def __init__(self, h):
                self.headers = h

        self.response = _Resp(headers)


def _policy(**kw):
    slept: list[float] = []

    async def fake_sleep(d):
        slept.append(d)

    kw.setdefault("base_interval", 10.0)
    kw.setdefault("max_interval", 300.0)
    kw.setdefault("max_retries", 5)
    p = RetryPolicy(sleep=fake_sleep, **kw)
    return p, slept


# ---------------------------------------------------------------- P0：长等待 → 暂停
def test_长RetryAfter立刻暂停而不是睡600秒():
    p, slept = _policy(inline_cap=90.0, wait_budget=300.0)
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise Fake429(600.0)

    t0 = time.time()
    reason = ""
    try:
        _run(p.call(fn))
        raise AssertionError("应该抛 RateLimitPaused")
    except RateLimitPaused as exc:
        assert exc.wait_seconds == 600.0
        reason = getattr(exc, "reason", "")
    assert time.time() - t0 < 1.0, "不能在请求里睡 600s"
    assert slept == [], f"不该 sleep：{slept}"
    assert calls["n"] == 1, "暂停前只尝试一次"
    assert "在线等待上限" in reason, reason


def test_短等待仍然原地重试():
    p, slept = _policy(inline_cap=90.0, wait_budget=300.0)
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Fake429(30.0)
        return "ok"

    assert _run(p.call(fn)) == "ok"
    assert slept == [30.0], slept


def test_累计等待超过预算就暂停():
    """服务端每次都说等 40s（在 cap 内），但累计超过 300s 预算 → 暂停。"""
    p, slept = _policy(inline_cap=90.0, wait_budget=100.0)

    async def fn():
        raise Fake429(40.0)

    try:
        _run(p.call(fn))
        raise AssertionError("应该抛 RateLimitPaused")
    except RateLimitPaused as e:
        assert "预算" in getattr(e, "reason", ""), getattr(e, "reason", "")
    assert sum(slept) <= 100.0, f"预算内才睡：{slept}"


def test_无hint时用AIMD且仍受cap约束():
    p, slept = _policy(inline_cap=15.0, wait_budget=300.0)
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Fake429(None)      # 无 Retry-After → AIMD 首跳 20s > cap 15
        return "ok"

    try:
        _run(p.call(fn))
    except RateLimitPaused as e:
        assert e.wait_seconds == 20.0 and e.severity is (severity_for(20.0)), e.severity
    else:
        raise AssertionError("AIMD 首跳 20s 超过 cap 15s，应暂停")
    assert slept == []


def test_暂停也带来源与动作说明():
    seen: list[dict] = []
    p, _ = _policy(inline_cap=10.0, on_wait=lambda a, d, info=None: seen.append(info or {}))

    async def fn():
        raise Fake429(600.0)

    try:
        _run(p.call(fn))
    except RateLimitPaused:
        pass
    assert seen and seen[0]["source"] == "server-retry-after"
    assert seen[0]["action"] == "paused" and seen[0]["retry_after"] == 600.0


# ---------------------------------------------------------------- P1：分桶 / 恢复 / TTL
def test_退避记忆按端点_key分桶互不传染(tmp_path):
    mem = RetryMemory(tmp_path / "rm.json")
    mem.note_wait("token.sensenova.cn|ZxZI", 600.0, severity="heavy")
    assert mem.last_interval("token.sensenova.cn|ZxZI") == 600.0
    # 另一把 key / 另一个端点**不受影响**（旧实现按 profile 名分桶 → 全体 600s）
    assert mem.last_interval("token.sensenova.cn|4r27") is None
    assert mem.last_interval("api.siliconflow.cn|blha") is None
    assert mem.last_interval("") is None
    # 落盘再读，桶还在
    again = RetryMemory(tmp_path / "rm.json")
    assert again.last_interval("token.sensenova.cn|ZxZI") == 600.0
    assert again.last_interval("api.siliconflow.cn|blha") is None


def test_比例恢复_几次成功就把间隔打回去():
    async def fn():
        return "ok"

    p, _ = _policy(base_interval=10.0, max_interval=300.0,
                   adaptive=True, inline_cap=90.0, wait_budget=3000.0)
    p.waits = 1
    p.current_interval = 300.0
    for _ in range(8):          # 每 2 次成功减半：300→150→75→37.5→18.75
        _run(p.call(fn))
    assert p.current_interval <= 20.0, f"比例恢复没生效：{p.current_interval}"
    # 对照：旧的加性恢复（每次 -5s）8 次只能从 300 降到 260
    assert p.current_interval < 260.0 - 200


def test_退避记忆TTL到点自动作废(tmp_path):
    mem = RetryMemory(tmp_path / "rm.json")
    mem.note_wait("ep|key", 600.0)
    key = "ep|key"
    mem._data[key]["at"] = time.time() - (RetryMemory.TTL_SECONDS + 5)
    assert mem.last_interval(key) is None, "过期桶要作废（惩罚不该无限期挂着）"


def test_旧schema文件被丢弃(tmp_path):
    f = tmp_path / "rm.json"
    f.write_text(json.dumps({"": {"last_interval": 600.0, "waits": 8}}), encoding="utf-8")
    mem = RetryMemory(f)
    assert mem.last_interval("") is None, "旧格式（没有时间戳/版本）应丢弃重建"


def test_from_config透传cap与预算():
    from dogbuddy.config import RetryConfig

    cfg = RetryConfig(base_interval=5, inline_cap=45, wait_budget=120)
    p = RetryPolicy.from_config(cfg)
    assert p.inline_cap == 45.0 and p.wait_budget == 120.0


def test_记忆键把端点与key尾都带上():
    from dogbuddy.llm.profile_registry import LLMProfileRegistry

    reg = LLMProfileRegistry.__new__(LLMProfileRegistry)
    k1 = reg._memory_key("", "https://token.sensenova.cn/v1", "sk-aaaa-1111")
    k2 = reg._memory_key("", "https://token.sensenova.cn/v1", "sk-bbbb-2222")
    k3 = reg._memory_key("", "https://api.siliconflow.cn/v1", "sk-aaaa-1111")
    assert k1 == "token.sensenova.cn|1111"
    assert len({k1, k2, k3}) == 3, f"三把组合必须三个桶：{k1} {k2} {k3}"
