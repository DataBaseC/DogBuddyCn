# DogBuddyCn — 群组运行的挂钟预算回归（v0.10.22）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测（2026-10-16，统一模型实验）：三个工位都指向同一个 key 时，
`deepseek-v4-flash` 的限速一路升级到单次退避 **600 秒**（`retry_memory.json`:
`last_interval=600, waits=7, severity=heavy`），5 分钟只跑完 1/3 个成员。

限速退避本身是"无限耐心"的设计（heavy 档还会追加重试预算），而 `GroupManager.run`
**没有任何总时间上限，也没有取消入口** —— 一次群组运行可以挂几小时。
这里用已有的 `task_timeout` 给每个成员封顶：到点停下并如实记 timeout。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.roles import register_builtin_roles  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.llm.provider import LLMResponse  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.services.group_manager import GroupManager  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class SlowLLM:
    """永远"在限速里等"的假模型：一次 chat 要 sleep 很久。"""

    def __init__(self, delay: float = 3.0):
        self.delay = delay

    async def chat(self, messages, tools=None, **kw):
        await asyncio.sleep(self.delay)
        return LLMResponse(content="done")

    async def stream_chat(self, messages, tools=None, **kw):
        await asyncio.sleep(self.delay)
        yield type("C", (), {"delta": "", "tool_calls": [], "finish_reason": "stop",
                             "usage": {}, "done": True})()


def _stack(tmp_path, task_timeout: float):
    register_builtin_roles()
    db = Database(str(tmp_path / "g.db"))
    llm = SlowLLM(delay=3.0)
    wm = WindowManager(db, llm_factory=lambda *a, **k: llm, bus=None,
                       checkpoints_dir=tmp_path / "ck")
    # 生产里参数解析器由 main 装配（config.effective_params）；这里等价注入，
    # 顺带钉住"群组预算取的是工位自己的 task_timeout"。
    wm.param_resolver = lambda w, project=None: {
        "max_iterations": 25, "max_context_tokens": 128000,
        "compact_threshold": 0.8, "task_timeout": task_timeout}
    for name in ("alice", "bob"):
        wm.create(name, workspace_path=str(tmp_path / name))
    gm = GroupManager(db, windows=wm)
    gm.create("slow-team")
    gm.assign("slow-team", db.get_window_by_name("alice").id, role="member")
    gm.assign("slow-team", db.get_window_by_name("bob").id, role="lead")
    return db, gm


def test_成员超过挂钟预算就停下并记timeout(tmp_path):
    db, gm = _stack(tmp_path, task_timeout=0.3)
    result = _run(gm.run("slow-team", "任务"))
    assert len(result["steps"]) == 2, "两个成员都要有交代（不能无声消失）"
    assert all(s["ok"] is False for s in result["steps"])
    assert all(s.get("timeout") is True for s in result["steps"])
    assert "超时" in result["steps"][0]["summary"]
    assert "task_timeout" in result["steps"][0]["summary"], "要告诉用户怎么放宽"
    db.close()


def test_预算充足时正常跑完(tmp_path):
    db, gm = _stack(tmp_path, task_timeout=30)
    result = _run(gm.run("slow-team", "任务"))
    assert all(s["ok"] for s in result["steps"]), result["steps"]
    assert not any(s.get("timeout") for s in result["steps"])
    db.close()


def test_每步都回报recap用量与提示词长度(tmp_path):
    db, gm = _stack(tmp_path, task_timeout=30)
    result = _run(gm.run("slow-team", "任务"))
    for s in result["steps"]:
        assert "recap_tokens" in s and "prompt_chars" in s
    assert result["recap_total"]["chars"] >= 0
    db.close()
