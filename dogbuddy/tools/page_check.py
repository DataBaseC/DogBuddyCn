# DogBuddyCn — static page check (L4 Capability)
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""page_check — 页面体检：给测试员/前端一个"能自己验页面"的工具。

为什么是**静态检查**而不是真浏览器（2026-10-22 实测环境）：
    ROADMAP 37.6 设想用 headless firefox 截图，但盒子上 `/usr/bin/firefox` 只是
    **snap 的空壳**（`requires the firefox snap to be installed`），没有 chromium，
    也没有 playwright/selenium/weasyprint —— 真渲染这条路当前环境走不通。
    对纯文本模型来说，截图像素也读不懂；**能读懂的信号是结构**：标签是否闭合、
    引用的 css/js/图片是否存在、a11y 基本项、重复 id、内联颜色对比度。
    这些用 stdlib 就能算，跑一次几十毫秒，正好当"测试员的浏览器沙盒"的替代品。

权限：只**读**工作区文件（required_scope 不设，走 read 权限），不写不改。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .registry import ToolContext, ToolResult

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_CONTAINERS = {"div", "section", "article", "main", "header", "footer", "nav",
               "aside", "ul", "ol", "li", "p", "h1", "h2", "h3", "h4", "h5",
               "h6", "form", "table", "tr", "td", "th", "span", "a", "button",
               "label", "figure", "blockquote", "pre", "code", "template"}
_HEX_RX = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")


