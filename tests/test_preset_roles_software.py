# DogBuddyCn — 软件开发系列预设角色 + page_check（v0.10.49）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""owner：「新建几个预设角色，软件开发系列的，比如测试员（可以用浏览器沙盒进行测试）等等。」

两条不变量：
  ① **只读角色不许有写工具**——测试员/审查员/架构师若拿到 write_file，就会"顺手改一下"，
     审查结论随即被自己污染；
  ② page_check 必须在注册表里，且只能读工作区（越界一律拒）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.roles import get_role, list_roles  # noqa: E402
from dogbuddy.tools.page_check import PageCheckTool  # noqa: E402
from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402

WRITE_TOOLS = {"write_file", "edit_file", "checkpoint_create", "checkpoint_rollback",
               "shell", "server_exec", "member_dispatch"}


def test_software_roles_registered():
    names = {r.name for r in list_roles()}
    for n in ("worker_tester", "worker_frontend", "worker_backend",
              "worker_reviewer", "worker_architect"):
        assert n in names, f"缺预设角色 {n}"
    assert get_role("worker_tester").display_name == "测试员"


def test_read_only_roles_have_no_write_tools():
    for name in ("worker_tester", "worker_reviewer", "worker_architect"):
        tools = set(get_role(name).tool_names)
        bad = tools & {"write_file", "edit_file", "checkpoint_create", "checkpoint_rollback"}
        assert not bad, f"{name} 是只读角色，却拿到了写工具 {bad}"
    # 架构师连 shell 都不给：有 shell 就有一百种改文件的办法
    assert "shell" not in set(get_role("worker_architect").tool_names)
    # 测试员要能跑测试，所以 shell 是允许的（但它是只读语义，写在提示词里）
    assert "shell" in set(get_role("worker_tester").tool_names)


def test_tester_can_do_page_checks():
    assert "page_check" in get_role("worker_tester").tool_names
    assert "page_check" in get_role("worker_frontend").tool_names
    assert "page_check" in build_registry().names()


def test_page_check_reports_real_problems(tmp_path):
    (tmp_path / "ok.html").write_text(
        '<!DOCTYPE html><html lang="zh-CN"><head><title>t</title>'
        '<meta name="description" content="d"><meta name="viewport" content="width=device-width">'
        '</head><body><h1>好</h1><img src="a.png" alt="图"></body></html>', encoding="utf-8")
    (tmp_path / "a.png").write_bytes(b"\x89PNG")
    (tmp_path / "bad.html").write_text(
        '<html><head><title>t</title></head>'
        '<body><div><img src="missing.png">'
        '<link rel="stylesheet" href="ghost.css"></body>', encoding="utf-8")

    ctx = ToolContext(window_id="w1", workspace=str(tmp_path), mode="BUILD")
    tool = PageCheckTool()
    res = asyncio.new_event_loop().run_until_complete(tool.execute({"path": "."}, ctx))
    assert res.ok
    assert "✓ ok.html" in res.output, res.output
    out = res.output
    # bad.html 的问题清单
    assert "missing.png" in out and "ghost.css" in out
    assert "div" in out, "未闭合 <div> 必须报出来"
    assert "viewport" in out and "description" in out and "lang" in out
    assert "alt" in out


def test_page_check_refuses_to_escape_workspace(tmp_path):
    ctx = ToolContext(window_id="w1", workspace=str(tmp_path), mode="BUILD")
    res = asyncio.new_event_loop().run_until_complete(
        PageCheckTool().execute({"path": "../../etc/passwd"}, ctx))
    assert not res.ok and "escape" in res.output.lower()
