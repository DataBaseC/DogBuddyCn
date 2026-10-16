# DogBuddyCn — 运行台账 API（P0，2026-10-23）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""`/api/runs`：**此刻谁在跑什么** + 取消。

owner 实测三连（"你在哪跑呢，我咋没看见" / "他好像卡住了" / "停掉"）之前，
控制台没有任何地方能回答"现在有什么在跑"，也没有取消入口。
这些接口是前端顶部指示器与抽屉的数据源。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class RunIn(BaseModel):
    kind: str = "manual"
    window: str = ""
    group: str = ""
    project_id: str = ""
    prompt: str = ""
    async_mode: bool = False


def router(svc, auth_deps) -> APIRouter:
    r = APIRouter(tags=["runs"])

    def _runs() -> Any:
        reg = getattr(svc, "runs", None)
        if reg is None:
            raise HTTPException(status_code=503, detail="运行台账未启用")
        return reg

    @r.get("/runs", dependencies=auth_deps)
    def list_runs(active: int = 0, limit: int = 50) -> dict:
        reg = _runs()
        if active:
            rows = [x.as_dict() for x in reg.active()]
        else:
            rows = reg.recent(limit=limit)
        return {"runs": rows, "summary": reg.summary()}

    @r.get("/runs/{run_id}", dependencies=auth_deps)
    def get_run(run_id: str) -> dict:
        reg = _runs()
        run = reg.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"没有这个运行：{run_id}")
        out = run.as_dict()
        # 子运行（群组 → 成员 / 组长派给组员的活）一起给出，界面才能展开
        out["children"] = [x.as_dict() for x in reg.active()
                           if x.parent_run_id == run_id]
        return {"run": out}

    @r.post("/runs/{run_id}/cancel", dependencies=auth_deps)
    def cancel_run(run_id: str) -> dict:
        """取消一次运行（幂等）。**不删产物、不算失败**。"""
        windows = getattr(svc, "windows", None)
        if windows is not None and hasattr(windows, "cancel_run"):
            out = windows.cancel_run(run_id)
        else:  # 非工位运行（群组/脚本）
            out = _runs().cancel(run_id)
        if not out.get("ok"):
            raise HTTPException(status_code=404, detail=out.get("reason", "取消失败"))
        return out

    @r.post("/runs/{run_id}/retry", dependencies=auth_deps)
    def retry_run(run_id: str) -> dict:
        """用原派单内容重跑一次（进程重启后"中断"的运行靠它恢复）。"""
        reg = _runs()
        run = reg.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"没有这个运行：{run_id}")
        windows = getattr(svc, "windows", None)
        rec = getattr(windows, "_manual_last", {}).get(run.window_id or "", {}) if windows else {}
        prompt = str(rec.get("prompt") or "")
        if not prompt:
            raise HTTPException(
                status_code=400,
                detail="找不到原始派单内容（进程重启后内存里的 prompt 不在了），请重新派单")
        if windows is None or not hasattr(windows, "dispatch_manual"):
            raise HTTPException(status_code=503, detail="工位服务未启用")
        windows.dispatch_manual(run.window_id, prompt)
        new = getattr(windows, "_manual_last", {}).get(run.window_id, {}).get("run_id", "")
        return {"ok": True, "retried_from": run_id, "run_id": new}

    return r
