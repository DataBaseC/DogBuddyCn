# DogBuddyCn — owner 2026-10-20 报的 6 个界面/记账问题（v0.10.43）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 实测反馈，逐条钉住：

1. 群组里显示了整段页面代码（工具执行细节进了共享频道 → recap 也读它 → 烧 token）；
2. 派活显示成"我"，其实应该是组长；工位/群组头像与名字要精确；
4. 缓存命中要显示**平均**（最后一次调用常是 0%，误导），本轮命中放气泡下小字；
5. 工位有时显示空白气泡（工具调用轮的 assistant 消息 content 为空）；
6. 日志审计筛选不管用、工位名显示成 id。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.agents.core.loop import _cache_ints  # noqa: E402
from dogbuddy.config import AppConfig, LLMConfig, PathsConfig, WebConfig  # noqa: E402
from dogbuddy.db import Database  # noqa: E402
from dogbuddy.main import DogBuddyApp  # noqa: E402
from dogbuddy.ports import Window  # noqa: E402
from dogbuddy.windows.session import WindowSession  # noqa: E402

TOKEN = "ui43-token"


def _sess(tmp_path, group_id: str = "g1") -> tuple[Database, WindowSession]:
    db = Database(str(tmp_path / "s.db"))
    w = Window(id="w1", name="程序员", role="worker_programmer",
               workspace_path=str(tmp_path), model="m", group_id=group_id)
    db.insert_window(w)
    return db, WindowSession(w, db, db, db)


# ------------------------------------------------ 1) 工具细节不进共享频道
def test_tool_messages_never_enter_group_channel(tmp_path):
    db, sess = _sess(tmp_path)
    m_tool = sess.append("tool", "<!DOCTYPE html>整段源码…",
                         meta={"tool_name": "read_file", "tool_call_id": "c1"})
    assert m_tool.group_id == "", "工具输出不是沟通，不该进共享频道"
    # 空 content 的 assistant（工具调用载体）也不进
    m_tc = sess.append("assistant", "", meta={"tool_calls": [{"id": "c1"}]})
    assert m_tc.group_id == ""
    # 有文字的 assistant 是发言，照常进
    m_say = sess.append("assistant", "我改好了")
    assert m_say.group_id == "g1"
    # user 提示词也照常进（它是派活内容）
    m_user = sess.append("user", "请实现")
    assert m_user.group_id == "g1"
    db.close()


def test_recap_skips_tool_roles(tmp_path):
    """历史数据里已经混进频道的 tool 消息，recap 也必须跳过（否则吃光预算）。"""
    from dogbuddy.services.group_manager import GroupManager
    db, sess = _sess(tmp_path)
    sess.append("assistant", "我完成了首页")
    sess.append("tool", "X" * 3000, meta={"tool_name": "read_file"})
    gm = GroupManager(db, windows=None)
    text, stats = gm._channel_recap("g1")
    assert "我完成了首页" in text
    assert "XXXX" not in text, "工具输出不该进 recap"
    db.close()


# ------------------------------------------------ 2) 派活方（sender）
def test_user_message_records_sender_and_dto_exposes_it(tmp_path):
    db, sess = _sess(tmp_path)
    sess.append("user", "实现登录页", meta={"sender": "方向"})
    dto = sess.recent(10)[-1]
    assert (dto.meta or {}).get("sender") == "方向"
    db.close()


def test_dispatch_carries_sender_into_ctx(tmp_path):
    """派单链路要把 sender 传到 ctx.extra（前端据此显示"方向 派发"）。"""
    from dogbuddy.events.bus import EventBus
    from dogbuddy.services.window_manager import WindowManager
    db = Database(str(tmp_path / "d.db"))
    wm = WindowManager(db=db, llm_factory=lambda: None, bus=EventBus(),
                       checkpoints_dir=tmp_path / "ck")
    w = wm.create("程序员", workspace_path=str(tmp_path / "ws"))
    rt = wm._rt(w.id)
    wm.dispatch(w.id, "任务", sender="方向")
    task = rt.queue.pending()[-1]
    assert (task.meta or {}).get("sender") == "方向"
    db.close()


# ------------------------------------------------ 4) 缓存平均
def test_cache_ints_normalizes_provider_shapes():
    class R:
        def __init__(self, usage):
            self.usage = usage
    assert _cache_ints(R({"prompt_tokens": 1000, "cache_hit_tokens": 900,
                          "cache_miss_tokens": 100})) == {"cache_hit": 900, "cache_miss": 100}
    assert _cache_ints(R({"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800,
                          "prompt_cache_miss_tokens": 200})) == {"cache_hit": 800, "cache_miss": 200}
    # OpenAI 嵌套：miss 由 prompt - hit 推
    assert _cache_ints(R({"prompt_tokens": 500,
                          "prompt_tokens_details": {"cached_tokens": 300}})) == {
        "cache_hit": 300, "cache_miss": 200}
    assert _cache_ints(R({})) == {}, "服务商没给缓存字段时不要编数字"


