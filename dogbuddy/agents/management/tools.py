# DogBuddyCn — management tool pack (L3 Domain, 角色无关)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""管理工具（window/workspace/task/context/config/memory/report）——**角色无关**。

Batch 9 · T9.2：从 `agents/admin/tools.py` 搬到这里，并给每个工具加
`required_scope` 元数据（`project` | `system`）。可用性由两层保证：
  1. **装配时**按 scope 过滤工具集（管理员=全部；主管=只装 project 级）
  2. **运行时**由 `PermissionGate.check_scope` 按作用域裁决目标资源（fail-closed）

**工具名与 schema 一个字都没改**（提示词契约稳定），只多了 `required_scope`。
管理员行为逐位不变：`build_admin_tools` = 全部工具 + system 作用域。
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from ...tools.registry import ToolContext, ToolResult

logger = logging.getLogger("dogbuddy.agents.admin")


class AdminServices:
    """Port shape expected from L2 (implemented by services/*). All optional;
    a missing service degrades the corresponding tool to a clear failure."""

    def __init__(self, windows: Any = None, jobs: Any = None, sessions: Any = None,
                 workspaces: Any = None, memory: Any = None, config: Any = None,
                 progress: Callable[[], dict] | None = None,
                 self_status: Callable[[bool], dict] | None = None,
                 self_update: Callable[..., dict] | None = None,
                 supervision: Any = None):
        self.windows = windows      # WindowManager: create/list/get/update/pause/resume/delete/status/dispatch
        self.jobs = jobs            # JobManager: create/list/get/update/pause/resume/remove/run_now
        self.sessions = sessions    # SessionManager: get/compress/reset/inject
        self.workspaces = workspaces  # create/list/assign
        self.memory = memory        # MemoryService
        self.config = config        # get/set/report_set
        self.progress = progress
        # self-upgrade: (check_remote: bool) -> dict  /  (ref, force, start_delay) -> dict
        self.self_status = self_status
        self.self_update = self_update
        # T9.4 指挥链：管理员→主管 的指令下发（`supervisor_command` 用）
        self.supervision = supervision


class FnTool:
    """Adapter turning a plain async function into the Tool protocol."""

    def __init__(self, name: str, description: str, parameters: dict[str, Any],
                 fn: Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult] | ToolResult],
                 permission: str = "read"):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.permission = permission
        self._fn = fn

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            out = self._fn(args, ctx)
            if hasattr(out, "__await__"):
                out = await out
            return out
        except Exception as e:  # noqa: BLE001
            logger.exception("admin tool %s failed", self.name)
            return ToolResult.failure(f"{self.name} failed: {e}")


_OBJ = {"type": "object", "properties": {}}


def _props(**kw: Any) -> dict[str, Any]:
    return {"type": "object", "properties": kw, "required": [k for k, v in kw.items() if v.get("required")]}


def _p(t: str, desc: str, required: bool = False, **extra: Any) -> dict[str, Any]:
    d = {"type": t, "description": desc}
    d.update(extra)
    d["required"] = required
    return d


