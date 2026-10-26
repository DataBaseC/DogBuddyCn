# DogBuddyCn — 新闻站运营（0.15.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""站点运营的核心约束：

  ① **不编造新闻**：内容是 HN 真实标题 + 原文链接；AI 只做翻译/导读；
  ② **抓取失败不覆盖**：宁可停在上一版，也不给访客白页；
  ③ **幂等**：同一 HN id 只发布一次；
  ④ **自动更新要显式开启**：默认 0 永不自动，开启后仍按间隔与预算闸门。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dogbuddy.db import Database  # noqa: E402
from dogbuddy.services.news_service import HN_ITEM, HN_TOP, NewsService  # noqa: E402
from dogbuddy.services.operations_service import OperationsService  # noqa: E402
from dogbuddy.services.pipeline_service import PipelineService  # noqa: E402
from dogbuddy.services.sensor_hub import SensorHub  # noqa: E402
from dogbuddy.web.routes.news import router as news_router  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _story(i: int, score: int = 100) -> dict:
    return {"id": i, "type": "story", "title": f"Story {i}", "url": f"https://ex.com/{i}",
            "score": score, "by": "alice", "descendants": i * 3, "time": 1700000000 + i}


@pytest.fixture()
def env(tmp_path):
    db = Database(str(tmp_path / "news.db"))
    metrics = SensorHub(db)
    feeds = {HN_TOP: [1, 2, 3, 4],
             HN_ITEM.format(1): _story(1, 120),
             HN_ITEM.format(2): _story(2, 80),
             HN_ITEM.format(3): _story(3, 10),      # 低于门槛
             HN_ITEM.format(4): _story(4, 90)}
    calls = {"fetched": []}

    def http(url, timeout=15.0):
        calls["fetched"].append(url)
        if url not in feeds:
            raise RuntimeError("boom")
        return feeds[url]

    def make(editor=None):
        return NewsService(db, site_dir=str(tmp_path / "site"), metrics=metrics,
                           editor=editor, http=http)

    yield make, metrics, db, feeds, calls, tmp_path
    db.close()


# ------------------------------------------------------------------ 发布
def test_update_publishes_real_titles_and_links(env):
    make, metrics, db, feeds, calls, tmp_path = env
    news = make()
    out = _run(news.update({"limit": 3, "min_score": 50}))
    assert out["new"] == 3 and out["total"] == 3      # id=3 分数不够
    assert tmp_path.joinpath("site/index.html").exists()
    html = tmp_path.joinpath("site/index.html").read_text(encoding="utf-8")
    assert "Story 1" in html and "https://ex.com/1" in html
    assert "Hacker News" in html
    data = json.loads(tmp_path.joinpath("site/data/articles.json").read_text(encoding="utf-8"))
    assert len(data["articles"]) == 3 and data["source"] == "Hacker News"
    # 指标：文章总数与新条目数
    assert metrics.latest("news.articles")["value"] == 3
    assert metrics.latest("news.new_items")["value"] == 3


def test_update_is_idempotent(env):
    make, metrics, db, feeds, calls, tmp_path = env
    news = make()
    _run(news.update({"limit": 3, "min_score": 50}))
    again = _run(news.update({"limit": 3, "min_score": 50}))
    assert again["new"] == 0 and again["updated"] is False
    # 新条目出现后只加新的
    feeds[HN_TOP] = [5, 1, 2, 3]
    feeds[HN_ITEM.format(5)] = _story(5, 99)
    third = _run(news.update({"limit": 3, "min_score": 50}))
    assert third["new"] == 1 and third["total"] == 4


def test_fetch_failure_keeps_old_site(env):
    make, metrics, db, feeds, calls, tmp_path = env
    news = make()
    _run(news.update({"limit": 3, "min_score": 50}))
    before = tmp_path.joinpath("site/index.html").read_text(encoding="utf-8")
    feeds[HN_TOP] = "not-a-list"
    with pytest.raises(RuntimeError) as exc:
        _run(news.update({"limit": 3, "min_score": 50}))
    assert "新闻源抓取失败" in str(exc.value)
    after = tmp_path.joinpath("site/index.html").read_text(encoding="utf-8")
    assert before == after, "抓取失败不能把已有站点清空"


