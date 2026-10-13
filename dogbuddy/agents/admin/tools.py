# DogBuddyCn — admin tool pack (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Management tools for the admin agent (window/workspace/task/context/config/
memory/report). The orchestration services are INJECTED (ports below); L3 never
imports L2 implementations. Each tool = name + JSON schema + execute."""
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
                 self_update: Callable[..., dict] | None = None):
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

    return tools
