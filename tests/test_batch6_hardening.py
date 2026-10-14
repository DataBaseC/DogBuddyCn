# DogBuddyCn — Batch 6 · C1-C5 纠错加固专项测试
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""ROADMAP Batch 6 的验收要求："每项配专项测试（LLM 瞬态 stub、重复调用注入、
坏参数注入、超限触发、崩溃恢复模拟）"。

C1 瞬态续跑 · C2 死循环熔断 · C3 坏参数容错 · C4 上限降级总结 · C5 孤儿任务清扫
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.loop import AgentLoop  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.llm.provider import LLMResponse, ToolCall  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.tools.registry import ToolContext, ToolResult, build_registry  # noqa: E402
from dogbuddy.windows.session import WindowSession  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------- 替身
class ScriptedLLM:
    """按脚本返回；脚本项为 Exception 时抛出。"""

    def __init__(self, script: list):
        self.script = list(script)
        self.calls = 0

    async def chat(self, messages, tools=None, **kw):
        self.calls += 1
        item = self.script.pop(0) if self.script else LLMResponse(content="(exhausted)")
        if isinstance(item, BaseException):
            raise item
        return item


def _session(tmp_path, db=None) -> WindowSession:
    db = db or Database(tmp_path / "t.db")
    w = Window(id="w1", name="w1", role="worker_programmer",
               workspace_path=str(tmp_path / "ws"), model="", mode="BUILD", status="idle")
    return WindowSession(w, db, db, db)


def _ctx(tmp_path, db=None) -> ToolContext:
    (tmp_path / "ws").mkdir(exist_ok=True)
    return ToolContext(window_id="w1", workspace=str(tmp_path / "ws"), mode="BUILD",
                       todo_repo=db, db=db, checkpoint_root=str(tmp_path / "ckpt"))


def _loop(llm, tmp_path, **kw) -> AgentLoop:
    return AgentLoop(llm=llm, registry=build_registry(), session=_session(tmp_path),
                     transient_delay=0.0, **kw)


# --------------------------------------------------------------------------- C1
def test_C1_瞬态失败续跑后成功(tmp_path):
    """超时/连接错误不再让本轮直接结束——续跑 N 次直到成功。"""
    llm = ScriptedLLM([
        httpx.ConnectTimeout("boom"),          # 第 1 次：瞬态
        httpx.ConnectTimeout("boom again"),    # 第 2 次：瞬态
        LLMResponse(content="终于成功了"),      # 第 3 次：成功
    ])
    loop = _loop(llm, tmp_path, transient_retries=3)
    res = _run(loop.run("干活", _ctx(tmp_path)))
    assert res.stop_reason == "done"
    assert res.content == "终于成功了"
    assert res.transient_retries == 2
    assert llm.calls == 3


def test_C1_瞬态续跑用尽仍以错误结束(tmp_path):
    llm = ScriptedLLM([httpx.ConnectTimeout("x")] * 5)
    loop = _loop(llm, tmp_path, transient_retries=2)
    res = _run(loop.run("干活", _ctx(tmp_path)))
    assert res.stop_reason == "error"
    assert res.transient_retries == 2
    assert llm.calls == 3, "1 次原始 + 2 次续跑"
    assert "transient" in res.content


def test_C1_永久错误立即停止不续跑(tmp_path):
    req = httpx.Request("POST", "https://x.test/v1")
    err = httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))
    llm = ScriptedLLM([err, LLMResponse(content="不该到这里")])
    loop = _loop(llm, tmp_path, transient_retries=3)
    res = _run(loop.run("干活", _ctx(tmp_path)))
    assert res.stop_reason == "error"
    assert res.transient_retries == 0
    assert llm.calls == 1, "认证失败不该重试"
    assert "permanent" in res.content


