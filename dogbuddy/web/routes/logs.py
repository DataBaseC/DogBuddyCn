# DogBuddyCn — logs & audit routes (L1 Interface)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""Audit log + job run history."""
from __future__ import annotations

from fastapi import APIRouter


def _actor_label(actor: str, detail: str, names: dict[str, str]) -> str:
    """把审计的 actor 翻成人能看懂的名字（v0.10.43）。

    owner 反馈："日志审计……不显示工位名称，显示的是 id"。根因：审计的 actor
    从来只有 `worker` / `system` / `web` / `admin:*`，**没有工位维度**；工位
    只出现在 detail 里（`{window_id} ...`）。这里按 detail 里的窗口 id 反查名字，
    这比改几十处审计调用点更稳。
    """
    text = str(detail or "")
    for wid, name in names.items():
        if wid and wid in text:
            return name
    a = str(actor or "")
    if a.startswith("admin"):
        return "管理员"
    return {"worker": "员工", "system": "系统", "web": "控制台"}.get(a, a or "—")


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["logs"])

    @r.get("/logs/audit", dependencies=auth_deps)
    def audit(limit: int = 1000, q: str = "", action: str = "",
              window: str = "") -> list[dict]:
        """审计日志（v0.10.43：支持 q/action/window 筛选 + 返回工位名）。

        以前只有 `limit`：前端只能拉 200 条在浏览器里过滤，更早的记录永远筛不到
        （owner："筛选不管用"），actor 也只会显示 `worker`/id。
        """
        size = max(1, min(int(limit or 1000), 5000))
        rows = svc.db.list_audit(size)
        try:
            names = {w.id: w.name for w in svc.db.list_windows()}
        except Exception:  # noqa: BLE001 — 名称解析失败不该让审计页 500
            names = {}
        needle = (q or "").strip().lower()
        want_action = (action or "").strip()
        want_window = (window or "").strip()
        out: list[dict] = []
        for e in rows:
            label = _actor_label(e.actor, e.detail, names)
            blob = f"{e.actor} {e.action} {e.detail} {label}".lower()
            if want_action and want_action.lower() not in str(e.action or "").lower():
                continue
            if want_window:
                wname = names.get(want_window, "")
                if want_window not in blob and (not wname or wname.lower() not in blob):
                    continue
            if needle and needle not in blob:
                continue
            out.append({"id": e.id, "actor": e.actor, "actor_label": label,
                        "action": e.action, "detail": e.detail,
                        "created_at": e.created_at})
        return out

    @r.get("/logs/runs", dependencies=auth_deps)
    def runs(limit: int = 100, window_id: str | None = None) -> list[dict]:
        rows = svc.jobs.runs(None, limit)
        if window_id:
            rows = [r for r in rows if r.get("window_id") == window_id]
        return rows

    return r
