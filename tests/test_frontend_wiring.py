# DogBuddyCn — 前端接线一致性（防止"视图写了但页面进不去"）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""前端零构建 + 单 bundle 的代价：没有任何编译期检查告诉我们"视图接通了没有"。

v0.4.0 交付就栽在这里：`views-capabilities.js` / `views-decisions.js` / `views-ledger.js`
三个视图都写完了，但 `index.html` 缺 section、缺导航项，`app.js` 的 `renderView` 缺 case
——点进去是空白页。文档却把对应批次标为"完成"。

本文件把 FRONTEND.md §2 那条"新增视图必须同步改三处"的规则变成可执行的断言：

1. `index.html` 的 `data-view="x"` 集合 == `id="view-x"` 集合（导航与页面一一对应）
2. `app.js` 的 `TITLES` 键集合 == 导航集合（标题栏不会漏）
3. `renderView` 的 `case` 覆盖全部导航项（点了不会什么都不发生）
4. `bundle-order.json` 登记了所有 `js/*.js` 源文件（否则不会被拼进 bundle = 页面加载不到）
5. `index.html` 只从 bundle 加载，且所有静态资源带 `?v=` 指纹（否则升级后浏览器吃旧缓存）
6. 每个被引用的 `#ic-*` 图标都有定义（否则按钮渲染成空白）

任何一条失败都说明 UI 没有闭环，**不要让这样的提交进门**。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "dogbuddy" / "web" / "static"
INDEX = STATIC / "index.html"
APP_JS = STATIC / "js" / "app.js"
BUNDLE_ORDER = REPO / "tools" / "bundle-order.json"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _nav_views() -> set[str]:
    return set(re.findall(r'data-view="([a-z][a-z0-9-]*)"', _read(INDEX)))


def _sections() -> set[str]:
    return set(re.findall(r'id="view-([a-z][a-z0-9-]*)"', _read(INDEX)))


def _titles() -> set[str]:
    text = _read(APP_JS)
    m = re.search(r"var TITLES\s*=\s*\{(.*?)\};", text, re.S)
    assert m, "app.js 里找不到 TITLES"
    return set(re.findall(r"([a-z][a-z0-9-]*)\s*:", m.group(1)))


def _render_view_cases() -> set[str]:
    text = _read(APP_JS)
    m = re.search(r"renderView:\s*function[^{]*\{(.*?)\n\s{4}\},", text, re.S)
    assert m, "app.js 里找不到 renderView"
    return set(re.findall(r"case\s+'([a-z][a-z0-9-]*)'\s*:", m.group(1)))


def test_every_nav_item_has_a_section_and_vice_versa():
    nav, sections = _nav_views(), _sections()
    assert nav == sections, (
        f"导航与页面不匹配。导航有但无页面: {sorted(nav - sections)}；"
        f"有页面但导航进不去: {sorted(sections - nav)}")


def test_titles_cover_every_view():
    missing = _nav_views() - _titles()
    assert not missing, f"TITLES 缺少这些视图的标题: {sorted(missing)}"


def test_render_view_handles_every_nav_item():
    missing = _nav_views() - _render_view_cases()
    assert not missing, (
        f"renderView 没有这些视图的 case（点进去是空白）: {sorted(missing)}；"
        f"已处理: {sorted(_render_view_cases())}")


def test_index_bundle_registers_every_js_source():
    order = json.loads(_read(BUNDLE_ORDER))
    bundles = [k for k in order if k.endswith(".bundle.js")]
    assert bundles, "bundle-order.json 里没有 bundle 条目"
    registered = {Path(p).name for b in bundles for p in order[b]}
    sources = {p.name for p in (STATIC / "js").glob("*.js")
               if not p.name.endswith(".bundle.js")}
    unregistered = sources - registered
    assert not unregistered, (
        f"这些源文件没登记进 bundle-order.json，不会被加载: {sorted(unregistered)}")
    stale = registered - sources
    assert not stale, f"bundle-order.json 登记了不存在的文件: {sorted(stale)}"


def test_index_loads_only_the_bundle_with_cache_fingerprints():
    html = _read(INDEX)
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', html)
    assert scripts == [s for s in scripts if "index.bundle.js" in s], (
        f"index.html 应只加载 bundle，实际: {scripts}")
    for url in scripts + re.findall(r'<link[^>]*href="(/assets/[^"]+)"', html):
        assert "?v=" in url, f"静态资源缺少缓存指纹（升级后浏览器会吃旧缓存）: {url}"


def test_every_referenced_icon_is_defined():
    index = _read(INDEX)
    defined = set(re.findall(r'<symbol id="ic-([a-z0-9-]+)"', index))
    used: set[str] = set()
    for path in [INDEX, *sorted(p for p in (STATIC / "js").glob("*.js")
                                if not p.name.endswith(".bundle.js"))]:
        used |= set(re.findall(r"#ic-([a-z0-9-]+)", _read(path)))
    missing = used - defined
    assert not missing, f"引用了未定义的图标（会渲染成空白按钮）: {sorted(missing)}"
