# DogBuddyCn — 自动化工具：自助排期 / 自调度 / 条件通知 (Batch 8 组一)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""让工位自己在对话里把"这事每天做"落成定时任务（T8.1），
任务结束时自己决定"下次什么时候再来"（T8.3），
并且**有事才通知**（T8.2）。

权限从紧（T8.1 明确要求）：工位只能管**自己**的任务，以及**同一个项目**里其他
工位的任务；跨项目/跨工位一律拒绝。所有写操作都走 PermissionGate + 审计。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .registry import ToolContext, ToolResult

logger = logging.getLogger("dogbuddy.tools.automation")

#: 自调度允许的最短/最长下次运行时间（防止工位把自己排成"1 秒后"疯狂空转，
#: 也防止排到 10 年后）
MIN_DELAY_SECONDS = 60
MAX_DELAY_SECONDS = 60 * 60 * 24 * 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_when(when: str) -> str | None:
    """把 "2h" / "30m" / "2026-10-15T09:00:00+08:00" 解析成 ISO 时间。

    返回 None = 解析不了（调用方报错让模型自改）。
    """
    raw = str(when or "").strip()
    if not raw:
        return None
    # 相对时长：30s / 15m / 2h / 3d
    unit = raw[-1].lower()
    if unit in "smhd" and raw[:-1].strip().replace(".", "", 1).isdigit():
        n = float(raw[:-1])
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        return (_now() + timedelta(seconds=n * mult)).isoformat()
    # ISO 时间（带不带时区都接受；不带时区按 UTC 解释）
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _clamp_next(iso_ts: str) -> tuple[str, str]:
    """把下次运行时间夹到 [now+MIN, now+MAX]。返回 (iso, 说明)。"""
    try:
        target = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return "", "时间格式无法解析"
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    now = _now()
    note = ""
    if target < now + timedelta(seconds=MIN_DELAY_SECONDS):
        target = now + timedelta(seconds=MIN_DELAY_SECONDS)
        note = f"（太近，已推迟到至少 {MIN_DELAY_SECONDS}s 后）"
    if target > now + timedelta(seconds=MAX_DELAY_SECONDS):
        target = now + timedelta(seconds=MAX_DELAY_SECONDS)
        note = "（太远，已限制在 90 天内）"
    return target.isoformat(), note


