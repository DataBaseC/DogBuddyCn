#!/usr/bin/env python3
# DogBuddyCn — 前端静态自检（UI 缺陷的第一道防线）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""扫描 Web 控制台，找出"用了但没定义"的 CSS 类 / SVG 图标。

控制台是零构建的原生 JS，没有编译期检查，所以"元素存在但没有样式/图标"这类
缺陷只能靠静态比对发现。改动 `web/static/**` 后请跑一次：

    python scripts/audit_ui.py            # 报告
    python scripts/audit_ui.py --strict   # 有发现时非零退出（可挂到 CI）

注意：本脚本只能覆盖**静态写在 class="..." / #ic-xxx 里的**名字。
动态拼接的类名（如 `'badge ' + (x ? 'badge-a' : 'badge-b')`）不会被统计，
所以 "报告为空" ≠ "UI 没问题"，运行时缺陷仍需人工/截图复现。
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATIC = REPO / "dogbuddy" / "web" / "static"

# 动态拼接或第三方注入的名字，不该出现在静态扫描结果里
IGNORE_CLASSES = {"cls", "key", "role", "class", "wide"}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _js_sources() -> list[Path]:
    """前端**源**文件。`*.bundle.js` 是 tools/rebuild-bundles.py 的拼接生成物，
    必须排除：否则同一个类名/图标会被计两次，报告也不再反映"源码里写了什么"。
    真正的前端正确性由 `tools/rebuild-bundles.py --check` 保证（指纹与 bundle 是否最新）。"""
    return sorted(p for p in (STATIC / "js").glob("*.js") if not p.name.endswith(".bundle.js"))


def scan_classes() -> tuple[dict[str, int], set[str]]:
    sources = [STATIC / "index.html", *_js_sources()]
    css = "\n".join(_read(p) for p in sorted((STATIC / "css").glob("*.css")))
    defined = set(re.findall(r"\.([a-zA-Z][a-zA-Z0-9_-]*)", css))

    used: collections.Counter[str] = collections.Counter()
    for path in sources:
        text = _read(path)
        for m in re.finditer(r'class="([^"]*)"', text):
            for token in m.group(1).split():
                if re.fullmatch(r"[a-z][a-z0-9-]*", token):
                    used[token] += 1
        for m in re.finditer(r"classList\.(?:add|toggle|remove)\('([a-z][a-z0-9-]*)'", text):
            used[m.group(1)] += 1
    return used, defined


def scan_icons() -> tuple[set[str], set[str]]:
    index_path = STATIC / "index.html"
    index = _read(index_path)
    defined = set(re.findall(r'<symbol id="ic-([a-z0-9-]+)"', index))
    used: set[str] = set()
    for path in [index_path, *_js_sources()]:
        used |= set(re.findall(r"#ic-([a-z0-9-]+)", _read(path)))
    return used, defined


def main() -> int:
    ap = argparse.ArgumentParser(description="audit console CSS classes and icons")
    ap.add_argument("--strict", action="store_true", help="exit 1 when anything is missing")
    args = ap.parse_args()

    problems = 0

    used, defined = scan_classes()
    missing = sorted(c for c in used if c not in defined and c not in IGNORE_CLASSES)
    print(f"[CSS] 静态使用 {len(used)} 个 class，其中 {len(missing)} 个没有定义")
    for c in missing:
        print(f"    ★ .{c}  (出现 {used[c]} 次)")
        problems += 1

    icons_used, icons_defined = scan_icons()
    icons_missing = sorted(icons_used - icons_defined)
    print(f"[图标] 引用 {len(icons_used)} 个，#ic- 定义 {len(icons_defined)} 个，"
          f"缺失 {len(icons_missing)} 个")
    for i in icons_missing:
        print(f"    ★ #ic-{i}")
        problems += 1

    print()
    if problems:
        print(f"共 {problems} 处可疑。缺失的 CSS 类会造成\"元素在但没有样式\"，"
              f"缺失的图标会渲染成空白按钮。")
    else:
        print("静态扫描未发现问题。注意：动态类名与运行时错误不受此检查覆盖。")
    return 1 if (problems and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
