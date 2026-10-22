# DogBuddyCn — 每日自动备份 SQLite（v0.10.50）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""把 `dogbuddy.db` 的每日快照做进应用本身。

为什么现在做（2026-10-22 盘点）：`/var/lib/dogbuddy` 只有 11M、DB 1.6M，
但**备份全靠手工**——最近两次是 10-16 的临时 dump。SQLite 开的是 WAL，
一旦 `-wal` 与主库不一致（断电/进程被 kill -9/磁盘满），恢复就靠运气。

做法刻意保守：
  * 先 `PRAGMA wal_checkpoint(TRUNCATE)` 把 WAL 并回主库（失败也继续，只是快照可能滞后）；
  * 再把 `db / db-wal / db-shm` **三个文件一起**拷进 `backups/<时间戳>/`（少一个都不完整）；
  * 保留最近 `keep` 份（默认 7），更早的目录删掉；
  * 全程 try/except：备份失败只记 WARNING + 状态，**绝不影响主流程**；
  * 状态挂进 `/healthz`（`backups` 字段），"最后一次成功是什么时候"一眼能看到。

不做什么：不做增量、不做异地、不加密（这些都超出"别把库丢了"这个目标）。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("dogbuddy.services.backup")

#: 距上次成功超过这个小时数才补一次（避免每次重启都立刻备份）
STALE_HOURS = 20.0


class BackupService:
    def __init__(self, db_path: str | Path, backup_dir: str | Path,
                 keep: int = 7, interval_hours: float = 24.0,
                 bus: Any = None, db: Any = None) -> None:
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        self.keep = max(1, int(keep))
        self.interval_hours = max(1.0, float(interval_hours))
        self.bus = bus
        self.db = db          # Database（写审计用；None 时只打日志）
        self._task: asyncio.Task | None = None
        self._status: dict[str, Any] = {
            "last_at": "", "last_ok": None, "last_path": "", "last_bytes": 0,
            "last_error": "", "count": 0, "keep": self.keep,
        }

    # ---------------------------------------------------------------- 视图
    def status(self) -> dict[str, Any]:
        return dict(self._status)

    # ---------------------------------------------------------------- 核心
    def _checkpoint(self) -> None:
        """把 WAL 并回主库。用独立连接（不能动正在跑的那个）。"""
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=5)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.commit()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — checkpoint 失败不阻断拷贝
            logger.warning("backup: wal_checkpoint failed (%s); 快照可能滞后于最新写入", e)

    def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        stamp = now.strftime("%Y%m%d-%H%M%S")
        dest = self.backup_dir / stamp
        # 同一秒内跑两次（测试 / 手动补一次）不能撞目录：加序号后缀
        seq = 1
        while dest.exists():
            seq += 1
            dest = self.backup_dir / f"{stamp}-{seq}"
        try:
            if not self.db_path.exists():
                raise FileNotFoundError(f"db 不存在：{self.db_path}")
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            self._checkpoint()
            dest.mkdir(parents=True, exist_ok=False)
            total = 0
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(self.db_path) + suffix)
                if src.exists():
                    shutil.copy2(src, dest / src.name)
                    total += src.stat().st_size
            if total <= 0:
                raise RuntimeError("备份出来是空文件")
            self._status.update({
                "last_at": now.isoformat(), "last_ok": True, "last_path": str(dest),
                "last_bytes": total, "last_error": "",
            })
            self._prune()
            self._status["count"] = self._count()
            logger.info("backup ok: %s (%d bytes)", dest, total)
            self._audit("backup:ok", f"{dest} bytes={total}")
            return {"ok": True, "path": str(dest), "bytes": total}
        except Exception as e:  # noqa: BLE001 — 备份绝不能把主流程带崩
            self._status.update({"last_at": now.isoformat(), "last_ok": False,
                                 "last_error": f"{type(e).__name__}: {e}"})
            logger.warning("backup failed: %s", e)
            self._audit("backup:failed", f"{type(e).__name__}: {e}")
            return {"ok": False, "error": str(e)}

    def _count(self) -> int:
        try:
            return len([p for p in self.backup_dir.iterdir() if p.is_dir()])
        except OSError:
            return 0

    def _prune(self) -> None:
        try:
            dirs = sorted((p for p in self.backup_dir.iterdir() if p.is_dir()),
                          key=lambda p: p.name)
        except OSError:
            return
        for old in dirs[: max(0, len(dirs) - self.keep)]:
            try:
                shutil.rmtree(old)
                logger.info("backup pruned: %s", old.name)
            except OSError as e:
                logger.warning("backup prune failed (%s): %s", old.name, e)

    def _audit(self, action: str, detail: str) -> None:
        if self.db is None:
            return
        try:
            self.db.audit("system", action, detail)
        except Exception:  # noqa: BLE001
            logger.debug("backup audit failed", exc_info=True)

    # ---------------------------------------------------------------- 调度
    def last_success_age_hours(self, now: datetime | None = None) -> float | None:
        last = str(self._status.get("last_at") or "")
        if not last or self._status.get("last_ok") is not True:
            return None
        try:
            dt = datetime.fromisoformat(last)
        except ValueError:
            return None
        now = now or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds() / 3600.0

    async def start(self) -> None:
        """后台循环：启动 60s 后按需补一次，之后每 interval_hours 一次。

        `start()` 幂等；异常一律吞掉（下一轮再试），绝不把应用拖崩。
        """
        if self._task is not None:
            return

        async def _loop() -> None:
            try:
                await asyncio.sleep(60)   # 让启动阶段先跑完（迁移、健康检查）
                age = self.last_success_age_hours()
                if age is None or age >= STALE_HOURS:
                    await asyncio.to_thread(self.run_once)
                while True:
                    await asyncio.sleep(self.interval_hours * 3600)
                    await asyncio.to_thread(self.run_once)
            except asyncio.CancelledError:  # pragma: no cover - 关闭时
                raise
            except Exception:  # noqa: BLE001
                logger.warning("backup loop crashed; will not retry", exc_info=True)

        self._task = asyncio.get_event_loop().create_task(_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None
