# DogBuddyCn — bounded tool output 回归（v0.10.20，token 优化）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""圆桌测试实测的 token 浪费：`shell` 的 20000 字符硬截把 PyPI simple 索引
全量、GitHub API root、360KB HTML 的**头部**原样喂回上下文——每次 ~5-7k token
的噪音，还被压缩摘要继承。

有界输出（借鉴 dsh-ops 的 PTC 思想）：head+tail + 省略量与翻页提示，
模型可显式调高 max_output_chars。这些测试锁住行为与默认值。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.tools.bound_output import (DEFAULT_HEAD_CHARS, DEFAULT_MAX_CHARS,  # noqa: E402
                                         DEFAULT_TAIL_CHARS, bound_output)
from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(tmp_path) -> ToolContext:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(window_id="w1", workspace=str(ws), mode="BUILD", actor="worker")


# ------------------------------------------------------------------ helper
def test_bound_output_keeps_head_and_tail():
    big = "H" * 100 + "M" * 100000 + "T" * 100
    out, stats = bound_output(big)
    assert stats["truncated"] is True
    assert out.startswith("H" * 100)
    assert out.endswith("T" * 100)
    assert "omitted 92200 chars" in out
    assert "sed -n" in out and "max_output_chars" in out
    assert stats["total_chars"] == len(big)
    assert len(out) <= DEFAULT_MAX_CHARS + 400  # marker 允许一点余量


def test_bound_output_scales_head_tail_with_cap():
    """只调大 max_chars 时，头尾按 3:1 比例放大——否则调了上限也看不到更多。"""
    big = "x" * 100000
    out, stats = bound_output(big, max_chars=40000)
    assert stats["head_chars"] == 30000 and stats["tail_chars"] == 10000
    assert len(out) < 41000


def test_bound_output_short_text_untouched():
    out, stats = bound_output("hello")
    assert out == "hello" and stats["truncated"] is False


def test_bound_output_respects_cap():
    big = "x" * 50000
    out, _ = bound_output(big, max_chars=1000, head_chars=800, tail_chars=100)
    assert len(out) < 1100
    assert out.startswith("x" * 800) and out.endswith("x" * 100)


# ------------------------------------------------------------------ shell
def _big_output_cmd() -> str:
    """跨平台制造 8 万字符输出的命令（用当前解释器，避免依赖 PATH 里的 python）。"""
    import sys
    return f'"{sys.executable}" -c "print(\'A\'*40000); print(\'B\'*40000)"'


def test_shell_output_bounded_by_default(tmp_path):
    reg = build_registry()
    res = _run(reg.execute("shell", {"command": _big_output_cmd()}, _ctx(tmp_path)))
    assert res.ok, res.output
    assert res.data.get("truncated") is True
    assert len(res.output) < 20000, "默认 8000 上限，不该再出现 20000 字符的输出"
    assert "omitted" in res.output and "max_output_chars" in res.output
    # 头（AAAA…）和尾（BBBB…）都在
    assert "A" * 100 in res.output and "B" * 100 in res.output


def test_shell_output_cap_overridable(tmp_path):
    reg = build_registry()
    res = _run(reg.execute("shell", {
        "command": _big_output_cmd(), "max_output_chars": 90000}, _ctx(tmp_path)))
    assert res.ok, res.output
    assert res.data.get("truncated") is False, "显式调高上限后不该再截"
    assert "A" * 40000 in res.output


def test_shell_small_output_not_marked(tmp_path):
    reg = build_registry()
    res = _run(reg.execute("shell", {"command": "echo hello"}, _ctx(tmp_path)))
    assert res.ok and res.data.get("truncated") is False and "hello" in res.output
