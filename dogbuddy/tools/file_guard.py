# DogBuddyCn — cross-window file write guard (v0.10.39)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""跨工位共享工作区的写入互斥 + 乐观并发（owner 2026-10-18：「咱们现在有没有
防止同时写入的机制」）。

**现状（本轮查明）**：单工位有串行队列（一个工位同时只跑一个任务），cron 有
进程锁——但**跨工位对同一文件的写入没有任何保护**。群组挂项目后全体成员共享
项目根（T7.3），`member_dispatch(wait=false)` 派活后组长与组员并发、或两个手动
派单的工位并发，会同时读写同一批文件：
  1. 两个写撞在同一时刻 → 交错/半截写；
  2. A 读 → B 写 → A 写 → **丢失更新**（A 用旧内容整段覆盖 B 的成果）。

**机制（两层，只覆盖 file 工具）**：
  1. **按路径的 asyncio 锁**（`write_guard`）：write_file/edit_file 的
     「读-改-写」全程持锁，同一路径的写操作串行化，杜绝交错写。应用是单进程
     （uvicorn 单 worker），进程内锁即全覆盖；锁按「事件循环 × 路径」分桶，
     测试各自的事件循环互不干扰。
  2. **乐观并发**（`expect_sha`）：read_file 返回内容 sha；write_file/edit_file
     可传 `expect_sha`——文件当前 sha 与期望不符就失败，并说明"已被他人修改，
     请重新 read"。锁只能防"同时"，防不了"先后"；防丢失更新只能靠这个。

**已知边界（知情决策）**：`shell` 是任意命令执行，锁它是一场必输的战斗
（`python -c open(...,'w')`、`sed -i`、重定向……无穷绕法）——同 shell_guard
的教训，这里不装出能防的样子。shell 写入的冲突由 checkpoint 快照兜底恢复，
由工位提示词层面分工（组长派活时指定不同文件）规避。
"""
from __future__ import annotations

import asyncio
import hashlib
import weakref
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

#: 「事件循环 × 路径」→ asyncio.Lock。用 WeakKeyDictionary 按循环对象分桶：
#: 测试每次新建事件循环（互不污染），生产只有一个循环（锁真正跨任务互斥），
#: 循环销毁后条目自动回收（不会泄漏）。
_LOCKS: "weakref.WeakKeyDictionary[Any, dict[str, asyncio.Lock]]" = \
    weakref.WeakKeyDictionary()


def _lock_for(path: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    per_loop = _LOCKS.get(loop)
    if per_loop is None:
        per_loop = {}
        _LOCKS[loop] = per_loop
    lock = per_loop.get(path)
    if lock is None:
        lock = asyncio.Lock()
        per_loop[path] = lock
    return lock


@asynccontextmanager
async def write_guard(path: Any) -> AsyncIterator[None]:
    """持住某个路径的写锁（进程内、跨工位生效）。

    用法：`async with write_guard(p): ...读-改-写...`
    同一路径的第二个写者会在这里排队，直到前一个写完——不会交错。
    """
    async with _lock_for(str(path)):
        yield


def content_sha(data: bytes) -> str:
    """内容指纹（sha1 前 12 位）——read_file 带回，expect_sha 比对用。"""
    return hashlib.sha1(data).hexdigest()[:12]


def current_sha(path: Any) -> str:
    """文件的当前指纹；文件不存在返回空串（新建场景）。"""
    try:
        return content_sha(path.read_bytes())
    except (OSError, ValueError):
        return ""
