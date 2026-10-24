# DogBuddyCn — 清生成物免确认 / 待决推送不再崩 / 空错误兜底（v0.10.54）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""三件都来自 2026-10-22～24 的生产遥测：

① 10 条待决里 10 条是 `rm -rf games/__pycache__` 这类**清理**命令，每次都要人点，
   人不在线 → 该步等掉 1800s 预算 → 整轮超时（9 条非 done 的 run 里 4 条如此）。
② `ReportService._push_pending_asks()` 调 `self._targets(None)` →
   `AttributeError: 'NoneType' object has no attribute 'payload'`，两天崩 20 次，
   导致待决汇总既没推送、也没进最近记录。
③ `LLM 调用失败（permanent）：。` —— 异常字符串为空时把原因弄丢了。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.permissions import (  # noqa: E402
    PermissionGate, is_benign_cleanup,
)
from dogbuddy.agents.core.scope import Scope  # noqa: E402
from dogbuddy.services.report_service import ReportService  # noqa: E402
from dogbuddy.tools.registry import ToolContext  # noqa: E402


# --------------------------------------------------------------- ① 清理白名单
@pytest.mark.parametrize("cmd", [
    "rm -rf games/__pycache__",
    "rm -rf __pycache__ .pytest_cache",
    "rm -f a.pyc b.pyo c.tmp",
    "rm -rfv build/__pycache__",
    "rm -rf games/__pycache__ && echo cleaned",
    "ls -la && rm -rf games/__pycache__",
])
def test_benign_cleanup_is_allowed(tmp_path, cmd):
    assert is_benign_cleanup(cmd, str(tmp_path)) is True


@pytest.mark.parametrize("cmd", [
    "rm -rf games",                        # 删真目录
    "rm -rf .",                            # 删当前目录
    "rm -rf /",                            # 根
    "rm -rf ../outside/__pycache__",       # 越界
    "rm -rf /etc/__pycache__",             # 绝对路径越界
    "cd games && rm -rf __pycache__",      # cd 改变基准，静态判断不可靠
    "rm -rf games/__pycache__ > /dev/null",  # 重定向
    "rm -rf games/__pycache__ | tee log",    # 管道
    "rm -rf $(pwd)/__pycache__",             # 命令替换
    "find . -name __pycache__ -delete",      # 不是 rm，不在此列
    "rm -rf games/__pycache__ && rm -rf games",  # 混了真目录
])
def test_risky_commands_still_need_confirmation(tmp_path, cmd):
    assert is_benign_cleanup(cmd, str(tmp_path)) is False


def test_cleanup_short_circuits_the_gate_but_real_delete_does_not(tmp_path):
    gate = PermissionGate(confirm_available=True)
    ctx = ToolContext(window_id="w1", workspace=str(tmp_path), mode="BUILD",
                      scope=Scope(level="project", actor="w1", source="role:worker"))
    ok = gate.check("shell", "shell", {"command": "rm -rf a/__pycache__"}, ctx, "")
    assert ok.allowed is True, "清 __pycache__ 不该再弹确认"
    bad = gate.check("shell", "shell", {"command": "rm -rf a"}, ctx, "")
    assert bad.allowed is False and bad.needs_confirm is True, "删真目录仍要人确认"
    root = gate.check("shell", "shell", {"command": "rm -rf /"}, ctx, "")
    assert root.allowed is False


def test_root_still_blocked_even_for_artifact_names(tmp_path):
    """名字像生成物也不行：`rm -rf /__pycache__` 在根目录下。"""
    gate = PermissionGate(confirm_available=True)
    ctx = ToolContext(window_id="w1", workspace=str(tmp_path), mode="BUILD",
                      scope=Scope(level="project", actor="w1", source="role:worker"))
    assert is_benign_cleanup("rm -rf /__pycache__", str(tmp_path)) is False
    assert gate.check("shell", "shell", {"command": "rm -rf /__pycache__"},
                      ctx, "").allowed is False


# --------------------------------------------------------------- ② 待决推送
def test_targets_tolerates_none_event():
    svc = ReportService.__new__(ReportService)      # 不跑 __init__，只验这一个方法
    svc.notifiers = [SimpleNamespace(name="console"), SimpleNamespace(name="qq")]
    assert len(svc._targets(None)) == 2, "event=None 必须回退成「所有渠道」（以前直接 AttributeError）"


def test_push_pending_asks_no_longer_crashes():
    """端到端跑一遍 `_push_pending_asks`：以前这里必抛 AttributeError。"""
    import asyncio

    sent = []

    class _Notifier:
        name = "console"

        async def send(self, text):
            sent.append(text)

    svc = ReportService.__new__(ReportService)
    svc.notifiers = [_Notifier()]
    svc._ask_delay = 0
    svc._ask_debounce = {}
    svc._recent = []
    svc.decisions = SimpleNamespace(
        list=lambda status="pending", limit=50: [
            SimpleNamespace(window_id="w1", question="要不要删 __pycache__？")])
    svc._remember = lambda text: svc._recent.append(text[:100])

    asyncio.new_event_loop().run_until_complete(svc._push_pending_asks("w1"))
    assert sent and "待决" in sent[0]
    assert svc._recent, "以前崩在 _remember 之前，最近记录也拿不到"


# --------------------------------------------------------------- ③ 空错误兜底
def test_empty_exception_message_falls_back_to_type_name():
    src = (Path(__file__).resolve().parents[1]
           / "dogbuddy/agents/core/loop.py").read_text(encoding="utf-8")
    assert "_detail = str(e).strip() or type(e).__name__" in src, \
        "异常字符串为空时必须回退到类型名，否则摘要变成「LLM 调用失败（permanent）：。」"
