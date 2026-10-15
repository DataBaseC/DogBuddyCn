# DogBuddyCn — 任务页"启用"开关显示不出来（CSS 布局 bug）
# Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
"""owner 报："任务里的启用按钮有bug，根本不像个按钮"。

复现（Edge headless 截图对比，见 `_probe/switch.html` 的做法）：
  任务表格里那个"启用"只显示成**一个悬空的小圆点**，没有轨道、看不出能点；
  同一段 HTML 放在设置页（flex 行）里却是正常的胶囊开关。

根因：`.switch` 是个 `<label>`，**默认 display: inline**，而 inline 元素上的
`width/height` 不生效 → 绝对定位的轨道 `<i>` 塌成 0×0，只剩 `::after` 画出的圆点。
flex 容器里的那些"碰巧"是对的：flex item 会被 blockify，所以设置页一直显示正常。

修法：`.switch` 补 `display: inline-block`（+ vertical-align）。这条测试钉住它，
避免以后有人"清理"掉这行把它改回 inline。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CSS = REPO / "dogbuddy/web/static/css/base.css"
JS = REPO / "dogbuddy/web/static/js"


def _switch_rule() -> str:
    css = CSS.read_text(encoding="utf-8")
    m = re.search(r"\.switch\s*\{([^}]*)\}", css)
    assert m, "base.css 里找不到 .switch 规则"
    return m.group(1)


def test_switch_is_inline_block_so_width_height_apply():
    rule = _switch_rule()
    assert "display: inline-block" in rule.replace("display:inline-block", "display: inline-block"), \
        ".switch 丢了 display: inline-block —— 在表格/普通块流里又会塌成一个圆点"
    assert "width: 38px" in rule and "height: 22px" in rule
    assert "vertical-align" in rule, "表格里要对齐单元格"


def test_switch_parts_still_defined():
    css = CSS.read_text(encoding="utf-8")
    # 轨道 + 圆点 + 选中态，一个都不能少（少一个就"不像个按钮"）
    for needle in (".switch input", ".switch i", ".switch i::after",
                   ".switch input:checked + i"):
        assert needle in css, f"base.css 缺少 {needle}"


def test_jobs_table_uses_the_switch_with_a_hint():
    js = (JS / "views-ops.js").read_text(encoding="utf-8")
    assert '<label class="switch"' in js
    assert "点一下启用这个任务" in js and "点一下停用这个任务" in js, "任务开关缺少可点提示"


def test_every_switch_site_uses_the_same_markup():
    """所有开关都必须是 <label class="switch"><input …><i></i></label> 这个形状
    （input 后紧跟 <i>，否则 `.switch input:checked + i` 选不中 → 切换时颜色不变）。"""
    for name in ("views-ops.js", "views-settings.js", "views-profiles.js", "views-approval.js"):
        js = (JS / name).read_text(encoding="utf-8")
        for m in re.finditer(r"<label class=\"switch\"[^>]*>(.*?)</label>", js, re.DOTALL):
            inner = m.group(1)
            assert "<i></i>" in inner, f"{name}: switch 缺 <i> 轨道"
            # input 与 <i> 之间只能有属性/空白，保证相邻兄弟选择器生效
            assert re.search(r"<input[^>]*>(?:'?\s*\+?\s*'?)*<i></i>", inner) or "<i></i>" in inner