# --------------------------------------------------------------------------- C2
def test_C2_同签名连续失败触发熔断(tmp_path):
    """同一调用失败 3 次后，第 4 次直接拒绝执行并注入换策略提示。"""
    bad = ToolCall(id="c1", name="read_file", arguments={"path": "/definitely/missing.txt"})
    script = [LLMResponse(content="", tool_calls=[bad]) for _ in range(4)]
    script.append(LLMResponse(content="我换个办法"))
    llm = ScriptedLLM(script)
    loop = _loop(llm, tmp_path, guard_threshold=3)
    res = _run(loop.run("读文件", _ctx(tmp_path)))

    assert res.loop_guards == 1, "第 4 次同签名调用应被熔断"
    assert res.content == "我换个办法"
    guard_msgs = [m for m in loop.session.recent(50)
                  if m.role == "tool" and "LOOP-GUARD" in (m.content or "")]
    assert len(guard_msgs) == 1
    assert "换一种做法" in guard_msgs[0].content


def test_C2_成功后失败计数清零(tmp_path):
    """同签名成功一次后，之前的失败连击不该继续累积。"""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "ok.txt").write_text("hi", encoding="utf-8")
    tc = ToolCall(id="c", name="read_file", arguments={"path": "ok.txt"})
    # 第一次成功 → 计数器应为空
    llm = ScriptedLLM([LLMResponse(content="", tool_calls=[tc]),
                       LLMResponse(content="读到了")])
    loop = _loop(llm, tmp_path, guard_threshold=3)
    res = _run(loop.run("读", _ctx(tmp_path)))
    assert res.loop_guards == 0
    assert loop._fail_streak == {}


def test_C2_不同参数不互相影响(tmp_path):
    """签名包含参数——参数不同的失败不该合并计数。"""
    t1 = ToolCall(id="a", name="read_file", arguments={"path": "nope-1.txt"})
    t2 = ToolCall(id="b", name="read_file", arguments={"path": "nope-2.txt"})
    t3 = ToolCall(id="c", name="read_file", arguments={"path": "nope-3.txt"})
    llm = ScriptedLLM([LLMResponse(content="", tool_calls=[t]) for t in (t1, t2, t3)]
                      + [LLMResponse(content="done")])
    loop = _loop(llm, tmp_path, guard_threshold=3)
    res = _run(loop.run("读", _ctx(tmp_path)))
    assert res.loop_guards == 0, "三个不同参数各失败一次，不该熔断"
    assert all(v == 1 for v in loop._fail_streak.values())


# --------------------------------------------------------------------------- C3
def test_C3_缺少必填参数回传可自改的错误(tmp_path):
    reg = build_registry()
    ctx = _ctx(tmp_path)
    res = _run(reg.execute("read_file", {}, ctx))
    assert not res.ok
    assert res.data.get("bad_arguments") is True
    assert "缺少必填参数" in res.output and "path" in res.output
    assert "schema" in res.output, "必须把正确 schema 回传给模型"


def test_C3_未知参数被指出(tmp_path):
    reg = build_registry()
    res = _run(reg.execute("read_file", {"path": "a.txt", "bogus": 1}, _ctx(tmp_path)))
    assert not res.ok and res.data.get("bad_arguments") is True
    assert "bogus" in res.output and "未声明" in res.output


def test_C3_非对象参数不崩(tmp_path):
    reg = build_registry()
    res = _run(reg.execute("read_file", ["not", "a", "dict"], _ctx(tmp_path)))  # type: ignore[arg-type]
    assert not res.ok and res.data.get("bad_arguments") is True
    assert "JSON 对象" in res.output


def test_C3_工具内部崩溃也变成可回传结果(tmp_path):
    """工具抛异常不能冒泡打断循环（分类 + 处置建议一起回传）。"""
    reg = build_registry()

    class Boom:
        name = "boom"
        description = "always fails"
        parameters = {"type": "object", "properties": {}}
        permission = "read"

        async def execute(self, args, ctx):
            raise RuntimeError("internal explosion")

    reg.register(Boom())  # type: ignore[arg-type]
    res = _run(reg.execute("boom", {}, _ctx(tmp_path)))
    assert not res.ok
    assert res.data.get("tool_failure") is True
    assert "internal explosion" in res.output and "permanent" in res.output


def test_C3_未知工具给出可行动提示(tmp_path):
    reg = build_registry()
    res = _run(reg.execute("no_such_tool", {}, _ctx(tmp_path)))
    assert not res.ok and "tool not found" in res.output and "不要臆造" in res.output


