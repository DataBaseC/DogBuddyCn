# DogBuddyCn — 唤醒未完成的运行（v0.10.40）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner：「要确保因为异常导致的循环停止后，管理员能够唤醒未完成的任务。」

**查明的硬缺口**（2026-10-20）：
1. `/api/runs/{id}/retry` 从 `windows._manual_last`（**内存**）取原始 prompt——
   进程重启后清空，retry 直接 400「请重新派单」。而"异常导致循环停止"最常见的
   场景恰恰是**进程重启/崩溃**。
2. 管理员**没有重试工具**（只有 run_list / run_status / run_cancel）——对话里
   "把那个失败的活重跑一下"做不到，只能让 owner 去控制台点。
3. 群组运行**完全没有重试路径**。

本模块是唯一实现（HTTP 路由与管理工具都调它），语义：
* **唤醒 = 先清掉卡住的那次，再重来一次**（活着的运行先取消，不是叠一个新的）；
* 原始任务文本优先取 `runs.prompt`（v0.10.40 落库，重启后仍在），老数据才回退
  到内存的 `_manual_last`；
* 按 kind 分派：定时任务 → `jobs.run_now`；群组 → 后台 `groups.run`；
  工位 → `windows.dispatch_manual`。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("dogbuddy.services.run_retry")

#: 后台群组任务要留引用（asyncio 只持弱引用，任务可能被 GC 掉）
_BACKGROUND: set[asyncio.Task] = set()


def _live(status: str) -> bool:
    return status in ("queued", "running", "paused", "cancelling")


async def retry_run(svc: Any, run_id: str) -> dict[str, Any]:
    """唤醒一次未完成的运行。

    返回 `{"ok": bool, "mode": "window|group|job", "run_id": 新运行 id, "note": str}`；
    `ok=False` 时带 `reason`（常见：0.10.40 之前的运行没存任务原文）。
    """
    runs = getattr(svc, "runs", None)
    if runs is None:
        return {"ok": False, "reason": "运行台账不可用"}
    run = runs.get(run_id)
    if run is None:
        return {"ok": False, "reason": f"没有这个运行：{run_id}"}

    windows = getattr(svc, "windows", None)
    notes: list[str] = []

    # 1) 活着的运行先取消——"唤醒"是重来一次，不是再叠一个。
    if _live(run.status):
        try:
            if windows is not None and hasattr(windows, "cancel_run"):
                out = windows.cancel_run(run.id)
            else:
                out = runs.cancel(run.id)
            if out.get("ok"):
                notes.append(f"已先停掉卡住的那次（{run.status}）")
            else:
                notes.append(f"停掉旧运行失败：{out.get('reason', '未知')}，仍继续重试")
        except Exception as e:  # noqa: BLE001 — 取消失败不阻止重试
            logger.warning("cancel before retry failed", exc_info=True)
            notes.append(f"停掉旧运行出错（{type(e).__name__}），仍继续重试")

    # 2) 取原始任务文本：优先**落库**的 prompt；老数据回退内存。
    prompt = str(getattr(run, "prompt", "") or "").strip()
    if not prompt and windows is not None:
        rec = getattr(windows, "_manual_last", {}).get(run.window_id or "", {})
        if rec.get("run_id") in ("", run.id):  # 只认"就是这次"的内存副本
            prompt = str(rec.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False,
                "reason": ("这条运行没有存下原始任务文本（v0.10.40 之前的运行），"
                           "唤不醒——请重新派单（window_dispatch / group_run）")}

    # 3) 按类型分派
    jobs = getattr(svc, "jobs", None)
    if run.job_id and jobs is not None:
        try:
            jobs.run_now(run.job_id)
            notes.append(f"定时任务 {run.job_id} 已立即重跑")
            return {"ok": True, "mode": "job", "job_id": run.job_id,
                    "note": "；".join(notes)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"重跑定时任务失败：{e}"}

    groups = getattr(svc, "groups", None)
    is_group = run.kind == "group" or (run.group_id and not run.window_id)
    if is_group and groups is not None and run.group_id:
        try:
            task = asyncio.create_task(groups.run(run.group_id, prompt))
            _BACKGROUND.add(task)
            task.add_done_callback(_BACKGROUND.discard)
            notes.append("群组运行已在后台重新发起（用 run_list 看新运行）")
            return {"ok": True, "mode": "group", "group_id": run.group_id,
                    "note": "；".join(notes)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"重新发起群组运行失败：{e}"}

    if run.window_id and windows is not None and hasattr(windows, "dispatch_manual"):
        try:
            windows.dispatch_manual(run.window_id, prompt)
            new_id = getattr(windows, "_manual_last", {}).get(run.window_id, {}).get("run_id", "")
            notes.append(f"已重新派给工位「{run.window_name or run.window_id}」（后台运行）")
            return {"ok": True, "mode": "window", "run_id": new_id,
                    "window_id": run.window_id, "note": "；".join(notes)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"重新派单失败：{e}"}

    return {"ok": False,
            "reason": "这条运行没有可重试的落点（无工位 / 群组 / 定时任务）"}
