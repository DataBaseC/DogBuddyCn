# DogBuddyCn — 运行中压缩（v0.10.46）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner：「显示的缓存命中率比较低，请排查」。

实测同一工位 90% 与 0% **交替**（平均被拉到 34%）：压缩以前只在 run **结束后**
跑一次，而一次工具密集的运行里消息数会从 48 冲到 60+ —— `ContextBuilder` 只取
`recent(60)`，窗口中途滑动 → 请求头部位移 → 提供商前缀匹配不上 → 那几次调用
0% 命中。修法：`build_context` 之前（第 2 轮起）也检查一次压缩。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.loop import AgentLoop  # noqa: E402
from dogbuddy.tools.registry import ToolContext, ToolResult  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _TC:
    def __init__(self, name="shell", args=None, cid="c1"):
        self.id, self.name, self.arguments = cid, name, (args or {})


class _Resp:
    def __init__(self, content="", tool_calls=None, usage=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage or {}
        self.finish_reason = "stop"


class _Session:
    def __init__(self):
        self.appended = []

    def append(self, role, content, tokens=0, meta=None):
        self.appended.append((role, content))
        return None

    def build_context(self, builder):
        return []

    def recent(self, n=200):
        return []


class _Registry:
    def schemas(self, only=None):
        return []

    async def execute(self, name, args, ctx):
        return ToolResult.success("ok")


class _Compactor:
    def __init__(self):
        self.calls = 0

    async def maybe_compact(self, session):
        self.calls += 1
        return None


class _LLM:
    """第 1 轮返回一个工具调用，第 2 轮收尾 —— 保证真的走两轮。"""

    def __init__(self):
        self.n = 0

    async def chat(self, messages, tools=None):
        self.n += 1
        if self.n == 1:
            return _Resp(tool_calls=[_TC()])
        return _Resp(content="done")


def _ctx() -> ToolContext:
    return ToolContext(window_id="w1", workspace=".", mode="AUTO", actor="worker")


def test_compaction_runs_between_iterations():
    comp = _Compactor()
    loop = AgentLoop(llm=_LLM(), registry=_Registry(), session=_Session(),
                     compactor=comp, tool_names=[], max_iterations=5)
    res = _run(loop.run("任务", _ctx()))
    assert res.stop_reason == "done" and res.iterations == 2
    # 1 次在**第 2 轮之前**（新加的）、1 次在运行结束后（原有的收尾压缩）
    assert comp.calls == 2, f"两轮运行应压 2 次（中途 1 + 收尾 1），实际 {comp.calls}"


def test_first_iteration_does_not_pay_for_compaction():
    """开跑第一轮不必压（省一次 LLM 调用）；只有续轮才检查。

    单轮运行只会有收尾那次（1 次），而不是 2 次。
    """
    comp = _Compactor()

    class OneShot:
        async def chat(self, messages, tools=None):
            return _Resp(content="ok")

    loop = AgentLoop(llm=OneShot(), registry=_Registry(), session=_Session(),
                     compactor=comp, tool_names=[], max_iterations=5)
    _run(loop.run("任务", _ctx()))
    assert comp.calls == 1, "单轮只该有收尾压缩，不该多付一次"


def test_compaction_failure_does_not_kill_the_run():
    class Boom:
        async def maybe_compact(self, session):
            raise RuntimeError("compactor down")

    loop = AgentLoop(llm=_LLM(), registry=_Registry(), session=_Session(),
                     compactor=Boom(), tool_names=[], max_iterations=5)
    res = _run(loop.run("任务", _ctx()))
    assert res.stop_reason == "done", "压缩失败不能把运行带崩"
