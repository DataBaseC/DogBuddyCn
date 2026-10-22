# DogBuddyCn — 自动备份 + 网关 @过滤/合并/状态（v0.10.50）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""两件事的回归：

① **备份**：2026-10-22 盘点时发现"DB 只有 1.6M，但备份全靠手工"——SQLite 跑 WAL，
   一旦 wal 与主库不一致就靠运气。这里钉住：三个文件一起拷、保留 N 份、失败不抛。

② **网关**（owner 决定 QQ 只接管理员）：群聊只认 @机器人（否则群里每句话都跑一轮
   管理员回合）、连发 2 秒合并成一段、状态可查（连没连上 / 丢了多少）。
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.gateway.adapters.onebot_v11 import OneBotV11Adapter  # noqa: E402
from dogbuddy.services.backup_service import BackupService  # noqa: E402


# ------------------------------------------------------------------ 备份
def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("create table t(x)")
    conn.execute("insert into t values (1)")
    conn.commit()
    conn.close()


def test_backup_copies_db_and_keeps_n(tmp_path):
    db_path = tmp_path / "dogbuddy.db"
    _make_db(db_path)
    svc = BackupService(db_path, tmp_path / "backups", keep=2)
    r1 = svc.run_once()
    assert r1["ok"], r1
    files = list(Path(r1["path"]).iterdir())
    assert any(f.name == "dogbuddy.db" for f in files), "主库必须在内"
    assert svc.status()["last_ok"] is True and svc.status()["last_bytes"] > 0

    # 再跑两次 → 只保留 2 份
    svc.run_once()
    svc.run_once()
    dirs = [p for p in (tmp_path / "backups").iterdir() if p.is_dir()]
    assert len(dirs) == 2, f"应只保留 2 份，实际 {len(dirs)}"
    assert svc.status()["count"] == 2


def test_backup_failure_is_swallowed(tmp_path):
    svc = BackupService(tmp_path / "nope.db", tmp_path / "backups")
    r = svc.run_once()
    assert r["ok"] is False
    assert svc.status()["last_ok"] is False
    assert "不存在" in svc.status()["last_error"]


# ------------------------------------------------------------------ 网关
def _msg(text: str, *, user="123", group=None, at=None, self_id="999") -> dict:
    segs: list[dict] = []
    if at:
        segs.append({"type": "at", "data": {"qq": at}})
    segs.append({"type": "text", "data": {"text": text}})
    data = {"post_type": "message", "self_id": self_id, "user_id": user,
            "message": segs, "raw_message": text}
    if group:
        data["group_id"] = group
    return data


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_group_message_without_at_is_dropped():
    ad = OneBotV11Adapter(whitelist=["123"], mention_only=True, merge_seconds=0)
    _run(ad._dispatch(_msg("大家好", group="g1"), None))
    assert ad.status()["dropped_no_mention"] == 1
    assert ad.status()["accepted"] == 0
    assert ad._events.empty()


def test_group_message_with_at_is_accepted():
    ad = OneBotV11Adapter(whitelist=["123"], mention_only=True, merge_seconds=0)
    _run(ad._dispatch(_msg("帮我看看", group="g1", at="999"), None))
    event = ad._events.get_nowait()
    assert event.text == "帮我看看"
    assert event.session_id == "group:g1"
    assert ad.status()["accepted"] == 1


def test_private_message_needs_no_mention():
    ad = OneBotV11Adapter(whitelist=["123"], mention_only=True, merge_seconds=0)
    _run(ad._dispatch(_msg("在吗"), None))
    assert ad._events.get_nowait().session_id == "private:123"


def test_burst_is_merged_into_one_turn():
    async def go():
        ad = OneBotV11Adapter(whitelist=["123"], mention_only=False, merge_seconds=0.05)
        await ad._dispatch(_msg("第一句"), None)
        await ad._dispatch(_msg("第二句"), None)
        await ad._dispatch(_msg("第三句"), None)
        await asyncio.sleep(0.15)
        return ad

    ad = _run(go())
    assert ad._events.qsize() == 1, f"连发应合并成 1 轮，实际 {ad._events.qsize()}"
    event = ad._events.get_nowait()
    assert event.text == "第一句\n第二句\n第三句"
    assert ad.status()["merged"] == 2


def test_non_whitelisted_is_counted_not_passed():
    ad = OneBotV11Adapter(whitelist=["123"], merge_seconds=0)
    _run(ad._dispatch(_msg("陌生人"), None))  # user=123 default → 换一个
    assert ad.status()["accepted"] == 1
    ad2 = OneBotV11Adapter(whitelist=["555"], merge_seconds=0)
    _run(ad2._dispatch(_msg("陌生人"), None))
    assert ad2.status()["dropped_whitelist"] == 1
    assert ad2._events.empty()


def test_status_never_leaks_the_token():
    ad = OneBotV11Adapter(access_token="super-secret-token", whitelist=["1"],
                          mention_only=True)
    st = ad.status()
    assert st["token_required"] is True
    assert "super-secret-token" not in str(st)
    assert st["mention_only"] is True and st["port"] == 8095
