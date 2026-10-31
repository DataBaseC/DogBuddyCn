# DogBuddyCn — 临时 skill + 稳定上下文前缀（0.18.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""两个 owner 实测问题的回归：

  ① 员工无法创建临时 skill —— 以前 `skills.py` 是 V1 stub，工具也不存在；
  ② 缓存命中低 —— `build_context` 只取最近 60 条，而压缩阈值是 96 条，
     60~96 之间每轮窗口滑动，服务商前缀缓存只能命中系统提示。

这里把“窗口隔离 / 下一轮注入 / 不滑动”钉死。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.skills import create_skill, delete_skill, load_skills  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402
from dogbuddy.tools.skill_ops import (  # noqa: E402
    SkillCreateTool, SkillDeleteTool, SkillListTool)
from dogbuddy.windows.context import ContextBuilder  # noqa: E402


def _ctx(window_id: str, actor: str = "worker") -> ToolContext:
    return ToolContext(window_id=window_id, workspace=".", mode="BUILD", actor=actor)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_skill_is_window_scoped_and_deletable(tmp_path):
    root = tmp_path / "skills"
    data = create_skill(root, "test-playbook", "1. run pytest\n2. report failures",
                        window_id="w1", description="how I test", actor="worker")
    assert data["slug"] == "test-playbook"
    assert (root / "test-playbook" / "SKILL.md").exists()
    assert len(load_skills(root, "w1")) == 1
    assert load_skills(root, "w2") == [], "别的工位不能看到"
    # 别的工位不能删
    try:
        delete_skill(root, "test-playbook", window_id="w2")
        raise AssertionError("cross-window delete should fail")
    except ValueError:
        pass
    assert delete_skill(root, "test-playbook", window_id="w1") is True
    assert load_skills(root, "w1") == []


def test_skill_tools_create_list_delete(tmp_path):
    root = str(tmp_path / "skills")
    ctx = _ctx("w1")
    out = _run(SkillCreateTool(root).execute(
        {"name": "deploy", "instructions": "1. backup\n2. deploy\n3. health check"}, ctx))
    assert out.ok, out
    listed = _run(SkillListTool(root).execute({}, ctx))
    assert "deploy" in listed.output
    # 另一个工位看不到
    other = _run(SkillListTool(root).execute({}, _ctx("w2")))
    assert "deploy" not in other.output
    deleted = _run(SkillDeleteTool(root).execute({"name": "deploy"}, ctx))
    assert deleted.ok


def test_worker_registry_exposes_skill_tools(tmp_path):
    from dogbuddy.agents.worker_programmer.tools import build_worker_registry
    reg = build_worker_registry("worker_programmer", skills_root=str(tmp_path / "skills"))
    names = {s["function"]["name"] for s in reg.schemas(only=None)}
    assert {"skill_create", "skill_list", "skill_delete"} <= names
    # 测试员是只读角色，不给写 skill（保持“发现问题只回报”的边界）
    from dogbuddy.agents.core.roles import TESTER_TOOLS
    assert "skill_create" not in TESTER_TOOLS


def test_skill_injected_into_system_prompt(tmp_path):
    root = tmp_path / "skills"
    create_skill(root, "verify", "always run the smoke test before finishing",
                 window_id="w1", description="finish checklist")
    session = SimpleNamespace(
        window=SimpleNamespace(id="w1", name="w", role="worker_programmer",
                               workspace_path="/tmp", mode="BUILD"),
        effective_workspace="/tmp",
        compaction_history=lambda: [],
    )
    builder = ContextBuilder(skills_root=str(root))
    block = builder.system_block(session)
    assert "Temporary skills" in block and "always run the smoke test" in block
    # 别的工位不注入
    session2 = SimpleNamespace(
        window=SimpleNamespace(id="w2", name="w2", role="worker_programmer",
                               workspace_path="/tmp", mode="BUILD"),
        effective_workspace="/tmp", compaction_history=lambda: [],
    )
    assert "Temporary skills" not in builder.system_block(session2)


def test_context_does_not_slide_before_compaction():
    """70 条消息必须全部保留；旧默认 60 条会从第 61 条起每轮滑一条。"""
    msgs = [SimpleNamespace(role="user", content=f"m{i}", meta={}) for i in range(70)]
    session = SimpleNamespace(
        window=SimpleNamespace(id="w1", name="w", role="worker_programmer",
                               workspace_path="/tmp", mode="BUILD"),
        effective_workspace="/tmp", compaction_history=lambda: [],
        recent=lambda limit=200: msgs[-limit:],
    )
    built = ContextBuilder().build(session)
    assert built[0]["role"] == "system"
    assert len(built) == 71, "默认 400 条内不允许裁剪（压缩器在 96 条才动手）"
    assert built[1]["content"] == "m0"
    assert built[-1]["content"] == "m69"


def test_frontend_trims_trailing_newline():
    root = Path(__file__).resolve().parents[1] / "dogbuddy/web/static/js"
    admin = (root / "views-chat.js").read_text(encoding="utf-8")
    worker = (root / "views-main.js").read_text(encoding="utf-8")
    assert "replace(/\\s+$/, '')" in admin
    assert "replace(/\\s+$/, '')" in worker
