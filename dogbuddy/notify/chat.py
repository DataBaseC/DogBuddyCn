# DogBuddyCn — console/chat report channel (L4 Capability)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""把汇报写进**用户的对话流**（控制台就能看到）。

为什么要有它：默认的 `ConsoleNotifier` 只写日志——用户不会去看日志，于是
"员工干完活逐级汇报"在他眼里等于没发生。2026-10-15 owner 的原话是：
"两个员工测试完成后，管理员没有向用户做简短汇报。"

渠道名仍然是 `console`（`report_channel` 的按名路由不变），只是把"日志"升级成
"日志 + 控制台对话流"。回调由 L2 注入（`AdminService.notify_user`），
所以这里不依赖任何上层模块。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("dogbuddy.notify")


class ChatNotifier:
    """Console channel that also posts the report into the user's chat."""

    def __init__(self, callback: Callable[[str], Any], name: str = "console") -> None:
        self.name = name
        self._callback = callback

    async def send(self, text: str) -> None:
        # 日志仍然留着（排障用，也是原来 ConsoleNotifier 的行为）
        try:
            logger.info("report: %s", str(text)[:300])
        except Exception:  # noqa: BLE001
            pass
        if not text:
            return
        try:
            out = self._callback(text)
            if hasattr(out, "__await__"):
                await out
        except Exception:  # noqa: BLE001 — 汇报失败绝不能影响任务本身
            logger.exception("chat notifier failed")
