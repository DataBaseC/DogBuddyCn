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
    async def retry_run(run_id: str) -> dict:
        """唤醒未完成的运行（异常/重启/限速暂停之后都能用）。

        v0.10.40：改走共享实现 `services.run_retry.retry_run`——原始任务文本
        从 **runs.prompt（落库）** 取，进程重启后照样能重试；活着的运行会先
        被取消再重来（"唤醒"不是"再叠一个"）。
        """
        from ...services.run_retry import retry_run as _retry
        _runs()  # 台账不可用时直接 503（与其它接口一致）
        out = await _retry(svc, run_id)
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=out.get("reason", "重试失败"))
        return out

    return r