# ---------------------------------------------------------------- 编辑工位
def test_editor_translation_used(env):
    make, metrics, db, feeds, calls, tmp_path = env
    seen = {}

    async def editor(window_id, prompt):
        seen["window"] = window_id
        seen["prompt"] = prompt
        return json.dumps([{"id": 1, "title_zh": "中文标题一",
                            "summary": "一句导读一"},
                           {"id": 2, "title_zh": "中文标题二",
                            "summary": "一句导读二"}], ensure_ascii=False)

    news = make(editor=editor)
    out = _run(news.update({"limit": 2, "min_score": 50, "editor_window": "w-ops"}))
    assert out["translated"] == 2
    assert seen["window"] == "w-ops" and "不要编造" in seen["prompt"]
    html = tmp_path.joinpath("site/index.html").read_text(encoding="utf-8")
    assert "中文标题一" in html and "一句导读一" in html


def test_editor_failure_falls_back(env):
    make, metrics, db, feeds, calls, tmp_path = env

    async def editor(window_id, prompt):
        return "这不是 JSON"

    news = make(editor=editor)
    out = _run(news.update({"limit": 2, "min_score": 50, "editor_window": "w"}))
    assert out["new"] == 2 and out["translated"] == 0
    html = tmp_path.joinpath("site/index.html").read_text(encoding="utf-8")
    assert "Story 1" in html and "热度" in html     # 降级：原始标题 + 事实元数据


def test_status(env):
    make, metrics, db, feeds, calls, tmp_path = env
    news = make()
    _run(news.update({"limit": 2, "min_score": 50}))
    st = news.status()
    assert st["articles"] == 2 and st["path"] == "/news/"
    assert st["latest"][0]["url"].startswith("https://ex.com/")


# ------------------------------------------------------------ 流水线动作
def test_pipeline_news_update_action(env):
    make, metrics, db, feeds, calls, tmp_path = env
    news = make()
    pl = PipelineService(db, news=news, workspace_root=str(tmp_path / "ws"))
    pl.upsert({"id": "site", "steps": [
        {"id": "publish", "action": "news_update",
         "params": {"limit": 2, "min_score": 50}}]})
    run = pl.start("site")
    st = _run(pl.tick(run["id"]))
    assert st["run"]["status"] == "done"
    assert st["steps"][0]["output"]["new"] == 2


# ------------------------------------------------------------ 自动更新
def test_auto_interval_is_opt_in(env):
    make, metrics, db, feeds, calls, tmp_path = env
    ops = OperationsService(db)
    with pytest.raises(ValueError):
        ops.upsert_goal({"id": "g", "name": "站", "auto_interval_s": 30})
    ops.upsert_goal({"id": "manual", "name": "手动站"})       # 默认 0
    ops.upsert_goal({"id": "auto", "name": "自动站", "auto_interval_s": 60})
    started = ops.auto_tick()
    assert [r["journal"]["goal_id"] for r in started] == ["auto"]
    # 刚跑过 → 间隔没到，不再自动发起
    assert ops.auto_tick() == []
    assert db.get_goal("auto")["auto_interval_s"] == 60


# ------------------------------------------------------------------ 接口
def test_news_status_route(env):
    make, metrics, db, feeds, calls, tmp_path = env
    news = make()
    _run(news.update({"limit": 2, "min_score": 50}))
    app = FastAPI()
    app.include_router(news_router(SimpleNamespace(news=news), []), prefix="/api")
    c = TestClient(app)
    body = c.get("/api/news/status").json()
    assert body["articles"] == 2 and body["path"] == "/news/"


def test_app_mounts_site_and_health():
    root = Path(__file__).resolve().parents[1] / "dogbuddy/web/app.py"
    src = root.read_text(encoding="utf-8")
    assert 'app.mount("/news"' in src
    assert '"news": (svc.news.status()' in src