# --------------------------------------------------------------------------- T8.1
class ScheduleTool:
    """schedule — 工位给自己（或本项目内工位）建/改/删定时任务。"""

    name = "schedule"
    description = (
        "Manage scheduled tasks (cron jobs). Use this when the user says things like "
        "'do this every day' / 'check it hourly'. You may only manage jobs of YOUR OWN "
        "window, or of windows that belong to the same project as you. "
        "action=create needs name+when+prompt; action=update/delete/pause/resume needs job_id. "
        "`when` is ENGLISH schedule syntax ONLY (translate the user's wording): "
        "'30m' / 'every 30m' / 'every 2h' (recurring interval), "
        "'in 2h' (one-shot delay), 'every day 9am' / 'weekdays at 9am' (daily/weekly), "
        "'0 9 * * *' (cron), '2026-10-15T09:00:00' (one-shot at time). "
        "report_strategy: brief (always report) | silent (never) | conditional (only when you "
        "call notify_owner — use this to avoid useless notifications).")
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "update", "delete", "list", "pause", "resume", "run_now"],
                "description": "What to do. list shows jobs you are allowed to manage.",
            },
            "name": {"type": "string", "description": "Job name (create)."},
            "when": {"type": "string",
                     "description": "English schedule syntax: '30m' / 'every 2h' / 'in 2h' / "
                                    "'every day 9am' / 'weekdays at 9am' / '0 9 * * *' / ISO time."},
            "prompt": {"type": "string", "description": "What to do each time it runs (create)."},
            "job_id": {"type": "string", "description": "Target job id (update/delete/pause/resume/run_now)."},
            "report_strategy": {"type": "string", "enum": ["brief", "silent", "conditional"],
                                "description": "Notification policy for this job."},
            "target_window": {"type": "string",
                              "description": "Optional: another window in YOUR project (name or id). Defaults to yourself."},
        },
        "required": ["action"],
    }
    permission = "write"

    def __init__(self, jobs: Any = None, runner: Any = None):
        # jobs: JobStore；runner: callable(job_id) -> 立即执行（可为 None）
        self.jobs = jobs
        self.runner = runner

    # ---- 允许管理的工位集合（T8.1 权限从紧）
    def _allowed_windows(self, ctx: ToolContext) -> dict[str, str]:
        db = getattr(ctx, "db", None)
        allowed: dict[str, str] = {ctx.window_id: ctx.window_id}
        me = db.get_window(ctx.window_id) if db is not None else None
        proj_id = getattr(me, "project_id", "") if me is not None else ""
        if proj_id and db is not None:
            for w in db.list_windows():
                if (getattr(w, "project_id", "") or "") == proj_id:
                    allowed[w.id] = w.name
        return allowed

    @staticmethod
    def _resolve_job(jobs: Any, ref: str) -> Any:
        if not ref:
            return None
        by_id = jobs.get(ref)
        if by_id is not None:
            return by_id
        for j in jobs.list():
            if j.name == ref:
                return j
        return None

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if self.jobs is None:
            return ToolResult.failure("排期服务不可用（jobs 未注入）")
        action = str(args.get("action") or "").strip().lower()
        allowed = self._allowed_windows(ctx)

        if action == "list":
            rows = [j for j in self.jobs.list() if j.window_id in allowed]
            if not rows:
                return ToolResult.success("（没有你名下的定时任务）", jobs=[])
            lines = [f"- {j.name} [{j.id}] {j.schedule_raw or j.schedule_type} "
                     f"{'启用' if j.enabled else '已暂停'} 下次={j.next_run_at or '-'} "
                     f"报告={j.report_strategy}"
                     for j in sorted(rows, key=lambda x: x.name)]
            return ToolResult.success("你名下的定时任务：\n" + "\n".join(lines),
                                      jobs=[j.id for j in rows])

        if action == "create":
            name = str(args.get("name") or "").strip()
            when = str(args.get("when") or "").strip()
            prompt = str(args.get("prompt") or "").strip()
            missing = [k for k, v in (("name", name), ("when", when), ("prompt", prompt)) if not v]
            if missing:
                return ToolResult.failure(f"create 缺少必填参数：{missing}")

            target = str(args.get("target_window") or "").strip()
            win_id = ctx.window_id
            if target:
                match = next((wid for wid, nm in allowed.items()
                              if wid == target or nm == target), None)
                if match is None:
                    return ToolResult.failure(
                        f"越权：工位 {target} 不在你的项目里，你只能给本工位/本项目排期。"
                        f"可选的工位：{sorted(allowed.values())}")
                win_id = match

            strategy = str(args.get("report_strategy") or "brief").strip().lower()
            if strategy not in ("brief", "silent", "conditional"):
                strategy = "brief"
            try:
                job = self.jobs.create(win_id, name, when, prompt,
                                       report_strategy=strategy, kind="prompt")
            except Exception as e:  # noqa: BLE001 — 排期表达式写错要回传模型自改
                return ToolResult.failure(
                    f"排期创建失败：{e}。when 只接受英文排期语法："
                    f"'30m' / 'every 2h' / 'in 2h' / 'every day 9am' / 'weekdays at 9am' / "
                    f"'0 9 * * *' / ISO 时间（把用户的口语翻译成这些形式）。")
            if job is None:
                return ToolResult.failure("排期创建失败：schedule 无法解析（换个写法再试）")
            return ToolResult.success(
                f"已排期：{job.name}（{job.schedule_raw}），下次运行 {job.next_run_at}",
                job_id=job.id, next_run_at=job.next_run_at)

        # 以下都需要 job_id，且必须在本工位/本项目范围内
        ref = str(args.get("job_id") or "").strip()
        if not ref:
            return ToolResult.failure(f"{action} 需要 job_id（先用 action=list 查）")
        job = self._resolve_job(self.jobs, ref)
        if job is None:
            return ToolResult.failure(f"找不到任务：{ref}")
        if job.window_id not in allowed:
            return ToolResult.failure(
                f"越权：任务 {job.id} 属于工位 {job.window_id}，不在你的项目里。")

        if action == "delete":
            ok = self.jobs.remove(job.id)
            return (ToolResult.success(f"已删除任务 {job.name}") if ok
                    else ToolResult.failure(f"删除失败：{ref}"))
        if action == "pause":
            self.jobs.set_enabled(job.id, False)
            return ToolResult.success(f"已暂停任务 {job.name}")
        if action == "resume":
            j = self.jobs.set_enabled(job.id, True)
            return ToolResult.success(f"已恢复任务 {job.name}，下次 {j.next_run_at}")
        if action == "run_now":
            if self.runner is None:
                return ToolResult.failure("立即执行不可用")
            self.runner(job.id)
            return ToolResult.success(f"已触发 {job.name} 立即执行")
        if action == "update":
            fields: dict[str, Any] = {}
            for key in ("name", "prompt", "report_strategy"):
                if args.get(key) is not None:
                    fields[key] = args[key]
            if args.get("when"):
                fields["schedule"] = args["when"]
            if not fields:
                return ToolResult.failure("update 至少要给一个要改的字段（when/prompt/name/report_strategy）")
            try:
                j = self.jobs.update(job.id, **fields)
            except Exception as e:  # noqa: BLE001
                return ToolResult.failure(f"更新失败：{e}")
            return ToolResult.success(f"已更新任务 {j.name}，下次 {j.next_run_at}",
                                      job_id=j.id, next_run_at=j.next_run_at)
        return ToolResult.failure(f"不支持的 action：{action}（可选 create/update/delete/list/pause/resume/run_now）")


