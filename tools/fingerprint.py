#!/usr/bin/env python3
# DogBuddyCn — 前端静态资源缓存指纹（?v=<hash>）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""资源缓存指纹：给静态资源引用打上 ?v=<hash>，强制浏览器缓存刷新。

为什么需要它（T1.3/T1.4）：
    用户反馈「改了按钮不显示 / 看到的还是老页面」——浏览器把 js/css 缓存住了，
    升级后 URL 没变就继续用旧缓存。给每个资源算内容哈希并拼进引用 URL
    （?v=<sha256 前 10 位>），文件内容一变 URL 就变，浏览器只能重新拉取，
    彻底告别「硬刷新才生效」。

做什么：
    1. 扫描静态根目录下的 js/*.js（含 *.bundle.js）、css/*.css、assets/**（若有）；
    2. 对每个文件算 sha256 前 10 位作为指纹；
    3. 重写 *.html 里所有对这些资源的 src/href 引用为 ?v=<hash>
       （替换已有的 ?v=xxx 或追加，其余查询参数保留）。
    只按「引用 URL 的路径尾部」匹配资源，兼容 /assets/js/app.js 这类带前缀的挂载路径。

用法：
    python3 tools/fingerprint.py            # 重写 HTML（幂等，可反复跑）
    python3 tools/fingerprint.py --check    # 只校验指纹是否新鲜，不写文件（CI 用）

由 tools/rebuild-bundles.py 在拼接完 bundle 后自动调用（--check 模式同样只校验）。
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 静态根目录：优先 public/（约定名），本仓库实际布局是 dogbuddy/web/static/
# （FastAPI 把它挂载到 /assets）。两处都有时以 public/ 为准。
STATIC_CANDIDATES = ("public", "dogbuddy/web/static")

# src="..." / href="..."（单双引号都认；不解析内联 <style>/url()，那不属于引用清单）
_REF_RE = re.compile(
    r'(?P<prefix>\b(?:src|href)\s*=\s*)(?P<q>["\'])(?P<url>[^"\']*)(?P=q)'
)


def static_root() -> Path:
    """返回前端静态根目录（含 js/ css/ 与 *.html 的那一层）。"""
    for rel in STATIC_CANDIDATES:
        cand = REPO / rel
        if cand.is_dir():
            return cand
    raise SystemExit("fingerprint: 找不到静态根目录（public/ 或 dogbuddy/web/static/）")


def sha10(path: Path) -> str:
    """资源指纹：文件内容 sha256 的前 10 位十六进制。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:10]


def collect_files(root: Path) -> dict:
    """收集受控资源 → {相对路径(posix): 指纹}。

    覆盖：js/*.js（源文件与 bundle 都算，谁被引用谁受益）、css/*.css、assets/**（若有）。
    """
    mapping: dict = {}
    for sub, pattern, recursive in (("js", "*.js", False),
                                    ("css", "*.css", False),
                                    ("assets", "*", True)):
        base = root / sub
        if not base.is_dir():
            continue
        paths = base.rglob("*") if recursive else base.glob(pattern)
        for p in sorted(paths):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            mapping[rel] = sha10(p)
    return mapping


def _match_key(url: str, mapping: dict) -> str | None:
    """把引用 URL 匹配到资源相对路径（按路径尾部匹配，容忍 /assets/ 之类的挂载前缀）。"""
    path = url.split("?", 1)[0].split("#", 1)[0]
    if path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if not path or "://" in path or path.startswith("data:"):
        return None
    if path in mapping:
        return path
    for key in sorted(mapping, key=len, reverse=True):  # 长键优先，避免子串误配
        if path.endswith("/" + key):
            return key
    return None


def _with_fingerprint(url: str, digest: str) -> str:
    """给 URL 追加 / 替换 ?v=<hash>；已有其他查询参数时保留。"""
    base, sep, query = url.partition("?")
    if not sep:
        return base + "?v=" + digest
    keep = [p for p in query.split("&")
            if p and p.split("=", 1)[0].strip().lower() != "v"]
    return base + "?" + "&".join(keep + ["v=" + digest])


def rewrite_html(text: str, mapping: dict) -> str:
    """重写 HTML 文本里所有命中受控资源的 src/href 引用；无命中则原样返回。"""

    def repl(m: re.Match) -> str:
        url = m.group("url")
        key = _match_key(url, mapping)
        if key is None:
            return m.group(0)
        return m.group("prefix") + m.group("q") + _with_fingerprint(url, mapping[key]) + m.group("q")

    return _REF_RE.sub(repl, text)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="静态资源缓存指纹 ?v=<hash>（幂等可重跑）")
    ap.add_argument("--check", action="store_true",
                    help="只校验 HTML 指纹是否新鲜，不写文件；过期则退出码 1")
    ap.add_argument("--root", default=None, help="静态根目录（默认自动探测）")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else static_root()
    mapping = collect_files(root)
    html_files = sorted(root.glob("*.html"))
    if not html_files:
        print("fingerprint: 没有找到 *.html，跳过", file=sys.stderr)
        return 0

    stale = []
    for html in html_files:
        original = html.read_text(encoding="utf-8")
        rewritten = rewrite_html(original, mapping)
        if rewritten == original:
            continue
        if args.check:
            stale.append(html.name)
        else:
            html.write_text(rewritten, encoding="utf-8")
            print("fingerprint: 已更新 %s" % html.name)

    if args.check and stale:
        print("fingerprint: 指纹过期，请跑 python3 tools/rebuild-bundles.py：%s"
              % ", ".join(stale), file=sys.stderr)
        return 1
    if args.check:
        print("fingerprint: 指纹检查通过（%d 个资源，%d 个页面）"
              % (len(mapping), len(html_files)))
    else:
        print("fingerprint: 完成（%d 个资源，%d 个页面）"
              % (len(mapping), len(html_files)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
