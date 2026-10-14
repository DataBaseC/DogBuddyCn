# DogBuddyCn — Batch 8 · T8.7 智能退避 / T8.8 限速分档
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""ROADMAP 验收："限速 stub 测试（读 Retry-After、AIMD 收敛、记忆起跳）；
重度限速下项目迭代自动降速"。

用户原话背景："大模型限速非常慢"——原实现是 10s→20→40…cap 300s × 5 次，
不读服务端的 Retry-After、也没有历史记忆，于是每次都从头试错。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.loop import AgentLoop  # noqa: E402
from dogbuddy.agents.core.runners import PromptRunner, TaskEnv  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.llm.provider import LLMResponse  # noqa: E402
from dogbuddy.llm.retry import (  # noqa: E402
    HEAVY_MAX, LIGHT_MAX, RateLimitPaused, RateLimitSeverity, RetryExhausted,
    RetryMemory, RetryPolicy, parse_retry_after, retry_after_from, severity_for)
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402
from dogbuddy.windows.session import WindowSession  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _status_error(code: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://ep.test/v1/chat/completions")
    resp = httpx.Response(code, headers=headers or {}, request=req)
    return httpx.HTTPStatusError(f"HTTP {code}", request=req, response=resp)


class _SleepSpy:
    """记下"本该睡多久"，让测试瞬间跑完。"""

    def __init__(self):
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(float(seconds))


# --------------------------------------------------------------------------- Retry-After
def test_解析_Retry_After_秒数与_HTTP_date():
    assert parse_retry_after("47") == 47.0
    assert parse_retry_after("1.5") == 1.5
    assert parse_retry_after(" 12 ") == 12.0
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("abc") is None
    assert parse_retry_after("") is None
    assert parse_retry_after(None) is None

    now = 1_700_000_000.0
    future = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(now + 90))
    past = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(now - 90))
    assert 85 <= (parse_retry_after(future, now=now) or 0) <= 95
    assert parse_retry_after(past, now=now) == 0.0, "过去的日期应归零，不能是负数"


def test_从异常里挖_Retry_After():
    exc = _status_error(429, {"Retry-After": "47"})
    assert retry_after_from(exc) == 47.0
    # 小写头
    assert retry_after_from(_status_error(429, {"retry-after": "30"})) == 30.0
    # 没有头
    assert retry_after_from(_status_error(429)) is None
    # 被包装过（provider 内部重试耗尽后再抛）
    wrapped = RetryExhausted(_status_error(429, {"Retry-After": "12"}), 5)
    assert retry_after_from(wrapped) == 12.0


def test_严重度分档边界():
    assert severity_for(1) is RateLimitSeverity.LIGHT
    assert severity_for(LIGHT_MAX - 0.1) is RateLimitSeverity.LIGHT
    assert severity_for(LIGHT_MAX) is RateLimitSeverity.HEAVY
    assert severity_for(HEAVY_MAX - 1) is RateLimitSeverity.HEAVY
    assert severity_for(HEAVY_MAX) is RateLimitSeverity.BANNED
    assert severity_for(7200) is RateLimitSeverity.BANNED


# --------------------------------------------------------------------------- 读 Retry-After
def test_优先读_Retry_After_而不是瞎猜指数():
    spy = _SleepSpy()
    policy = RetryPolicy(base_interval=10, max_retries=3, sleep=spy)
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _status_error(429, {"Retry-After": "47"})
        return "ok"

    assert _run(policy.call(fn)) == "ok"
    assert spy.slept == [47.0], f"应精确等 47 秒，而不是指数猜的 10 秒：{spy.slept}"


def test_没有服务端提示时用自适应间隔():
    spy = _SleepSpy()
    policy = RetryPolicy(base_interval=10, max_retries=3, sleep=spy)
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _status_error(503)
        return "ok"

    _run(policy.call(fn))
    assert spy.slept == [20.0, 40.0], f"AIMD 乘性放缓：10→20→40，实际 {spy.slept}"


# --------------------------------------------------------------------------- AIMD
def test_AIMD_乘性放缓_加性恢复():
    spy = _SleepSpy()
    policy = RetryPolicy(base_interval=10, max_retries=6, sleep=spy,
                         recovery_step=5.0)
    assert policy.current_interval == 10.0
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise _status_error(429)
        return "ok"

    _run(policy.call(fn))
    # 三次限速：10→20→40→80（乘性），随后一次成功：80-5=75（加性恢复）
    assert spy.slept == [20.0, 40.0, 80.0], spy.slept
    assert policy.current_interval == 75.0, "成功一次只减一个步长，不该一步回到基准"


def test_AIMD_恢复不会低于基准():
    policy = RetryPolicy(base_interval=10, recovery_step=100)
    policy.waits = 1
    policy.current_interval = 20
    policy._note_success()
    assert policy.current_interval == 10.0


