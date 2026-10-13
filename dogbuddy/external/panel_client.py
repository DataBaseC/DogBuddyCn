# DogBuddyCn — AegisServerPanel client (L5 Infrastructure)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Thin async client for AegisServerPanel's agent API.

Only calls `POST /api/terminal/exec` with header `X-Agent-Token`; the panel is
never modified. Timeout is clamped to the panel's 1..600s contract.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx


class PanelError(RuntimeError):
    pass


class PanelClient:
    def __init__(self, base_url: str, agent_token: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.agent_token = agent_token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Agent-Token": self.agent_token, "Content-Type": "application/json"}

    async def exec(self, command: str, timeout: float | None = None) -> dict[str, Any]:
        """Run a shell command on the server through the panel.

        Returns {"stdout": ..., "stderr": ..., "returncode": ...} (panel payload passthrough).
        """
        t = min(max(timeout or self.timeout, 1.0), 600.0)
        payload = {"command": command, "timeout": int(t)}
        try:
            async with httpx.AsyncClient(timeout=t + 10.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/terminal/exec",
                    json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise PanelError(f"panel request failed: {e}") from e
        if resp.status_code == 401:
            raise PanelError("panel rejected X-Agent-Token (401)")
        if resp.status_code >= 400:
            raise PanelError(f"panel error {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        return {
            "stdout": str(data.get("stdout", data.get("output", ""))),
            "stderr": str(data.get("stderr", "")),
            "returncode": data.get("returncode", data.get("code", 0)),
        }

    async def exec_sync(self, command: str, timeout: float | None = None) -> dict[str, Any]:
        return await self.exec(command, timeout)


class NullPanelClient:
    """Panel disabled: refuses everything loudly instead of silently doing nothing."""

    async def exec(self, command: str, timeout: float | None = None) -> dict[str, Any]:
        raise PanelError("panel integration disabled in config (panel.enabled=false)")


def build_panel_client(base_url: str, agent_token: str, enabled: bool = True) -> Any:
    if not enabled:
        return NullPanelClient()
    return PanelClient(base_url, agent_token)
