# DogBuddyCn — gateway (IM) notifier channel (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Gateway channel: proactive push through a connected IM adapter.

Uses ``BaseAdapter.push(PushTarget(type, id), text)``. The channel is skipped
at build time when no adapter is available; runtime push failures are logged
as warnings and never propagate.
"""
from __future__ import annotations

import logging
from typing import Any

from ..gateway.adapters.base import PushTarget

logger = logging.getLogger("dogbuddy.notify")


def resolve_push_target(channel: Any, default_target: dict[str, Any] | None = None) -> PushTarget | None:
    """Resolve the push target for a gateway channel.

    Uses ``channel.target_type``/``channel.target_id``; when both are empty,
    falls back to ``default_target`` (``{"type": ..., "id": ...}``). A partial
    channel target fills missing fields from the default. Returns ``None``
    when no usable (type, id) pair can be resolved.
    """
    ttype = str(getattr(channel, "target_type", "") or "").strip()
    tid = str(getattr(channel, "target_id", "") or "").strip()
    if not ttype or not tid:
        d = default_target or {}
        ttype = ttype or str(d.get("type") or "").strip()
        tid = tid or str(d.get("id") or "").strip()
    if not ttype or not tid:
        return None
    return PushTarget(type=ttype, id=tid)


class GatewayNotifier:
    def __init__(self, name: str, adapter: Any, target: PushTarget) -> None:
        self.name = name
        self.adapter = adapter
        self.target = target

    async def send(self, text: str) -> None:
        try:
            await self.adapter.push(self.target, text)
        except Exception:  # noqa: BLE001 — delivery failure must not propagate
            logger.warning("gateway %s push failed (target=%s:%s)",
                           self.name, self.target.type, self.target.id, exc_info=True)
