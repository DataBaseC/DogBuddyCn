# DogBuddyCn — 待决卡片必须可交互（v0.10.53）
# Copyright (C) DogBuddyCn contributors — AGPL-3.0
"""owner 实测 bug：「待决的前端，无论点击批准按钮还是手动打字回复之后，都没有反应」。

真因（不是样式问题，是**渲染少了一个开始标签**）：
`views-decisions.js::card()` 的普通分支返回的是
    `return head + '<div class="dec-question">…' + … + '</div>'`
—— `head` 以 `<div class="dec-head">…</div>` 开头且自带闭合，返回串里**从来没有
卡片根元素 `<div class="dec-card" data-dec-id>`**，末尾那个 `</div>` 是多余的
（还把 `#dec-list` 提前闭合）。而 `bind()` 靠 `U.$$('.dec-card')` 找人：
一个都找不到 → 所有按钮没有监听器 → 点批准 / 点回答 / 回车全部无反应。

这里用 node 真跑一遍 `card()`，钉住三条：
  ① 两种分支的输出都以 `<div class="dec-card" data-dec-id="…">` 开头；
  ② div 开闭数量配平（不再有游离的 `</div>`）；
  ③ 审批类（confirm / server_exec / options 恰为「批准/拒绝」）出真按钮
     `dec-approve` / `dec-reject`，而不是只能"填进输入框"的 chip。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "dogbuddy/web/static/js/views-decisions.js"

HARNESS = r"""
global.window = global;
global.document = { getElementById: function () { return null; }, addEventListener: function () {} };
global.UI = {
  esc: function (s) {
    return String(s === undefined || s === null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  },
  relTime: function () { return '刚刚'; },
  countdown: function () { return '9m 59s'; },
  icon: function () { return '<svg class="ic"></svg>'; },
  $: function () { return null; },
  $$: function () { return []; },
  toast: function () {}
};
global.API = {};
global.SSE = { on: function () {} };
require(SRC);
var D = global.DogBuddyViews.Decisions;
var base = { window_id: 'w1', question: '要不要执行？', status: 'pending',
             created_at: '2026-10-24T00:00:00Z', expires_at: '2026-10-24T00:30:00Z' };
function mk(over) {
  var o = {}, k;
  for (k in base) o[k] = base[k];
  for (k in over) o[k] = over[k];
  return D.card(o);
}
var out = {
  plain:   mk({ id: 11, source: 'ask_user', options: [] }),
  exec:    mk({ id: 12, source: 'server_exec', options: ['批准', '拒绝'] }),
  confirm: mk({ id: 13, source: 'confirm', options: ['批准', '拒绝'] }),
  options: mk({ id: 14, source: 'ask_user', options: ['批准', '拒绝'] }),
  custom:  mk({ id: 15, source: 'ask_user', options: ['A 方案', 'B 方案'] })
};
process.stdout.write(JSON.stringify(out));
""".replace("SRC", json.dumps(str(SRC)))


def _render() -> dict[str, str] | None:
    node = shutil.which("node")
    if not node:
        return None
    proc = subprocess.run([node, "-e", HARNESS], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def rendered():
    data = _render()
    if data is None:
        pytest.skip("没有 node，跳过 JS 渲染断言")
    return data


@pytest.mark.parametrize("key", ["plain", "exec", "confirm", "options", "custom"])
def test_card_always_has_a_root_element(rendered, key):
    html = rendered[key]
    assert html.startswith('<div class="dec-card'), (
        f"{key} 分支没有卡片根元素（就是 owner 遇到的「点了没反应」）: {html[:80]}")
    assert 'data-dec-id="' in html.split(">")[0], "根元素必须带 data-dec-id，bind() 靠它找人"


@pytest.mark.parametrize("key", ["plain", "exec", "confirm", "options", "custom"])
def test_div_tags_are_balanced(rendered, key):
    html = rendered[key]
    opens = html.count("<div")
    closes = html.count("</div>")
    assert opens == closes, f"{key} 分支 div 不配平：{opens} 开 / {closes} 闭"


def test_approval_cards_get_real_buttons(rendered):
    """审批类（confirm / server_exec / options 恰为「批准/拒绝」）必须是真按钮。

    以前这些全落到普通卡：chips 点一下只是把"批准"填进输入框，用户以为点了批准，
    实际什么都没提交 —— 加上根元素缺失，就表现成"点了没反应"。
    """
    for key in ("exec", "confirm", "options"):
        html = rendered[key]
        assert "dec-approve" in html and "dec-reject" in html, \
            f"{key} 是审批类，必须给「批准/拒绝」真按钮（chip 只会填输入框）"


def test_plain_question_keeps_input_and_answer_button(rendered):
    html = rendered["plain"]
    assert "dec-input" in html and "dec-answer" in html


def test_custom_options_still_render_chips(rendered):
    """非审批类（A/B 方案）保持原来的 chip 填入 + 回答，不误判成审批。"""
    html = rendered["custom"]
    assert "dec-opt" in html and "dec-approve" not in html
    assert "dec-input" in html and "dec-answer" in html


def test_bind_falls_back_when_class_missing():
    js = SRC.read_text(encoding="utf-8")
    assert "if (!cards.length) cards = U.$$('[data-dec-id]');" in js, \
        "bind 需要选择器兜底，避免再因为一个 class 写错就整页按钮失灵"
