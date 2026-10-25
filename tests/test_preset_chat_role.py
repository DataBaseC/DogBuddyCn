# DogBuddyCn — 纯聊天预设角色（v0.10.56）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""owner：「员工加一个纯聊天的预设」，用途是跑团/角色扮演。

不变量：**一个能改文件、能跑命令的"聊天员"不是聊天员**。
它的工具箱里只该有：工位记忆（读写）、项目记忆（只读）、通知、提问。
连 read_file / web_search 都不给——跑团不需要，给了只会白占 schema token，
还会让模型以为自己能去查资料/改文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.roles import get_role, list_roles  # noqa: E402

FORBIDDEN = {
    "read_file", "write_file", "edit_file", "glob", "grep", "shell", "server_exec",
    "web_search", "web_fetch", "explore_subagent", "checkpoint_create",
    "checkpoint_rollback", "member_dispatch", "schedule", "reschedule",
}
EXPECTED = {"window_memory_read", "window_memory_write", "project_memory_read",
            "notify_owner", "ask_user"}


def test_chat_role_is_registered():
    names = {r.name for r in list_roles()}
    assert "worker_chat" in names
    assert get_role("worker_chat").display_name == "聊天员"


def test_chat_role_has_no_capability_tools():
    tools = set(get_role("worker_chat").tool_names)
    assert not (tools & FORBIDDEN), f"纯聊天角色不该有这些工具：{sorted(tools & FORBIDDEN)}"


def test_chat_role_keeps_only_talk_tools():
    assert set(get_role("worker_chat").tool_names) == EXPECTED


def test_chat_role_meta_is_sane():
    r = get_role("worker_chat")
    assert r.default_mode == "BUILD"
    assert r.needs_approval is False
    assert "角色扮演" in r.domains
    assert "没有任何文件" in r.note


def test_chat_role_prompt_forbids_fake_actions():
    prompt = get_role("worker_chat").system_prompt
    assert "Never claim you edited a file" in prompt, \
        "必须明确禁止「假装改了文件」——它没有任何工具能做到"
