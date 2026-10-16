/* ============================================================
   DogBuddyCn 控制台 · ui.js
   通用 UI 基础件：DOM 工具 / 格式化 / 图标 / Toast / Modal / 确认框
   骨架屏 / 空状态
   ============================================================ */
(function (global) {
  'use strict';

  /* ---------------- DOM 小工具 ---------------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function el(html) {
    var t = document.createElement('template');
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  /* ---------------- 图标 ---------------- */
  function icon(name, cls) {
    return '<svg class="ic ' + (cls || '') + '" aria-hidden="true"><use href="#ic-' + name + '"/></svg>';
  }

  /* ---------------- 时间 / 数字格式化 ---------------- */
  function parseTime(v) {
    if (!v) return null;
    if (typeof v === 'number') return new Date(v < 1e12 ? v * 1000 : v);
    var s = String(v).trim();
    if (/^\d{4}-\d{2}-\d{2}T/.test(s) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) {
      s = s.replace(' ', 'T');
      return new Date(s);
    }
    s = s.replace(' ', 'T');
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function fmtTime(v) {
    var d = parseTime(v);
    if (!d) return '—';
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
      ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }
  function fmtClock(v) {
    var d = parseTime(v);
    if (!d) return '—';
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }
  function fmtDate(v) {
    var d = parseTime(v);
    if (!d) return '—';
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  function relTime(v) {
    var d = parseTime(v);
    if (!d) return '—';
    var diff = Date.now() - d.getTime();
    var future = diff < 0;
    diff = Math.abs(diff);
    var s = Math.floor(diff / 1000);
    var txt;
    if (s < 30) txt = '刚刚';
    else if (s < 60) txt = s + ' 秒';
    else if (s < 3600) txt = Math.floor(s / 60) + ' 分钟';
    else if (s < 86400) txt = Math.floor(s / 3600) + ' 小时';
    else txt = Math.floor(s / 86400) + ' 天';
    return future ? (txt + '后') : (txt + '前');
  }
  function countdown(v) {
    var d = parseTime(v);
    if (!d) return '—';
    var diff = d.getTime() - Date.now();
    if (diff <= 0) return '待调度';
    var s = Math.floor(diff / 1000);
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
    if (s < 86400) return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
    return Math.floor(s / 86400) + 'd ' + Math.floor((s % 86400) / 3600) + 'h';
  }
  function fmtSize(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
    return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }

  /* ---------------- Toast ---------------- */
  var TOAST_ICONS = { success: 'check', error: 'alert', warn: 'alert', info: 'info' };
  function toast(type, msg, ttl) {
    var root = document.getElementById('toast-root');
    if (!root) return;
    var node = el(
      '<div class="toast toast-' + (type || 'info') + '">' +
      '<span class="toast-icon">' + icon(TOAST_ICONS[type] || 'info', 'sm') + '</span>' +
      '<div class="toast-msg">' + esc(msg) + '</div>' +
      '<button class="toast-close" aria-label="关闭">' + icon('x', 'sm') + '</button>' +
      '</div>'
    );
    function close() {
      if (!node.parentNode) return;
      node.classList.add('out');
      setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); }, 180);
    }
    node.querySelector('.toast-close').addEventListener('click', close);
    root.appendChild(node);
    setTimeout(close, ttl || 3600);
    return close;
  }

  /* ---------------- Modal ---------------- */
  var modalStack = [];
  function modal(opts) {
    opts = opts || {};
    var root = document.getElementById('modal-root');
    var mask = el('<div class="modal-mask"></div>');
    var box = el(
      '<div class="modal' + (opts.wide ? ' wide' : '') + '" role="dialog" aria-modal="true">' +
      '<div class="modal-head"><h3 class="modal-title">' + esc(opts.title || '') + '</h3>' +
      '<button class="icon-btn modal-x" aria-label="关闭">' + icon('x', 'sm') + '</button></div>' +
      '<div class="modal-body"></div>' +
      '<div class="modal-foot"></div>' +
      '</div>'
    );
    var body = box.querySelector('.modal-body');
    var foot = box.querySelector('.modal-foot');
    if (typeof opts.body === 'string') body.innerHTML = opts.body;
    else if (opts.body) body.appendChild(opts.body);
    (opts.buttons || []).forEach(function (b) {
      var btn = el('<button class="btn ' + (b.cls || 'btn-ghost') + '">' + esc(b.label) + '</button>');
      btn.addEventListener('click', function () {
        if (b.onClick) {
          var r = b.onClick(body, api);
          if (r === false) return;
        }
        if (b.keepOpen) return;
        api.close();
      });
      foot.appendChild(btn);
    });
    if (!(opts.buttons || []).length) foot.remove();
    mask.appendChild(box);
    root.appendChild(mask);

    function close() {
      if (mask.parentNode) mask.parentNode.removeChild(mask);
      modalStack = modalStack.filter(function (m) { return m !== api; });
    }
    var api = { close: close, body: body, el: box };
    modalStack.push(api);
    mask.addEventListener('mousedown', function (e) {
      if (e.target === mask && opts.dismissible !== false) api.close();
    });
    box.querySelector('.modal-x').addEventListener('click', function () { api.close(); });
    setTimeout(function () {
      var f = body.querySelector('input, textarea, select');
      if (f) f.focus();
    }, 30);
    return api;
  }
  function closeTopModal() {
    if (modalStack.length) {
      modalStack[modalStack.length - 1].close();
      return true;
    }
    return false;
  }

  /* 统一确认框：confirm({title, text, danger}) → Promise<boolean> */
  function confirm(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var done = false;
      var m = modal({
        title: opts.title || '确认操作',
        body: '<p style="font-size:13.5px;line-height:1.7;color:var(--tx-2)">' + (opts.html || esc(opts.text || '确定继续吗？')) + '</p>',
        buttons: [
          { label: '取消', cls: 'btn-ghost', onClick: function () { if (!done) { done = true; resolve(false); } } },
          {
            label: opts.okLabel || '确认',
            cls: opts.danger ? 'btn-danger' : 'btn-primary',
            onClick: function () { if (!done) { done = true; resolve(true); } }
          }
        ]
      });
      var origClose = m.close;
      m.close = function () { origClose(); if (!done) { done = true; resolve(false); } };
      m.el.querySelector('.modal-x').addEventListener('click', function () { if (!done) { done = true; resolve(false); } });
    });
  }

  /* ---------------- 骨架屏 ---------------- */
  function skeleton(kind) {
    if (kind === 'cards') {
      return '<div class="grid stat-grid">' +
        [0, 1, 2, 3].map(function () { return '<div class="sk sk-card"></div>'; }).join('') + '</div>';
    }
    if (kind === 'rows') {
      return [0, 1, 2, 3, 4].map(function () { return '<div class="sk sk-line"></div>'; }).join('');
    }
    return '<div class="sk sk-line" style="width:60%"></div><div class="sk sk-line"></div><div class="sk sk-line" style="width:80%"></div>';
  }

  /* ---------------- 空状态（几何插画） ---------------- */
  var ART = {
    box: '<svg class="empty-art" width="88" height="72" viewBox="0 0 88 72" fill="none">' +
      '<rect x="16" y="26" width="56" height="36" rx="7" stroke="rgba(255,255,255,0.16)" stroke-width="1.5"/>' +
      '<path d="M16 34h56" stroke="rgba(255,255,255,0.10)" stroke-width="1.5"/>' +
      '<rect x="30" y="12" width="28" height="18" rx="6" stroke="rgba(79,140,255,0.5)" stroke-width="1.5"/>' +
      '<circle cx="44" cy="48" r="5" stroke="rgba(79,140,255,0.5)" stroke-width="1.5"/></svg>',
    chat: '<svg class="empty-art" width="92" height="72" viewBox="0 0 92 72" fill="none">' +
      '<rect x="10" y="12" width="48" height="30" rx="9" stroke="rgba(255,255,255,0.16)" stroke-width="1.5"/>' +
      '<path d="M20 42l-4 10 12-8" stroke="rgba(255,255,255,0.16)" stroke-width="1.5" stroke-linejoin="round"/>' +
      '<rect x="42" y="34" width="42" height="26" rx="9" stroke="rgba(79,140,255,0.5)" stroke-width="1.5"/>' +
      '<path d="M74 60l4 8-11-6" stroke="rgba(79,140,255,0.5)" stroke-width="1.5" stroke-linejoin="round"/>' +
      '<circle cx="26" cy="27" r="2" fill="rgba(255,255,255,0.22)"/><circle cx="35" cy="27" r="2" fill="rgba(255,255,255,0.22)"/><circle cx="44" cy="27" r="2" fill="rgba(255,255,255,0.22)"/></svg>',
    folder: '<svg class="empty-art" width="92" height="72" viewBox="0 0 92 72" fill="none">' +
      '<path d="M14 24a5 5 0 0 1 5-5h13l5 6h32a5 5 0 0 1 5 5v26a5 5 0 0 1-5 5H19a5 5 0 0 1-5-5z" stroke="rgba(255,255,255,0.16)" stroke-width="1.5"/>' +
      '<circle cx="70" cy="18" r="9" stroke="rgba(79,140,255,0.5)" stroke-width="1.5"/>' +
      '<path d="M70 14v8M66 18h8" stroke="rgba(79,140,255,0.5)" stroke-width="1.5"/></svg>',
    doc: '<svg class="empty-art" width="80" height="72" viewBox="0 0 80 72" fill="none">' +
      '<path d="M22 10h22l14 14v34a4 4 0 0 1-4 4H22a4 4 0 0 1-4-4V14a4 4 0 0 1 4-4z" stroke="rgba(255,255,255,0.16)" stroke-width="1.5"/>' +
      '<path d="M44 10v14h14" stroke="rgba(255,255,255,0.16)" stroke-width="1.5"/>' +
      '<path d="M28 38h24M28 47h18" stroke="rgba(79,140,255,0.5)" stroke-width="1.5"/></svg>',
    chart: '<svg class="empty-art" width="92" height="72" viewBox="0 0 92 72" fill="none">' +
      '<path d="M12 58h68" stroke="rgba(255,255,255,0.16)" stroke-width="1.5"/>' +
      '<rect x="22" y="34" width="12" height="24" rx="3" stroke="rgba(255,255,255,0.16)" stroke-width="1.5"/>' +
      '<rect x="42" y="22" width="12" height="36" rx="3" stroke="rgba(79,140,255,0.5)" stroke-width="1.5"/>' +
      '<rect x="62" y="42" width="12" height="16" rx="3" stroke="rgba(255,255,255,0.16)" stroke-width="1.5"/></svg>'
  };
  function empty(opts) {
    opts = opts || {};
    return '<div class="empty">' + (ART[opts.art] || ART.box) +
      '<div class="empty-title">' + esc(opts.title || '暂无数据') + '</div>' +
      '<div class="empty-desc">' + esc(opts.desc || '') + '</div>' +
      (opts.action ?
        '<button class="btn btn-primary btn-sm" data-empty-action="' + esc(opts.actionId || '') + '">' +
        icon('plus', 'sm') + esc(opts.action) + '</button>' : '') +
      '</div>';
  }

  /* ---------------- 上下文用量条（工位详情 / 管理员对话共用） ---------------- */
  function fmtInt(n) {
    n = Number(n) || 0;
    return n.toLocaleString('en-US');
  }

  /**
   * 用量条 HTML：ratio 填充，超过 threshold 变橙，超过 0.95 变红。
   * 约定字段：`{context_tokens, max_context_tokens, ratio, threshold, estimated}`
   * @param {Object} u 后端 usage 响应
   * @param {Object=} opts `{label}` 覆盖标题文案
   * @returns {string} 用量条 HTML
   */
  function usageHTML(u, opts) {
    u = u || {};
    opts = opts || {};
    var ratio = Math.max(0, Math.min(1, Number(u.ratio) || 0));
    var level = ratio > 0.95 ? 'crit'
      : (Number(u.threshold) > 0 && ratio > Number(u.threshold) ? 'warn' : '');
    var pct = Math.round(ratio * 100);
    /* v0.10.20：缓存命中（DeepSeek 的 prompt_cache_hit/miss_tokens）。
       服务商没给这些字段时（全 0）不显示这一行，而不是显示假的 0%。 */
    var cacheHit = Number(u.cache_hit_tokens) || 0;
    var cacheMiss = Number(u.cache_miss_tokens) || 0;
    var cacheTotal = cacheHit + cacheMiss;
    var cacheLine = cacheTotal > 0
      ? '<span class="hint">缓存命中 ' + Math.round(cacheHit / cacheTotal * 100) + '%' +
        ' (' + fmtInt(cacheHit) + ' 命中 / ' + fmtInt(cacheMiss) + ' 未命中)</span>'
      : '';
    return '<div class="ctx-usage-top">' +
      '<span class="ctx-usage-label">' + icon('activity', 'sm') +
      esc(opts.label || '上下文用量') + '</span>' +
      '<span class="ctx-usage-text num">' + (u.estimated ? '≈' : '') + fmtInt(u.context_tokens) +
      ' / ' + fmtInt(u.max_context_tokens) + ' tokens</span>' +
      '</div>' +
      '<div class="ctx-usage-track"><div class="ctx-usage-fill ' + level +
      '" style="width:' + pct + '%"></div></div>' +
      '<div class="ctx-usage-foot"><span>' + pct + '%</span>' +
      '<span class="hint">压缩阈值 ' + Math.round((Number(u.threshold) || 0) * 100) + '%' +
      (u.estimated ? ' · 粗估' : '') + '</span>' + cacheLine + '</div>';
  }

  /**
   * 拉取到的模型列表 → 填进一个**真正能选**的 `<select>`，并可选地回写输入框。
   *
   * 为什么不只用 `<datalist>`：浏览器的 datalist 候选**按输入框里已有的文字过滤**。
   * 所以"拉取后自动填第一个"（或输入框里本来就有旧模型名）时，下拉里就只剩那一项——
   * 用户看到的就是"只能选第一个"。实测：SiliconFlow 返回 99 个模型，界面上只选得到 1 个。
   *
   * @param {string} selectId 目标 `<select>` 的 id
   * @param {string[]} models 拉取到的模型名
   * @param {string=} mirrorInputId 选中后回写到哪个输入框
   * @returns {number} 填入的条数
   */
  function fillModelSelect(selectId, models, mirrorInputId) {
    var sel = document.getElementById(selectId);
    if (!sel) return 0;
    var list = models || [];
    sel.innerHTML = '<option value="">（选择…共 ' + list.length + ' 个）</option>' +
      list.map(function (m) {
        return '<option value="' + esc(m) + '">' + esc(m) + '</option>';
      }).join('');
    if (!sel.getAttribute('data-wired') && mirrorInputId) {
      sel.setAttribute('data-wired', '1');
      sel.addEventListener('change', function () {
        var inp = document.getElementById(mirrorInputId);
        if (inp && sel.value) inp.value = sel.value;
      });
    }
    return list.length;
  }

  /* ---------------- 防抖 ---------------- */
  function debounce(fn, ms) {
    var t = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms || 180);
    };
  }

  /* ---------------- 极简代码高亮 ---------------- */
  function highlightCode(code, lang) {
    var s = esc(code);
    var kw = /\b(function|const|let|var|return|if|else|for|while|class|import|export|from|async|await|def|self|None|True|False|elif|try|except|finally|with|as|new|this|typeof|interface|type|public|private|static|void|null|undefined|true|false|do|switch|case|break|continue|raise|yield|lambda|pass|global|not|and|or|in|is)\b/g;
    // 先保护字符串与注释（简化版：够用即可）
    var parts = [];
    s = s.replace(/(&quot;[^&]*?&quot;|&#39;[^&]*?&#39;|`[^`]*`)/g, function (m) {
      parts.push('<span class="hl-str">' + m + '</span>');
      return '\u0000' + (parts.length - 1) + '\u0000';
    });
    s = s.replace(/(#[^\n]*|\/\/[^\n]*|\/\*[\s\S]*?\*\/)/g, function (m) {
      parts.push('<span class="hl-com">' + m + '</span>');
      return '\u0000' + (parts.length - 1) + '\u0000';
    });
    s = s.replace(kw, '<span class="hl-key">$&</span>');
    s = s.replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="hl-num">$1</span>');
    s = s.replace(/\u0000(\d+)\u0000/g, function (m, i) { return parts[Number(i)]; });
    return s;
  }

  /* v0.10.26：与后端 `model_service.endpoint_of(...).split('/')[0]` 同口径的 host[:port]。
     模型页（判断会不会撞上同身份条目）与设置向导（定位要更新哪条条目）都要用。 */
  function endpointHost(url) {
    var s = String(url || '').trim();
    if (!s) return '';
    var m = s.match(/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\/([^/]*)/);
    return (m ? m[1] : s.split('/')[0]).toLowerCase();
  }

  global.UI = {
    $: $, $$: $$, el: el, esc: esc, icon: icon,
    parseTime: parseTime, fmtTime: fmtTime, fmtClock: fmtClock, fmtDate: fmtDate,
    relTime: relTime, countdown: countdown, fmtSize: fmtSize,
    toast: toast, modal: modal, confirm: confirm, closeTopModal: closeTopModal,
    skeleton: skeleton, empty: empty, debounce: debounce, highlightCode: highlightCode,
    fmtInt: fmtInt, usageHTML: usageHTML, fillModelSelect: fillModelSelect,
    endpointHost: endpointHost
  };
})(window);