def build_admin_tools(svc: AdminServices) -> list[Any]:
    tools: list[Any] = []
    W, J, S = svc.windows, svc.jobs, svc.sessions

    def need(obj: Any, name: str) -> None:
        if obj is None:
            raise RuntimeError(f"service {name} not wired")

    # ------------------------------------------------------------- windows
    async def window_create(args: dict, ctx: ToolContext) -> ToolResult:
        need(W, "windows")
        w = W.create(name=args["name"], role=args.get("role", "worker_programmer"),
                     workspace_path=args["workspace"], model=args.get("model", ""),
                     mode=args.get("mode", "BUILD"),
                     llm_profile=args.get("llm_profile", ""))
        return ToolResult.success(f"window created: {w.id} ({w.name})", window_id=w.id)

    tools.append(FnTool("window_create",
        "Create an employee window (workstation). Omit `model`/`llm_profile` unless the user "
        "asked for a specific model: empty means the new window inherits the same model the "
        "admin itself runs on, which is the desired default.",
        _props(name=_p("string", "window name", True), role=_p("string", "role, default worker_programmer"),
               workspace=_p("string", "workspace path", True),
               model=_p("string", "model override; leave empty to inherit the admin's model"),
               mode=_p("string", "BUILD|PLAN|AUTO, default BUILD"),
               llm_profile=_p("string", "named LLM config set to use (empty = inherit)")),
        window_create, "write"))

    async def window_list(args: dict, ctx: ToolContext) -> ToolResult:
        need(W, "windows")
        rows = []
        for w in W.list():
            rows.append(f"{w.id} | {w.name} | role={w.role} | model={w.model} | mode={w.mode} | status={w.status}")
        return ToolResult.success("\n".join(rows) or "(no windows)")

    tools.append(FnTool("window_list", "List all windows with status.", _OBJ, window_list))

    async def window_status(args: dict, ctx: ToolContext) -> ToolResult:
        need(W, "windows")
        info = W.status(args.get("window_id") or args.get("name", ""))
        return ToolResult.success(str(info), **{k: v for k, v in info.items() if isinstance(v, (int, str, bool))})

    tools.append(FnTool("window_status", "Detailed status of one window (current task, queue, tokens).",
        _props(window_id=_p("string", "window id or name", True)), window_status))

    async def window_update(args: dict, ctx: ToolContext) -> ToolResult:
        need(W, "windows")
        fields = {k: v for k, v in args.items()
                  if k in ("name", "model", "mode", "llm_profile") and v is not None and v != ""}
        w = W.update(args["window_id"], **fields)
        return ToolResult.success(f"updated {w.id}: {fields}")

    tools.append(FnTool("window_update", "Update window name/model/mode/llm_profile.",
        _props(window_id=_p("string", "window id", True), name=_p("string", "new name"),
               model=_p("string", "new model"), mode=_p("string", "BUILD|PLAN|AUTO"),
               llm_profile=_p("string", "named LLM config set")),
        window_update, "write"))

    for action, verb in (("window_pause", "pause"), ("window_resume", "resume"), ("window_delete", "delete")):
        def make(verb: str):
            async def fn(args: dict, ctx: ToolContext) -> ToolResult:
                need(W, "windows")
                ok = getattr(W, verb)(args["window_id"])
                return ToolResult.success(f"{verb} {args['window_id']}: {'ok' if ok else 'not found'}")
            return fn
        tools.append(FnTool(action, f"{verb.capitalize()} a window.",
            _props(window_id=_p("string", "window id", True)), make(verb), "write"))

    # ---------------------------------------------------------- workspaces
    async def workspace_create(args: dict, ctx: ToolContext) -> ToolResult:
        need(svc.workspaces, "workspaces")
        info = svc.workspaces.create(args["path"], git_init=bool(args.get("git_init")))
        return ToolResult.success(f"workspace ready: {info}")

    tools.append(FnTool("workspace_create", "Create a workspace directory (optionally git init).",
        _props(path=_p("string", "absolute directory path", True),
               git_init=_p("boolean", "run git init")), workspace_create, "write"))

    async def workspace_list(args: dict, ctx: ToolContext) -> ToolResult:
        need(svc.workspaces, "workspaces")
        return ToolResult.success("\n".join(svc.workspaces.list()) or "(none)")

    tools.append(FnTool("workspace_list", "List known workspaces.", _OBJ, workspace_list))

    async def workspace_assign(args: dict, ctx: ToolContext) -> ToolResult:
        need(W, "windows")
        w = W.update(args["window_id"], workspace_path=args["path"])
        return ToolResult.success(f"window {w.id} workspace -> {args['path']}")

    tools.append(FnTool("workspace_assign", "Point a window at a workspace.",
        _props(window_id=_p("string", "window id", True), path=_p("string", "workspace path", True)),
        workspace_assign, "write"))

    # ---------------------------------------------------------------- jobs
    async def task_create(args: dict, ctx: ToolContext) -> ToolResult:
        need(J, "jobs")
        job = J.create(window_id=args["window_id"], name=args["name"], schedule=args["schedule"],
                       prompt=args["prompt"], report_strategy=args.get("report_strategy", "brief"),
                       kind=args.get("kind", "prompt"),
                       report_channel=args.get("report_channel", ""),
                       llm_profile=args.get("llm_profile", ""))
        return ToolResult.success(f"job created: {job.id} ({job.name}) next={job.next_run_at}", job_id=job.id)

    tools.append(FnTool("task_create",
        "Create a scheduled task for a window. schedule formats: '30m', 'every monday 9am', "
        "'0 9 * * *', ISO timestamp '2026-02-03T14:00:00'.",
        _props(window_id=_p("string", "target window id", True), name=_p("string", "task name", True),
               schedule=_p("string", "schedule expression", True),
               prompt=_p("string", "what the employee should do", True),
               report_strategy=_p("string", "silent|brief|detailed"),
               kind=_p("string", "prompt|script"),
               report_channel=_p("string", "notifier channel name (empty = all channels)"),
               llm_profile=_p("string", "named LLM config set (empty = inherit window)")),
        task_create, "write"))

    async def task_list(args: dict, ctx: ToolContext) -> ToolResult:
        need(J, "jobs")
        rows = []
        for j in J.list(args.get("window_id")):
            rows.append(f"{j.id} | {j.name} | win={j.window_id} | {j.schedule_raw} | "
                        f"enabled={j.enabled} | next={j.next_run_at} | fails={j.fail_count}")
        return ToolResult.success("\n".join(rows) or "(no jobs)")

    tools.append(FnTool("task_list", "List scheduled tasks.",
        _props(window_id=_p("string", "filter by window")), task_list))

    async def task_update(args: dict, ctx: ToolContext) -> ToolResult:
        need(J, "jobs")
        fields = {k: v for k, v in args.items() if k in ("name", "schedule", "prompt", "report_strategy") and v}
        job = J.update(args["job_id"], **fields)
        return ToolResult.success(f"job updated: {job.id} -> {fields}")

    tools.append(FnTool("task_update", "Update task fields (schedule re-parsed).",
        _props(job_id=_p("string", "job id", True), name=_p("string", "new name"),
               schedule=_p("string", "new schedule"), prompt=_p("string", "new prompt"),
               report_strategy=_p("string", "silent|brief|detailed")), task_update, "write"))

    for action, verb in (("task_pause", "pause"), ("task_resume", "resume"),
                         ("task_remove", "remove"), ("task_run_now", "run_now")):
        def make(verb: str):
            async def fn(args: dict, ctx: ToolContext) -> ToolResult:
                need(J, "jobs")
                out = getattr(J, verb)(args["job_id"])
                return ToolResult.success(f"{verb} {args['job_id']}: {out}")
            return fn
        tools.append(FnTool(action, f"{verb} a task (run_now inserts at queue head).",
            _props(job_id=_p("string", "job id", True)), make(verb), "write"))

    # ------------------------------------------------------------- context
    async def context_view(args: dict, ctx: ToolContext) -> ToolResult:
        need(S, "sessions")
        info = S.view(args["window_id"])
        return ToolResult.success(info["text"], tokens=info.get("tokens", 0), messages=info.get("messages", 0))

    tools.append(FnTool("context_view", "View a window's session context usage and tail.",
        _props(window_id=_p("string", "window id", True)), context_view))

    async def context_compress(args: dict, ctx: ToolContext) -> ToolResult:
        need(S, "sessions")
        rec = await S.compress(args["window_id"])
        return ToolResult.success(f"compacted: replaced {rec.replaced_count if rec else 0} messages")

    tools.append(FnTool("context_compress", "Force context compaction now (anti-loss summary).",
        _props(window_id=_p("string", "window id", True)), context_compress, "write"))

    async def context_reset(args: dict, ctx: ToolContext) -> ToolResult:
        need(S, "sessions")
        n = S.reset(args["window_id"])
        return ToolResult.success(f"context reset, dropped {n} messages")

    tools.append(FnTool("context_reset", "Drop all session messages of a window (memory files kept).",
        _props(window_id=_p("string", "window id", True)), context_reset, "write"))

    async def context_inject(args: dict, ctx: ToolContext) -> ToolResult:
        need(S, "sessions")
        S.inject(args["window_id"], args["text"])
        return ToolResult.success("text injected into window context")

    tools.append(FnTool("context_inject", "Inject a note/answer into a window's context.",
        _props(window_id=_p("string", "window id", True), text=_p("string", "text to inject", True)),
        context_inject, "write"))

    # --------------------------------------------------------------- config
    async def config_get(args: dict, ctx: ToolContext) -> ToolResult:
        need(svc.config, "config")
        if args.get("key"):
            return ToolResult.success(f"{args['key']} = {svc.config.get(args['key'])!r}")
        return ToolResult.success(str(svc.config.dump()))

    tools.append(FnTool("config_get", "Read global config (all or one key).",
        _props(key=_p("string", "config key")), config_get))

    async def config_set(args: dict, ctx: ToolContext) -> ToolResult:
        need(svc.config, "config")
        svc.config.set(args["key"], args["value"])
        return ToolResult.success(f"config set: {args['key']} = {args['value']!r}")

    tools.append(FnTool("config_set", "Set a global config key.",
        _props(key=_p("string", "config key", True), value=_p("string", "value (JSON allowed)", True)),
        config_set, "write"))

    async def report_set(args: dict, ctx: ToolContext) -> ToolResult:
        need(svc.config, "config")
        strategy = args.get("strategy", "brief")
        if args.get("job_id"):
            need(J, "jobs")
            J.update(args["job_id"], report_strategy=strategy)
            return ToolResult.success(f"job {args['job_id']} report strategy -> {strategy}")
        svc.config.set("report.default_strategy", strategy)
        return ToolResult.success(f"default report strategy -> {strategy}")

    tools.append(FnTool("report_set", "Set report strategy (default or per job).",
        _props(strategy=_p("string", "silent|brief|detailed", True),
               job_id=_p("string", "optional job id")), report_set, "write"))

    # --------------------------------------------------------- llm profiles
    def _app_cfg():
        need(svc.config, "config")
        app = getattr(svc.config, "app", None)
        if app is None:
            raise RuntimeError("config service has no AppConfig bound")
        return app

    async def profile_list(args: dict, ctx: ToolContext) -> ToolResult:
        app = _app_cfg()
        lines = [f"- (default): {app.llm.provider} {app.llm.base_url} model={app.llm.model or '(unset)'}"
                 + (" [ACTIVE]" if not app.active_llm_profile else "")]
        for name, section in sorted(app.llm_profiles.items()):
            mark = " [ACTIVE]" if name == app.active_llm_profile else ""
            lines.append(f"- {name}: {section.provider} {section.base_url} model={section.model or '(unset)'}{mark}")
        return ToolResult.success("\n".join(lines), active=app.active_llm_profile or "(default)")

    tools.append(FnTool("profile_list", "List named LLM config sets (profiles) and which is active.",
        _OBJ, profile_list))

    async def profile_save(args: dict, ctx: ToolContext) -> ToolResult:
        app = _app_cfg()
        name = str(args["name"]).strip()
        section = app.llm_profiles.get(name) or app.llm.model_copy(deep=True)
        for key in ("provider", "base_url", "model", "api_key", "temperature",
                    "max_context_tokens", "compact_threshold", "request_timeout"):
            if args.get(key) not in (None, ""):
                setattr(section, key, args[key])
        app.save_profile(name, section)
        if args.get("set_active"):
            app.active_llm_profile = name
        svc.config.db.audit("admin", "profile:save", name)
        return ToolResult.success(f"profile saved: {name}"
                                  + (" (active)" if app.active_llm_profile == name else ""))

    tools.append(FnTool("profile_save",
        "Create or update a named LLM config set (model/base_url/key may be updated partially).",
        _props(name=_p("string", "profile name", True),
               provider=_p("string", "openai_compat|ollama"), base_url=_p("string", "API base url"),
               model=_p("string", "model id"), api_key=_p("string", "api key"),
               temperature=_p("number", "sampling temperature"),
               set_active=_p("boolean", "make this profile the default for new windows")),
        profile_save, "write"))

    async def profile_remove(args: dict, ctx: ToolContext) -> ToolResult:
        app = _app_cfg()
        name = str(args["name"]).strip()
        ok = app.delete_profile(name)
        if ok and app.active_llm_profile == name:
            app.active_llm_profile = ""
        svc.config.db.audit("admin", "profile:remove", name)
        return ToolResult.success(f"removed: {name}" if ok else f"not found: {name}")

    tools.append(FnTool("profile_remove", "Delete a named LLM config set (the default one is kept).",
        _props(name=_p("string", "profile name", True)), profile_remove, "write"))

    # --------------------------------------------------------------- memory
    async def memory_write(args: dict, ctx: ToolContext) -> ToolResult:
        need(svc.memory, "memory")
        svc.memory.write(args.get("scope", "global"), args["fact"])
        return ToolResult.success(f"memory written ({args.get('scope', 'global')})")

    tools.append(FnTool("memory_write", "Write a fact to memory (global or a window id).",
        _props(scope=_p("string", "'global' or window id"), fact=_p("string", "the fact", True)),
        memory_write, "write"))

    async def memory_recall(args: dict, ctx: ToolContext) -> ToolResult:
        need(svc.memory, "memory")
        scope = args.get("scope", "global")
        text = svc.memory.recall(scope) if scope == "global" else svc.memory.recall_window(scope)
        return ToolResult.success(text or "(empty)")

    tools.append(FnTool("memory_recall", "Read memory (global or a window id).",
        _props(scope=_p("string", "'global' or window id")), memory_recall))

    # -------------------------------------------------------------- report
    async def progress_report(args: dict, ctx: ToolContext) -> ToolResult:
        need(svc.progress, "progress")
        data = svc.progress()
        return ToolResult.success(data.get("text", str(data)))

    tools.append(FnTool("progress_report", "Summarize all windows, tasks and recent runs.",
        _OBJ, progress_report))

    # ---------------------------------------------------------- self upgrade
    async def self_version(args: dict, ctx: ToolContext) -> ToolResult:
        need(svc.self_status, "self_status")
        info = svc.self_status(bool(args.get("check_remote")))
        lines = [f"当前版本: {info.get('version')}",
                 f"构建标记: {info.get('build') or '(none)'}"]
        g = info.get("git") or {}
        if g.get("is_git"):
            lines.append(f"代码来源: git {g.get('describe') or g.get('short') or '?'}"
                         f" @ {g.get('remote') or '(no remote)'}")
        else:
            lines.append("代码来源: 离线 bundle 安装（无 .git）——只能从本机升级")
        lu = info.get("last_update")
        if lu:
            lines.append("上次升级: {status} {prev} → {ver} @ {when}{extra}".format(
                status=lu.get("status"), prev=lu.get("previous") or "?",
                ver=lu.get("version") or "?", when=lu.get("finished_at_iso") or "?",
                extra=("（" + str(lu.get("detail")) + "）") if lu.get("detail") else ""))
        else:
            lines.append("上次升级: 无记录")
        remote = info.get("remote")
        if isinstance(remote, dict):
            if not remote.get("ok"):
                lines.append(f"远端检查: 失败（{remote.get('error')}）")
            elif remote.get("update_available"):
                lines.append(f"远端检查: 有新版本可升级（{str(remote.get('remote'))[:10]}）")
            else:
                lines.append("远端检查: 已是最新")
        return ToolResult.success("\n".join(lines),
                                  version=info.get("version"), last_update=lu or {})

    tools.append(FnTool("self_version",
        "Show DogBuddyCn's own version, where its code came from, the result of the last "
        "self-upgrade, and (with check_remote=true) whether the remote has a newer commit.",
        _props(check_remote=_p("boolean", "also ask the git remote (needs network)")),
        self_version))

    async def self_update(args: dict, ctx: ToolContext) -> ToolResult:
        need(svc.self_update, "self_update")
        res = svc.self_update(
            ref=str(args.get("ref") or "").strip(),
            force=bool(args.get("force")),
            start_delay=int(args.get("start_delay") or 45))
        if not res.get("started"):
            return ToolResult.failure(f"无法启动自升级: {res.get('error') or res}")
        return ToolResult.success(
            "自升级已在后台启动（pid {pid}）。\n"
            "流程：等待 {d}s（让本轮对话落盘）→ 拉取代码 → 装依赖 → 重启服务 → "
            "健康检查断言新版本号 → 不通过则自动回滚。\n"
            "**请立刻结束本轮回复**，不要继续调用工具（服务马上会重启）。\n"
            "告诉用户：升级进行中，约 1~2 分钟后可以再问一次「现在什么版本」查看结果。\n"
            "升级日志: {log}".format(pid=res.get("pid"), d=res.get("start_delay"),
                                     log=res.get("log")),
            started=True, pid=res.get("pid"))

    tools.append(FnTool("self_update",
        "Upgrade DogBuddyCn's own code from the git remote (fetch → install deps → restart → "
        "health check → rollback on failure). Returns immediately; the service restarts ~45s "
        "later, so end the turn right after calling it. Only call when the user asks to "
        "upgrade/update the software.",
        _props(ref=_p("string", "git ref, default origin/main"),
               force=_p("boolean", "stash local code changes instead of aborting"),
               start_delay=_p("integer", "seconds to wait before switching code (default 45)")),
        self_update, "shell"))

    # -------------------------------------------------- T9.4 管理员 → 主管（P1-1）
    async def supervisor_command(args: dict, ctx: ToolContext) -> ToolResult:
        """把目标/优先级下发给项目主管（指挥链的第一跳）。

        初版这里**没有任何入口**：`SupervisionService.command()` 零调用者，
        四向信息流的第一向在生产里不可达（审查报告 P1-1）。
        """
        sup = getattr(svc, "supervision", None)
        if sup is None:
            return ToolResult.failure("指挥链服务不可用（supervision 未注入）")
        pid = str(args.get("project_id") or "").strip()
        text = str(args.get("text") or "").strip()
        if not pid or not text:
            return ToolResult.failure("project_id 与 text 都不能为空")
        if svc.windows is not None and svc.windows.get(pid) is not None:
            return ToolResult.failure(f"{pid} 是一个工位 id，不是项目 id——请用 window_list 之外的项目列表确认。")
        try:
            target = sup.supervisor_for(pid)
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"无法解析项目主管：{e}")
        if not target:
            return ToolResult.failure(
                f"项目 {pid} 还没有主管工位：请先建一个 role=supervisor 的工位，"
                f"并用 project_patch 把 owner_supervisor_id 指到它（或把主管工位挂进该项目）。")
        sup.command(pid, text)
        return ToolResult.success(f"已把指令下发给项目 {pid} 的主管工位 {target}。",
                                  project_id=pid, supervisor=target)

    tools.append(FnTool("supervisor_command",
        "Send a goal / instruction from the ADMIN to a project's SUPERVISOR "
        "(管理员→主管，指挥链第一跳). The supervisor then breaks it down and dispatches to its "
        "project's workers. Use this instead of dispatching to employee windows directly when "
        "the project has a supervisor.",
        _props(project_id=_p("string", "project id", True),
               text=_p("string", "the goal/instruction to hand down", True)),
        supervisor_command, "write"))

    # -------------------------------------------------- T9.6 审批闭环（P1-6）
    async def approve_decision(args: dict, ctx: ToolContext) -> ToolResult:
        """主管裁决一条**投给自己**的待批事项（员工升级上来的高危操作）。

        初版完全没有这个通道：`loop.py` 把审批升级给主管后只投一条文本就返回失败，
        主管手上**没有任何工具**能批准它 → 操作永久卡死（"配了主管反而更差"：
        无主管时用户还能在控制台批准并重放）。现在升级会落 `pending_decisions`，
        这个工具负责把它裁决掉，员工那边的 `decisions.wait()` 随即拿到答复并重放。
        """
        decisions = (getattr(ctx, "extra", None) or {}).get("decisions")
        if decisions is None:
            return ToolResult.failure("待决事项服务不可用（decisions 未注入）")
        try:
            did = int(args.get("decision_id"))
        except (TypeError, ValueError):
            return ToolResult.failure("decision_id 必须是整数")
        approve = bool(args.get("approve"))
        d = None
        try:
            d = ctx.db.get_pending_decision(did) if getattr(ctx, "db", None) is not None else None
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"查询待决事项失败：{e}")
        if d is None:
            return ToolResult.failure(f"待决事项 #{did} 不存在")
        if d.status != "pending":
            return ToolResult.failure(f"待决事项 #{did} 已经是 {d.status}，无需再裁决。")
        # 只能裁决**投给自己**的（防止代理去批别的工位/别的项目的审批）。
        # system 作用域（管理员）例外——它是全权层，也是升级链的终点。
        scope = getattr(ctx, "scope", None)
        is_system = bool(getattr(scope, "is_system", False))
        if not is_system and str(getattr(d, "window_id", "")) != str(ctx.window_id or ""):
            return ToolResult.failure(
                f"待决事项 #{did} 不属于你（收件人 {getattr(d, 'window_id', '?')}），无权裁决。")
        answer = "批准" if approve else "拒绝"
        if decisions.answer(did, answer) is None:
            return ToolResult.failure(f"裁决 #{did} 失败（可能已被别处处理）")
        return ToolResult.success(
            f"已{'批准' if approve else '拒绝'}待决事项 #{did}；"
            + ("申请方将继续执行该操作。" if approve else "申请方会收到拒绝并停止该操作。"),
            decision_id=did, approved=approve)

    tools.append(FnTool("approve_decision",
        "Approve or reject a pending approval request that was escalated TO YOU by a worker "
        "(high-risk operation needing your sign-off). Use `decision_list`/the console to see "
        "the id — it is also quoted in the escalation message.",
        _props(decision_id=_p("integer", "pending decision id", True),
               approve=_p("boolean", "true = 批准, false = 拒绝", True)),
        approve_decision, "write"))

    async def decision_list(args: dict, ctx: ToolContext) -> ToolResult:
        """列出**投给自己**的待裁决事项（主管的收件箱）。"""
        decisions = (getattr(ctx, "extra", None) or {}).get("decisions")
        if decisions is None:
            return ToolResult.failure("待决事项服务不可用（decisions 未注入）")
        try:
            items = [d for d in decisions.list(status="pending", limit=100)
                     if is_system
                     or str(getattr(d, "window_id", "")) == str(ctx.window_id or "")]
        except Exception as e:  # noqa: BLE001
            return ToolResult.failure(f"读取待决事项失败：{e}")
        if not items:
            return ToolResult.success("(没有待你裁决的事项)")
        lines = [f"#{d.id} {'⚠️' if getattr(d, 'source', '') == 'escalated_to_supervisor' else '❓'} "
                 f"{str(d.question).splitlines()[0][:120]}" for d in items]
        return ToolResult.success("\n".join(lines), count=len(items))

    tools.append(FnTool("decision_list",
        "List the pending approval requests / questions waiting for YOU (the supervisor's inbox).",
        _OBJ, decision_list))

    return tools