class _Audit(HTMLParser):
    """一次遍历收齐：标签栈、引用、图片 alt、id、颜色。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.unclosed: list[str] = []
        self.mismatched: list[str] = []
        self.refs: list[str] = []
        self.ids: list[str] = []
        self.img_no_alt = 0
        self.colors: list[str] = []
        self.has_title = False
        self.has_desc = False
        self.has_viewport = False
        self.lang = ""
        self.line = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        if tag == "title":
            self.has_title = True
        if tag == "meta":
            name = a.get("name", "").lower()
            if name == "description" and a.get("content", "").strip():
                self.has_desc = True
            if name == "viewport":
                self.has_viewport = True
        if tag in ("link", "script", "img", "source", "iframe"):
            for key in ("href", "src"):
                v = a.get(key, "").strip()
                if v and not v.startswith(("http://", "https://", "data:", "#", "mailto:", "//")):
                    self.refs.append(v)
        if tag == "img" and "alt" not in a:
            self.img_no_alt += 1
        if a.get("id"):
            self.ids.append(a["id"])
        for v in a.values():
            self.colors.extend(_HEX_RX.findall(v))
        if a.get("style"):
            self.colors.extend(_HEX_RX.findall(a["style"]))
        if tag in _CONTAINERS:
            self.stack.append((tag, self.line))
        elif tag not in _VOID and tag not in ("html", "head", "body"):
            self.stack.append((tag, self.line))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag not in _CONTAINERS and tag in _VOID:
            return
        # html/head/body 不进栈（它们是隐式结构，HTML5 允许省略），关闭时也不能报"多余"
        if tag in ("html", "head", "body") and not any(t == tag for t, _ in self.stack):
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                dropped = self.stack[i + 1:]
                if dropped:
                    self.mismatched.append(
                        f"<{tag}> 关闭时仍开着 " +
                        ", ".join(f"<{t}>(L{n})" for t, n in dropped[:3]))
                del self.stack[i:]
                return
        self.mismatched.append(f"多余的 </{tag}>")


def _luminance(hex6: str) -> float:
    r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _expand(h: str) -> str:
    return h * 2 if len(h) == 3 else h


def check_one(f: Path, base: Path) -> list[str]:
    """返回问题清单（空 = 没发现问题）。"""
    html = f.read_text(encoding="utf-8", errors="replace")
    p = _Audit()
    try:
        p.feed(html)
        p.close()
    except Exception as e:  # noqa: BLE001 — 解析崩了就是最严重的问题
        return [f"HTML 解析失败：{type(e).__name__}: {e}"]

    problems: list[str] = []
    if not p.has_title:
        problems.append("缺 <title>")
    if not p.has_desc:
        problems.append('缺 <meta name="description">')
    if not p.has_viewport:
        problems.append('缺 <meta name="viewport">（移动端会按 980px 缩放）')
    if not p.lang:
        problems.append("<html> 缺 lang 属性")
    for ref in p.refs:
        target = (f.parent / ref.split("?")[0].split("#")[0]).resolve()
        if not target.exists():
            problems.append(f"引用的本地资源不存在：{ref}")
        try:
            target.relative_to(base)
        except ValueError:
            problems.append(f"引用跑到项目外了：{ref}")
    if p.img_no_alt:
        problems.append(f"{p.img_no_alt} 个 <img> 没有 alt（读屏软件没法读）")
    for m in p.mismatched:
        problems.append(f"标签不配对：{m}")
    for tag, line in p.stack:
        problems.append(f"<{tag}> 未闭合（自 L{line} 起）")
    dup = sorted({i for i in p.ids if p.ids.count(i) > 1})
    if dup:
        problems.append(f"重复的 id：{dup[:5]}")

    # 对比度：只在"页面里同一份文件出现前后景色"时给提示，不误判为错误
    colors = list(dict.fromkeys(_expand(c.lower()) for c in p.colors))
    if len(colors) >= 2:
        worst = None
        for i in range(len(colors)):
            for j in range(i + 1, len(colors)):
                ratio = _contrast(colors[i], colors[j])
                if worst is None or ratio < worst[0]:
                    worst = (ratio, colors[i], colors[j])
        if worst and worst[0] < 3.0:
            problems.append(
                f"两处颜色对比度偏低：#{worst[1]} vs #{worst[2]} = {worst[0]:.2f}:1"
                f"（WCAG 正文要 ≥4.5:1；如果它们是前景/背景就值得看一眼）")
    return problems


class PageCheckTool:
    name = "page_check"
    description = ("页面体检（静态）：检查工作区里的 HTML —— 标签闭合、本地 css/js/图片引用是否存在、"
                   "title/description/viewport/lang、img alt、重复 id、内联颜色对比度。"
                   "当前服务器没有可用的浏览器内核，所以这里不做像素渲染/截图。")
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "工作区内的 HTML 文件或目录（相对路径，缺省 = 工作区根）"},
            "max_files": {"type": "integer", "description": "最多检查多少个文件（默认 30）",
                          "minimum": 1, "maximum": 200},
        },
        "required": [],
    }
    permission = "read"
    required_scope = ""

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .file_ops import _resolve_in_workspace  # 复用同一套"不许越界"的守卫

        base = Path(str(ctx.workspace)).resolve()
        try:
            target = _resolve_in_workspace(ctx, args.get("path") or ".")
        except ValueError as e:
            return ToolResult.failure(str(e))
        limit = max(1, min(int(args.get("max_files") or 30), 200))
        if not target.exists():
            return ToolResult.failure(f"路径不存在：{args.get('path')}")

        files = [target] if target.is_file() else sorted(target.rglob("*.html"))[:limit]
        files = [f for f in files if f.is_file()]
        if not files:
            return ToolResult.success("没有找到 .html 文件", checked=0, problems=0)

        lines: list[str] = []
        total = 0
        for f in files:
            try:
                problems = check_one(f, base)
            except OSError as e:
                problems = [f"读不了：{e}"]
            rel = f.relative_to(base) if f.is_relative_to(base) else f
            total += len(problems)
            if problems:
                lines.append(f"✗ {rel}  —— {len(problems)} 个问题")
                lines.extend(f"    - {x}" for x in problems[:12])
                if len(problems) > 12:
                    lines.append(f"    - ...另有 {len(problems) - 12} 条")
            else:
                lines.append(f"✓ {rel}  通过")
        head = f"检查 {len(files)} 个 HTML，共 {total} 个问题"
        return ToolResult.success(head + "\n" + "\n".join(lines),
                                  checked=len(files), problems=total)


def all_tools() -> list[Any]:
    return [PageCheckTool()]
