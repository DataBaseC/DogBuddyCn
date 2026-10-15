# DogBuddyCn — 工具调用配对修复（0.10.17）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测的 400：

    [LLM error/permanent] permanent: RuntimeError: LLM error 400:
    {"error":{"message":"Messages with role 'tool' must be a response to a
     preceding message with 'tool_calls'" ...}}

**这不是他"模型没配好"**：是我们重建请求时的**消息形状**错了。历史一旦被
`recent_limit` 裁剪、被压缩、或被 `context_reset` 清掉一半，就会出现
"有 tool 结果、没有配对的 assistant.tool_calls"。DeepSeek 对这条特别严格，
直接整轮 400 —— 而用户看到的是一个像"配置问题"的报错。

这里用**和 DeepSeek 同规则**的校验器钉住 `ContextBuilder.build()` 的输出形状。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.windows.context import ContextBuilder  # noqa: E402
from dogbuddy.windows.session import WindowSession  # noqa: E402


def _session(tmp_path) -> WindowSession:
    db = Database(tmp_path / "t.db")
    w = Window(id="w1", name="w1", role="worker_programmer",
               workspace_path=str(tmp_path / "ws"), model="", mode="BUILD", status="idle")
    return WindowSession(w, db, db, db)


def assert_openai_shape(messages: list[dict]) -> None:
    """复刻服务端校验：tool 必须应答**前面**声明过的 tool_calls；且不能有没被应答的。"""
    declared: set[str] = set()
    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "assistant":
            declared = {tc.get("id") for tc in (m.get("tool_calls") or []) if tc.get("id")}
        elif role == "tool":
            cid = m.get("tool_call_id")
            assert cid, f"#{i} tool 消息没有 tool_call_id"
            assert cid in declared, (
                f"#{i} tool 消息 {cid} 不是对前面 tool_calls 的应答 —— 服务商会 400")
            declared.discard(cid)
        else:
            assert not declared, f"#{i} 前一条 assistant 的 tool_calls 没人应答：{declared}"
            declared = set()
    assert not declared, f"结尾仍有没人应答的 tool_calls：{declared}"


def test_orphan_tool_result_is_downgraded_to_user_text(tmp_path):
    s = _session(tmp_path)
    s.append("user", "开始")
    s.append("tool", "Bash: ls 输出……", meta={"tool_call_id": "call_orphan"})   # 孤儿
    s.append("assistant", "继续")
    msgs = ContextBuilder().build(s)
    assert_openai_shape(msgs)
    assert any(m["role"] == "user" and "[tool result]" in m["content"] for m in msgs), \
        "孤儿工具结果应该降级成 user 文本（信息不丢，形状合法）"
    assert all(m["role"] != "tool" for m in msgs)


def test_missing_tool_results_get_a_placeholder(tmp_path):
    s = _session(tmp_path)
    s.append("user", "开始")
    s.append("assistant", "", meta={"tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "Bash", "arguments": "{}"}}]})
    s.append("user", "结果被裁掉了")          # 结果不见了
    msgs = ContextBuilder().build(s)
    assert_openai_shape(msgs)
    tools = [m for m in msgs if m["role"] == "tool"]
    assert tools and tools[0]["tool_call_id"] == "call_1"


def test_well_formed_round_trip_is_untouched(tmp_path):
    s = _session(tmp_path)
    s.append("user", "开始")
    s.append("assistant", "", meta={"tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "Bash", "arguments": "{}"}}]})
    s.append("tool", "Bash: ok", meta={"tool_call_id": "call_1"})
    s.append("assistant", "做完了")
    msgs = ContextBuilder().build(s)
    assert_openai_shape(msgs)
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "tool", "assistant"], roles
    assert "not available" not in str(msgs), "配对完好时不该插占位"


def test_legacy_tool_message_without_id_becomes_user_text(tmp_path):
    s = _session(tmp_path)
    s.append("user", "开始")
    s.append("tool", "旧库里没有 tool_call_id 的结果")   # 没有 meta
    msgs = ContextBuilder().build(s)
    assert_openai_shape(msgs)
    assert any("[tool result]" in m["content"] for m in msgs)


def test_trimmed_history_starting_mid_round_is_valid(tmp_path):
    """历史被裁剪后，开头正好是 tool 结果 —— 也必须能发出去。"""
    s = _session(tmp_path)
    for i in range(3):
        s.append("user", f"第 {i} 轮")
        s.append("assistant", "", meta={"tool_calls": [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": "Bash", "arguments": "{}"}}]})
        s.append("tool", f"结果 {i}", meta={"tool_call_id": f"call_{i}"})
    msgs = ContextBuilder().build(s, recent_limit=1)   # 只剩最后一条 tool
    assert_openai_shape(msgs)
    assert msgs[-1]["role"] == "user" and "[tool result]" in msgs[-1]["content"]