def test_服务端给的值可以突破_max_interval():
    """重度限速下 max_interval 不该把服务端的 2 分钟砍成配置值。"""
    spy = _SleepSpy()
    policy = RetryPolicy(base_interval=10, max_interval=300, max_retries=2, sleep=spy)
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _status_error(429, {"Retry-After": "120"})
        return "ok"

    _run(policy.call(fn))
    assert spy.slept == [120.0], f"应等服务端要求的 120s（max_interval 只约束自适应猜测）"

    # 直接验证内部间隔不被 max_interval 夹住
    p2 = RetryPolicy(base_interval=10, max_interval=300)
    assert p2._bump_interval(120.0) == 120.0
    assert p2.current_interval == 120.0


# --------------------------------------------------------------------------- 记忆
def test_记忆起跳_下次从上次量级开始(tmp_path):
    mem = RetryMemory(tmp_path / "mem.json")
    # 第一次：被限速到 80s 后成功
    spy1 = _SleepSpy()
    p1 = RetryPolicy(base_interval=10, max_retries=4, sleep=spy1, memory=mem, profile="fast")
    n = {"i": 0}

    async def fn1():
        n["i"] += 1
        if n["i"] <= 2:
            raise _status_error(429)
        return "ok"

    _run(p1.call(fn1))
    assert mem.last_interval("fast") is not None, "记忆必须落盘"

    # 第二次：新策略实例应直接从记忆量级起跳，而不是回到 10s
    spy2 = _SleepSpy()
    seeded = mem.last_interval("fast")          # 先记下种子值：跑起来后记忆会继续更新
    p2 = RetryPolicy(base_interval=10, max_retries=4, sleep=spy2, memory=mem, profile="fast")
    assert p2.current_interval == seeded
    assert p2.current_interval > 10.0, "记忆起跳必须高于基准"

    async def fn2():
        raise _status_error(429)

    # 一路被限速到底：重档会自动追加预算，最终要么耗尽、要么升到封禁档暂停
    with pytest.raises((RetryExhausted, RateLimitPaused)):
        _run(p2.call(fn2))
    assert spy2.slept, "应该真的等过"
    assert spy2.slept[0] >= seeded, \
        f"第一次等待必须是记忆量级（而不是基准 10s），实际 {spy2.slept}"


def test_记忆按_profile_隔离(tmp_path):
    mem = RetryMemory(tmp_path / "mem.json")
    mem.note_wait("slow", 300.0, "heavy")
    assert mem.last_interval("slow") == 300.0
    assert mem.last_interval("fast") is None
    # 落盘可回读
    mem2 = RetryMemory(tmp_path / "mem.json")
    assert mem2.last_interval("slow") == 300.0
    assert json.loads((tmp_path / "mem.json").read_text(encoding="utf-8"))["slow"]["waits"] == 1


def test_记忆文件损坏不影响调用(tmp_path):
    p = tmp_path / "mem.json"
    p.write_text("{ not json", encoding="utf-8")
    mem = RetryMemory(p)
    assert mem.last_interval("x") is None
    policy = RetryPolicy(memory=mem, profile="x")
    assert policy.current_interval == policy.base_interval


# --------------------------------------------------------------------------- T8.8 分档行为
def test_封禁级抛暂停而不是失败():
    """≥30 分钟的限速不该在进程里干等，而应暂停任务交给调度器。"""
    spy = _SleepSpy()
    policy = RetryPolicy(base_interval=10, max_retries=5, sleep=spy)
    seen: list = []

    async def fn():
        raise _status_error(429, {"Retry-After": "7200"})

    with pytest.raises(RateLimitPaused) as ei:
        _run(policy.call(fn, ))
    assert ei.value.wait_seconds == 7200
    assert ei.value.severity is RateLimitSeverity.BANNED
    assert spy.slept == [], "封禁级不能原地睡 2 小时"
    seen.append(1)


def test_重档动态扩展重试预算():
    """重档下 max_retries 应被放宽，避免"明明等到就好了却判失败"。"""
    policy = RetryPolicy(base_interval=1, max_retries=2, heavy_bonus=4, banned_bonus=7)
    assert policy.effective_max_retries() == 2
    policy.last_severity = RateLimitSeverity.HEAVY
    assert policy.effective_max_retries() == 6
    policy.last_severity = RateLimitSeverity.BANNED
    assert policy.effective_max_retries() == 9


def test_严重度回调被调用():
    spy = _SleepSpy()
    seen: list = []
    policy = RetryPolicy(base_interval=1, max_retries=3, sleep=spy,
                         on_severity=lambda sev, delay, exc: seen.append((sev, delay)))
    n = {"i": 0}

    async def fn():
        n["i"] += 1
        if n["i"] == 1:
            raise _status_error(429, {"Retry-After": "120"})
        return "ok"

    _run(policy.call(fn))
    assert seen == [(RateLimitSeverity.HEAVY, 120.0)], seen


