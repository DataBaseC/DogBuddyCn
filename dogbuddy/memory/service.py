# DogBuddyCn — file-based memory service (L3 Domain)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Memory: memory/global.md + memory/windows/<id>.md (OpenCode-inspired, file-based).

Reading is injected into context assembly; writing happens via admin tool and
during compaction (fact precipitation).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("dogbuddy.memory")


class MemoryService:
    def __init__(self, memory_dir: str | Path):
        self.root = Path(memory_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "windows").mkdir(parents=True, exist_ok=True)
        self.global_file = self.root / "global.md"

    # ------------------------------------------------------------- global
    def recall(self, scope: str = "global") -> str:
        if scope == "global":
            return self._read(self.global_file)
        return ""

    def write(self, scope: str, fact: str) -> None:
        if scope == "global":
            self.append_global(fact)
            return
        self.append_window(scope, fact)

    def append_global(self, fact: str) -> None:
        self._append(self.global_file, fact)

    # ------------------------------------------------------------- window
    def window_file(self, window_id: str) -> Path:
        safe = "".join(ch for ch in window_id if ch.isalnum() or ch in "-_")
        return self.root / "windows" / f"{safe}.md"

    def recall_window(self, window_id: str) -> str:
        return self._read(self.window_file(window_id))

    def append_window(self, window_id: str, fact: str) -> None:
        self._append(self.window_file(window_id), fact)

    # ------------------------------------------------------------- helper
    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""
        except OSError as e:
            logger.warning("memory read failed %s: %s", path, e)
            return ""

    @staticmethod
    def _append(path: Path, fact: str) -> None:
        fact = fact.strip()
        if not fact:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n- [{stamp}] {fact}\n")
        except OSError as e:
            logger.warning("memory write failed %s: %s", path, e)
