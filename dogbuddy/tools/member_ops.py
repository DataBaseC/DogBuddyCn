# DogBuddyCn — 组长派活给组员（子代理）(v0.10.33)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""`member_dispatch` — 让**组长**把任务派给群组里的组员，并拿回结果。

owner 的观察（2026-10-16）："组长没给组员派活，按理说组长可以让组员成为他的子代理进行工作。"

事实确实是：员工工具箱里**没有任何"派活给别的工位"的工具**（`explore_subagent` 只是
一个进程内只读研究小助手，不是组员工位），而且群组运行把组长**排在最后**（`order =
其他成员 + [组长]`）—— 组长那一轮只能"汇总"，没有机会分工。

安全口径（fail-closed，全部在工具内部裁决，不依赖作用域元数据 —— 员工的作用域是 none，
声明 `required_scope` 会把组长自己也挡住）：
1. 调用者必须处于**群组运行**里（`ctx.extra["group_id"]`），且 `is_lead=True`；
2. 目标工位必须是**同一个群组**的成员；
3. 不能给自己派活。
"""
from __future__ import annotations

from typing import Any

from .registry import ToolContext, ToolResult


class MemberDispatchTool:
    """组长 → 组员：派活（可等结果）。"""

    name = "member_dispatch"
    description = (
        "As the GROUP LEAD, delegate a piece of work to one of your group members "
        "(they act as your sub-agent): the work is dispatched to that member's window, "
        "and with wait=true you get their result back and can keep orchestrating. "
        "Use wait=false to queue the work without blocking. Only the group lead may "
        "call this, and only for members of the same group.")
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "window": {"type": "string",
                       "description": "member window id or name (must be in this group)"},
            "task": {"type": "string",
                     "description": "the concrete task for that member (self-contained: "
                                    "goal, deliverable, where to put it, how to verify)"},
            "wait": {"type": "boolean",
                     "description": "wait for the member's result and return it (default true)"},
            "timeout": {"type": "integer",
                        "description": "seconds to wait at most (default: the member's task_timeout)"},
        },
        "required": ["window", "task"],
    }
    permission = "write"

    def __init__(self, coordinator: Any = None):
        #: 需要 `dispatch(window_id, prompt)` 与
        #: `dispatch_and_wait(window_id, prompt, timeout=...)`（由 WindowManager 提供）
        self.coordinator = coordinator

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        extra = dict(getattr(ctx, "extra", None) or {})
        gid = str(extra.get("group_id") or "").strip()
        if not gid:
            return ToolResult.failure(
                "只有**群组任务里的组长**能派活（当前运行不在群组里）。"
                "普通工位请直接把活干完，或用 ask_user 向管理员求助。")
        if not extra.get("is_lead"):
            return ToolResult.failure("只有组长能派活给组员（组员之间不能互相指挥）。")
        db = getattr(ctx, "db", None)
        if db is None:
            return ToolResult.failure("member_dispatch 需要 db（运行时注入）")
        target_ref = str(args.get("window") or "").strip()
        task = str(args.get("task") or "").strip()
        if not target_ref or not task:
            return ToolResult.failure("window 与 task 都不能为空")
        w = db.get_window(target_ref) or db.get_window_by_name(target_ref)
        if w is None:
            return ToolResult.failure(f"组员不存在：{target_ref}")
        member_ids = [m.window_id for m in db.list_group_members(gid)]
        if w.id not in member_ids:
            return ToolResult.failure(
                f"越权：工位 {w.name} 不在本群组里（fail-closed）。"
                f"本群组成员：{member_ids}")
        if str(w.id) == str(getattr(ctx, "window_id", "")):
            return ToolResult.failure("不能给自己派活——那段活你自己干。")
        if self.coordinator is None:
            return ToolResult.failure("派活能力不可用：调度器未注入（请管理员检查装配）。")
        wait = bool(args.get("wait", True))
        timeout = args.get("timeout")
        #: 让调用方（GroupManager）知道这次派单：① 接力轮不再重复跑 ② 收尾前要 join
        dispatched = extra.setdefault("dispatched_windows", [])
        if w.id not in dispatched:
            dispatched.append(w.id)
        #: 派单台账（**共享同一个 list**）：收尾轮开始前靠它 join + 步骤级续跑。
        #: v0.10.36：以前只记一个 id，`wait=false` 时收尾汇总会在组员还没开工时跑，
        #: 组员被限速时也没人重试这一步（那两个洞是 owner 那个前端项目的真风险）。
        entry = {"window_id": w.id, "window_name": w.name, "task": task,
                 "wait": wait, "status": "queued", "attempts": 0, "summary": "",
                 "timeout": float(timeout) if timeout else None}
        extra.setdefault("delegated", []).append(entry)
        if not wait:
            # 不阻塞组长这一轮，但留下可等待的句柄（收尾前 join）
            pending = extra.setdefault("pending_runs", [])
            fut = None
            if hasattr(self.coordinator, "dispatch_pending"):
                try:
                    fut = self.coordinator.dispatch_pending(
                        w.id, task, run_parent_id=extra.get("run_id", ""))
                except TypeError:      # 兼容不支持 run_parent_id 的实现
                    try:
                        fut = self.coordinator.dispatch_pending(w.id, task)
                    except Exception as e:  # noqa: BLE001
                        return ToolResult.failure(f"派活给 {w.name} 失败：{e}")
                except Exception as e:  # noqa: BLE001
                    return ToolResult.failure(f"派活给 {w.name} 失败：{e}")
            else:  # 兼容：只能 fire-and-forget（收尾时无法 join，会在 run 里记为未知）
                self.coordinator.dispatch(w.id, task)
            if fut is not None:
                pending.append((w.id, fut))
            return ToolResult.success(
                f"已把任务排给 {w.name}（不等结果，但**收尾前系统会等它干完**）。",
                window_id=w.id, waited=False)
        try:
            outcome = await self.coordinator.dispatch_and_wait(
                w.id, task, coordinated=True, run_parent_id=extra.get("run_id", ""),
                **({"timeout": float(timeout)} if timeout else {}))
        except TypeError:  # 兼容没有 coordinated/timeout/run_parent_id 参数的实现
            try:
                outcome = await self.coordinator.dispatch_and_wait(w.id, task,
                                                                  coordinated=True)
            except TypeError:
                outcome = await self.coordinator.dispatch_and_wait(w.id, task)
        except Exception as e:  # noqa: BLE001 — 派活失败要如实回报，不能把组长带崩
            entry["status"] = "failed"
            entry["summary"] = str(e)
            return ToolResult.failure(f"派活给 {w.name} 失败：{e}")
        status = str(getattr(outcome, "status", "") or "")
        summary = str(getattr(outcome, "summary", "") or "")
        entry["status"] = status or "unknown"
        entry["summary"] = summary
        note = {
            "ok": "完成",
            "rate_limited": "被限速暂停（非失败，稍后可再派）",
            "timeout": "超时（任务仍在后台跑）",
            "failed": "失败",
        }.get(status, status or "结束")
        return ToolResult.success(
            f"{w.name} 的结果（{note}）：\n{summary[:2000] or '(无内容)'}",
            window_id=w.id, status=status, waited=True)


def all_tools(coordinator: Any = None) -> list[Any]:
    return [MemberDispatchTool(coordinator)]
