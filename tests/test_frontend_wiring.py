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


# ===========================================================================
# 以下六条是**审查报告第六节**补的：上面那 6 条不变量守得住"四处集合一致"，
# 但守不住"接线真的生效"。报告的变异测试用三种手法实测绕过了它们，全部 6 passed：
#
#   A. 四处齐全但 case 体是空的：加 data-view="phantom" + <section id="view-phantom">
#      + TITLES.phantom + `case 'phantom': break;` → 6 passed
#      （`_render_view_cases()` 只用正则抓 `case 'x':`，不检查 case 体是否真的渲染）
#   B. 导出改名（v0.4.0 前科复现）：`DogBuddyViews.Projects` → `ProjectsX`
#      → 6 passed，`--check` exit=0，点「项目」空白页
#   C. 命名空间打错一个字母：`DogBuddyViews.Decisions` → `DogBuddyViews.Decision`
#      → 6 passed（app.js 是守护式 `if (V.Decisions)`，静默不加载）
#
# 结构性根因：只校验"文件名登记进 bundle-order.json"，**不校验文件里真的导出了
# `DogBuddyViews.X`**；`_render_view_cases()` 只看 `case` 标签，不看 case 体。
# ===========================================================================

def _js_sources() -> list[Path]:
    return [p for p in sorted((STATIC / "js").glob("*.js"))
            if not p.name.endswith(".bundle.js")]


def _exported_namespaces() -> dict[str, str]:
    """`DogBuddyViews.X = ...` → {X: 文件名}。"""
    out: dict[str, str] = {}
    for p in _js_sources():
        for m in re.finditer(r"DogBuddyViews\.([A-Za-z_$][\w$]*)\s*=", _read(p)):
            out.setdefault(m.group(1), p.name)
    return out


def _render_view_bodies() -> str:
    text = _read(APP_JS)
    m = re.search(r"renderView:\s*function[^{]*\{(.*?)\n\s{4}\},", text, re.S)
    assert m, "app.js 里找不到 renderView"
    return m.group(1)


def test_每个视图的_case_体真的调用了渲染函数():
    """绕过手法 A：`case 'x': break;` 这种空壳必须被抓住。

    初版只断言"case 存在"，于是"四处齐全但什么都不做"能全绿。
    """
    body = _render_view_bodies()
    # 按 case 切块：`case 'x': ... 直到下一个 case/default`
    blocks = re.split(r"case\s+'([a-z][a-z0-9-]*)'\s*:", body)
    cases = {}
    for i in range(1, len(blocks) - 1, 2):
        cases[blocks[i]] = blocks[i + 1]
    assert cases, "没抓到任何 case"

    empty = []
    for view, chunk in cases.items():
        # 去掉 break / 注释 / 空白后还在不在
        code = re.sub(r"//[^\n]*", "", chunk)
        code = code.split("case ")[0]
        code = re.sub(r"\bbreak\b|[\s;]", "", code)
        if not code:
            empty.append(view)
    assert not empty, (
        f"这些视图的 case 是空壳（点进去什么都不发生）: {sorted(empty)}。"
        f"审查报告的手法 A 就是这样绕过旧断言的。")


def test_每个视图导出的命名空间真实存在():
    """绕过手法 B/C：`DogBuddyViews.Projects` 改名成 `ProjectsX`，或命名空间
    打错一个字母（`DogBuddyViews.Decision`）——守护式调用会静默不加载。

    初版只校验"文件名登记进 bundle-order.json"，**不校验文件里真的导出了它**。
    """
    exported = _exported_namespaces()
    assert exported, "一个 DogBuddyViews.* 导出都没找到，正则或结构变了"

    # 对每个视图，从 bundle-order.json 里找到它的源文件，确认该文件确实导出了
    # 一个 DogBuddyViews.X（而不是只登记了文件名）
    order = json.loads(_read(BUNDLE_ORDER))
    registered = {Path(p).name for b in order for p in order[b]}
    for name in sorted(registered):
        if not name.startswith("views-") or name.endswith(".bundle.js"):
            continue
        text = _read(STATIC / "js" / name)
        assert re.search(r"DogBuddyViews\.[A-Za-z_$][\w$]*\s*=", text), (
            f"{name} 没有导出任何 DogBuddyViews.<Name>（改名/打错就静默不加载）")


def test_视图_case_体引用的命名空间确实被导出():
    """把 case 体里引用的 `V.X` / `DogBuddyViews.X` 与真实导出对照。"""
    exported = _exported_namespaces()
    body = _render_view_bodies()
    referenced = set(re.findall(r"\bV\.([A-Za-z_$][\w$]*)", body))
    referenced |= set(re.findall(r"DogBuddyViews\.([A-Za-z_$][\w$]*)", body))
    assert referenced, "renderView 里没有引用任何视图命名空间？"
    missing = sorted(referenced - set(exported))
    assert not missing, (
        f"renderView 引用了这些**没有被导出**的命名空间（点进去是空白）: {missing}；"
        f"实际导出的: {sorted(exported)}")


def test_未知_action_有兜底而不是静默():
    """P1-16 的根因：`Windows.action()` 既没有 `params` 分支也没有 `else`，
    于是按钮点下去**静默无反应**。现在必须有 else 出声。"""
    text = _read(STATIC / "js" / "views-main.js")
    m = re.search(r"action:\s*function[^{]*\{(.*?)\n\s{4}\},", text, re.S)
    assert m, "找不到 Windows.action"
    body = m.group(1)
    assert re.search(r"\}\s*else\s*\{", body), \
        "Windows.action 必须有 else 兜底（否则未接线的按钮静默无反应）"


def test_指挥链接口有前端调用者():
    """P1-2：`GET /api/projects/{id}/chain` 后端一直在，但前端零调用者。"""
    text = _read(STATIC / "js" / "api.js")
    assert "cmdChain" in text, "api.js 缺少指挥链接口客户端"
    assert re.search(r"'/api/projects/'\s*\+\s*enc\(id\)\s*\+\s*'/chain'", text), \
        "cmdChain 必须指向 /api/projects/{id}/chain"
    views = _read(STATIC / "js" / "views-projects.js")
    assert "cmdChain" in views and "cmd-chain" in views, \
        "views-projects.js 必须真的调用它（否则还是只能 curl 的孤儿接口）"


def test_参数面板真的有调用点():
    """P1-16：`Params.openPanel` 初版只有定义、零调用点。"""
    main = _read(STATIC / "js" / "views-main.js")
    assert re.search(r"Params\.openPanel\s*\(", main), \
        "views-main.js 必须真的调用 Params.openPanel（否则那个按钮点了没反应）"
    assert "'params'" in main, "Windows.action 必须有 params 分支"


def test_主管角色在控制台可选():
    """P1-2：主管是 Batch 9 的核心能力，但前端 `supervisor` 出现 0 次。"""
    main = _read(STATIC / "js" / "views-main.js")
    assert 'value="supervisor"' in main, "新建工位的角色下拉必须能选 supervisor"
    assert 'value="admin"' not in main, (
        "不该再提供 admin 工位（v2 只有一个管理员；那个工位是空壳且口径冲突）")
    projects = _read(STATIC / "js" / "views-projects.js")
    assert "owner_supervisor_id" in projects and "prj-f-sup" in projects, \
        "项目表单必须有『指定主管』字段（原来只能手工 PATCH）"

