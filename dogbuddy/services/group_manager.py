# DogBuddyCn — shared work-channel groups (L2 Application, ROADMAP T3)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""共享工作频道：几个员工（窗口）在一个频道里协作干一件事。

Task flow (T3.2, 主持人式接力 — deliberately simple and debuggable):
  1. the task is replayed into the channel context (what everyone said so far);
  2. each member runs one turn on it ("continue from the channel, do your part");
     every member's output lands in the shared channel (messages.group_id);
  3. the lead runs the final turn and produces the summary — that is the
     group run's answer.

权限取最严 (T3.2): every member runs with the INTERSECTION of all members'
tool sets — if any member lacks a capability, the group does not get it either.
One sloppy member cannot pull out `shell` for the whole team.

Concurrency (T3.4): members run strictly sequentially — a shared channel with
concurrent writers would interleave turns into nonsense. This also sidesteps
`drop_messages` races (compaction only ever runs between steps).
"""
from __future__ import annotations

import logging
from typing import Any

from ..agents.core.roles import get_role
from ..db import new_id
from ..ports import Group

logger = logging.getLogger("dogbuddy.services.groups")


class GroupManager:
    def __init__(self, db: Any, windows: Any, bus: Any = None):
        self.db = db
        self.windows = windows
        self.bus = bus

    # ---------------------------------------------------------- lifecycle
    def create(self, name: str, description: str = "",
               lead_window_id: str = "") -> Group:
        if self.db.get_group(name):
            raise ValueError(f"group name already used: {name}")
        g = Group(id=new_id(), name=name, description=description,
                  lead_window_id=lead_window_id or "")
        self.db.insert_group(g)
        if lead_window_id:
            self.assign(g.id, lead_window_id, role="lead")
        self.db.audit("system", "group:create", f"{g.id} {name}")
        return g

    def get(self, group_ref: str) -> Group | None:
        return self.db.get_group(group_ref)

    def list(self) -> list[Group]:
        return self.db.list_groups()

    def delete(self, group_ref: str) -> bool:
        g = self.db.get_group(group_ref)
        if g is None:
            return False
        for m in self.db.list_group_members(g.id):
            w = self.db.get_window(m.window_id)
            if w:
                self.db.update_window(w.id, group_id="")
        ok = self.db.delete_group(g.id)
        self.db.audit("system", "group:delete", f"{g.id}")
        return ok

    def assign(self, group_ref: str, window_ref: str, role: str = "member") -> Group:
        g = self.db.get_group(group_ref)
        w = self.db.get_window(window_ref) or self.db.get_window_by_name(window_ref)
        if g is None:
            raise ValueError(f"group not found: {group_ref}")
        if w is None:
            raise ValueError(f"window not found: {window_ref}")
        self.db.group_add_member(g.id, w.id, role=role or "member")
        self.db.update_window(w.id, group_id=g.id)  # session messages join the channel
        if role == "lead":
            self.db.update_group(g.id, lead_window_id=w.id)
        self.db.audit("system", "group:assign", f"{g.id} <- {w.id} ({role})")
        return self.db.get_group(g.id) or g

    def unassign(self, group_ref: str, window_ref: str) -> bool:
        g = self.db.get_group(group_ref)
        w = self.db.get_window(window_ref) or self.db.get_window_by_name(window_ref)
        if g is None or w is None:
            return False
        self.db.group_remove_member(g.id, w.id)
        self.db.update_window(w.id, group_id="")
        return True

    def members(self, group_ref: str) -> list[dict]:
        g = self.db.get_group(group_ref)
        if g is None:
            raise ValueError(f"group not found: {group_ref}")
        out = []
        for m in self.db.list_group_members(g.id):
            w = self.db.get_window(m.window_id)
            out.append({"window_id": m.window_id,
                        "window_name": w.name if w else m.window_id,
                        "role": m.role, "joined_at": m.joined_at,
                        "exists": w is not None})
        return out

    def channel(self, group_ref: str, limit: int = 100) -> list[dict]:
        g = self.db.get_group(group_ref)
        if g is None:
            raise ValueError(f"group not found: {group_ref}")
        names = {w.id: w.name for w in self.db.list_windows()}
        return [{"id": m.id, "window_id": m.window_id,
                 "window_name": names.get(m.window_id, m.window_id),
                 "role": m.role, "content": m.content,
                 "created_at": m.created_at}
                for m in self.db.list_group_messages(g.id, limit)]

    # -------------------------------------------------------------- runs
    def _strict_tools(self, member_ids: list[str]) -> list[str] | None:
        """权限取最严: the tool intersection of every member's role."""
        sets: list[set[str]] = []
        for wid in member_ids:
            w = self.db.get_window(wid)
            spec = get_role(w.role) if w else None
            sets.append(set(spec.tool_names) if spec and spec.tool_names else set())
        if not sets:
            return None
        common = set.intersection(*sets) if sets else None
        return sorted(common) if common is not None else None

    def _channel_recap(self, gid: str, limit: int = 30) -> str:
        msgs = self.db.list_group_messages(gid, limit)
        if not msgs:
            return "(频道暂无历史记录)"
        names = {w.id: w.name for w in self.db.list_windows()}
        lines = [f"[{names.get(m.window_id, m.window_id)}] {m.role}: {m.content[:400]}"
                 for m in msgs]
        return "\n".join(lines[-limit:])

    async def run(self, group_ref: str, prompt: str) -> dict:
        """One group task: members接力 one turn each, lead summarizes (T3.2)."""
        from ..agents.core.loop import AgentLoop
        from ..tools.registry import ToolContext

        g = self.db.get_group(group_ref)
        if g is None:
            raise ValueError(f"group not found: {group_ref}")
        member_rows = self.db.list_group_members(g.id)
        lead_id = g.lead_window_id or (member_rows[0].window_id if member_rows else "")
        order = [m.window_id for m in member_rows if m.window_id != lead_id]
        if lead_id:
            order.append(lead_id)  # lead goes last (summary turn)
        if not order:
            raise ValueError(f"group {g.name} has no members")

        tools = self._strict_tools(order)
        steps: list[dict] = []
        for wid in order:
            w = self.db.get_window(wid)
            if w is None:
                continue
            rt = self.windows._rt(wid)
            is_lead = wid == lead_id
            recap = self._channel_recap(g.id)
            step_prompt = (
                f"【共享频道任务 · 工位「{w.name}」】\n"
                f"总任务：{prompt}\n\n"
                f"--- 共享频道最新记录 ---\n{recap}\n---\n\n"
                + ("你是组长，请基于频道记录汇总各方产出，给出最终交付（简洁、可执行）。"
                   if is_lead else
                   "你是组员，请接着频道记录完成你负责的部分；只输出你的产出与必要的下一步建议，"
                   "不要重复别人已说的内容。")
            )
            role_spec = get_role(w.role)
            member_tools = tools if tools is not None else (
                role_spec.tool_names if role_spec else None)
            loop = AgentLoop(
                llm=self.windows._llm(w.llm_profile or None, w.model or None),
                registry=rt.registry, session=rt.session,
                builder=self.windows.builder,
                compactor=self.windows._compactor_for(w),
                tool_names=member_tools,
                max_iterations=self.windows.params_for(w).get("max_iterations", 25))
            ctx = ToolContext(
                window_id=w.id, workspace=w.workspace_path, mode=w.mode, actor="worker",
                todo_repo=self.db, db=self.db, panel=self.windows.panel,
                llm_factory=(lambda p=None: self.windows._llm(
                    p or w.llm_profile or None, w.model or None)),
                ask_user_fn=self.windows._make_ask_user(w.id),
                checkpoint_root=self.windows.checkpoints_dir)
            try:
                result = await loop.run(step_prompt, ctx)
                steps.append({"window_id": wid, "window_name": w.name,
                              "is_lead": is_lead, "ok": result.stop_reason == "done",
                              "summary": (result.content or "")[:1000]})
            except Exception as e:  # noqa: BLE001 — one member failing doesn't kill the run
                logger.exception("group %s step failed for %s", g.id, wid)
                steps.append({"window_id": wid, "window_name": w.name,
                              "is_lead": is_lead, "ok": False,
                              "summary": f"[step failed] {e}"})
        summary = next((s["summary"] for s in reversed(steps) if s["is_lead"] and s["ok"]),
                       (steps[-1]["summary"] if steps else ""))
        return {"group_id": g.id, "group_name": g.name,
                "steps": steps, "summary": summary}
