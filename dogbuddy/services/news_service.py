# DogBuddyCn — 站点运营：新闻聚合站（0.15.0）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""把"运营一个站点"落成一个真实可跑的服务。

内容来源：**Hacker News 公共 API**（真实标题 + 原文链接 + 热度/讨论数）。
AI 只做**翻译与导读**，不编造事件；抓不到 AI 摘要时就用原始标题照常发布。
这样站点既"有内容"，也不会变成假新闻生成器。

站点是纯静态（`index.html` + `data/articles.json`），由主应用挂在 `/news`
下提供访问；每轮更新幂等（按 HN id 去重），只新增、不重复。

红线：
  * 不抓正文、不转载全文，只放标题 + 一句导读 + 原文链接（版权留在原作者）；
  * 抓取失败时**不覆盖**已有站点（宁可停在上一版，也不给访客白页）；
  * AI 摘要失败只降级、不阻塞发布。
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("dogbuddy.services.news")

HN_TOP = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
GITHUB_SEARCH = "https://api.github.com/search/repositories"
MAX_KEEP = 120
#: 推荐口径：近 N 天**新建**、星数达门槛，按星数排序（避免全是几个超级仓库）
GITHUB_DEFAULT_DAYS = 90
GITHUB_DEFAULT_STARS = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ago(ts: float) -> str:
    try:
        secs = max(0, time.time() - float(ts))
    except (TypeError, ValueError):
        return "—"
    if secs < 3600:
        return f"{int(secs // 60)} 分钟前"
    if secs < 86400:
        return f"{int(secs // 3600)} 小时前"
    return f"{int(secs // 86400)} 天前"


