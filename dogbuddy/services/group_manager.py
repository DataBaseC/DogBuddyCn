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

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..agents.core.roles import get_role
from ..db import new_id
from ..ports import Group

logger = logging.getLogger("dogbuddy.services.groups")

#: 群组编排提示词的签名。带它的消息**不进共享频道**、也不进 recap ——
#: 否则"提示词里有 recap、recap 又读回提示词"会自指增长（实测 3 人一轮
#: recap 重复注入 ≈3523 tokens，最后一条提示词被撑到 3028 字符 / 嵌套 10 层）。
GROUP_STEP_MARK = "【共享频道任务"
#: recap 的**字符预算**（÷3 ≈ tokens）。按"条数"截断对嵌套内容毫无意义。
RECAP_BUDGET_CHARS = 3600
#: 单条消息进 recap 的截断长度
RECAP_ITEM_CHARS = 300


class GroupManager:
    def __init__(self, db: Any, windows: Any, bus: Any = None):
        self.db = db
        self.windows = windows
        self.bus = bus

    # ---------------------------------------------------------- lifecycle
    def create(self, name: str, description: str = "",
               lead_window_id: str = "", project_id: str = "") -> Group:
        if self.db.get_group(name):
            raise ValueError(f"group name already used: {name}")
        g = Group(id=new_id(), name=name, description=description,
                  lead_window_id=lead_window_id or "",
                  project_id=project_id or "")  # T7.3: 挂项目 = 成员共享工作区
        self.db.insert_group(g)
        if lead_window_id:
            self.assign(g.id, lead_window_id, role="lead")
        self.db.audit("system", "group:create",
                      f"{g.id} {name} project={project_id or '-'}")
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

    def post(self, group_ref: str, window_id: str, content: str) -> dict:
        """往共享频道发一条消息（群频道唯一的生产入口）。

        管理员/主管靠 `group_channel_post` 工具把结论/指令投进频道；
        工人消息则在窗口 assign 进组后由 `WindowSession.append` 自动带入
        （`window.group_id` → `messages.group_id`）。
        """
        g = self.db.get_group(group_ref)
        if g is None:
            raise ValueError(f"group not found: {group_ref}")
        text = str(content or "").strip()
        if not text:
            raise ValueError("channel post content is empty")
        actor = str(window_id or "").strip() or "admin"
        m = self.db.add_message(actor, "assistant", text, 0, None, group_id=g.id)
        self.db.audit("system", "group:channel_post",
                      f"{g.id} by {actor} ({len(text)} chars)")
        return {"id": m.id, "group_id": g.id, "window_id": actor,
                "role": "assistant", "content": text,
                "created_at": getattr(m, "created_at", "")}

    # -------------------------------------------------------------- runs
    def _strict_tools(self, member_ids: list[str]) -> list[str] | None:
        """权限取最严：所有成员角色白名单的**交集**。

        返回 `[]` = **一个工具都不给**（交集为空，或有成员角色未知）。
        注意 `AgentLoop` 里 `[]` 是"空白名单"，而 `None` 才是"不过滤"——
        初版把 `[]` 当成不过滤，于是交集为空时**全量放行**（审查报告 P1-7）。
        返回 `None` 只用于"拿不到成员列表"，调用方会退回各自角色白名单。
        """
        if not member_ids:
            return None
        sets: list[set[str]] = []
        for wid in member_ids:
            w = self.db.get_window(wid)
            spec = get_role(w.role) if w is not None else None
            if spec is None or not spec.tool_names:
                # 判不出这个成员能给什么 → 交集必然为空；不猜，直接不给工具
                return []
            sets.append(set(spec.tool_names))
        return sorted(set.intersection(*sets))


    # ------------------------------------------------------------ 频道摘要
    def _readable_line(self, m: Any, names: dict[str, str]) -> str | None:
        """频道里**哪条消息值得进 recap**。"""
        content = str(getattr(m, "content", "") or "").strip()
        if not content:
            return None
        # 编排提示词不是产出，而且它自带上一轮的 recap → 会让 recap 自指增长
        if GROUP_STEP_MARK in content:
            return None
        who = names.get(getattr(m, "window_id", ""), getattr(m, "window_id", ""))
        body = content[:RECAP_ITEM_CHARS]
        if len(content) > RECAP_ITEM_CHARS:
            body += "…"
        return f"[{who}] {getattr(m, 'role', '')}: {body}"

    @staticmethod
    def _fit_budget(lines: list[str], budget: int, keep_min: int = 3) -> tuple[list[str], int]:
        """按**字符预算**裁（÷3 ≈ tokens）。先丢最旧的，但不丢掉最后 keep_min 条。"""
        def size(xs: list[str]) -> int:
            return sum(len(x) + 1 for x in xs)

        dropped = 0
        while len(lines) > keep_min and size(lines) > budget:
            lines.pop(0)
            dropped += 1
        return lines, dropped

    def _channel_recap(self, gid: str, *, since_id: int = 0, upto_id: int = 0,
                       budget: int = RECAP_BUDGET_CHARS) -> tuple[str, dict[str, Any]]:
        """频道摘要（v0.10.21 重写）。

        旧实现每次都给**全量最近 30 条 × 400 字符**，而且把编排提示词也算进去 ——
        于是"提示词里有 recap、recap 又读回提示词"，自指增长。实测一次 3 人群组任务
        重复注入 ≈10571 字符 ≈3523 tokens，最后一条提示词被撑到 3028 字符（嵌套 10 层）。

        新实现三条规则：
        1. **只取产出**（跳过带签名的编排提示词）；
        2. **增量**：`since_id` 之后的新消息（本轮此前成员的产出）；`upto_id` 之前的是
           有界历史尾部（预算减半）；
        3. **按字符预算裁**，不是按条数——并对省略量给出可行动的提示。
        """
        msgs = self.db.list_group_messages(gid, 200)
        names = {w.id: w.name for w in self.db.list_windows()}
        lines: list[str] = []
        for m in msgs:
            mid = int(getattr(m, "id", 0) or 0)
            if since_id and mid <= since_id:
                continue
            if upto_id and mid > upto_id:
                continue
            line = self._readable_line(m, names)
            if line:
                lines.append(line)
        lines, dropped = self._fit_budget(lines, budget)
        stats = {"chars": sum(len(x) + 1 for x in lines), "lines": len(lines), "dropped": dropped}
        if not lines:
            return "", stats
        text = "\n".join(lines)
        if dropped:
            text += (f"\n…（更早的 {dropped} 条已省略；要全文请读工作区里的频道记录，"
                     f"或让管理员用 group_list 查）")
        return text, stats

    def _last_channel_id(self, gid: str) -> int:
        """频道里最新的消息 id（本轮"水位线"）。"""
        msgs = self.db.list_group_messages(gid, 1)
        return int(getattr(msgs[-1], "id", 0) or 0) if msgs else 0

    def _recap_block(self, gid: str, start_id: int) -> tuple[str, dict[str, Any]]:
        """拼出本步要注入的频道上下文：本轮增量 + 有界历史尾部。"""
        delta, d_stats = self._channel_recap(gid, since_id=start_id)
        tail, t_stats = self._channel_recap(gid, upto_id=start_id,
                                            budget=RECAP_BUDGET_CHARS // 2)
        parts: list[str] = []
        if delta:
            parts.append("--- 本轮此前成员的产出 ---\n" + delta)
        if tail:
            parts.append("--- 更早的频道要点（已按预算截断）---\n" + tail)
        stats = {"chars": d_stats["chars"] + t_stats["chars"],
                 "tokens": (d_stats["chars"] + t_stats["chars"]) // 3,
                 "delta_lines": d_stats["lines"], "tail_lines": t_stats["lines"],
                 "dropped": d_stats["dropped"] + t_stats["dropped"]}
        return ("\n\n".join(parts) if parts else "(频道暂无历史记录)"), stats

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

        # v0.10.21：本轮水位线 —— 每个成员只读"本轮此前成员的新产出"，
        # 而不是把整条频道历史重发一遍（N 个人 → N 倍重复）。
        start_id = self._last_channel_id(g.id)
        recap_total = {"chars": 0, "tokens": 0}

        tools = self._strict_tools(order)
        # T7.3: 群组挂载的项目 → 共享工作区（成员共用项目根目录）
        shared_ws = ""
        proj_ref = getattr(g, "project_id", "") or ""
        if proj_ref:
            proj = self.db.get_project(proj_ref)
            if proj is not None and str(proj.root_path or "").strip():
                shared_ws = str(Path(proj.root_path).expanduser())
                try:
                    Path(shared_ws).mkdir(parents=True, exist_ok=True)
                except OSError:
                    logger.warning("shared workspace not creatable: %s", shared_ws)
        steps: list[dict] = []
        for wid in order:
            w = self.db.get_window(wid)
            if w is None:
                continue
            rt = self.windows._rt(wid)
            is_lead = wid == lead_id
            recap, recap_stats = self._recap_block(g.id, start_id)
            recap_total["chars"] += recap_stats["chars"]
            recap_total["tokens"] += recap_stats["tokens"]
            step_prompt = (
                f"{GROUP_STEP_MARK} · 工位「{w.name}」】\n"
                f"总任务：{prompt}\n\n"
                f"{recap}\n---\n\n"
                + ("你是组长，请基于本期产出汇总各方结果，给出最终交付（简洁、可执行）。"
                   if is_lead else
                   "你是组员，请完成你负责的部分；只输出你的产出与必要的下一步建议，"
                   "不要重复别人已说的内容。")
            )
            # 这条编排提示词不该进共享频道（否则被下一个成员读回来 → 自指增长）
            rt.session.channel_mute = {"marker": GROUP_STEP_MARK, "left": 1}
            role_spec = get_role(w.role)
            # `_strict_tools` 返回 `[]`（交集为空）时**必须一个工具都不给**；
            # 返回 None（算不出来）才退回该角色自己的白名单。初版 `[]` 会被
            # `schemas(only=...)` 当成"不过滤"→ 全量放行（审查报告 P1-7）。
            member_tools = tools if tools is not None else (
                role_spec.tool_names if role_spec else [])
            loop = AgentLoop(
                llm=self.windows._llm(w.llm_profile or None, w.model or None),
                registry=rt.registry, session=rt.session,
                builder=self.windows.builder,
                compactor=self.windows._compactor_for(w),
                tool_names=member_tools,
                max_iterations=self.windows.params_for(w).get("max_iterations", 25))
            # T7.3: 群组挂了项目 → **全体成员跑项目根**（共享工作区）。
            # 这是"群组协作"能成立的前提：组员看到同一批文件，组长汇总的产物
            # 就在同一个目录里，不用互相传。没挂项目才退回各自工作区。
            ws = shared_ws or self.windows.effective_workspace(w)
            rt.session.workspace_override = ws
            ctx = ToolContext(
                window_id=w.id, workspace=ws, mode=w.mode, actor="worker",
                todo_repo=self.db, db=self.db, panel=self.windows.panel,
                llm_factory=(lambda p=None: self.windows._llm(
                    p or w.llm_profile or None, w.model or None)),
                ask_user_fn=self.windows._make_ask_user(w.id),
                checkpoint_root=self.windows.checkpoints_dir,
                memory_service=getattr(self.windows, "memory", None),
                # T9.1：作用域由角色推导后注入（初版漏了这条路径 → 主管在群组里
                # 用不了管理工具，fail-closed 变成"干不了活"，审查报告 P1-5）
                scope=self.windows.scope_resolver.for_window(w),
                extra={"trust": (w.params or {}).get("trust", "normal"),
                       "project_id": getattr(w, "project_id", "") or ""})
            try:
                # v0.10.22：单成员**挂钟预算**。限速退避是"无限耐心"的（heavy 档会
                # 追加重试预算，实测单次退避就 600s），一次群组运行因此可能挂几分钟
                # 到几小时，而且**没有取消入口**（owner 实测：统一模型后 5 分钟只跑完
                # 1/3 个成员，全程挂在 600s 等待里）。这里用已有的 `task_timeout`
                # （默认 1800s）给每个成员封顶：到点就停这一步、如实记成 timeout，
                # 而不是无限等下去。
                budget = float(self.windows.params_for(w).get("task_timeout") or 1800)
                result = await asyncio.wait_for(loop.run(step_prompt, ctx), timeout=budget)
                # v0.10.28：成员被限速而**暂停**（非失败）要如实标出来，别混进 ok=False
                # 的"失败"里 —— 它只是"这次没轮上"，稍后重跑就能继续。
                limited = result.stop_reason == "rate_limited"
                steps.append({"window_id": wid, "window_name": w.name,
                              "is_lead": is_lead, "ok": result.stop_reason == "done",
                              "rate_limited": limited,
                              "error_code": getattr(result, "error_code", "") or "",
                              # v0.10.21：每步注入的频道上下文用量**看得见**
                              # （以前是黑盒：不知道 recap 悄悄烧了多少 token）
                              "recap_chars": recap_stats["chars"],
                              "recap_tokens": recap_stats["tokens"],
                              "recap_lines": recap_stats["delta_lines"],
                              "recap_history_lines": recap_stats["tail_lines"],
                              "recap_dropped": recap_stats["dropped"],
                              "prompt_chars": len(step_prompt),
                              "summary": (result.content or "")[:1000]})
            except asyncio.TimeoutError:
                logger.warning("group %s step %s exceeded budget %.0fs (rate-limit waits?)",
                               g.id, wid, budget)
                steps.append({"window_id": wid, "window_name": w.name, "is_lead": is_lead,
                              "ok": False, "timeout": True,
                              "recap_chars": recap_stats["chars"],
                              "recap_tokens": recap_stats["tokens"],
                              "prompt_chars": len(step_prompt),
                              "summary": (f"[超时] 该成员在 {budget:.0f}s 内没跑完"
                                          f"（多为限速退避吃掉了预算）。"
                                          f"要放宽就调该工位的 task_timeout。")})
            except Exception as e:  # noqa: BLE001 — one member failing doesn't kill the run
                logger.exception("group %s step failed for %s", g.id, wid)
                steps.append({"window_id": wid, "window_name": w.name,
                              "is_lead": is_lead, "ok": False,
                              "recap_chars": recap_stats["chars"],
                              "recap_tokens": recap_stats["tokens"],
                              "prompt_chars": len(step_prompt),
                              "summary": f"[step failed] {e}"})
        summary = next((s["summary"] for s in reversed(steps) if s["is_lead"] and s["ok"]),
                       (steps[-1]["summary"] if steps else ""))
        limited_n = sum(1 for s in steps if s.get("rate_limited"))
        if limited_n:
            # v0.10.28：把"被限速暂停"说清楚，并给出下一步（不是失败，别让人瞎猜）
            summary = (f"[限速暂停 {limited_n}/{len(steps)} 名成员] " + summary)
        self.db.audit("system", "group:run",
                      f"{g.id} members={len(steps)} rate_limited={limited_n} "
                      f"recap_chars={recap_total['chars']} "
                      f"recap_tokens≈{recap_total['tokens']} "
                      f"max_prompt_chars={max((s.get('prompt_chars', 0) for s in steps), default=0)}")
        return {"group_id": g.id, "group_name": g.name,
                "start_id": start_id, "recap_total": recap_total,
                "rate_limited": limited_n,
                "steps": steps, "summary": summary}
