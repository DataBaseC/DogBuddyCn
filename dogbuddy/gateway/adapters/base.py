# DogBuddyCn — IM platform adapter abstraction (L1 Interface)
# Derived from AstrBot core/platform/platform.py (https://github.com/AstrBotDevs/AstrBot)
# Copyright (c) AstrBotDevs and contributors — AGPL-3.0
# Modifications (2026-10-12, DogBuddyCn contributors):
#   - trimmed to recv/send/push + MessageEvent; metrics/status plumbing dropped
#   - method names aligned with ARCHITECTURE.md L1 contract (BaseAdapter)
"""Unified IM adapter contract. V1 implements OneBot v11; official QQ / WeChat
adapters register here later without touching L2+."""
from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MessageEvent:
    platform: str          # "onebot_v11" | "web" | ...
    session_id: str        # "private:<qq>" | "group:<gid>" | "web:<uid>"
    user_id: str
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    group_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PushTarget:
    type: str   # "private" | "group"
    id: str

    @classmethod
    def from_session(cls, session_id: str) -> "PushTarget":
        kind, _, ident = session_id.partition(":")
        return cls(type=kind if kind in ("private", "group") else "private", id=ident)


class BaseAdapter(abc.ABC):
    """One connected IM platform."""

    def __init__(self) -> None:
        self.id = uuid.uuid4().hex[:8]

    @abc.abstractmethod
    async def recv(self) -> MessageEvent:
        """Next inbound message event (blocks)."""
        raise NotImplementedError

    @abc.abstractmethod
    async def send(self, target: PushTarget, content: str) -> None:
        """Reply within the conversation the event came from."""
        raise NotImplementedError

    @abc.abstractmethod
    async def push(self, target: PushTarget, content: str) -> None:
        """Proactive push to a configured target (group/private)."""
        raise NotImplementedError

    async def run(self) -> None:
        """Optional long-running task (WS server etc.)."""

    async def terminate(self) -> None:
        """Optional cleanup."""
