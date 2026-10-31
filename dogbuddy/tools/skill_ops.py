# DogBuddyCn — temporary skill tools (window-scoped)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""Let an employee write a short procedure for its OWN workstation.

This is deliberately *not* `memory_write`: memory is a fact list, a skill is an
executable checklist ("how to run this project's tests") that is injected into
the system prompt on the next turn. Both are window-scoped here, so one employee
cannot pollute another's prompt.
"""
from __future__ import annotations

import json
from typing import Any

from ..skills import create_skill, delete_skill, load_skills
from .registry import ToolContext, ToolResult


def _wid(ctx: ToolContext) -> str:
    return str(getattr(ctx, "window_id", "") or "").strip()


class _SkillBase:
    def __init__(self, skills_root: str = "") -> None:
        self.skills_root = str(skills_root or "")

    def _root(self) -> str:
        return self.skills_root


class SkillCreateTool(_SkillBase):
    name = "skill_create"
    description = (
        "Create/update a TEMPORARY skill for **your own workstation**. Use it for a reusable "
        "procedure you want to follow on later turns (e.g. exact test commands, deploy checklist, "
        "review rubric). It is injected into your system prompt next turn and is invisible to "
        "other windows. Keep it short and actionable; do not put secrets in it. Overwrites a "
        "skill with the same name created by this window.")
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string",
                     "description": "Short id, lowercase letters/digits/_/- (e.g. test-playbook)."},
            "instructions": {"type": "string",
                             "description": "The procedure, step by step. 10-8000 chars."},
            "description": {"type": "string",
                            "description": "Optional one-line summary shown in the skill list."},
        },
        "required": ["name", "instructions"],
    }
    permission = "write"
    required_scope = ""

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not self._root():
            return ToolResult.failure("skill 服务未配置（skills_root 为空）")
        wid = _wid(ctx)
        if not wid:
            return ToolResult.failure("无法确定工位：ctx.window_id 为空")
        try:
            data = create_skill(self._root(), str(args.get("name") or ""),
                                str(args.get("instructions") or ""),
                                window_id=wid,
                                description=str(args.get("description") or ""),
                                actor=str(getattr(ctx, "actor", "") or ""))
        except ValueError as e:
            return ToolResult.failure(str(e))
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"创建 skill 失败：{e}")
        return ToolResult.success(
            f"已创建临时 skill `{data['slug']}`（仅本工位 {wid} 可见，下一轮生效）",
            skill=data["slug"], window_id=wid)


class SkillListTool(_SkillBase):
    name = "skill_list"
    description = ("List temporary skills visible to this workstation (created here, plus global "
                   "skills dropped in by the owner).")
    parameters = {"type": "object", "properties": {}, "required": []}
    permission = "read"
    required_scope = ""

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not self._root():
            return ToolResult.success("skill 服务未配置（暂无 skill）", count=0)
        rows = load_skills(self._root(), _wid(ctx))
        text = json.dumps([{"slug": r.get("slug") or r["name"],
                            "name": r["name"],
                            "description": r["description"]} for r in rows],
                          ensure_ascii=False)
        return ToolResult.success(text, count=len(rows))


class SkillDeleteTool(_SkillBase):
    name = "skill_delete"
    description = "Delete a temporary skill created by THIS workstation (cannot delete another window's)."
    parameters = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Skill slug/name to delete."}},
        "required": ["name"],
    }
    permission = "write"
    required_scope = ""

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not self._root():
            return ToolResult.failure("skill 服务未配置（skills_root 为空）")
        wid = _wid(ctx)
        try:
            ok = delete_skill(self._root(), str(args.get("name") or ""), window_id=wid)
        except ValueError as e:
            return ToolResult.failure(str(e))
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"删除 skill 失败：{e}")
        if not ok:
            return ToolResult.failure("skill 不存在")
        return ToolResult.success("已删除该临时 skill", window_id=wid)


def all_tools(skills_root: str = "") -> list[Any]:
    return [SkillCreateTool(skills_root), SkillListTool(skills_root),
            SkillDeleteTool(skills_root)]
