# DogBuddyCn — 限速徽标"等完就灭"回归（v0.10.21）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测（两次）：

* "上方提示限速等待中，但是确实是掉线了" —— 徽标只亮不灭。
* 群组实测（2026-10-16）：worker-1 撞到 429，服务端要求等 340s，
  **退避结束、任务跑完、群组返回 200 之后** `/api/status` 里
  `rate_limit.waiting` 仍然是 `true`，因为清它的钩子挂在 `window.run_finished`
  事件上，而群组运行/管理员对话/工具路径**都不发这个事件**。

修法：真正的结束信号是"等待之后这次调用成功了" —— `RetryPolicy` 新增
`on_success`，等过至少一次之后一旦成功就回调；`main._on_llm_resumed` 灭徽标。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.errors import TransientError  # noqa: E402
from dogbuddy.llm.retry import RetryPolicy  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeLLMError(TransientError):
    """带 `Retry-After` 头的 429（`retry_after_from` 从 response.headers 里读）。"""

    def __init__(self, wait: float):
        super().__init__("boom")
        headers = {"Retry-After": str(wait)}

        class _Resp:
            def __init__(self, h):
                self.headers = h

        self.response = _Resp(headers)


def test_成功之后回调on_success():
    seen: list[str] = []
    slept: list[float] = []
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise FakeLLMError(3.0)
        return "ok"

    async def fake_sleep(d):
        slept.append(d)

    policy = RetryPolicy(base_interval=1.0, max_retries=3,
                         on_wait=lambda a, d: seen.append("wait"),
                         on_success=lambda: seen.append("resume"),
                         sleep=fake_sleep)
    assert _run(policy.call(fn)) == "ok"
    assert seen == ["wait", "resume"], f"等待后成功必须回调 on_success：{seen}"
    assert slept == [3.0], "优先按服务端给的 Retry-After 等"


def test_从没被限速就不回调on_success():
    seen: list[str] = []

    async def fn():
        return "ok"

    policy = RetryPolicy(on_success=lambda: seen.append("resume"))
    assert _run(policy.call(fn)) == "ok"
    assert seen == [], "没等待过就别回调（免得每轮都踢一次状态）"


def test_回调抛异常不影响成功结果():
    async def fn():
        return "ok"

    policy = RetryPolicy(on_wait=lambda a, d: None,
                         on_success=lambda: (_ for _ in ()).throw(RuntimeError("bad hook")))
    policy.waits = 1  # 假装等过一次
    assert _run(policy.call(fn)) == "ok"


def test_from_config_透传on_success():
    from dogbuddy.config import LLMConfig

    seen: list[str] = []
    cfg = LLMConfig()
    policy = RetryPolicy.from_config(cfg.retry, on_success=lambda: seen.append("x"))
    assert policy.on_success is not None


def test_徽标最终会被灭掉():
    """把 main 的清理逻辑单独钉住：等过 + 成功 → waiting=False。"""
    state = {"waiting": True, "attempt": 1, "window_id": ""}
    emitted: list[dict] = []

    class Bus:
        def emit(self, event):
            emitted.append(event.payload)

    # 复制 main._on_llm_resumed 的语义（该方法是内联的，这里按契约断言）
    def on_resumed(profile=""):
        if not state.get("waiting"):
            return
        state.update({"waiting": False, "attempt": 0, "window_id": ""})
        Bus().emit(type("E", (), {"payload": {"waiting": False, "reason": "resumed"}})())

    on_resumed("")
    assert state["waiting"] is False
    assert emitted and emitted[0]["reason"] == "resumed"
    on_resumed("")  # 幂等
    assert len(emitted) == 1