# --------------------------------------------------------------- T9.2 作用域元数据
#: 需要 **system** 作用域的（仅管理员）：全局配置 / 密钥相关 / 账号 / 自升级 / 全局记忆
SYSTEM_TOOL_NAMES: frozenset[str] = frozenset({
    "config_get", "config_set",
    "profile_list", "profile_save", "profile_remove",
    "report_set",                     # 全局上报策略
    "self_version", "self_update",    # 自升级：绝不下放给主管
    "memory_write", "memory_recall",  # 全局记忆（主管只有项目/工位记忆）
    "supervisor_command",             # 管理员→主管 的指令下发（T9.4，主管不能命令自己）
})

#: 需要 **project** 作用域的：管本项目里的工位 / 任务 / 上下文 / 工作区
PROJECT_TOOLS: frozenset[str] = frozenset({
    "window_create", "window_list", "window_status", "window_update",
    "window_pause", "window_resume", "window_delete",
    "workspace_create", "workspace_list", "workspace_assign",
    "task_create", "task_list", "task_update", "task_pause", "task_resume",
    "task_remove", "task_run_now",
    "context_view", "context_compress", "context_reset", "context_inject",
    "progress_report",
    # T9.6 审批闭环（P1-6）：主管裁决员工升级上来的高危操作
    "approve_decision", "decision_list",
})


def annotate_scopes(tools: list[Any]) -> list[Any]:
    """给每个工具打上 `required_scope`。

    **未知名字按 system 处理**（fail-closed：新加的管理工具默认不下放给主管，
    必须显式纳入 `PROJECT_TOOLS` 才能给主管用）。
    """
    for t in tools:
        name = getattr(t, "name", "")
        t.required_scope = "project" if name in PROJECT_TOOLS else "system"
    return tools


def build_management_tools(svc: "AdminServices", scopes: set[str] | None = None) -> list[Any]:
    """按所需 scope 装配管理工具。

    `scopes=None` = 全装（管理员）。`scopes={"project"}` = 只装项目级（主管）。
    """
    tools = annotate_scopes(build_admin_tools(svc))
    if scopes is None:
        return tools
    return [t for t in tools if getattr(t, "required_scope", "system") in scopes]


def build_supervisor_tools(svc: "AdminServices", scopes: set[str] | None = None) -> list[Any]:
    """主管的管理工具集：**只含 project 级**（不含 config / self_update / 全局记忆）。"""
    return build_management_tools(svc, scopes=scopes or {"project"})
