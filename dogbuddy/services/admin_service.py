# DogBuddyCn — admin conversation service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""AdminService: user message → admin AgentLoop → reply.

The admin agent is itself a persistent session (window_id="admin") with the
management tool pack. Users never talk to employees directly (M-B rules).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from ..agents.admin.tools import AdminServices, build_admin_tools
from ..agents.core.loop import AgentLoop
from ..agents.core.roles import get_role
from ..agents.core.scope import Scope
from ..db import _now_iso
from ..ports import Window
from ..tools.registry import ToolContext, ToolRegistry
from ..windows.context import ContextBuilder
from ..windows.session import WindowSession

logger = logging.getLogger("dogbuddy.services.admin")

ADMIN_WINDOW_ID = "admin"


class AdminService:
    def __init__(
        self,
        db: Any,
        llm_factory: Callable[[], Any],
        windows: Any,          # WindowManager
        jobs: Any,             # JobManager
        sessions: Any,         # SessionManager
        workspaces: Any,       # WorkspaceManager
        memory: Any,           # MemoryService
        config: Any,           # ConfigService
        models: Any = None,    # ModelService（模型目录；管理员只能选不能造）
        groups: Any = None,    # GroupManager（群组/共享频道，v0.10.20）
        # v0.10.30：AegisServerPanel 客户端 —— `server_exec` 靠它执行宿主机命令。
        # 以前这里没有这个参数，于是管理员的 ToolContext 里 `panel=None`，
        # 工具一调就报 "panel client not configured"（owner 实测：让管理员整理
        # 线上工作区，它如实回报"工具存在但面板客户端未配置"）。
        panel: Any = None,
        # v0.10.37（P0）：运行台账 —— 管理员的 group_run/window_dispatch/run_list/
        # run_cancel/run_status 与 progress_report 都靠它（**"此刻谁在跑什么"的唯一真相**）
        runs: Any = None,
        bus: Any = None,
        # v0.10.50（方案B）：server_exec 的人工确认通道 —— 落 pending_decision，
        # 控制台「待决」页批准后重放。没有它时 server_exec 直接拒绝（fail-closed）。
        decisions: Any = None,
        # v0.11.0：成本账本（管理员对话的每一次 LLM 调用都要记账）
        cost: Any = None,
        max_iterations: int = 20,
        self_status: Callable[[bool], dict] | None = None,
        self_update: Callable[..., dict] | None = None,
    ):
        self.db = db
        self.llm_factory = llm_factory
        self.windows = windows
        self.jobs = jobs
        self.sessions = sessions
        self.workspaces = workspaces
        self.memory = memory
        self.config = config
        self.models = models
        self.groups = groups
        self.panel = panel
        self.runs = runs
        self.bus = bus
        self.decisions = decisions
        self.cost = cost
        self.max_iterations = max_iterations
        self.self_status = self_status
        self.self_update = self_update

        self.builder = ContextBuilder(memory_service=memory)
        self._sessions: dict[str, WindowSession] = {}
        # one turn per admin conversation at a time: the console (HTTP), the QQ
        # gateway and internal notifications can all target the same session, and
        # two AgentLoops sharing one WindowSession would interleave badly.
        self._locks: dict[str, asyncio.Lock] = {}
        self.session = self.session_for("default")  # legacy alias for the default admin

        svc = AdminServices(windows=windows, jobs=jobs, sessions=sessions,
                            workspaces=workspaces, memory=memory, config=config,
                            progress=self._progress_dict,
                            runs=self.runs,
                            self_status=self_status, self_update=self_update,
                            # P1-9：agent 路径改 profile 也要落盘
                            persist=getattr(config, "persist", None),
                            # v0.10.17：管理员只能从模型目录里"选"模型
                            models=models,
                            # v0.10.20：群组工具（圆桌测试暴露管理员没有 group 工具）
                            groups=groups)
        self.registry = ToolRegistry(gate=None, audit=db)
        self.registry.gate = _AdminGate()
        for tool in build_admin_tools(svc):
            self.registry.register(tool)
        # T9.3：主管工位要用的管理工具（只含 project 级）。存下 svc 供装配取用。
        self._svc = svc

    def supervisor_tools(self) -> list:
        """主管工具集：**只含 project 级管理工具**（不含 config/self_update/全局记忆）。"""
        from ..agents.management.tools import build_supervisor_tools
        return build_supervisor_tools(self._svc)

    def tools_for(self, window: Any) -> list:
        """按工位角色给管理工具（审查报告 P1-3）。

        * `supervisor` → 只给 project 级（系统级永不下放）
        * `admin` → 全给（它是管理员；空壳工位比多给工具更糟）
        * 其他角色 → 空（普通员工不装管理工具）
        """
        role = (getattr(window, "role", "") or "")
        if role == "supervisor":
            return self.supervisor_tools()
        if role == "admin":
            from ..agents.management.tools import build_admin_tools
            return build_admin_tools(self._svc)
        return []

    def set_supervision(self, supervision: Any) -> None:
        """T9.4（审查报告 P1-1）：把指挥链交给管理侧工具。

        `supervisor_command`（管理员→主管）需要它；不注入时该工具会明确报"指挥链
        服务不可用"，而不是静默什么都不做。
        """
        self.supervision = supervision
        if getattr(self, "_svc", None) is not None:
            self._svc.supervision = supervision

    # ------------------------------------------------------------- sessions
    #: 默认对话的 id —— 老部署（迁移前只有一条会话）沿用同一个键，升级后历史不丢
    DEFAULT_CONVERSATION = "default"

    def session_for(self, sender: str) -> WindowSession:
        """按**对话 id** 取（或惰性建）一个持久会话，消息落在合成窗口 `admin:<id>`。

        v2 口径：**管理员只有一个**（与用户对接的唯一窗口），但对话可以有多个——
        每个对话是一份独立的上下文，互不串味。
        """
        key = (sender or self.DEFAULT_CONVERSATION).strip() or self.DEFAULT_CONVERSATION
        sess = self._sessions.get(key)
        if sess is None:
            window = Window(id=f"admin:{key}", name=f"admin:{key}", role="admin",
                            workspace_path="", model="", mode="AUTO", status="idle")
            # v0.10.36：管理员会话也按消息推送（工具调用/系统提示不再"要刷新才出现"）
            sess = WindowSession(window, self.db, self.db, self.db,
                                 on_message=lambda m, _id=window.id: self._emit_message(_id, m))
            self._sessions[key] = sess
        return sess

    def _emit_message(self, window_id: str, m: Any) -> None:
        """管理员的每条新消息推给控制台（形状与工位一致，前端同一套处理）。"""
        if not self.bus:
            return
        meta = dict(getattr(m, "meta", None) or {})
        try:
            self.bus.emit(Event(type="window.message", payload={
                "window_id": window_id,
                "message": {
                    "id": getattr(m, "id", 0),
                    "role": getattr(m, "role", "") or "",
                    "content": getattr(m, "content", "") or "",
                    "created_at": getattr(m, "created_at", "") or "",
                    "tokens": int(getattr(m, "tokens", 0) or 0),
                    "tool_name": meta.get("tool_name", ""),
                    "tool_call_id": meta.get("tool_call_id", ""),
                    "group_id": getattr(m, "group_id", "") or "",
                    "meta": meta,
                },
            }))
        except Exception:  # noqa: BLE001 — 推送失败不能影响对话
            logger.debug("admin message emit failed", exc_info=True)

    def active_admins(self) -> list[str]:
        """（兼容旧接口）当前已加载的会话键。v2 只有一个管理员，这里是"已打开的对话"。"""
        return sorted(self._sessions)

    # ------------------------------------------------------- conversations (v9)
    def ensure_default_conversation(self) -> dict[str, Any]:
        """保证至少有一个对话存在；**并接管老版本留下的会话历史**。

        升级自愈有两个层次，缺一不可（第一版只做了其中一个，线上实测漏了）：
          1. 对话表是空的 → 直接把老的 `admin:<key>` 会话接管成对话
          2. 对话表已有内容（比如上一版先建了空的 `default`）→ **补登记**缺失的老会话，
             并把那个空占位删掉，否则用户看不到自己 28 条历史，只看到一个空对话
        """
        convs = self.db.list_conversations(include_archived=True)
        if convs:
            self.sync_legacy_conversations()
            convs = self.db.list_conversations(include_archived=True)
            return convs[0] if convs else self.db.insert_conversation(
                self.DEFAULT_CONVERSATION, "默认对话")
        try:
            legacy = self.db.legacy_admin_windows()
        except Exception:  # noqa: BLE001 — 接管失败也要能给出默认对话
            legacy = []
        for window_id, count in legacy:
            if window_id == "admin:system" or count <= 0:
                continue          # 系统通知会话不是"用户的对话"
            conv_id = window_id.split(":", 1)[1] if window_id.startswith("admin:") else window_id
            if not conv_id:
                continue
            title = "默认对话" if conv_id in ("console", "default") else conv_id
            conv = self.db.insert_conversation(conv_id, title)
            logger.info("接管老会话为对话：%s（%d 条消息）", conv_id, count)
            return conv
        return self.db.insert_conversation(self.DEFAULT_CONVERSATION, "默认对话")

    def sync_legacy_conversations(self) -> int:
        """把老版本遗留在 `messages` 里的管理员会话补登记成对话（**幂等**）。

        只在"该会话还没有对话行"时补；补进来之后如果发现有个**空占位对话**
        （自动命名、0 条消息），就把它删掉——否则列表里会多一个空对话，
        用户会以为自己翻到了错的地方。
        返回补登记的条数。
        """
        try:
            legacy = self.db.legacy_admin_windows()
        except Exception:  # noqa: BLE001
            return 0
        known = {c["id"] for c in self.db.list_conversations(include_archived=True)}
        imported: list[str] = []
        for window_id, count in legacy:
            if count <= 0 or window_id == "admin:system":
                continue
            conv_id = window_id.split(":", 1)[1] if window_id.startswith("admin:") else window_id
            if not conv_id or conv_id in known:
                continue
            title = "默认对话" if conv_id in ("console", "default") else conv_id
            self.db.insert_conversation(conv_id, title)
            imported.append(conv_id)
            logger.info("补登记老会话为对话：%s（%d 条消息）", conv_id, count)
        if not imported:
            return 0
        # 清掉空的自动占位对话（有消息的、或用户手改过名的都不动）
        for c in self.db.list_conversations(include_archived=True):
            cid = c["id"]
            if cid in imported:
                continue
            title = (c.get("title") or "").strip()
            if title not in ("默认对话", "") and not title.startswith("对话 "):
                continue
            try:
                if self.db.list_messages(f"admin:{cid}", 1):
                    continue
            except Exception:  # noqa: BLE001 — 判不出来就保守地留着
                continue
            self.db.delete_conversation(cid, drop_messages=False)
            logger.info("清掉空的占位对话：%s", cid)
        # 如果只剩下被清空的 default 而一个都不剩，补一个回来
        if not self.db.list_conversations(include_archived=True):
            self.db.insert_conversation(self.DEFAULT_CONVERSATION, "默认对话")
        return len(imported)

    def list_conversations(self, include_archived: bool = False) -> list[dict[str, Any]]:
        self.ensure_default_conversation()
        out = []
        for c in self.db.list_conversations(include_archived=include_archived):
            cid = c["id"]
            try:
                msgs = self.db.list_messages(f"admin:{cid}", 200)
                count = len(msgs)
                last = next((m.content for m in reversed(msgs) if m.role == "user"), "")
            except Exception:  # noqa: BLE001 — 统计失败不影响列出
                count, last = 0, ""
            out.append({**c, "message_count": count,
                        "preview": (last or "")[:80]})
        return out

    def create_conversation(self, title: str = "") -> dict[str, Any]:
        """新建对话（用户点「新建对话」）。标题留空则按"对话 N"自动命名。"""
        self.ensure_default_conversation()
        existing = self.db.list_conversations(include_archived=True)
        cid = f"c-{uuid.uuid4().hex[:10]}"
        if not (title or "").strip():
            used = {c.get("title", "") for c in existing}
            n = 1
            while f"对话 {n}" in used:
                n += 1
            title = f"对话 {n}"
        conv = self.db.insert_conversation(cid, title.strip())
        self.db.audit("web", "conversation:create", f"{cid} {title}")
        return conv

    def rename_conversation(self, conv_id: str, title: str) -> dict[str, Any]:
        if self.db.get_conversation(conv_id) is None:
            raise KeyError(f"对话不存在：{conv_id}")
        conv = self.db.update_conversation(conv_id, title=(title or "").strip())
        self.db.audit("web", "conversation:rename", f"{conv_id} -> {title}")
        return conv or {}

    def delete_conversation(self, conv_id: str) -> bool:
        conv = self.db.get_conversation(conv_id)
        if conv is None:
            return False
        self._sessions.pop(conv_id, None)          # 丢掉内存里的会话
        ok = self.db.delete_conversation(conv_id)
        self.ensure_default_conversation()          # 删光了也要留一个可用的
        self.db.audit("web", "conversation:delete", conv_id)
        return ok

    def _touch_conversation(self, conv_id: str, user_text: str) -> None:
        """更新"最近活动时间"；**首条用户消息自动当标题**（用户不用手动命名）。"""
        conv = self.db.get_conversation(conv_id)
        if conv is None:
            conv = self.db.insert_conversation(conv_id, "")
        patch: dict[str, Any] = {"last_message_at": _now_iso()}
        title = (conv.get("title") or "").strip()
        # 只有"还是自动名/空"时才用首条消息命名，不覆盖用户手改的标题
        if user_text and (not title or title == "默认对话" or title.startswith("对话 ")):
            first = " ".join(user_text.split())[:40]
            if first:
                patch["title"] = first
        self.db.update_conversation(conv_id, **patch)

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def history(self, sender: str = "default", limit: int = 100) -> list[dict[str, Any]]:
        """Persisted conversation of one admin session (console chat view)."""
        session = self.session_for(sender)
        return [{"id": m.id, "role": m.role, "content": m.content,
                 "tokens": m.tokens, "created_at": m.created_at,
                 # v0.10.52：折叠卡片要显示**真实工具名**。以前 DTO 没这一列，
                 # 前端只能写死 "tool"，展开后看不出这是哪次调用。
                 "tool_name": str((getattr(m, "meta", None) or {}).get("tool_name", "") or "")}
                for m in session.recent(limit)]

    def reset(self, sender: str = "default") -> int:
        """Drop the conversation history of one admin session (keeps memory/todos)."""
        session = self.session_for(sender)
        msgs = session.recent(100000)
        if not msgs:
            return 0
        return session.drop_messages(msgs[-1].id)

    # ----------------------------------------------------------- conversation
    async def handle_user_message(self, text: str, sender: str = "user") -> str:
        async with self._lock_for(sender):
            return await self._run_turn(text, sender)

    async def _run_turn(self, text: str, sender: str) -> str:
        role_spec = get_role("admin")
        session = self.session_for(sender)
        # v9：对话首条消息自动成为标题；同时刷新"最近活动时间"
        if text and sender != "system":
            try:
                self._touch_conversation(sender, text)
            except Exception:  # noqa: BLE001 — 元数据失败不能挡住对话本身
                logger.warning("conversation touch failed", exc_info=True)
        # T9.1：管理员工位是 system 作用域。`_AdminGate` 本来就不做作用域裁决
        # （全权），但作用域必须在 ctx 里声明——否则一旦有人把 gate 换成
        # PermissionGate，这条路径上的所有管理工具都会被 fail-closed 拒掉
        # （审查报告 P1-5：作用域只在 window_manager 一条路径注入）。
        # v0.11.0：先把 LLM 客户端建出来 —— 成本账要拿它的 `model` 名去查单价
        # （AdminService.config 是 ConfigService，不是 AppConfig，取不到 llm.model；
        #  0.11.0 首次上线实测就是这里没取到，账记上了 tokens 但金额是 0）
        llm_client = self.llm_factory()
        ctx = ToolContext(window_id=session.window.id, workspace=".", mode="AUTO",
                          actor=f"admin:{sender}", todo_repo=self.db, db=self.db,
                          # v0.10.30：server_exec 要用它（否则管理员永远拿不到面板能力）
                          panel=self.panel,
                          llm_factory=self.llm_factory,
                          scope=Scope(level="system", actor=f"admin:{sender}",
                                      source="role:admin"),
                          memory_service=self.memory,
                          # v0.10.50（方案B）：宿主机命令的确认通道
                          ask_user_fn=self._confirm_asker(sender),
                          extra={"manual": True, "conversation": sender,
                                 "decisions": self.decisions,
                                 # v0.10.51：工具调用实时回执 → SSE admin.tool
                                 "on_tool": self._tool_emitter(sender),
                                 # v0.11.0：成本账本（每次 LLM 调用记一笔；
                                 # 模型名由 loop 回调时把 llm 传进来，见 _usage_hook）
                                 "on_usage": self._usage_hook(sender)})
        loop = AgentLoop(
            llm=llm_client, registry=self.registry, session=session,
            builder=self.builder, tool_names=role_spec.tool_names if role_spec else [],
            max_iterations=self.max_iterations)
        # 多对话时不要把发送者前缀塞进提示词（v2 只有一个管理员，前缀只会干扰）
        prompt = text if sender == self.DEFAULT_CONVERSATION else (text or "")

        # v0.10.13 打字机：管理员以前**没有** on_delta（只有工位有 window.stream），
        # 所以控制台上永远是"转圈→整段蹦出来"。这里按对话发 admin.stream 事件；
        # 60ms 合并一次，避免每个 token 一帧把 SSE 队列（500）刷爆。
        on_delta = self._delta_emitter(sender)
        try:
            result = await loop.run(prompt, ctx, on_delta=on_delta)
        finally:
            # 收尾：把不足 60ms 的尾巴也发出去，否则界面上会少最后几个字
            if on_delta is not None:
                on_delta.flush()
        return result.content or "(no reply)"

    def _usage_hook(self, sender: str, model: str = "", provider: str = ""):
        """成本记账钩子（v0.11.0）：管理员对话每次 LLM 调用记一笔。

        `model`/`provider` 由调用方从**刚建出来的 LLM 客户端**上取（客户端才知道自己
        跑的是哪个模型；`self.config` 是 ConfigService，没有 llm 段）。
        幂等键用"对话 + 轮次"：同一轮被重放（重试/断线重连）时不会重复计费。
        """
        cost = getattr(self, "cost", None)
        if cost is None:
            return None
        default_model = str(model or "")
        provider = str(provider or "")
        seq = {"n": 0}

        def hook(resp: Any, llm: Any = None) -> None:
            seq["n"] += 1
            usage = getattr(resp, "usage", None) or {}
            # 模型名优先问**客户端**（`Provider.model_name`）—— 0.11.0 首跑就是这里
            # 拿不到模型、金额记成 0（tokens 有、钱是 0），实测抓到后补的。
            model = (str(getattr(llm, "model_name", "") or "")
                     or str(getattr(resp, "model", "") or "") or default_model)
            cost.record(
                window_id=f"admin:{sender}", kind="llm",
                provider=str(getattr(resp, "provider", "") or provider),
                model=model,
                in_tokens=int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                out_tokens=int(usage.get("completion_tokens")
                               or usage.get("output_tokens") or 0),
                cache_hit_tokens=int(usage.get("cache_read_input_tokens")
                                     or usage.get("prompt_cache_hit_tokens") or 0),
                idem_key=f"admin:{sender}:{seq['n']}",
                meta={"phase": "admin_chat"},
            )

        return hook

    def _tool_emitter(self, sender: str):
        """把"刚调用了哪个工具、成功没有"实时推给控制台（v0.10.51）。

        owner 报："管理员调用工具不是实时显示"——以前工具卡片只在整轮结束后随
        `/messages` 一起返回，一轮几十秒里界面像卡死。现在每次工具返回就发一帧
        `admin.tool`（带参数摘要与输出前 1200 字），前端边跑边折成一张卡片。

        没有 bus 时返回 None（loop 里 `callable(hook)` 会跳过）。
        """
        if self.bus is None:
            return None
        from ..events.bus import ADMIN_TOOL, Event

        def hook(name: str, args: dict, result: Any) -> None:
            try:
                self.bus.emit(Event(type=ADMIN_TOOL, payload={
                    "conversation": sender,
                    "name": str(name),
                    "args": str(json.dumps(args, ensure_ascii=False))[:600],
                    "ok": bool(getattr(result, "ok", False)),
                    "pending": bool(getattr(result, "needs_confirm", False)),
                    "output": str(getattr(result, "output", "") or "")[:1200],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }))
            except Exception:  # noqa: BLE001 — 回执坏了不能影响工具结果
                logger.debug("admin tool emit failed", exc_info=True)

        return hook

    def _confirm_asker(self, sender: str):
        """给 `server_exec` 的人工确认通道（v0.10.50 · 方案B）。

        `AgentLoop._approval_flow` 的契约是：`answer = ask(question, options=[...])`，
        answer 可 await 就 await。所以这里返回**同步函数**，结果是个 coroutine。

        确认不弹窗、不阻塞人找按钮：落一条 `pending_decision`（控制台「待决」页
        有红点，批准/拒绝两个按钮），然后在这里等裁决。**没有 DecisionService
        就判拒绝**——宁可让管理员说"这台机器上执行不了"，也不能静默放行 root 命令。
        """
        def ask(question: str, options: list | None = None):
            return self._await_decision(sender, question, list(options or ["批准", "拒绝"]))
        return ask

    async def _await_decision(self, sender: str, question: str,
                             options: list[str]) -> str:
        decisions = getattr(self, "decisions", None)
        if decisions is None:
            return "拒绝：没有确认通道（fail-closed，server_exec 不执行）"
        try:
            pending = decisions.ask(f"admin:{sender}", question, options=options,
                                    source="server_exec", supersede=False)
        except Exception as e:  # noqa: BLE001 — 落不了库就拒绝
            logger.warning("server_exec confirmation failed to enqueue: %s", e)
            return f"拒绝：确认请求入队失败（{e}）"
        try:
            answer = await asyncio.wait_for(decisions.wait(pending.id), timeout=600)
        except asyncio.TimeoutError:
            return "拒绝：10 分钟内没有确认（已超时，命令未执行）"
        except Exception as e:  # noqa: BLE001
            logger.warning("server_exec confirmation wait failed: %s", e)
            return f"拒绝：等待确认失败（{e}）"
        return str(answer or "")

    def _delta_emitter(self, sender: str):
        """把 LLM 增量合并成 admin.stream 事件（没有 bus 时返回 None）。"""
        if self.bus is None:
            return None
        import time

        from ..events.bus import ADMIN_STREAM, Event
        buf: list[str] = []
        last = [0.0]

        def flush() -> None:
            if not buf:
                return
            text = "".join(buf)
            buf.clear()
            last[0] = time.monotonic()
            try:
                self.bus.emit(Event(type=ADMIN_STREAM,
                                    payload={"conversation": sender, "delta": text}))
            except Exception:  # noqa: BLE001 — 打字机坏了也不能影响对话本身
                logger.debug("admin stream emit failed", exc_info=True)

        def on_delta(delta: str) -> None:
            if not delta:
                return
            buf.append(str(delta))
            if time.monotonic() - last[0] >= 0.06:
                flush()

        on_delta.flush = flush  # type: ignore[attr-defined]  # 收尾时把尾巴也发出去
        return on_delta

    async def notify_user(self, text: str, conversation: str = "") -> dict[str, Any]:
        """把一条汇报**写进用户的对话流**（控制台里看得见），并推一条 SSE。

        为什么不是走 `notify()`：`notify()` 是"让管理员自己读一条系统事件"，它的回复
        落在 `admin:system` 会话里，用户在控制台看不到。汇报要出现在**他正在看的对话**里。

        目标对话：显式指定 > 最近有活动的那个（`conversations.last_message_at`）> default。
        """
        text = str(text or "").strip()
        if not text:
            return {}
        conv = conversation
        if not conv:
            try:
                rows = [c for c in self.db.list_conversations() if c.get("id") != "system"]
                conv = str(rows[0]["id"]) if rows else self.DEFAULT_CONVERSATION
            except Exception:  # noqa: BLE001 — 取不到就用默认对话
                logger.warning("pick report conversation failed", exc_info=True)
                conv = self.DEFAULT_CONVERSATION
        session = self.session_for(conv)
        msg = session.append("assistant", text, meta={"notice": True, "source": "report"})
        payload = {"conversation": conv, "text": text, "message_id": getattr(msg, "id", None),
                   "created_at": getattr(msg, "created_at", "")}
        if self.bus is not None:
            try:
                from ..events.bus import ADMIN_NOTICE, Event
                self.bus.emit(Event(type=ADMIN_NOTICE, payload=payload))
            except Exception:  # noqa: BLE001 — 推送失败不影响落库
                logger.debug("admin.notice emit failed", exc_info=True)
        self.db.audit("system", "admin:report_to_user", f"conversation={conv} len={len(text)}")
        return payload

    async def notify(self, text: str) -> str:
        """Push an internal notification into the system admin session and get its reply."""
        session = self.session_for("system")
        session.append("user", f"[system event] {text}")
        return await self.handle_user_message("", sender="system")

    def _progress_dict(self) -> dict[str, Any]:
        lines = []
        for w in self.windows.list():
            st = self.windows.status(w.id)
            lines.append(f"- {w.name}({w.id}) [{w.role}] status={st['status']} "
                         f"mode={w.mode} task={st['current_task']!r} tokens={st['tokens']}")
        job_lines = []
        for j in (self.jobs.list() if getattr(self, "jobs", None) is not None else []):
            job_lines.append(f"- {j.name}({j.id}) win={j.window_id} sched={j.schedule_raw} "
                             f"enabled={j.enabled} next={j.next_run_at} fails={j.fail_count}")
        runs = (self.jobs.runs(limit=5)
                if getattr(self, "jobs", None) is not None else [])
        # v0.10.19：`summary` 可能是 NULL（任务是"没事可报"或被人为清过），
        # 直接 `r['summary'][:80]` 会 TypeError: 'NoneType' object is not subscriptable
        # —— 表现就是"主管查全局进度"这个核心工具整个挂掉（圆桌测试里实测到）。
        run_lines = [f"- {r.get('status')} {r.get('started_at')} "
                     f"{str(r.get('summary') or '')[:80]}" for r in runs]
        # v0.10.37（P0 · 逻辑复核 W2）：**正在跑的运行**必须在这里。
        # 以前只读 windows.status + cron 的 job_runs —— 群组运行中途问"进度怎么样"，
        # 管理员会回答"没有任务在跑"（不是不详细，是**错的**）。
        live_lines: list[str] = []
        run_summary: dict[str, Any] = {}
        if getattr(self, "runs", None) is not None:
            try:
                for r in self.runs.active():
                    live_lines.append(
                        f"- {r.id} [{r.kind}] {r.window_name or r.group_name or '-'} "
                        f"phase={r.phase or '-'} status={r.status} "
                        f"限速暂停{r.pause_count}次/共{r.paused_seconds:.0f}s "
                        f"started={r.started_at}")
                run_summary = self.runs.summary()
            except Exception:  # noqa: BLE001
                logger.debug("runs lookup failed in progress_report", exc_info=True)
        text = ("## 正在跑（runs）\n" + ("\n".join(live_lines) or "(无)") +
                "\n## Windows\n" + ("\n".join(lines) or "(none)") +
                "\n## Jobs\n" + ("\n".join(job_lines) or "(none)") +
                "\n## Recent runs\n" + ("\n".join(run_lines) or "(none)"))
        # v0.10.38：**人工派单/群组运行的近期台账**。上面 "Recent runs" 只是 cron 的
        # job_runs；手动派的活、群组接力全在 runs 台账里——owner 问"刚才让员工干的
        # 活干完了吗"，以前管理员只能看到定时任务那一本账，答不上来。
        reg_lines: list[str] = []
        if getattr(self, "runs", None) is not None:
            try:
                # 注意：runs.recent() 返回 **dict**（活跃 + 历史合并）
                for r in self.runs.recent(limit=8):
                    reg_lines.append(
                        f"- {r.get('id')} [{r.get('kind')}] "
                        f"{r.get('window_name') or r.get('group_name') or '-'} "
                        f"{r.get('status')} {str(r.get('started_at'))[:19]} "
                        f"{(r.get('summary') or '')[:80]}")
            except Exception:  # noqa: BLE001 — 台账读失败不该炸掉整个汇报
                logger.debug("runs recent lookup failed in progress_report",
                             exc_info=True)
        text += ("\n## Recent runs (manual/group)\n"
                 + ("\n".join(reg_lines) or "(none)"))
        return {"text": text, "windows": len(lines), "jobs": len(job_lines),
                "live_runs": len(live_lines), "recent_runs": len(reg_lines),
                "run_summary": run_summary}


class _AdminGate:
    """管理员控制台全权放行——**作用域对它不适用**。

    管理员是 system 作用域（覆盖全部项目），所以任何 `required_scope` 都不会
    让它越权；这里显式接受该参数并放行，是**有意为之**，不是漏洞
    （审查报告第五节第 6 条同样如此判定）。

    但参数必须存在：`required_scope` 是 `PermissionGate` 端口契约的一部分，
    初版只收 4 参 → 注册表每次都走 `except TypeError` 回退（审查报告实测 3/3）。
    回退分支现已删除，实现方必须满足契约。
    """

    def check(self, tool_name: str, permission: str, args: dict[str, Any],
              ctx: ToolContext, required_scope: str = ""):
        from ..tools.registry import GateDecision
        return GateDecision(True)