def test_cache_totals_sums_across_messages(tmp_path):
    db, sess = _sess(tmp_path)
    sess.append("assistant", "a", meta={"cache_hit": 900, "cache_miss": 100})
    sess.append("assistant", "b", meta={"cache_hit": 450, "cache_miss": 550})
    sess.append("assistant", "c", meta={"tool_calls": [{"id": "x"}]})  # 无缓存字段
    hit, miss = db.cache_totals("w1")
    assert (hit, miss) == (1350, 650)
    assert round(hit / (hit + miss), 3) == 0.675
    db.close()


def test_usage_endpoint_reports_average_cache(tmp_path):
    cfg = AppConfig(
        llm=LLMConfig(base_url="http://invalid.local/v1", api_key="k", model="fake",
                      request_timeout=5.0),
        web=WebConfig(auth_token=TOKEN),
        paths=PathsConfig(data_dir=str(tmp_path / "data"),
                          workspaces_dir=str(tmp_path / "workspaces")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    from fastapi.testclient import TestClient
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        w = app.windows.create(name="dev", workspace_path=str(tmp_path / "ws"))
        rt = app.windows._rt(w.id)
        rt.session.append("assistant", "a", meta={"cache_hit": 900, "cache_miss": 100})
        rt.session.append("assistant", "b", meta={"cache_hit": 0, "cache_miss": 1000})
        r = client.get(f"/api/windows/{w.id}/usage").json()
        assert r["cache_avg_hit_tokens"] == 900
        assert r["cache_avg_miss_tokens"] == 1100
        assert r["cache_avg_ratio"] == round(900 / 2000, 4), "必须是平均，不是最后一次"
    app.db.close()


# ------------------------------------------------ 6) 审计：筛选 + 工位名
def test_audit_filters_and_resolves_window_names(tmp_path):
    cfg = AppConfig(
        llm=LLMConfig(base_url="http://invalid.local/v1", api_key="k", model="fake",
                      request_timeout=5.0),
        web=WebConfig(auth_token=TOKEN),
        paths=PathsConfig(data_dir=str(tmp_path / "data"),
                          workspaces_dir=str(tmp_path / "workspaces")))
    cfg.gateway.enabled = False
    app = DogBuddyApp(cfg)
    from fastapi.testclient import TestClient
    with TestClient(app.web_app) as client:
        client.headers["X-Auth-Token"] = TOKEN
        w = app.windows.create(name="审查", workspace_path=str(tmp_path / "ws"))
        app.db.audit("worker", "tool:shell", f"{w.id} cmd=ls -la")
        app.db.audit("system", "group:run", "g1 members=3")
        rows = client.get("/api/logs/audit").json()
        by_action = {r["action"]: r for r in rows}
        assert by_action["tool:shell"]["actor_label"] == "审查", "要显示工位名而不是 id"
        assert by_action["group:run"]["actor_label"] == "系统"
        # 关键字/工位/动作三种筛选都要真的生效（以前只有 limit）
        assert {r["action"] for r in client.get("/api/logs/audit?q=ls -la").json()} == {"tool:shell"}
        # 按工位筛选：命中的都是"提到了这个工位"的记录（窗口创建日志也含 id，属正确）
        winset = {r["action"] for r in
                  client.get(f"/api/logs/audit?window={w.id}").json()}
        assert "tool:shell" in winset, "按工位筛选必须命中该工位的操作"
        assert "group:run" not in winset, "没提到该工位的记录不该出现"
        assert {r["action"] for r in
                client.get("/api/logs/audit?action=group:run").json()} == {"group:run"}
    app.db.close()


# ------------------------------------------------ 前端接线
def test_frontend_wiring_for_all_six():
    js = Path(__file__).resolve().parents[1] / "dogbuddy/web/static/js"
    main = (js / "views-main.js").read_text(encoding="utf-8")
    groups = (js / "views-groups.js").read_text(encoding="utf-8")
    ops = (js / "views-ops.js").read_text(encoding="utf-8")
    ui = (js / "ui.js").read_text(encoding="utf-8")
    # 2) 署名
    assert "m.sender" in main and "派发" in main
    # 4) 平均缓存 + 气泡小字
    assert "cache_avg_hit_tokens" in ui and "平均缓存命中" in ui
    assert "cache_hit" in main and "缓存 '" in main
    # 5) 空气泡
    assert "blank" in main and "role === 'assistant' && blank" in main
    # 1) 频道不显示工具消息
    assert "!== 'tool'" in groups
    # 6) 审计显示工位名 + 按工位筛选
    assert "actor_label" in ops and "winSel" in ops
