# DogBuddyCn — read archived resource cards (v0.20.0)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Read full evidence archived by compaction without loading it into every prompt.

The context only carries cards (id + summary). When the model needs an exact
old error/log/decision, it calls `read_resource` and gets a page.
"""
from __future__ import annotations

from typing import Any

from .registry import ToolContext, ToolResult

MAX_PAGE = 20000


class ReadResourceTool:
    name = "read_resource"
    description = (
        "Read the full text behind a resource card (older messages archived during "
        "compaction). Use the `resource_id` shown in `## Resource cards`. Content is "
        "paged: pass `offset` from a previous `next_offset`. Only resources owned by "
        "this workstation (or global) are readable.")
    parameters = {
        "type": "object",
        "properties": {
            "resource_id": {"type": "string", "description": "e.g. rs-ab12cd34ef"},
            "offset": {"type": "integer", "description": "Character offset, default 0."},
            "limit": {"type": "integer",
                      "description": "Max chars to return (default 4000, max 20000)."},
        },
        "required": ["resource_id"],
    }
    permission = "read"
    required_scope = ""

    def __init__(self, resources: Any = None):
        self.resources = resources

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if self.resources is None:
            return ToolResult.failure("资源库未启用（kernel/resources 未注入）")
        rid = str(args.get("resource_id") or "").strip()
        if not rid:
            return ToolResult.failure("resource_id 不能为空")
        row = self.resources.get_resource(rid)
        if row is None:
            return ToolResult.failure(f"没有这个资源：{rid}")
        owner = str(row.get("window_id") or "")
        wid = str(getattr(ctx, "window_id", "") or "")
        if owner and owner != wid:
            return ToolResult.failure("无权读取其他工位的资源")
        body = str(row.get("body") or "")
        try:
            offset = max(0, int(args.get("offset") or 0))
            limit = max(1, min(int(args.get("limit") or 4000), MAX_PAGE))
        except (TypeError, ValueError):
            offset, limit = 0, 4000
        chunk = body[offset:offset + limit]
        nxt = offset + len(chunk)
        more = nxt < len(body)
        output = chunk or "(empty)"
        if more:
            output += f"\n\n…[next_offset={nxt} of {len(body)} chars]"
        return ToolResult.success(
            output, resource_id=rid, title=row.get("title", ""),
            kind=row.get("kind", ""), offset=offset, next_offset=nxt if more else 0,
            total_chars=len(body), truncated=more)


def all_tools(resources: Any = None) -> list[Any]:
    # 始终注册：角色白名单里可能有它；未注入资源库时执行会 fail-closed。
    return [ReadResourceTool(resources)]
