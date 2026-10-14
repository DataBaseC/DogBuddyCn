# DogBuddyCn — shared entities & ports (interface contracts)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Cross-layer contracts: plain entities and Protocol ports.

Nothing here does IO. L3/L4 depend on these interfaces only; the concrete
implementations (db.py, llm/, tools/) satisfy them and are injected at
assembly time in main.py (dependency-injection rule of ARCHITECTURE.md §3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------- entities

Mode = Literal["BUILD", "PLAN", "AUTO"]
ReportStrategy = Literal["silent", "brief", "detailed"]


@dataclass
class Window:
    """员工工位 = 角色 + 工作区 + 模型 + 持久会话 + 待办 + 定时任务集."""

    id: str
    name: str
    role: str
    workspace_path: str
    model: str
    mode: str = "BUILD"
    status: str = "idle"  # idle | running | paused | rate_limited
    llm_profile: str = ""  # named LLM config set; "" = default
    params: dict[str, Any] = field(default_factory=dict)  # 工位级参数覆盖；空 = 全继承
    group_id: str | None = None  # 群组（共享工作频道）id
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Message:
    id: int
    window_id: str
    role: str  # user | assistant | tool | system
    content: str
    tokens: int
    created_at: str
    meta: dict[str, Any] = field(default_factory=dict)
    group_id: str = ""  # 共享工作频道（T3.1）：非空 = 频道可见消息


@dataclass
class Compaction:
    """抗损压缩记录：summary 含对话摘要 + 结构化事实."""

    id: int
    window_id: str
    summary: dict[str, Any]  # {narrative, file_changes, todos, decisions, lessons}
    replaced_count: int
    created_at: str


@dataclass
class Todo:
    id: int
    window_id: str
    content: str
    done: bool
    sort: int


@dataclass
class Job:
    """定时任务（cron）。schedule 是 Hermes 式解析结果 dict."""

    id: str
    window_id: str
    name: str
    schedule_type: str  # once | interval | cron
    schedule_raw: str
    schedule: dict[str, Any]
    prompt: str
    report_strategy: str = "brief"
    kind: str = "prompt"  # prompt | script | <registered runner kind>
    report_channel: str = ""  # notifier channel name override; "" = all configured channels
    llm_profile: str = ""  # profile override; "" = inherit from the window
    enabled: bool = True
    last_run_at: str | None = None
    next_run_at: str | None = None
    fail_count: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class JobRun:
    id: int
    job_id: str
    window_id: str
    started_at: str
    finished_at: str | None
    status: str  # running | ok | failed | rate_limited
    summary: str
    log_path: str


@dataclass
class Group:
    """共享工作频道（ROADMAP T3）：一组窗口共享上下文与任务流。"""
    id: str
    name: str
    description: str = ""
    lead_window_id: str = ""  # 组长（汇总者）
    created_at: str = ""
    updated_at: str = ""


@dataclass
class GroupMember:
    group_id: str
    window_id: str
    role: str = "member"  # member | lead
    joined_at: str = ""


@dataclass
class PendingDecision:
    """An open question from an agent awaiting the owner (ROADMAP T2.3)."""
    id: int
    window_id: str
    question: str
    options: list[Any] = field(default_factory=list)
    answer: str = ""
    status: str = "pending"  # pending | answered | timeout | cancelled
    source: str = "ask_user"
    created_at: str = ""
    answered_at: str | None = None
    expires_at: str | None = None


@dataclass
class AuditEntry:
    id: int
    actor: str
    action: str
    detail: str
    created_at: str


@dataclass
class WhitelistEntry:
    """IM whitelist entry — role opens the door to non-unique admins / tiers later."""

    qq_id: str
    name: str = ""
    role: str = "admin"  # owner | admin | viewer


# ---------------------------------------------------------------- ports


@runtime_checkable
class WindowRepo(Protocol):
    def insert_window(self, w: Window) -> Window: ...
    def get_window(self, window_id: str) -> Window | None: ...
    def get_window_by_name(self, name: str) -> Window | None: ...
    def list_windows(self) -> list[Window]: ...
    def update_window(self, window_id: str, **fields: Any) -> Window | None: ...
    def delete_window(self, window_id: str) -> bool: ...


@runtime_checkable
class MessageRepo(Protocol):
    def add_message(self, window_id: str, role: str, content: str, tokens: int = 0) -> Message: ...
    def list_messages(self, window_id: str, limit: int = 200) -> list[Message]: ...
    def sum_tokens(self, window_id: str) -> int: ...
    def delete_messages(self, window_id: str, before_id: int) -> int: ...


@runtime_checkable
class TodoRepo(Protocol):
    def add_todo(self, window_id: str, content: str, sort: int = 0) -> Todo: ...
    def list_todos(self, window_id: str) -> list[Todo]: ...
    def set_todo_done(self, todo_id: int, done: bool) -> bool: ...
    def delete_todo(self, todo_id: int) -> bool: ...


@runtime_checkable
class JobRepo(Protocol):
    def upsert_job(self, job: Job) -> Job: ...
    def get_job(self, job_id: str) -> Job | None: ...
    def list_jobs(self, window_id: str | None = None) -> list[Job]: ...
    def delete_job(self, job_id: str) -> bool: ...
    def due_jobs(self, now_iso: str) -> list[Job]: ...
    def start_run(self, job_id: str, window_id: str) -> JobRun: ...
    def finish_run(self, run_id: int, status: str, summary: str = "") -> None: ...


@runtime_checkable
class ConfigRepo(Protocol):
    def get_config(self, key: str, default: Any = None) -> Any: ...
    def set_config(self, key: str, value: Any) -> None: ...


@runtime_checkable
class AuditSink(Protocol):
    def audit(self, actor: str, action: str, detail: str = "") -> None: ...


@runtime_checkable
class LLMProvider(Protocol):
    """L4 llm abstraction (see llm/provider.py for the AstrBot-derived base)."""

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Any: ...