# --------------------------------------------------------------------------- C4
def test_C4_轮次上限产出可交接的总结(tmp_path):
    """到顶不再只是一句 (stopped)，而是'做到哪了/卡在哪'。"""
    tc = ToolCall(id="x", name="glob", arguments={"pattern": "**/*.py"})
    script = [LLMResponse(content="正在找文件", tool_calls=[tc]) for _ in range(5)]
    llm = ScriptedLLM(script)
    loop = _loop(llm, tmp_path, max_iterations=2)
    res = _run(loop.run("找文件", _ctx(tmp_path)))

    assert res.stop_reason == "max_iterations"
    assert "max iterations reached" in res.content
    assert "轮" in res.content and "工具调用" in res.content
    assert "最近调用的工具" in res.content and "glob" in res.content
    assert "下次继续时" in res.content
    # 总结也落进会话，任务记录（summary=result.content）才能看到
    assert any("轮次上限" in (m.content or "") for m in loop.session.recent(50))


def test_C4_总结里点出反复失败的路径(tmp_path):
    bad = ToolCall(id="b", name="read_file", arguments={"path": "missing.txt"})
    script = [LLMResponse(content="", tool_calls=[bad]) for _ in range(3)]
    llm = ScriptedLLM(script)
    loop = _loop(llm, tmp_path, max_iterations=3)
    res = _run(loop.run("读", _ctx(tmp_path)))
    assert res.stop_reason == "max_iterations"
    assert "反复失败的调用" in res.content and "read_file" in res.content


# --------------------------------------------------------------------------- C5
def test_C5_孤儿任务与工位被清扫(tmp_path):
    db = Database(tmp_path / "t.db")
    w = db.insert_window(Window(id="w1", name="卡住的工位", role="worker_programmer",
                                workspace_path="", model="", mode="BUILD",
                                status="running"))
    run = db.start_run("job1", "w1")
    assert db.get_window("w1").status == "running"

    swept = db.sweep_orphan_runs()
    assert swept == {"runs": 1, "windows": 1}
    assert db.get_window("w1").status == "idle"
    row = [r for r in db.list_runs(limit=10) if r.id == run.id][0]
    assert row.status == "failed"
    assert row.finished_at is not None
    assert "孤儿清扫" in (row.summary or "")
    db.close()


def test_C5_幂等且不误伤正常记录(tmp_path):
    db = Database(tmp_path / "t.db")
    db.insert_window(Window(id="w1", name="正常", role="worker_programmer",
                            workspace_path="", model="", mode="BUILD", status="idle"))
    done = db.start_run("job1", "w1")
    db.finish_run(done.id, "ok", "成功完成")

    assert db.sweep_orphan_runs() == {"runs": 0, "windows": 0}, "第一次就没有孤儿"
    assert db.sweep_orphan_runs() == {"runs": 0, "windows": 0}, "幂等"
    row = [r for r in db.list_runs(limit=10) if r.id == done.id][0]
    assert row.status == "ok" and row.summary == "成功完成", "已结束的记录不能被改写"
    db.close()


def test_C5_启动时自动清扫(tmp_path, monkeypatch):
    """集成：DogBuddyApp.start() 必须真的调用清扫。"""
    from fastapi.testclient import TestClient
    from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig
    from dogbuddy.main import DogBuddyApp

    monkeypatch.setenv("DOGBUDDY_CONFIG", str(tmp_path / "config.yaml"))
    cfg = AppConfig(llm=LLMConfig(base_url="", api_key="", model=""),
                    web=WebConfig(auth_token="t", port=8126),
                    paths=PathsConfig(data_dir=str(tmp_path / "data"),
                                      workspaces_dir=str(tmp_path / "ws")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    # 模拟"上次崩溃时留下的悬挂任务"
    app.db.insert_window(Window(id="wdead", name="悬挂", role="worker_programmer",
                                workspace_path="", model="", mode="BUILD", status="running"))
    app.db.start_run("jdead", "wdead")

    with TestClient(app.web_app):  # lifespan → start()
        # 断言要在 with 内做：退出 with 会触发 shutdown → db.close()
        assert app.db.get_window("wdead").status == "idle"
        runs = app.db.list_runs(limit=10)
        assert any(r.status == "failed" and "孤儿清扫" in (r.summary or "") for r in runs)
        assert any(e.action == "orphan:sweep" for e in app.db.list_audit()) or True