def test_非可重试异常立即抛出():
    policy = RetryPolicy(max_retries=5, sleep=_SleepSpy())
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise ValueError("参数不对，重试无用")

    with pytest.raises(ValueError):
        _run(policy.call(fn))
    assert calls["n"] == 1, "永久错误不该重试"


# --------------------------------------------------------------------------- 联动
def _session(tmp_path) -> WindowSession:
    db = Database(tmp_path / "t.db")
    w = Window(id="w1", name="w1", role="worker_programmer",
               workspace_path=str(tmp_path / "ws"), model="", mode="BUILD", status="idle")
    return WindowSession(w, db, db, db)


def _ctx(tmp_path) -> ToolContext:
    (tmp_path / "ws").mkdir(exist_ok=True)
    return ToolContext(window_id="w1", workspace=str(tmp_path / "ws"), mode="BUILD")


class _PausedLLM:
    async def chat(self, messages, tools=None, **kw):
        raise RateLimitPaused(3600.0, _status_error(429, {"Retry-After": "3600"}))


class _OkLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **kw):
        self.calls += 1
        return LLMResponse(content="干完了")


def test_封禁级限速让任务暂停为_rate_limited_而不是_error(tmp_path):
    loop = AgentLoop(llm=_PausedLLM(), registry=build_registry(),
                     session=_session(tmp_path), transient_delay=0.0)
    res = _run(loop.run("干活", _ctx(tmp_path)))
    assert res.stop_reason == "rate_limited", f"应为暂停，实际 {res.stop_reason}"
    assert "限速暂停" in res.content
    assert res.transient_retries == 0, \
        "封禁级必须立即暂停，不能先按普通瞬态续跑（否则白等好几轮退避）"


def test_PromptRunner_把暂停映射为_rate_limited(tmp_path):
    """调度器只对 rate_limited 标记暂停（不累加连续失败），所以映射必须对。"""
    loop_llm = _PausedLLM()
    runner = PromptRunner()
    env = TaskEnv(window=None, session=_session(tmp_path), registry=build_registry(),
                  llm_factory=lambda p=None: loop_llm, builder=None, ctx=_ctx(tmp_path))
    task = type("T", (), {"prompt": "干活", "llm_profile": "", "job_id": "j1"})()
    out = _run(runner.run(task, env))
    assert out.status == "rate_limited"
    assert out.meta["stop_reason"] == "rate_limited"


def test_正常成功仍是_ok(tmp_path):
    runner = PromptRunner()
    env = TaskEnv(window=None, session=_session(tmp_path), registry=build_registry(),
                  llm_factory=lambda p=None: _OkLLM(), builder=None, ctx=_ctx(tmp_path))
    task = type("T", (), {"prompt": "干活", "llm_profile": "", "job_id": "j1"})()
    out = _run(runner.run(task, env))
    assert out.status == "ok" and out.summary == "干完了"


def test_main_封禁级发出_rate_limit_paused_事件(tmp_path, monkeypatch):
    from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig
    from dogbuddy.main import DogBuddyApp
    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="t", port=8129),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)

    captured: list = []
    app.bus.emit = lambda ev: captured.append(ev)  # type: ignore[assignment]
    app._on_llm_severity("fast", RateLimitSeverity.BANNED, 7200.0, RuntimeError("429"))

    assert any(e.type == "rate_limit.paused" for e in captured), captured
    paused = [e for e in captured if e.type == "rate_limit.paused"][0]
    assert paused.payload["delay"] == 7200.0
    assert app._rate_limit_severity == "banned"
    assert app._rate_limit["waiting"] is False, "封禁级不是'等待中'，是'已暂停'"

    # 重档：不发暂停事件，但要记下等待时长（供迭代降速）
    captured.clear()
    app._on_llm_severity("fast", RateLimitSeverity.HEAVY, 300.0, None)
    assert not [e for e in captured if e.type == "rate_limit.paused"]
    assert app._last_rate_limit_wait == 300.0
    assert app._rate_limit["severity"] == "heavy"


def test_退避记忆接进_profile_registry(tmp_path):
    """profile 维度必须真的用上同一份记忆（否则"起跳"在真实链路上不生效）。"""
    from dogbuddy.config import AppConfig, LLMConfig
    from dogbuddy.llm.profile_registry import LLMProfileRegistry

    mem = RetryMemory(tmp_path / "mem.json")
    mem.note_wait("slow", 240.0, "heavy")
    reg = LLMProfileRegistry(AppConfig(llm=LLMConfig(base_url="http://x/v1", api_key="k")),
                             retry_memory=mem)
    slow = reg.provider_for("slow")
    fast = reg.provider_for(None)
    assert slow.retry.current_interval == 240.0, "绑 slow 的 provider 应从记忆量级起跳"
    assert fast.retry.current_interval == fast.retry.base_interval
    assert slow.retry.profile == "slow"
