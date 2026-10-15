# DogBuddyCn — notifier channel plugin contract & factory (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Multi-channel report delivery contract (L4 Capability).

`Notifier` is the outbound channel protocol consumed by L2 services
(``report_service``). Concrete channels: console (log), webhook (HTTP POST),
gateway (IM adapter push). `build_notifiers` turns ``config.NotifyConfig``
channels into live channel instances; invalid or under-provisioned channels
are skipped with a warning so a bad config never blocks startup.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Protocol

from .chat import ChatNotifier
from .console import ConsoleNotifier
from .gateway import GatewayNotifier, resolve_push_target
from .webhook import WebhookNotifier

logger = logging.getLogger("dogbuddy.notify")

__all__ = [
    "Notifier",
    "build_notifiers",
    "ConsoleNotifier",
    "ChatNotifier",
    "WebhookNotifier",
    "GatewayNotifier",
]


class Notifier(Protocol):
    """One outbound report channel. ``send`` must never propagate failures."""

    name: str

    async def send(self, text: str) -> None: ...


def _dedupe_name(base: str, taken: set[str]) -> str:
    """`name`, then `name-2`, `name-3`, ... for same-name channels."""
    name = base
    n = 2
    while name in taken:
        name = f"{base}-{n}"
        n += 1
    taken.add(name)
    return name


def build_notifiers(
    notify_cfg: Any,
    gateway_adapter: Any = None,
    gateway_default_target: dict[str, Any] | None = None,
    chat_sink: Callable[[str], Any] | None = None,
) -> list[Notifier]:
    """Build the enabled channels from ``NotifyConfig.channels``.

    - channels empty (or cfg missing) → a single ``console`` channel.
    - ``chat_sink`` 非空时，console 渠道用 `ChatNotifier`（写进用户对话流，控制台可见）；
      为空时退回只写日志的 `ConsoleNotifier`（测试/嵌入式场景）。
    - ``channel.name`` defaults to its ``type``; duplicate names get a numeric
      suffix (``webhook``, ``webhook-2``, ...) so per-job routing stays unique.
    - ``gateway`` channels need ``gateway_adapter`` (a BaseAdapter); their
      push target comes from ``channel.target_type``/``channel.target_id``,
      or ``gateway_default_target`` (``{"type": ..., "id": ...}``) when both
      channel fields are empty.
    - invalid / under-provisioned / disabled channels are skipped with a
      ``logger.warning`` — never raise.
    """
    def _console(name: str):
        return ChatNotifier(chat_sink, name=name) if chat_sink is not None \
            else ConsoleNotifier(name=name)

    channels = list(getattr(notify_cfg, "channels", None) or [])
    if not channels:
        return [_console("console")]

    notifiers: list[Notifier] = []
    taken: set[str] = set()
    for ch in channels:
        try:
            if not getattr(ch, "enabled", True):
                logger.info("notify channel %r disabled; skipped",
                            getattr(ch, "name", "") or getattr(ch, "type", ""))
                continue
            ctype = str(getattr(ch, "type", "") or "console").strip().lower()
            base_name = str(getattr(ch, "name", "") or "").strip() or ctype

            if ctype == "console":
                notifiers.append(_console(_dedupe_name(base_name, taken)))
            elif ctype == "webhook":
                url = str(getattr(ch, "url", "") or "").strip()
                if not url:
                    logger.warning("notify channel %r skipped: webhook needs url", base_name)
                    continue
                notifiers.append(WebhookNotifier(name=_dedupe_name(base_name, taken), url=url))
            elif ctype == "gateway":
                if gateway_adapter is None:
                    logger.warning("notify channel %r skipped: no gateway adapter", base_name)
                    continue
                target = resolve_push_target(ch, gateway_default_target)
                if target is None:
                    logger.warning("notify channel %r skipped: no push target "
                                   "(set target_type/target_id or gateway.push_target)",
                                   base_name)
                    continue
                notifiers.append(GatewayNotifier(
                    name=_dedupe_name(base_name, taken),
                    adapter=gateway_adapter, target=target))
            else:
                logger.warning("notify channel %r skipped: unknown type %r", base_name, ctype)
        except Exception:  # noqa: BLE001 — one broken channel must not break the rest
            logger.warning("notify channel build failed; skipped", exc_info=True)
    return notifiers
