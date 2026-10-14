# DogBuddyCn — Batch 7 · T7.4 放行开关端到端（工位参数 trust → ctx.extra → 放行）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""T7.4 的完整语义是"越界必须走 ELEVATED 信任级才放行"。

只把越界拦住是不够的——还要证明那个放行开关**真的可达**：给工位设
`params.trust = "elevated"`，执行时 `ctx.extra["trust"]` 必须是 `elevated`，
否则这套机制就成了死锁（拦住了却永远放不开）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.runners import TaskEnv  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.memory.service import MemoryService  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.services.window_manager import WindowManager  # noqa: E402
from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _wm(tmp_path) -> WindowManager:
    db = Database(tmp_path / "t.db")
    mem = MemoryService(tmp_path / "memory")
    return WindowManager(
        db=db, llm_factory=lambda: None, bus=EventBus(), memory=mem,
        checkpoints_dir=tmp_path / "ckpt", model_resolver=AppConfig().effective_model)


def test_默认信任级是_normal(tmp_path):
    wm = _wm(tmp_path)
    wm.db.insert_window(Window(id="w1", name="普通", role="worker_programmer",
                               workspace_path=str(tmp_path / "ws"), model="",
                               mode="BUILD", status="idle"))
    rt = wm._rt("w1") if hasattr(wm, "_rt") else None
    if rt is None:  # 未 boot 时直接按同一条规则算
        w = wm.db.get_window("w1")
        trust = (w.params or {}).get("trust", "normal")
        assert trust == "normal"
        return
    assert (rt.window.params or {}).get("trust", "normal") == "normal"


def test_工位参数_trust_elevated_注入到_ctx_extra(tmp_path):
    """核心不变量：window.params.trust 必须出现在工具 ctx.extra 里。"""
    wm = _wm(tmp_path)
    w = wm.db.insert_window(Window(id="w1", name="特权", role="worker_programmer",
                                   workspace_path=str(tmp_path / "ws"), model="",
                                   mode="BUILD", status="idle"))
    # DAO 的契约是按 `params` 传 dict，内部才序列化成 params_json
    wm.db.update_window("w1", params={"trust": "elevated"})

    w = wm.db.get_window("w1")
    assert (w.params or {}).get("trust") == "elevated"

    # 走真实执行路径：用一个假 runner 捕获 env.ctx.extra
    captured: dict = {}

    class ProbeRunner:
        kind = "probe"

        async def run(self, task, env: TaskEnv):
            captured["extra"] = dict(env.ctx.extra or {})
            from dogbuddy.agents.core.runners import RunnerResult
            return RunnerResult(status="ok", summary="probe")

    from dogbuddy.agents.core.runners import RunnerRegistry
    reg = RunnerRegistry()
    reg.register(ProbeRunner())
    wm.runners = reg
    rt = wm._rt("w1")
    _run(wm._execute(rt, type("T", (), {"prompt": "x", "kind": "probe", "job_id": None,
                                       "llm_profile": ""})()))

    assert captured["extra"].get("trust") == "elevated", \
        f"trust 没传进工具上下文: {captured['extra']}"


def test_elevated_工位的越界命令被放行(tmp_path):
    """把三块拼起来：工位 trust=elevated → shell 越界不再被拒。"""
    from dogbuddy.db import Database as DB
    db = DB(tmp_path / "t.db")
    reg = build_registry(audit=db)
    (tmp_path / "ws").mkdir(exist_ok=True)
    ctx = ToolContext(window_id="w1", workspace=str(tmp_path / "ws"), mode="BUILD",
                      actor="worker", db=db, checkpoint_root=str(tmp_path / "ckpt"),
                      extra={"trust": "elevated"})
    res = _run(reg.execute("shell", {"command": "cd .. && echo ok"}, ctx))
    assert res.ok, res.output
    db.close()


def test_normal_工位的越界命令被拒(tmp_path):
    from dogbuddy.db import Database as DB
    db = DB(tmp_path / "t.db")
    reg = build_registry(audit=db)
    (tmp_path / "ws").mkdir(exist_ok=True)
    ctx = ToolContext(window_id="w1", workspace=str(tmp_path / "ws"), mode="BUILD",
                      actor="worker", db=db, checkpoint_root=str(tmp_path / "ckpt"),
                      extra={"trust": "normal"})
    res = _run(reg.execute("shell", {"command": "cd .. && echo ok"}, ctx))
    assert not res.ok and res.data.get("boundary") is True
    db.close()
