# DogBuddyCn — cross-window file write guard (v0.10.39)
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 问：「咱们现在有没有防止同时写入的机制」。

查明：单工位串行队列 + cron 进程锁有，但**跨工位对同一文件的写入没有任何保护**——
群组共享项目根后，member_dispatch(wait=false) 派活导致组长/组员并发写同一批文件。
本轮两层机制：按路径的写锁（防交错写）+ expect_sha 乐观并发（防丢失更新）。
这些测试钉住两者，以及"opt-in 不破坏旧行为"。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.tools.file_guard import content_sha, current_sha, write_guard  # noqa: E402
from dogbuddy.tools.registry import ToolContext, build_registry  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(tmp_path, db=None) -> ToolContext:
    return ToolContext(window_id="w1", workspace=str(tmp_path / "ws"),
                       mode="BUILD", actor="worker", db=db)


def _ws(tmp_path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ws


# ------------------------------------------------------------ read 带回 sha
def test_read_file_returns_content_sha(tmp_path):
    _ws(tmp_path)
    reg = build_registry()
    res = _run(reg.execute("write_file", {"path": "a.txt", "content": "hello"}, _ctx(tmp_path)))
    assert res.ok
    r = _run(reg.execute("read_file", {"path": "a.txt"}, _ctx(tmp_path)))
    assert r.ok and "hello" in r.output
    sha = r.data.get("sha")
    assert sha and len(sha) == 12, f"read_file 要带回内容指纹: {r.data}"
    assert sha == content_sha(b"hello")
    assert sha == current_sha(tmp_path / "ws" / "a.txt")


# ------------------------------------------------------------ expect_sha
def test_write_with_matching_expect_sha_succeeds(tmp_path):
    _ws(tmp_path)
    reg = build_registry()
    _run(reg.execute("write_file", {"path": "a.txt", "content": "v1"}, _ctx(tmp_path)))
    r = _run(reg.execute("read_file", {"path": "a.txt"}, _ctx(tmp_path)))
    res = _run(reg.execute("write_file",
                           {"path": "a.txt", "content": "v2", "expect_sha": r.data["sha"]},
                           _ctx(tmp_path)))
    assert res.ok, res.output
    assert (tmp_path / "ws" / "a.txt").read_text(encoding="utf-8") == "v2"
    assert res.data.get("sha") == content_sha(b"v2")


def test_write_with_stale_expect_sha_refuses_lost_update(tmp_path):
    """核心场景：A 读 → B 改 → A 拿着旧 sha 写 → 必须被拒（否则 B 的成果被无声覆盖）。"""
    _ws(tmp_path)
    reg = build_registry()
    _run(reg.execute("write_file", {"path": "a.txt", "content": "v1"}, _ctx(tmp_path)))
    r = _run(reg.execute("read_file", {"path": "a.txt"}, _ctx(tmp_path)))
    # 另一个工位（同一共享工作区）改了文件
    other = ToolContext(window_id="w2", workspace=str(tmp_path / "ws"),
                        mode="BUILD", actor="worker")
    _run(reg.execute("write_file", {"path": "a.txt", "content": "v1 + B 的改进"}, other))
    # A 拿着旧指纹写 → 拒绝，且说明白发生了什么
    res = _run(reg.execute("write_file",
                           {"path": "a.txt", "content": "A 的旧印象整段", "expect_sha": r.data["sha"]},
                           _ctx(tmp_path)))
    assert not res.ok, "陈旧期望值必须拒绝写入"
    assert "已被其他工位修改" in res.output and "重新 read_file" in res.output
    # B 的内容完好（没被覆盖）
    assert "B 的改进" in (tmp_path / "ws" / "a.txt").read_text(encoding="utf-8")


def test_write_without_expect_sha_still_overwrites(tmp_path):
    """opt-in：不传 expect_sha 时行为不变（不破坏既有调用方/旧习惯）。"""
    _ws(tmp_path)
    reg = build_registry()
    _run(reg.execute("write_file", {"path": "a.txt", "content": "v1"}, _ctx(tmp_path)))
    res = _run(reg.execute("write_file", {"path": "a.txt", "content": "v2"}, _ctx(tmp_path)))
    assert res.ok
    assert (tmp_path / "ws" / "a.txt").read_text(encoding="utf-8") == "v2"


def test_edit_file_expect_sha_guards_read_modify_write(tmp_path):
    _ws(tmp_path)
    reg = build_registry()
    _run(reg.execute("write_file", {"path": "a.txt", "content": "hello world"}, _ctx(tmp_path)))
    r = _run(reg.execute("read_file", {"path": "a.txt"}, _ctx(tmp_path)))
    other = ToolContext(window_id="w2", workspace=str(tmp_path / "ws"),
                        mode="BUILD", actor="worker")
    _run(reg.execute("edit_file",
                     {"path": "a.txt", "old_text": "world", "new_text": "世界"}, other))
    res = _run(reg.execute("edit_file",
                           {"path": "a.txt", "old_text": "hello", "new_text": "hi",
                            "expect_sha": r.data["sha"]}, _ctx(tmp_path)))
    assert not res.ok and "已被其他工位修改" in res.output
    ok = _run(reg.execute("edit_file",
                          {"path": "a.txt", "old_text": "hello", "new_text": "hi"}, _ctx(tmp_path)))
    assert ok.ok
    assert (tmp_path / "ws" / "a.txt").read_text(encoding="utf-8") == "hi 世界"


def test_expect_sha_on_deleted_file_refuses(tmp_path):
    _ws(tmp_path)
    reg = build_registry()
    _run(reg.execute("write_file", {"path": "a.txt", "content": "v1"}, _ctx(tmp_path)))
    r = _run(reg.execute("read_file", {"path": "a.txt"}, _ctx(tmp_path)))
    (tmp_path / "ws" / "a.txt").unlink()
    res = _run(reg.execute("write_file",
                           {"path": "a.txt", "content": "v2", "expect_sha": r.data["sha"]},
                           _ctx(tmp_path)))
    assert not res.ok and "不存在" in res.output


# ------------------------------------------------------------ 按路径的写锁
def test_write_guard_serializes_same_path_across_tasks(tmp_path):
    """同一路径的第二个写者必须等第一个放锁——并发写因此不会交错。"""
    p = tmp_path / "locked.txt"
    order = []

    async def scenario():
        async with write_guard(p):
            order.append("first-in")
            # 第二个任务此刻尝试拿同一把锁
            t = asyncio.create_task(_second(p, order))
            await asyncio.sleep(0.05)
            order.append("first-still-holding")
        await t

    async def _second(path, log):
        async with write_guard(path):
            log.append("second-in")

    _run(scenario())
    assert order == ["first-in", "first-still-holding", "second-in"], order


def test_write_guard_isolated_per_loop(tmp_path):
    """测试各自新建事件循环——锁按循环分桶，互不污染（不会 cross-loop 报错）。"""
    p = tmp_path / "x.txt"
    for _ in range(3):
        async def once():
            async with write_guard(p):
                return True
        assert _run(once()) is True


def test_concurrent_write_file_calls_all_land_whole(tmp_path):
    """两个工位并发写同一文件：都成功，且最终内容是其中一个的完整版本（无半截写）。"""
    _ws(tmp_path)
    reg = build_registry()
    ctx_a = ToolContext(window_id="a", workspace=str(tmp_path / "ws"),
                        mode="BUILD", actor="worker")
    ctx_b = ToolContext(window_id="b", workspace=str(tmp_path / "ws"),
                        mode="BUILD", actor="worker")
    big_a = "A" * 5000 + "-tail-a"
    big_b = "B" * 5000 + "-tail-b"

    async def both():
        return await asyncio.gather(
            reg.execute("write_file", {"path": "same.txt", "content": big_a}, ctx_a),
            reg.execute("write_file", {"path": "same.txt", "content": big_b}, ctx_b),
        )

    ra, rb = _run(both())
    assert ra.ok and rb.ok
    final = (tmp_path / "ws" / "same.txt").read_text(encoding="utf-8")
    assert final in (big_a, big_b), "最终内容必须是其中一个写者的完整版本"
