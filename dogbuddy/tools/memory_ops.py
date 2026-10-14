# DogBuddyCn — 工位记忆工具（员工可用的"本工位记忆"）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""工位级记忆：`memory/windows/<id>.md`。

**为什么单开这两个工具**（审查报告 P1-4）：

`ROLES.md` 的权限矩阵写着「记忆（memory 读写）—— 员工 ✅ **本工位**」，但实现里
`memory_write` / `memory_recall` 只在 `ADMIN_TOOLS`（system 级），员工白名单里
**一个记忆工具都没有** —— 那张表里"员工"这一列的记忆能力整体是空的。

而那对工具**不能直接下放给员工**：它们收一个 `scope` 参数，可以是 `global` 或
**任意工位 id** —— 给员工等于让他读写任何工位的记忆和全局记忆。

所以这里提供**只认 `ctx.window_id`** 的版本：模型没有参数可以指向别处，
作用域由构造决定，因此 `required_scope=""`（不需要作用域裁决）。这与
`project_memory_ops` 是同一思路：能力收窄到自己的范围，就不需要额外的裁决。
"""
from __future__ import annotations

from typing import Any

from .registry import ToolContext, ToolResult

MAX_READ_CHARS = 20000
MAX_WRITE_CHARS = 20000


def _window_id(ctx: ToolContext) -> str:
    return str(getattr(ctx, "window_id", "") or "").strip()


class _WindowMemoryBase:
    @staticmethod
    def _service(ctx: ToolContext) -> Any:
        return getattr(ctx, "memory_service", None)


class WindowMemoryReadTool(_WindowMemoryBase):
    name = "window_memory_read"
    description = (
        "Read **your own workbench's** long-term memory: facts you (or the compactor) wrote "
        "down in earlier sessions — environment quirks, decisions, lessons. This is your own "
        "notebook; it is not the project-wide memory (use project_memory_read for that) and "
        "you cannot read anyone else's.")
    parameters = {"type": "object", "properties": {}, "required": []}
    permission = "read"
    required_scope = ""

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        svc = self._service(ctx)
        if svc is None:
            return ToolResult.failure("工位记忆不可用（memory service 未注入）")
        wid = _window_id(ctx)
        if not wid:
            return ToolResult.failure("无法确定工位：ctx.window_id 为空")
        try:
            text = svc.recall_window(wid)[:MAX_READ_CHARS]
        except Exception as e:  # noqa: BLE001 — 记忆读失败不该炸掉这一轮
            return ToolResult.failure(f"读取工位记忆失败：{e}")
        return ToolResult.success(text or "(本工位记忆还是空的)", window_id=wid, content=text)


class WindowMemoryWriteTool(_WindowMemoryBase):
    name = "window_memory_write"
    description = (
        "Append a durable fact to **your own workbench's** memory: a convention you must keep "
        "following, a pitfall you hit, a decision made this session. Keep it to one short "
        "factual sentence per call; do not dump conversation logs. It persists across "
        "sessions and is injected back into your context.")
    parameters = {
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "One short factual sentence to remember."},
        },
        "required": ["fact"],
    }
    permission = "write"
    required_scope = ""

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        svc = self._service(ctx)
        if svc is None:
            return ToolResult.failure("工位记忆不可用（memory service 未注入）")
        wid = _window_id(ctx)
        if not wid:
            return ToolResult.failure("无法确定工位：ctx.window_id 为空")
        fact = str(args.get("fact") or "").strip()
        if not fact:
            return ToolResult.failure("fact 不能为空")
        if len(fact) > MAX_WRITE_CHARS:
            return ToolResult.failure(f"fact 太长（>{MAX_WRITE_CHARS} 字符），请精简成一句话。")
        try:
            svc.append_window(wid, fact)
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"写入工位记忆失败：{e}")
        return ToolResult.success(f"已记入本工位（{wid}）的记忆", window_id=wid)


def all_tools() -> list[Any]:
    return [WindowMemoryReadTool(), WindowMemoryWriteTool()]
