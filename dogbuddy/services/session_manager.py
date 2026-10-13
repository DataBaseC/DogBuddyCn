# DogBuddyCn — session management service (L2 Application)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""SessionManager: view / compress / reset / inject window contexts (admin tooling)."""
from __future__ import annotations

from typing import Any


class SessionManager:
    def __init__(self, windows: Any, db: Any):
        self.windows = windows  # WindowManager
        self.db = db

    def _rt(self, window_id: str) -> Any:
        return self.windows._rt(window_id)

    def view(self, window_id: str) -> dict[str, Any]:
        rt = self._rt(window_id)
        msgs = rt.session.recent(10)
        tail = "\n".join(f"[{m.role}] {m.content[:120]}" for m in msgs)
        return {
            "text": (f"tokens={rt.session.token_usage()} messages={len(rt.session.recent(1000))}\n"
                     f"--- tail ---\n{tail}"),
            "tokens": rt.session.token_usage(),
            "messages": len(rt.session.recent(1000)),
        }

    async def compress(self, window_id: str) -> Any:
        rt = self._rt(window_id)
        compactor = self.windows._compactor_for(rt.window)
        return await compactor.compact_now(rt.session)

    def reset(self, window_id: str) -> int:
        rt = self._rt(window_id)
        msgs = rt.session.recent(100000)
        if not msgs:
            return 0
        return rt.session.drop_messages(msgs[-1].id)

    def inject(self, window_id: str, text: str) -> None:
        rt = self._rt(window_id)
        rt.session.append("user", f"[injected by admin] {text}")
