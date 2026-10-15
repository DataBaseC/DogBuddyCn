# DogBuddyCn — bounded tool output (v0.10.20, token optimization)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""工具输出的**有界引用**（借鉴 dsh-ops 的 PTC 思想，移植到 Linux 服务端）。

圆桌测试（2026-10-15）实测的 token 浪费：一次 `pip index` 输出把 PyPI
simple 索引全量 dump（几千行）、一次 GitHub API root、一个 360KB 的 HTML 探测
结果——`shell` 的 20000 字符硬截留 HEAD 20000 字符，对这种输出毫无信息量，
却每次都要烧 ~5-7k token 喂回上下文，还被压缩摘要继承。

做法：保留**头 + 尾**（头部通常是结构/摘要，尾部通常是结论/报错），中间标注
省略量与翻页办法。模型需要全文时可以显式调高 `max_output_chars`，或用
`sed -n`/`grep`/`head`/`tail` 取它真正要的段——把"读多少"的决定权交给模型，
而不是每次无条件全量。
"""
from __future__ import annotations

from typing import Any

#: 默认总上限（字符）。约 2-3k token——够看结构，不够 dump 全场。
DEFAULT_MAX_CHARS = 8000
#: 头部保留（命令输出开头通常是命令回显/结构/摘要）
DEFAULT_HEAD_CHARS = 6000
#: 尾部保留（结尾通常是结论/报错/统计）
DEFAULT_TAIL_CHARS = 2000


def bound_output(text: str, max_chars: int = DEFAULT_MAX_CHARS,
                 head_chars: int | None = None,
                 tail_chars: int | None = None) -> tuple[str, dict[str, Any]]:
    """把超长输出切成 head+tail，中间放省略量与翻页提示。

    只传 `max_chars` 时按默认比例（3:1）分配头尾——调大上限就应该真的
    看到更多内容，而不是头尾仍是 6000/2000。返回 (bounded_text, stats)，
    stats 供 ToolResult.data 带走：`truncated`/`omitted_chars`/
    `omitted_lines`/`total_chars`。
    """
    s = str(text or "")
    total = len(s)
    cap = max(1, int(max_chars))
    if total <= cap:
        return s, {"truncated": False, "total_chars": total}
    if head_chars is None or tail_chars is None:
        head = cap * DEFAULT_HEAD_CHARS // (DEFAULT_HEAD_CHARS + DEFAULT_TAIL_CHARS)
        tail = cap - head
    else:
        head = max(0, min(int(head_chars), cap))
        tail = max(0, min(int(tail_chars), cap - head))
    if head + tail > cap:  # 配置不当（head+tail 超过 cap）时保头弃尾
        tail = 0
    if tail:
        middle = s[head:total - tail]
        out = s[:head] + _marker(len(middle), middle, head, tail, total) + s[total - tail:]
    else:
        middle = s[head:]
        out = s[:head] + _marker(len(middle), middle, head, tail, total)
    return out, {"truncated": True, "omitted_chars": len(middle),
                 "omitted_lines": middle.count("\n"), "total_chars": total,
                 "head_chars": head, "tail_chars": tail}


def _marker(omitted_chars: int, middle: str, head: int, tail: int, total: int) -> str:
    return (
        f"\n...[omitted {omitted_chars} chars / {middle.count(chr(10))} lines in the middle; "
        f"showing first {head} + last {tail} of {total}. "
        f"Page with sed -n 'START,ENDp' / grep / head / tail, "
        f"or pass max_output_chars to raise the cap]...\n"
    )