# --------------------------------------------------------------------------- T8.3
class RescheduleTool:
    """reschedule — 本次任务自己决定"下次什么时候再来"（覆盖默认 cron）。"""

    name = "reschedule"
    description = (
        "Decide when THIS task should run again, overriding the job's default schedule. "
        "Use it when the work dictates the rhythm (e.g. 'nothing to do now, check again "
        "tomorrow morning' or 'a build is running, come back in 20 minutes'). "
        "Give either `after` (relative: '2h', '30m', '1d') or `at` (ISO time). "
        "Only affects the CURRENT run's next occurrence; the original cron resumes after that.")
    parameters = {
        "type": "object",
        "properties": {
            "after": {"type": "string", "description": "Relative delay, e.g. '30m', '2h', '1d'."},
            "at": {"type": "string", "description": "Absolute ISO time, e.g. '2026-10-15T09:00:00+08:00'."},
            "reason": {"type": "string", "description": "Why (logged for the operator)."},
        },
    }
    permission = "write"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = str(args.get("after") or args.get("at") or "").strip()
        if not raw:
            return ToolResult.failure("需要给 after（如 '2h'）或 at（ISO 时间）")
        iso = _parse_when(raw)
        if iso is None:
            return ToolResult.failure(
                f"时间无法解析：{raw}。after 用 '30m'/'2h'/'1d'，at 用 ISO 时间。")
        clamped, note = _clamp_next(iso)
        if not clamped:
            return ToolResult.failure(f"时间无法解析：{raw}")
        ctx.extra["next_run_at"] = clamped
        ctx.extra["reschedule_reason"] = str(args.get("reason") or "")[:200]
        logger.info("window %s self-scheduled next run at %s (%s)",
                    ctx.window_id, clamped, note or "ok")
        return ToolResult.success(f"已设定下次运行时间：{clamped} {note}".strip(),
                                  next_run_at=clamped)


# --------------------------------------------------------------------------- T8.2
class NotifyOwnerTool:
    """notify_owner — 条件任务：**有事才通知**。

    配合 `report_strategy=conditional` 使用：只有调用过本工具的任务才会真的通知
    管理员，没调用就静默记进 `job_runs`（砍掉"没事也报一遍"的噪音）。
    """

    name = "notify_owner"
    description = (
        "Notify the owner about something that needs attention. For scheduled jobs with "
        "report_strategy=conditional, calling this is what makes the job report at all — "
        "if there is nothing worth telling, do NOT call it and the run stays silent. "
        "Use it only when a human really needs to act or know.")
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "What to tell the owner (concise, actionable)."},
            "level": {"type": "string", "enum": ["info", "warn", "alert"],
                      "description": "info (FYI) | warn (needs a look) | alert (needs action now)."},
        },
        "required": ["text"],
    }
    permission = "write"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        text = str(args.get("text") or "").strip()
        if not text:
            return ToolResult.failure("text 不能为空")
        level = str(args.get("level") or "info").strip().lower()
        if level not in ("info", "warn", "alert"):
            level = "info"
        ctx.extra["notify"] = {"text": text[:2000], "level": level}
        logger.info("window %s requested owner notification (%s)", ctx.window_id, level)
        return ToolResult.success(f"已推送通知给管理员（{level}）：{text[:120]}",
                                  notify=True, level=level)


def all_tools(jobs: Any = None, runner: Any = None) -> list:
    return [ScheduleTool(jobs=jobs, runner=runner), RescheduleTool(), NotifyOwnerTool()]