class NewsService:
    def __init__(self, db: Any, *, site_dir: str, metrics: Any = None,
                 editor: Callable[[str, str], Any] | None = None,
                 cost: Any = None,
                 http: Callable[[str, float], Any] | None = None) -> None:
        self.db = db
        self.site_dir = Path(site_dir)
        self.metrics = metrics
        #: async (window_id, prompt) -> str；由 main 注入工位派活通道
        self.editor = editor
        self.cost = cost
        self._http = http or self._http_get
        self.site_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- 抓取
    @staticmethod
    def _http_get(url: str, timeout: float = 15.0) -> Any:
        req = urllib.request.Request(url, headers={"User-Agent": "DogBuddyCn-News/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "null")

    def _data_path(self) -> Path:
        return self.site_dir / "data" / "articles.json"

    def _load(self) -> dict[str, Any]:
        path = self._data_path()
        if not path.exists():
            return {"articles": [], "updated_at": ""}
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(blob, dict) and isinstance(blob.get("articles"), list):
                return blob
        except Exception:  # noqa: BLE001 — 坏文件不能拦住重建
            logger.warning("news cache unreadable, rebuilding", exc_info=True)
        return {"articles": [], "updated_at": ""}

    def _item_to_article(self, sid: int, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict) or item.get("deleted") or item.get("dead"):
            return None
        if str(item.get("type") or "") != "story":
            return None
        url = str(item.get("url") or "")
        if not url.startswith("http"):
            url = f"https://news.ycombinator.com/item?id={sid}"
        return {"id": sid, "title": str(item.get("title") or "").strip(),
                "url": url, "score": int(item.get("score") or 0),
                "by": str(item.get("by") or ""),
                "comments": int(item.get("descendants") or 0),
                "hn_time": int(item.get("time") or 0),
                "source": "Hacker News",
                "first_seen": time.time()}

    def _fetch_new(self, limit: int, min_score: int,
                   known: set[int]) -> list[dict[str, Any]]:
        """从 HN 榜单往下取，跳过已发布的，凑够 limit 条。

        为什么要并发：HN 的 item 接口是**一条一个请求**，串行时遇到慢连接
        （单条 8–15s）能把一轮拖到十几分钟。这里按 6 条一批并发，并且**最多
        扫描 40 条**——宁可这一轮少发两条，也不能把运营回合卡死。
        """
        ids = self._http(HN_TOP, 12)
        if not isinstance(ids, list):
            raise RuntimeError("HN 榜单返回异常")
        candidates: list[int] = []
        for raw in ids[:40]:
            try:
                sid = int(raw)
            except (TypeError, ValueError):
                continue
            if sid not in known:
                candidates.append(sid)
        out: list[dict[str, Any]] = []

        def _one(sid: int) -> Any:
            try:
                return self._http(HN_ITEM.format(sid), 8)
            except Exception:  # noqa: BLE001 — 单条失败跳过，不拖垮整轮
                logger.debug("hn item fetch failed: %s", sid, exc_info=True)
                return None

        with ThreadPoolExecutor(max_workers=6) as pool:
            for start in range(0, len(candidates), 6):
                batch = candidates[start:start + 6]
                for sid, item in zip(batch, pool.map(_one, batch)):
                    art = self._item_to_article(sid, item)
                    if art and art["score"] >= min_score:
                        out.append(art)
                        if len(out) >= limit:
                            return out
        return out

    # ------------------------------------------------------- GitHub 推荐
    def _fetch_github(self, limit: int, min_score: int, known: set[int],
                      days: int = GITHUB_DEFAULT_DAYS) -> list[dict[str, Any]]:
        """GitHub Search：近 N 天新建、星数达门槛，按星数取推荐。

        一次请求拿 50 条，按已知项目数翻页 —— 不做逐仓库请求（会直接撞限速）。
        未认证 Search API 10 次/分钟，我们每轮只调 1 次，10 分钟一轮远低于上限。
        """
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
                 ).strftime("%Y-%m-%d")
        # fork/archived 直接排掉；`forks:>=5` 专治"新建仓库刷星但没人用"
        q = (f"created:>={since} stars:>={max(0, int(min_score))} "
             f"forks:>=5 fork:false archived:false")
        page = 1 + (len(known) // 50)
        url = (f"{GITHUB_SEARCH}?q={urllib.parse.quote(q)}&sort=stars&order=desc"
               f"&per_page=50&page={page}")
        payload = self._http(url, 20)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError("GitHub 搜索返回异常（可能是限速或网络）")
        out: list[dict[str, Any]] = []
        for item in payload["items"]:
            if not isinstance(item, dict):
                continue
            try:
                rid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if rid in known:
                continue
            full = str(item.get("full_name") or "").strip()
            url = str(item.get("html_url") or "")
            if not full or not url.startswith("http"):
                continue
            stars = int(item.get("stargazers_count") or 0)
            if stars < int(min_score):
                continue
            desc = str(item.get("description") or "").strip()
            # 没有描述的仓库没法写"推荐理由"，硬编就是编造；直接跳过
            if len(desc) < 8:
                continue
            out.append({
                "id": rid, "title": full, "url": url, "score": stars,
                "by": str((item.get("owner") or {}).get("login") or ""),
                "comments": int(item.get("forks_count") or 0),
                "language": str(item.get("language") or ""),
                "desc": desc,
                "created_at": str(item.get("created_at") or ""),
                "pushed_at": str(item.get("pushed_at") or ""),
                "source": "GitHub", "first_seen": time.time()})
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------- 摘要
    async def _translate(self, items: list[dict[str, Any]], window_id: str,
                         source: str = "hackernews") -> dict[int, dict[str, str]]:
        """让运营工位给中文标题 + 一句导读；失败就返回空（走降级）。"""
        if not items or not window_id or self.editor is None:
            return {}
        if source == "github":
            payload = [{"id": it["id"], "repo": it["title"][:120],
                        "desc": (it.get("desc") or "")[:300],
                        "language": it.get("language") or "",
                        "stars": it.get("score")} for it in items]
            prompt = (
                "你在给一个中文『GitHub 项目推荐』站做编辑。下面是仓库名、真实描述、"
                "语言和星数。请只做两件事：① 写一句不超过 30 字的中文推荐语；"
                "② 写一句不超过 50 字的用途说明。**只能基于给出的描述和语言，"
                "不要编造功能、不要承诺效果、不要加我不知道的事实**。\n"
                "严格只返回 JSON 数组，不要 markdown/前后缀，格式：\n"
                '[{"id": 123, "title_zh": "推荐语", "summary": "用途说明"}]\n'
                f"仓库：{json.dumps(payload, ensure_ascii=False)}")
        else:
            payload = [{"id": it["id"], "title": it["title"][:160]} for it in items]
            prompt = (
                "你在给一个中文科技新闻站做编辑。下面是 Hacker News 的真实标题。"
                "请只做两件事：① 把标题翻成简洁中文；② 写一句不超过 40 字的导读，"
                "**只能基于标题信息，不要编造正文细节、不要加我不知道的事实**。\n"
                "严格只返回 JSON 数组，不要 markdown/前后缀，格式：\n"
                '[{"id": 123, "title_zh": "…", "summary": "…"}]\n'
                f"标题：{json.dumps(payload, ensure_ascii=False)}")
        try:
            raw = await asyncio.wait_for(self.editor(window_id, prompt), timeout=90)
        except Exception:  # noqa: BLE001
            logger.warning("news editor failed; falling back to raw titles", exc_info=True)
            return {}
        text = str(raw or "")
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            logger.warning("news editor returned non-JSON; fallback")
            return {}
        try:
            rows = json.loads(text[start:end + 1])
        except Exception:  # noqa: BLE001
            logger.warning("news editor JSON parse failed; fallback", exc_info=True)
            return {}
        out: dict[int, dict[str, str]] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                rid = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            out[rid] = {"title_zh": str(row.get("title_zh") or "").strip()[:120],
                        "summary": str(row.get("summary") or "").strip()[:160]}
        return out

    # ------------------------------------------------------------- 更新
    async def update(self, params: dict[str, Any] | None = None,
                     payload: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        source = str(params.get("source") or "hackernews").lower()
        if source not in ("github", "hackernews"):
            raise ValueError("source 只能是 github / hackernews")
        limit = max(1, min(int(params.get("limit") or 5), 20))
        min_score = int(params.get("min_score") or
                        (GITHUB_DEFAULT_STARS if source == "github" else 30))
        window_id = str(params.get("editor_window") or "")
        store = self._load()
        # 换源必须清空旧条目，否则 HN 和 GitHub 会混在一个站里
        old_key = str(store.get("source_key") or
                      ("github" if store.get("source") == "GitHub" else "hackernews"))
        rows = store.get("articles") or [] if old_key == source else []
        existing = {int(a.get("id")): a for a in rows
                    if str(a.get("id", "")).lstrip("-").isdigit()}
        try:
            if source == "github":
                fresh = await asyncio.to_thread(
                    self._fetch_github, limit, min_score, set(existing),
                    int(params.get("days") or GITHUB_DEFAULT_DAYS))
            else:
                fresh = await asyncio.to_thread(self._fetch_new, limit, min_score,
                                                set(existing))
        except Exception as e:  # noqa: BLE001
            # 抓不到就**不覆盖**：宁可停在上一版，也不给访客白页
            raise RuntimeError(f"内容源抓取失败：{type(e).__name__}: {e}") from e
        if not fresh:
            return {"new": 0, "total": len(existing), "updated": False,
                    "source": source,
                    "note": "没有更新的条目（源未变化或都低于门槛）"}

        trans = await self._translate(fresh, window_id, source)
        for item in fresh:
            t = trans.get(int(item["id"]))
            if t:
                item["title_zh"] = t.get("title_zh") or ""
                item["summary"] = t.get("summary") or ""
            if not item.get("summary"):
                if source == "github":
                    bits = [item.get("language") or "开源项目",
                            f"★ {item['score']}", f"{item['comments']} forks"]
                    desc = str(item.get("desc") or "").strip()
                    if desc:
                        bits.append(desc[:80])
                    item["summary"] = " · ".join(bits)
                else:
                    item["summary"] = (f"Hacker News 热度 {item['score']} 分 · "
                                       f"{item['comments']} 条讨论")
            existing[int(item["id"])] = item

        articles = sorted(existing.values(), key=lambda a: a.get("first_seen") or 0,
                          reverse=True)[:MAX_KEEP]
        store = {"articles": articles, "updated_at": _now(),
                 "source_key": source,
                 "source": "GitHub" if source == "github" else "Hacker News",
                 "count": len(articles)}
        self._write_site(store)
        if self.metrics is not None:
            try:
                self.metrics.push("news.articles", len(articles), unit="条",
                                  source="news")
                self.metrics.push("news.new_items", len(fresh), unit="条",
                                  source="news")
            except Exception:  # noqa: BLE001
                logger.debug("news metric push failed", exc_info=True)
        return {"new": len(fresh), "total": len(articles), "updated": True,
                "source": source, "translated": len(trans),
                "article_ids": [a["id"] for a in fresh]}

    # ------------------------------------------------------------- 渲染
    def _write_site(self, store: dict[str, Any]) -> None:
        data_dir = self.site_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp = data_dir / "articles.json.tmp"
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, data_dir / "articles.json")
        index = self.site_dir / "index.html"
        tmp_html = self.site_dir / "index.html.tmp"
        tmp_html.write_text(self._render(store), encoding="utf-8")
        os.replace(tmp_html, index)

    def _render(self, store: dict[str, Any]) -> str:
        arts = store.get("articles") or []
        updated = store.get("updated_at") or ""
        gh = (store.get("source_key") == "github"
              or store.get("source") == "GitHub")
        try:
            stamp = datetime.fromisoformat(updated.replace("Z", "+00:00")).astimezone()
            shown = stamp.strftime("%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            shown = "—"
        hero = arts[0] if arts else None
        cards = arts[1:25]
        hero_html = ""
        if hero:
            if gh:
                title = hero.get("title") or ""
                dek = " ".join(x for x in (hero.get("title_zh"), hero.get("summary")) if x)
                meta = (f"★ {hero.get('score', 0)} · {html.escape(hero.get('language') or '开源')}"
                        f" · {hero.get('comments', 0)} forks · {_ago(hero.get('first_seen'))}")
                link_label = "查看仓库 ↗"
                orig = (f'<div class="orig">原描述：{html.escape(hero.get("desc") or "")}</div>'
                        if hero.get("desc") else "")
            else:
                title = hero.get("title_zh") or hero.get("title") or ""
                dek = hero.get("summary") or ""
                meta = (f"来源 Hacker News · 热度 {hero.get('score', 0)}"
                        f" · {hero.get('comments', 0)} 条讨论 · {_ago(hero.get('first_seen'))}")
                link_label = "读原文 ↗"
                orig = (f'<div class="orig">原标题：{html.escape(hero.get("title") or "")}</div>'
                        if hero.get("title_zh") else "")
            hero_html = f'''
      <section class="hero">
        <div class="tag">{"今日推荐 · PICK OF THE DAY" if gh else "头条 · TOP STORY"}</div>
        <h1><a href="{html.escape(hero["url"], quote=True)}" target="_blank" rel="noopener">{html.escape(title)}</a></h1>
        <p class="dek">{html.escape(dek)}</p>
        <div class="byline">{meta}
          <a class="src" href="{html.escape(hero["url"], quote=True)}" target="_blank" rel="noopener">{link_label}</a></div>
        {orig}
      </section>'''
        card_html = []
        for a in cards:
            if gh:
                title = a.get("title") or ""
                meta = (f'<span class="pill">★ {a.get("score", 0)}</span> · '
                        f'{html.escape(a.get("language") or "开源")} · {_ago(a.get("first_seen"))}')
                foot = (f'{html.escape(a.get("source") or "GitHub")} · '
                        f'{a.get("comments", 0)} forks')
            else:
                title = a.get("title_zh") or a.get("title") or ""
                meta = (f'<span class="pill">{a.get("score", 0)} 分</span> · '
                        f'{_ago(a.get("first_seen"))}')
                foot = (f'{html.escape(a.get("source") or "Hacker News")} · '
                        f'{a.get("comments", 0)} 条讨论')
            card_html.append(f'''
        <article class="card">
          <div class="meta">{meta}</div>
          <h2><a href="{html.escape(a["url"], quote=True)}" target="_blank" rel="noopener">{html.escape(title)}</a></h2>
          <p>{html.escape(a.get("summary") or "")}</p>
          <div class="foot">{foot}</div>
        </article>''')
        if not hero:
            hero_html = '<section class="hero"><h1>正在准备第一期内容…</h1></section>'
        return _HTML_TEMPLATE.format(
            brand="开源雷达" if gh else "前沿速递",
            brand_sub="GITHUB PICKS" if gh else "HORIZON WIRE",
            doc_title="开源雷达 · GitHub 项目推荐" if gh else "前沿速递 · Horizon Wire",
            count_label="个项目" if gh else "篇",
            footer=(
                "<b>关于这个站</b>：项目来自 GitHub Search API（真实仓库名、描述、语言、"
                "星数与 forks），AI 只写中文推荐语与用途说明，不编造功能；"
                "点击仓库名查看源码与 README。"
                if gh else
                "<b>关于这个站</b>：内容来自 Hacker News 公共 API（标题、热度、讨论数），"
                "AI 只做中文翻译与一句导读，不编造正文；版权归原作者，点击标题阅读原文。"),
            shown=html.escape(shown), count=len(arts), hero=hero_html,
            cards="".join(card_html),
            empty=('<p class="empty">还没有更多推荐，下一次更新会自动补上。</p>'
                   if gh else
                   '<p class="empty">还没有更多条目，下一次更新会自动补上。</p>')
                  if not cards else "")

    # ------------------------------------------------------------- 状态
    def status(self) -> dict[str, Any]:
        store = self._load()
        arts = store.get("articles") or []
        return {"articles": len(arts), "updated_at": store.get("updated_at") or "",
                "source": store.get("source_key") or store.get("source") or "",
                "latest": [{"id": a.get("id"),
                            "title": a.get("title") if store.get("source_key") == "github"
                            else (a.get("title_zh") or a.get("title")),
                            "url": a.get("url")} for a in arts[:3]],
                "site_dir": str(self.site_dir), "path": "/news/"}


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>{doc_title}</title>
<style>
:root {{
  --bg:#0b1020; --panel:#111a2e; --line:#22304d; --tx:#e8eefc; --muted:#8fa3c8;
  --brand:#7dd3fc; --accent:#a78bfa; --ok:#34d399;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:radial-gradient(1200px 500px at 10% -10%, #1b2a4d 0%, var(--bg) 55%);
  color:var(--tx); font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif; }}
a {{ color:inherit; text-decoration:none; }}
.wrap {{ max-width:1040px; margin:0 auto; padding:0 20px; }}
header.head {{ border-bottom:1px solid var(--line); background:rgba(11,16,32,.7);
  backdrop-filter:blur(8px); position:sticky; top:0; z-index:2; }}
.head .wrap {{ display:flex; align-items:center; justify-content:space-between; height:64px; gap:12px; }}
.brand {{ display:flex; align-items:center; gap:10px; font-weight:800; letter-spacing:.3px; }}
.brand .dot {{ width:9px; height:9px; border-radius:50%; background:var(--ok);
  box-shadow:0 0 0 4px rgba(52,211,153,.15); }}
.brand small {{ font-weight:500; color:var(--muted); font-size:12px; letter-spacing:1.2px; }}
.meta {{ color:var(--muted); font-size:12.5px; text-align:right; }}
.hero {{ margin:34px 0 10px; padding:26px 26px 22px; border:1px solid var(--line);
  border-radius:18px; background:linear-gradient(160deg, rgba(125,211,252,.10), rgba(167,139,250,.06) 60%, transparent); }}
.tag {{ font-size:11px; letter-spacing:2px; color:var(--brand); font-weight:700; }}
.hero h1 {{ font-size:clamp(24px,3.4vw,40px); line-height:1.25; margin:10px 0 8px; }}
.hero h1 a:hover {{ color:var(--brand); }}
.dek {{ color:#c7d5f0; font-size:16px; margin:0 0 14px; }}
.byline {{ color:var(--muted); font-size:12.5px; }}
.src {{ color:var(--brand); margin-left:6px; }}
.orig {{ margin-top:8px; color:#6f83a8; font-size:12px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:16px; margin:18px 0 40px; }}
.card {{ border:1px solid var(--line); border-radius:14px; padding:18px; background:var(--panel);
  transition:transform .15s ease, border-color .15s ease; }}
.card:hover {{ transform:translateY(-2px); border-color:#34507d; }}
.card .meta {{ color:var(--muted); font-size:12px; }}
.pill {{ background:rgba(125,211,252,.12); color:var(--brand); padding:1px 8px; border-radius:999px; }}
.card h2 {{ font-size:17.5px; line-height:1.45; margin:8px 0 6px; }}
.card p {{ color:#b8c7e4; font-size:14px; margin:0 0 10px; }}
.foot {{ color:#6f83a8; font-size:12px; border-top:1px dashed var(--line); padding-top:8px; }}
.empty, .muted {{ color:var(--muted); }}
footer {{ border-top:1px solid var(--line); color:var(--muted); font-size:12.5px; padding:18px 0 40px; }}
footer b {{ color:#a9bde0; }}
@media (max-width:600px) {{ .head .wrap {{ height:auto; padding:12px 20px; flex-direction:column; align-items:flex-start; }} }}
</style>
</head>
<body>
<header class="head"><div class="wrap">
  <div class="brand"><span class="dot"></span>{brand} <small>{brand_sub}</small></div>
  <div class="meta">实时推荐 · {count} {count_label} · 更新于 {shown}<br>页面每 60 秒自动刷新</div>
</div></header>
<main class="wrap">
{hero}
<div class="grid">{cards}{empty}</div>
<footer>
  {footer}<br>
  由 DogBuddy 运营回合自动更新 · 指标 <code>news.articles</code> 可在控制台「运营」页查看。
</footer>
</main>
</body>
</html>
"""
