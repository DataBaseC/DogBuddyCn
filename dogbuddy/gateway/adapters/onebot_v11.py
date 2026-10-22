# DogBuddyCn — OneBot v11 reverse-WebSocket adapter (L1 Interface)
# Adapter layer concept derived from AstrBot (https://github.com/AstrBotDevs/AstrBot)
# Copyright (c) AstrBotDevs and contributors — AGPL-3.0
# Modifications (2026-10-12, DogBuddyCn contributors):
#   - rewritten for DogBuddyCn's BaseAdapter contract (AstrBot's own OneBot
#     implementation is NOT copied — only the adapter abstraction is derived)
"""Reverse WebSocket server speaking OneBot v11 (NapCat / Lagrange connect to us).

Whitelist filtering happens HERE (before L2). Access token checked on handshake.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from .base import BaseAdapter, MessageEvent, PushTarget

logger = logging.getLogger("dogbuddy.gateway.onebot")

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]


class OneBotV11Adapter(BaseAdapter):
    def __init__(self, host: str = "0.0.0.0", port: int = 8095, access_token: str = "",
                 whitelist: list[str | int] | None = None,
                 mention_only: bool = True, merge_seconds: float = 0.0):
        super().__init__()
        self.host = host
        self.port = port
        self.access_token = access_token
        self.whitelist = {str(q) for q in (whitelist or [])}
        #: v0.10.50：群聊只在 @机器人 时才响应。以前群里任何白名单成员每说一句
        #: 都跑一轮管理员逻辑（一轮几十秒、几十万 token），等于拿群聊当输入法。
        self.mention_only = bool(mention_only)
        #: v0.10.50：连发合并窗口（秒）。同一人 2 秒内补的几句拼成一段再跑一轮。
        #: 适配器默认 0 = 不合并（便于单测直接取事件）；生产由 main.py 从
        #: `config.gateway.merge_seconds`（默认 2.0）注入。
        self.merge_seconds = max(0.0, float(merge_seconds))
        self._events: asyncio.Queue[MessageEvent] = asyncio.Queue()
        self._connections: list[Any] = []
        self._echo = 0
        self._pending: dict[Any, asyncio.Future] = {}
        self._server = None
        self._merge: dict[str, dict[str, Any]] = {}
        #: 控制台「网关状态」卡片的数据源（绝不含 token）
        self._stats: dict[str, Any] = {
            "accepted": 0, "merged": 0, "dropped_whitelist": 0,
            "dropped_no_mention": 0, "dropped_empty": 0,
            "last_message_at": "", "listening": False,
        }

    def status(self) -> dict[str, Any]:
        """给 `/api/gateway/status`：连接数、丢/收计数、最近一条消息时间。"""
        return {
            "host": self.host, "port": self.port,
            "token_required": bool(self.access_token),
            "mention_only": self.mention_only,
            "merge_seconds": self.merge_seconds,
            "whitelist_size": len(self.whitelist),
            "clients": len(self._connections),
            **self._stats,
        }

    # ------------------------------------------------------------ BaseAdapter
    async def recv(self) -> MessageEvent:
        return await self._events.get()

    async def send(self, target: PushTarget, content: str) -> None:
        await self.push(target, content)

    async def push(self, target: PushTarget, content: str) -> None:
        if target.type == "group":
            action, params = "send_group_msg", {"group_id": int(target.id), "message": content}
        else:
            action, params = "send_private_msg", {"user_id": int(target.id), "message": content}
        await self._call(action, params)

    # ---------------------------------------------------------------- server
    async def run(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets package required for the OneBot adapter")
        self._server = await websockets.serve(
            self._handler, self.host, self.port,
            process_request=self._check_token if self.access_token else None)
        self._stats["listening"] = True
        logger.info("OneBot v11 reverse WS listening on %s:%s", self.host, self.port)
        await asyncio.Future()  # run until cancelled

    async def terminate(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _check_token(self, path: str, headers: Any) -> Any:
        """websockets process_request hook — reject wrong tokens at handshake."""
        from urllib.parse import parse_qs, urlparse
        if not self.access_token:
            return None
        q = parse_qs(urlparse(path).query)
        token = (q.get("access_token") or [""])[0]
        if not token:
            auth = headers.get("Authorization", "") if hasattr(headers, "get") else ""
            token = auth.replace("Bearer ", "")
        if token != self.access_token:
            return (401, [], b"invalid access token")
        return None

    async def _handler(self, ws: Any) -> None:
        self._connections.append(ws)
        logger.info("OneBot client connected (%d total)", len(self._connections))
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                await self._dispatch(data, ws)
        finally:
            if ws in self._connections:
                self._connections.remove(ws)
            logger.info("OneBot client disconnected (%d left)", len(self._connections))

    async def _dispatch(self, data: dict[str, Any], ws: Any) -> None:
        # response to one of our API calls
        if "echo" in data and data.get("echo") in self._pending:
            fut = self._pending.pop(data["echo"])
            if not fut.done():
                fut.set_result(data)
            return
        if data.get("post_type") != "message":
            return
        self_id = str(data.get("self_id") or "")
        user_id = str(data.get("user_id", ""))
        group_id = data.get("group_id")
        raw_message = data.get("message")
        text = self._extract_text(raw_message, data.get("raw_message", ""))
        if not text:
            self._stats["dropped_empty"] += 1
            return
        # whitelist gate (L1 responsibility) — silent drop otherwise
        if self.whitelist and user_id not in self.whitelist:
            self._stats["dropped_whitelist"] += 1
            logger.info("dropped message from non-whitelisted QQ %s", user_id)
            return
        # v0.10.50：群聊只认 @机器人（self_id 未知时不做判断，保持旧行为）
        if group_id and self.mention_only and self_id and not self._mentions(raw_message, self_id):
            self._stats["dropped_no_mention"] += 1
            return
        text = re.sub(r"[ \t]+", " ", text).strip()
        if not text:
            self._stats["dropped_empty"] += 1
            return
        session_id = f"group:{group_id}" if group_id else f"private:{user_id}"
        self._stats["last_message_at"] = datetime.now(timezone.utc).isoformat()
        self._enqueue(session_id, user_id, text,
                      str(group_id) if group_id else None, data)

    @staticmethod
    def _mentions(message: Any, self_id: str) -> bool:
        """消息段里有没有 at 机器人自己。"""
        if not isinstance(message, list):
            return False
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "at":
                if str((seg.get("data") or {}).get("qq", "")) == self_id:
                    return True
        return False

    def _enqueue(self, session_id: str, user_id: str, text: str,
                 group_id: str | None, raw: dict[str, Any]) -> None:
        """v0.10.50：2 秒内的连发合并成一段，再交给 L2。

        以前用户一口气发三句 = 跑三轮管理员回合（第一轮还没答完，后两轮的上下文
        已经变了），既慢又贵。合并后只跑一轮。
        """
        key = f"{session_id}|{user_id}"
        entry = self._merge.get(key)
        if entry is not None and self.merge_seconds > 0:
            entry["text"] = entry["text"] + "\n" + text
            self._stats["merged"] += 1
            timer = entry.get("timer")
            if timer is not None:
                timer.cancel()
            entry["timer"] = asyncio.get_event_loop().create_task(self._flush_later(key))
            return
        entry = {"session_id": session_id, "user_id": user_id, "text": text,
                 "group_id": group_id, "raw": raw, "timer": None}
        self._merge[key] = entry
        if self.merge_seconds <= 0:
            self._merge.pop(key, None)
            self._emit(entry)
            return
        entry["timer"] = asyncio.get_event_loop().create_task(self._flush_later(key))

    async def _flush_later(self, key: str) -> None:
        try:
            await asyncio.sleep(self.merge_seconds)
        except asyncio.CancelledError:
            return
        entry = self._merge.pop(key, None)
        if entry is not None:
            self._emit(entry)

    def _emit(self, entry: dict[str, Any]) -> None:
        self._events.put_nowait(MessageEvent(
            platform="onebot_v11", session_id=entry["session_id"],
            user_id=entry["user_id"], text=entry["text"],
            group_id=entry["group_id"], attachments=[], raw=entry["raw"]))
        self._stats["accepted"] += 1

    @staticmethod
    def _extract_text(message: Any, raw: str) -> str:
        if isinstance(message, str):
            return message.strip()
        if isinstance(message, list):
            parts = []
            for seg in message:
                if isinstance(seg, dict):
                    if seg.get("type") == "text":
                        parts.append(str(seg.get("data", {}).get("text", "")))
                    elif seg.get("type") == "image":
                        parts.append("[image]")
                elif isinstance(seg, str):
                    parts.append(seg)
            return "".join(parts).strip()
        return (raw or "").strip()

    async def _call(self, action: str, params: dict[str, Any]) -> Any:
        if not self._connections:
            raise RuntimeError("no OneBot client connected")
        self._echo += 1
        echo = self._echo
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[echo] = fut
        payload = {"action": action, "params": params, "echo": echo}
        for conn in list(self._connections):
            try:
                await conn.send(json.dumps(payload, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                continue
        try:
            return await asyncio.wait_for(fut, timeout=15.0)
        except asyncio.TimeoutError:
            self._pending.pop(echo, None)
            raise RuntimeError(f"onebot action {action} timed out") from None
