#!/usr/bin/env python3
# DogBuddyCn — 前端 bundle 拼接脚本
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""按 tools/bundle-order.json 把 public/js/*.js 源文件拼接成单个 <page>.bundle.js。

背景：项目零构建（原生 JS + 浏览器 IIFE 模块，无打包器），但页面逐个 <script>
加载一堆源文件既慢又容易漏登。这里做「最朴素的拼接」：按登记顺序把源文件
以「/* ==== js/<name>.js ==== */」横幅为界连起来，生成 js/<page>.bundle.js，
页面只加载 bundle 一个文件。

约定：
    * bundle-order.json 格式：{ "bundle 文件名": ["源文件相对路径（相对静态根目录）", ...] }
    * **新增源文件必须登记进 bundle-order.json**，否则不会被打进 bundle、页面加载不到
    * 拼接完自动调用 tools/fingerprint.py 给 HTML 引用打 ?v=<hash> 缓存指纹

用法：
    python3 tools/rebuild-bundles.py            # 生成 bundle + 刷新指纹（幂等）
    python3 tools/rebuild-bundles.py --check    # CI 校验：bundle/指纹是否最新，不写文件

静态根目录自动探测：public/（约定名）→ dogbuddy/web/static/（本仓库实际布局）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fingerprint  # noqa: E402  同目录工具模块，构建末尾调用

REPO = Path(__file__).resolve().parents[1]
ORDER_FILE = Path(__file__).resolve().parent / "bundle-order.json"
STATIC_CANDIDATES = ("public", "dogbuddy/web/static")

HEADER = (
    "/* ============================================================\n"
    "   {name}（自动生成，请勿手改）\n"
    "   由 tools/rebuild-bundles.py 按 tools/bundle-order.json 拼接\n"
    "   ============================================================ */\n"
)


def static_root() -> Path:
    """返回前端静态根目录（含 js/ css/ *.html 的那一层）。"""
    for rel in STATIC_CANDIDATES:
        cand = REPO / rel
        if cand.is_dir():
            return cand
    raise SystemExit("rebuild-bundles: 找不到静态根目录（public/ 或 dogbuddy/web/static/）")


def load_order() -> dict:
    """读取 bundle-order.json → {bundle 文件名: [源文件顺序]}。"""
    try:
        order = json.loads(ORDER_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit("rebuild-bundles: 缺少 %s" % ORDER_FILE) from None
    except json.JSONDecodeError as e:
        raise SystemExit("rebuild-bundles: bundle-order.json 不是合法 JSON：%s" % e) from None
    if not isinstance(order, dict) or not order:
        raise SystemExit("rebuild-bundles: bundle-order.json 必须是 {bundle: [源文件]} 非空对象")
    for bundle, sources in order.items():
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            raise SystemExit("rebuild-bundles: %s 的源文件顺序必须是字符串数组" % bundle)
    return order


def build_bundle(root: Path, bundle_name: str, sources: list) -> str:
    """按顺序拼接源文件，每段前置「/* ==== js/<name>.js ==== */」横幅。"""
    parts = [HEADER.format(name=bundle_name)]
    for rel in sources:
        src = root / rel
        if not src.is_file():
            raise SystemExit("rebuild-bundles: 源文件不存在：%s（登记于 %s）"
                             % (src, bundle_name))
        text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
        parts.append("/* ==== %s ==== */\n%s\n" % (rel.replace("\\", "/"), text.rstrip("\n")))
    return "\n".join(parts)


def warn_unregistered(root: Path, order: dict) -> None:
    """提示没登记进任何 bundle 的 js 源文件（约定：新增源文件必须登记）。"""
    registered = {rel for sources in order.values() for rel in sources}
    js_dir = root / "js"
    if not js_dir.is_dir():
        return
    orphans = [p.relative_to(root).as_posix()
               for p in sorted(js_dir.glob("*.js"))
               if not p.name.endswith(".bundle.js")
               and p.relative_to(root).as_posix() not in registered]
    if orphans:
        print("rebuild-bundles: 警告 —— 以下源文件未登记到 tools/bundle-order.json，"
              "不会被打进 bundle：\n  " + "\n  ".join(orphans), file=sys.stderr)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="拼接前端 bundle（并刷新 ?v= 缓存指纹）")
    ap.add_argument("--check", action="store_true",
                    help="只校验 bundle 与指纹是否最新，不写文件；过期则退出码 1")
    args = ap.parse_args(argv)

    root = static_root()
    order = load_order()
    warn_unregistered(root, order)

    stale = []
    for bundle_name, sources in order.items():
        expected = build_bundle(root, bundle_name, sources)
        out = root / bundle_name
        current = out.read_text(encoding="utf-8") if out.is_file() else None
        if current == expected:
            continue
        if args.check:
            stale.append(bundle_name)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(expected, encoding="utf-8")
            print("rebuild-bundles: 已生成 %s（%d 个源文件）" % (bundle_name, len(sources)))

    if args.check and stale:
        print("rebuild-bundles: bundle 过期，请跑 python3 tools/rebuild-bundles.py：%s"
              % ", ".join(stale), file=sys.stderr)
        return 1

    # 构建链收尾：给 HTML 引用打 / 校验 ?v=<hash> 缓存指纹（--check 下不写文件）
    fp_args = ["--root", str(root)] + (["--check"] if args.check else [])
    rc = fingerprint.main(fp_args)
    if rc:
        return rc
    if args.check:
        print("rebuild-bundles: 检查通过（%d 个 bundle）" % len(order))
    else:
        print("rebuild-bundles: 完成（%d 个 bundle）" % len(order))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
