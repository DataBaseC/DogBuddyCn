# DogBuddyCn — 群组频道 recap 的 token 优化回归（v0.10.21）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测（2026-10-16）："将群聊记录重新全发一遍会浪费很多的 token"。

生产数据（群组 `9174e4b248cc`，一次 3 人任务）：
* 频道 20 条消息，**6 条（30%）是编排提示词本身**；
* 提示词长度逐条膨胀 385 → 915 → 1403 → 1910 → 2471 → 3028 字符，
  最后那条里嵌套了 10 层同样的提示词 —— 因为提示词里有 recap、recap 又读回提示词；
* 按旧口径模拟：recap 每次注入 402 → 958 → 1462 → 2018 → 2579 → 3152 字符，
  一轮重复注入合计 ≈10571 字符 ≈**3523 tokens**。

这些测试钉住三条新规则：只取产出、增量、按预算裁。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.events.bus import EventBus  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.services.group_manager import (GROUP_STEP_MARK, RECAP_BUDGET_CHARS,  # noqa: E402
                                             GroupManager)
from dogbuddy.services.window_manager import WindowManager  # noqa: E402


def _stack(tmp_path):
    db = Database(str(tmp_path / "g.db"))
    bus = EventBus()
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=bus,
                       checkpoints_dir=tmp_path / "ckpt")
    ws = tmp_path / "ws"
    ws.mkdir()
    for wid, name in (("w1", "worker-1"), ("w2", "worker-2"), ("w3", "worker-3")):
        db.insert_window(Window(id=wid, name=name, role="worker_programmer", mode="BUILD",
                                workspace_path=str(ws), model="m"))
    gm = GroupManager(db, wm, bus=bus)
    g = gm.create("测试群")
    gm.assign(g.id, "w1", role="lead")
    gm.assign(g.id, "w2")
    gm.assign(g.id, "w3")
    return db, gm, g.id


def test_编排提示词不进频道也不进recap(tmp_path):
    db, gm, gid = _stack(tmp_path)
    # 模拟"步骤提示词"：它自己带签名，而且**内含**上一条提示词（自指）
    step1 = f"{GROUP_STEP_MARK} · 工位「worker-1」】\n总任务：写 hello\n---\n(频道暂无历史记录)"
    step2 = (f"{GROUP_STEP_MARK} · 工位「worker-2」】\n总任务：写 hello\n"
             f"--- 共享频道最新记录 ---\n{step1}\n---")
    db.add_message("w1", "user", step1, 0, None, group_id=gid)
    db.add_message("w1", "assistant", "worker-1 写完了 hello.txt", 0, None, group_id=gid)
    db.add_message("w2", "user", step2, 0, None, group_id=gid)
    db.add_message("w2", "assistant", "worker-2 写完了 hello.txt", 0, None, group_id=gid)

    text, stats = gm._channel_recap(gid)
    assert GROUP_STEP_MARK not in text, "编排提示词不能出现在 recap 里（它会把上一条 recap 套进来）"
    assert "worker-1 写完了" in text and "worker-2 写完了" in text
    assert stats["lines"] == 2, f"只应剩两条产出，实际 {stats}"
    db.close()


def test_recap_按字符预算裁而不是按条数(tmp_path):
    db, gm, gid = _stack(tmp_path)
    for i in range(40):
        db.add_message("w1", "assistant", f"第 {i} 条产出 " + "x" * 500, 0, None, group_id=gid)
    text, stats = gm._channel_recap(gid, budget=1200)
    assert stats["chars"] <= 1200 + RECAP_BUDGET_CHARS, f"预算没生效：{stats}"
    assert stats["dropped"] > 0 and "已省略" in text, "省略了多少要给可行动的提示"
    assert "第 39 条产出" in text, "预算裁的是最旧的，最新产出必须在"
    assert "第 0 条产出" not in text
    db.close()


def test_增量_recap_只给本轮新产出(tmp_path):
    db, gm, gid = _stack(tmp_path)
    db.add_message("w1", "assistant", "上一轮的旧结论", 0, None, group_id=gid)
    start = gm._last_channel_id(gid)
    assert start > 0
    # 本轮：member2 之后的新产出
    db.add_message("w2", "assistant", "本轮 member2 的新产出", 0, None, group_id=gid)
    delta, d_stats = gm._channel_recap(gid, since_id=start)
    assert "本轮 member2 的新产出" in delta and "上一轮的旧结论" not in delta
    assert d_stats["lines"] == 1
    # 历史尾部（水位之前）仍然按预算给
    tail, t_stats = gm._channel_recap(gid, upto_id=start, budget=1800)
    assert "上一轮的旧结论" in tail and "本轮 member2" not in tail
    db.close()


def test_recap_block_拼出两段且有用量统计(tmp_path):
    db, gm, gid = _stack(tmp_path)
    db.add_message("w1", "assistant", "旧产出 A" * 50, 0, None, group_id=gid)
    start = gm._last_channel_id(gid)
    db.add_message("w2", "assistant", "新产出 B" * 50, 0, None, group_id=gid)
    text, stats = gm._recap_block(gid, start)
    assert "本轮此前成员的产出" in text and "更早的频道要点" in text
    assert stats["delta_lines"] == 1 and stats["tail_lines"] == 1
    assert stats["tokens"] == stats["chars"] // 3
    db.close()


def test_跨轮次不线性膨胀而是被预算封顶(tmp_path):
    """核心回归：跑 8 轮，recap 注入量**饱和**而不是随轮次线性膨胀（旧实现会）。"""
    db, gm, gid = _stack(tmp_path)
    sizes = []
    for rnd in range(8):
        start = gm._last_channel_id(gid)
        # 每轮：三个成员各一条产出（编排提示词不进频道）
        for wid in ("w2", "w3", "w1"):
            db.add_message(wid, "user",
                           f"{GROUP_STEP_MARK} · 工位「{wid}」】\n总任务：第 {rnd} 轮",
                           0, None, group_id=gid)
            db.add_message(wid, "assistant", f"第 {rnd} 轮 {wid} 的产出", 0, None, group_id=gid)
        _text, stats = gm._recap_block(gid, start)
        sizes.append(stats["tokens"])
    # 上限 = 增量预算 3600 字符 + 历史尾部预算 1800 字符（÷3 ≈ tokens），留点余量
    cap = (RECAP_BUDGET_CHARS + RECAP_BUDGET_CHARS // 2) // 3 + 60
    assert max(sizes) <= cap, f"recap 必须被预算封顶：max={max(sizes)} cap={cap} 序列={sizes}"
    assert sizes[-1] - sizes[-2] < 60, f"应当饱和而不是继续增长：{sizes}"
    assert sizes[-1] < sizes[0] * 12, f"8 轮只该涨一个有界的量：{sizes}"
    db.close()


def test_幂等_空频道返回占位(tmp_path):
    db, gm, gid = _stack(tmp_path)
    text, stats = gm._recap_block(gid, 0)
    assert text == "(频道暂无历史记录)" and stats["chars"] == 0
    db.close()
