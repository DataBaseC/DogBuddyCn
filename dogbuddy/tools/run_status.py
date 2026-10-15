# DogBuddyCn — worker self-status tool (v0.10.20)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""run_status — 只读的本人运行状态（预算/环境），替代 worker 去撞 `env`/`/proc`。

圆桌测试（2026-10-15）里 qa-ops 想查自己的上下文预算，`env` 被边界守卫以
"可伪造环境"为由拒绝、`/proc/*` 以越界为由拒绝 → 它只能瞎跑，最后撞上
`max iterations reached` 硬中断。这里给它一个**正规的只读入口**：

已用 token（账本）、上下文上限与压缩阈值、轮次上限、单轮等待上限、信任级、
工作区与可写性、待办数量、本条消息是定时还是人工触发。

只读本人（ctx.window_id），没有任何参数可以指向别的工位——所以不需要
作用域裁决（与 window_memory_read 同思路，审查报告 P1-4）。
"""
from __future__ import annotations

import os
from typing import Any

from .registry import ToolContext, ToolResult


class RunStatusTool:
    """run_status — read-only view of my own budget and environment."""

    name = "run_status"
    description = (
        "Read-only status of THIS window: ledger tokens used, context limit and "
        "compaction threshold, iteration cap, per-turn wait cap, trust level, "
        "workspace path and writability, open todos, and whether this run was "
        "scheduled or manual. Use it instead of shelling out to `env` or reading "
        "/proc (those are blocked by the boundary guard)."
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    permission = "read"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        db = getattr(ctx, "db", None)
        wid = str(getattr(ctx, "window_id", "") or "")
        if db is None or not wid:
            return ToolResult.failure("run_status 需要 db 与 window_id（运行时注入）")
        try:
            w = db.get_window(wid)
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"查询工位失败：{e}")
        if w is None:
            return ToolResult.failure(f"window not found: {wid}")

        params = dict(getattr(w, "params", None) or {})
        retry = params.get("retry") or {}
        lines: list[str] = []
        lines.append(f"工位: {getattr(w, 'name', wid)} ({wid}) role={getattr(w, 'role', '?')}"
                     f" mode={getattr(w, 'mode', '?')} status={getattr(w, 'status', '?')}")
        lines.append(f"模型: {getattr(w, 'model', '') or '（继承管理员/全局）'}")
        ws = str(getattr(ctx, "workspace", "") or "")
        writable = "?"
        if ws:
            try:
                writable = "是" if os.access(ws, os.W_OK) else "否"
            except OSError:
                writable = "否"
        lines.append(f"工作区: {ws or '(未设)'} 可写={writable}")
        try:
            tokens = int(db.sum_tokens(wid))
        except Exception:  # noqa: BLE001 — 账本读不到不影响其他字段
            tokens = 0
        max_ctx = int(params.get("max_context_tokens") or 0)
        threshold = params.get("compact_threshold")
        lines.append(f"已用 token（账本口径，累计花费）: {tokens}")
        if max_ctx:
            lines.append(f"上下文上限: {max_ctx}"
                         + (f"，压缩阈值 {threshold}" if threshold else "")
                         + "（压缩判据是最近一次调用的 prompt_tokens，不是这个累计值）")
        lines.append(f"轮次上限: {params.get('max_iterations', '?')}"
                     f"；单轮等待上限: {params.get('task_timeout', '?')}s")
        if retry:
            lines.append(f"重试策略: base={retry.get('base_interval', '?')}s "
                         f"max={retry.get('max_interval', '?')}s "
                         f"次数={retry.get('max_retries', '?')} backoff={retry.get('backoff', '?')}")
        lines.append(f"信任级: {(ctx.extra or {}).get('trust', 'normal')}")
        try:
            todos = db.list_todos(wid) if hasattr(db, "list_todos") else []
            open_n = len([t for t in todos if not getattr(t, "done", False)])
        except Exception:  # noqa: BLE001
            open_n = -1
        if open_n >= 0:
            lines.append(f"待办: {open_n} 条未完成")
        lines.append("本条运行: " + ("定时任务触发" if (ctx.extra or {}).get("scheduled")
                                     else "人工/对话触发"))
        return ToolResult.success("\n".join(lines), window_id=wid, tokens=tokens)


def all_tools() -> list:
    return [RunStatusTool()]
