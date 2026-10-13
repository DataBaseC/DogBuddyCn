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
from typing import Any

from .base import BaseAdapter, MessageEvent, PushTarget

logger = logging.getLogger("dogbuddy.gateway.onebot")

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None  # type: ignore[assignment]


class OneBotV11Adapter(BaseAdapter):
    def __init__(self, host: str = "0.0.0.0", port: int = 8095, access_token: str = "",
                 whitelist: list[str | int] | None = None):
        super().__init__()
        self.host = host
        self.port = port
        self.access_token = access_token
        self.whitelist = {str(q) for q in (whitelist or [])}
        self._events: asyncio.Queue[MessageEvent] = asyncio.Queue()
        self._connections: list[Any] = []
        self._echo = 0
        self._pending: dict[Any, asyncio.Future] = {}
        self._server = None

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
        user_id = str(data.get("user_id", ""))
        group_id = data.get("group_id")
        text = self._extract_text(data.get("message"), data.get("raw_message", ""))
        if not text:
            return
        # whitelist gate (L1 responsibility) — silent drop otherwise
        if self.whitelist and user_id not in self.whitelist:
            logger.info("dropped message from non-whitelisted QQ %s", user_id)
            return
        session_id = f"group:{group_id}" if group_id else f"private:{user_id}"
        event = MessageEvent(
            platform="onebot_v11", session_id=session_id, user_id=user_id,
            text=text, group_id=str(group_id) if group_id else None,
            attachments=[], raw=data)
        self._events.put_nowait(event)

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
