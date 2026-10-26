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
from dogbuddy.services.news_service import (  # noqa: E402
    GITHUB_SEARCH, HN_ITEM, HN_TOP, NewsService)
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
    assert "内容源抓取失败" in str(exc.value)
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


def _repo(i: int, stars: int = 1200, lang: str = "Python") -> dict:
    return {"id": 9000 + i, "full_name": f"acme/repo{i}", "html_url": f"https://github.com/acme/repo{i}",
            "description": f"Repo {i} does useful things", "stargazers_count": stars,
            "forks_count": i * 10, "language": lang,
            "owner": {"login": "acme"}, "created_at": "2026-08-01T00:00:00Z",
            "pushed_at": "2026-10-20T00:00:00Z"}


def test_github_recommendations(env):
    make, metrics, db, feeds, calls, tmp_path = env
    payload = {"items": [_repo(1), _repo(2, 800, "Go"), _repo(3, 10)]}

    def gh_http(url, timeout=15.0):
        assert url.startswith(GITHUB_SEARCH)
        assert "stars" in url and "created" in url
        return payload

    news = NewsService(db, site_dir=str(tmp_path / "gh"), metrics=metrics, http=gh_http)
    out = _run(news.update({"source": "github", "limit": 3,
                            "min_score": 200, "days": 30}))
    assert out["new"] == 2 and out["source"] == "github"     # 10 星的被门槛挡掉
    html = tmp_path.joinpath("gh/index.html").read_text(encoding="utf-8")
    assert "开源雷达" in html and "GITHUB PICKS" in html
    assert "acme/repo1" in html and "acme/repo2" in html
    assert "Story 1" not in html
    data = json.loads(tmp_path.joinpath("gh/data/articles.json").read_text(encoding="utf-8"))
    assert data["source_key"] == "github" and data["source"] == "GitHub"
    assert {a["language"] for a in data["articles"]} == {"Python", "Go"}
    assert metrics.latest("news.articles")["value"] == 2
    st = news.status()
    assert st["source"] == "github" and st["latest"][0]["title"].startswith("acme/")


def test_switching_source_resets_old_items(env):
    make, metrics, db, feeds, calls, tmp_path = env
    hn = make()
    _run(hn.update({"limit": 2, "min_score": 50}))
    assert "Story 1" in tmp_path.joinpath("site/index.html").read_text(encoding="utf-8")

    def gh_http(url, timeout=15.0):
        return {"items": [_repo(7)]}

    gh = NewsService(db, site_dir=str(tmp_path / "site"), metrics=metrics, http=gh_http)
    out = _run(gh.update({"source": "github", "limit": 2, "min_score": 200}))
    assert out["new"] == 1 and out["total"] == 1
    html = tmp_path.joinpath("site/index.html").read_text(encoding="utf-8")
    assert "acme/repo7" in html and "Story 1" not in html, "换源必须清掉旧条目"


def test_github_editor_prompt_is_about_repos(env):
    make, metrics, db, feeds, calls, tmp_path = env
    seen = {}

    async def editor(window_id, prompt):
        seen["prompt"] = prompt
        return json.dumps([{"id": 9001, "title_zh": "数据库小工具",
                            "summary": "用来查询和导出数据"}], ensure_ascii=False)

    def gh_http(url, timeout=15.0):
        return {"items": [_repo(1)]}

    news = NewsService(db, site_dir=str(tmp_path / "gh2"), metrics=metrics,
                       editor=editor, http=gh_http)
    out = _run(news.update({"source": "github", "limit": 1, "min_score": 200,
                            "editor_window": "w-ops"}))
    assert out["translated"] == 1
    assert "GitHub 项目推荐" in seen["prompt"] and "不要编造功能" in seen["prompt"]
    html = tmp_path.joinpath("gh2/index.html").read_text(encoding="utf-8")
    assert "数据库小工具" in html and "用来查询和导出数据" in html


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
