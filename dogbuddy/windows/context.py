# DogBuddyCn — context builder (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Assemble the LLM message list for a window:

    [system: role prompt + global memory + window memory + compaction facts]
    + recent messages (with tool-call round trips preserved)

Business rule (compression policy glue) lives here in Domain, not in L1/L2.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ..ports import Message

logger = logging.getLogger("dogbuddy.windows.context")

# keep at most this many compaction blocks in context
MAX_COMPACTION_BLOCKS = 3


def message_to_llm(m: Message) -> dict[str, Any]:
    """Rebuild one OpenAI-style message from persisted state."""
    meta = m.meta or {}
    if m.role == "assistant":
        out: dict[str, Any] = {"role": "assistant", "content": m.content or ""}
        if meta.get("tool_calls"):
            out["tool_calls"] = meta["tool_calls"]
        return out
    if m.role == "tool":
        out = {"role": "tool", "content": m.content or ""}
        if meta.get("tool_call_id"):
            out["tool_call_id"] = meta["tool_call_id"]
        else:
            # legacy/unknown shape: present as user text so the model still sees it
            out = {"role": "user", "content": f"[tool result] {m.content or ''}"}
        return out
    return {"role": "user", "content": m.content or ""}


class ContextBuilder:
    def __init__(self, memory_service: Any = None, skills_root: str = "",
                 resources: Any = None, runtime_snapshot: bool = False,
                 snapshot_store: Any = None):
        self.memory = memory_service
        self.skills_root = str(skills_root or "")
        #: v0.20.0：压缩归档的资源卡片；全文用 read_resource 按需读
        self.resources = resources
        #: v0.29.0：RuntimeSnapshot 只在 hash 变化时追加为 user 消息，
        #: 锚定在当轮 user 消息之前，不再重写历史前的 system。
        self.runtime_snapshot = bool(runtime_snapshot)
        self.snapshot_store = snapshot_store

    def dynamic_parts(self, session: Any) -> dict[str, str]:
        """Named dynamic blocks; hashes are used for cache reset attribution."""
        w = session.window
        out: dict[str, str] = {}
        mem: list[str] = []
        if self.memory is not None:
            g = self.memory.recall("global")
            if g:
                mem.append(f"## Global memory\n{g}")
            wm = self.memory.recall_window(w.id)
            if wm:
                mem.append(f"## Window memory ({w.name})\n{wm}")
        if mem:
            out["memory"] = "\n\n".join(mem)
        if self.skills_root:
            try:
                from ..skills import format_skills, load_skills
                skill_block = format_skills(load_skills(self.skills_root, w.id))
                if skill_block:
                    out["skills"] = skill_block
            except Exception:  # noqa: BLE001 — 坏 skill 不能拖垮整个会话
                logger.warning("skill load failed", exc_info=True)
        facts = self.compaction_block(session)
        if facts:
            out["compaction"] = facts
        if self.resources is not None:
            try:
                cards = self.resources.list_resources(w.id, limit=8)
            except Exception:  # noqa: BLE001 — 卡片坏了不能拖垮会话
                cards = []
            if cards:
                lines = ["## Resource cards (older evidence archived on compaction)",
                         "Full text is NOT inlined. Use `read_resource(resource_id, offset)` "
                         "when you need the original details."]
                for c in cards:
                    lines.append(
                        f"- `{c.get('id')}` [{c.get('kind')}] {c.get('title')} — "
                        f"{(c.get('summary') or '')[:240]} "
                        f"({c.get('token_estimate') or 0} tok)")
                out["resources"] = "\n".join(lines)
        out["workstation"] = (
            f"## Workstation\nwindow_id={w.id} name={w.name} role={w.role} "
            f"workspace={getattr(session, 'effective_workspace', '') or w.workspace_path} "
            f"mode={w.mode}")
        return out

    def dynamic_block(self, session: Any) -> str:
        parts = self.dynamic_parts(session)
        return "\n\n".join(parts[k] for k in
                           ("memory", "skills", "compaction", "resources",
                            "workstation") if parts.get(k))

    def system_block(self, session: Any, include_dynamic: bool = True) -> str:
        parts = [self._role_prompt(session.window)]
        if include_dynamic:
            dynamic = self.dynamic_block(session)
            if dynamic:
                parts.append(dynamic)
        return "\n\n".join(parts)

    @staticmethod
    def _role_prompt(w: Any) -> str:
        from ..agents.core.roles import get_role
        spec = get_role(w.role)
        return spec.system_prompt if spec else f"You are an employee agent ({w.role})."

    @staticmethod
    def compaction_block(session: Any) -> str:
        """Structured-loss-resistant facts from recent compactions."""
        comps = session.compaction_history()[-MAX_COMPACTION_BLOCKS:]
        if not comps:
            return ""
        chunks = []
        for c in comps:
            s = c.summary or {}
            chunks.append(
                f"### Compacted context ({c.created_at})\n"
                f"Summary: {s.get('narrative', '')}\n"
                f"File changes: {json.dumps(s.get('file_changes', []), ensure_ascii=False)}\n"
                f"Decisions: {json.dumps(s.get('decisions', []), ensure_ascii=False)}\n"
                f"Lessons: {json.dumps(s.get('lessons', []), ensure_ascii=False)}"
            )
        return "## Compacted conversation facts (older wording dropped)\n" + "\n\n".join(chunks)

    def build(self, session: Any, recent_limit: int = 400) -> list[dict[str, Any]]:
        recent = list(session.recent(recent_limit))
        stable = self.system_block(session, include_dynamic=not self.runtime_snapshot)
        msgs: list[dict[str, Any]] = [{"role": "system", "content": stable}]
        snapshots: list[dict[str, Any]] = []
        if self.runtime_snapshot:
            dynamic = self.dynamic_block(session)
            if self.snapshot_store is not None and dynamic:
                digest = hashlib.sha256(
                    dynamic.encode("utf-8", "replace")).hexdigest()[:16]
                latest = self.snapshot_store.latest_runtime_snapshot(
                    session.window.id)
                if latest is None or str(latest.get("content_hash") or "") != digest:
                    anchor = 0
                    for m in reversed(recent):
                        if getattr(m, "role", "") == "user":
                            anchor = int(getattr(m, "id", 0) or 0)
                            break
                    self.snapshot_store.create_runtime_snapshot(
                        window_id=session.window.id, content_hash=digest,
                        content=dynamic, before_message_id=anchor)
                snapshots = self.snapshot_store.list_runtime_snapshots(
                    session.window.id)
            elif dynamic:
                # 无 snapshot_store（单测/嵌入）时保留旧布局，生产总是注入 store。
                msgs.append({"role": "system",
                             "content": "## Runtime snapshot (reference; may change)\n"
                                        + dynamic})

        by_anchor: dict[int, list[dict[str, Any]]] = {}
        unmatched: list[dict[str, Any]] = []
        recent_ids = {int(getattr(m, "id", 0) or 0) for m in recent}
        for s in snapshots:
            aid = int(s.get("before_message_id") or 0)
            if aid and aid in recent_ids:
                by_anchor.setdefault(aid, []).append(s)
            else:
                unmatched.append(s)
        if unmatched:
            snap = unmatched[-1]
            msgs.append({"role": "user",
                         "content": "## Runtime snapshot (reference; may change)\n"
                                    + str(snap.get("content") or "")})
        # v0.10.17：**修复 tool / tool_calls 配对**。
        # 历史一旦被裁剪（recent_limit）、压缩或被外部清空，就可能出现
        # "有 tool 结果、没有配对的 assistant.tool_calls"。DeepSeek 会直接 400：
        #   Messages with role 'tool' must be a response to a preceding message
        #   with 'tool_calls' (request_id …)
        # 这是**我们重建请求时的形状问题**，不是用户配置问题 —— 所以在这里补形状：
        #   · 孤儿 tool 结果 → 降级成 user 文本（信息不丢，形状合法）
        #   · assistant 声明了 tool_calls 却没有结果 → 补一条占位 tool 结果
        pending: list[str] = []          # 当前 assistant 声明、还没被满足的 tool_call_id

        def flush_missing() -> None:
            """把没人应答的 tool_calls 补上占位结果（必须在下一个非 tool 消息之前）。"""
            for cid in pending:
                msgs.append({"role": "tool", "tool_call_id": cid,
                             "content": "(tool result not available in this context)"})
            pending.clear()

        for m in recent:
            mid = int(getattr(m, "id", 0) or 0)
            for s in by_anchor.get(mid, []):
                flush_missing()
                msgs.append({"role": "user",
                             "content": "## Runtime snapshot (reference; may change)\n"
                                        + str(s.get("content") or "")})
            item = message_to_llm(m)
            role = item.get("role")
            if role == "assistant":
                flush_missing()
                msgs.append(item)
                pending = [str(tc.get("id") or "") for tc in (item.get("tool_calls") or [])
                           if tc.get("id")]
            elif role == "tool":
                cid = str(item.get("tool_call_id") or "")
                if cid and cid in pending:
                    pending.remove(cid)
                    msgs.append(item)
                else:
                    # 孤儿：转成 user 文本，避免整轮请求被服务商拒绝
                    msgs.append({"role": "user",
                                 "content": f"[tool result] {item.get('content') or ''}"})
            else:
                flush_missing()
                msgs.append(item)
        flush_missing()
        return msgs
