/* ============================================================
   js/index.bundle.js（自动生成，请勿手改）
   由 tools/rebuild-bundles.py 按 tools/bundle-order.json 拼接
   ============================================================ */

/* ==== js/ui.js ==== */
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

/* ==== js/api.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · api.js
   fetch 封装：自动携带 X-Auth-Token，401 自动回登录层
   全部接口与后端契约一一对应（纯 JSON）
   ============================================================ */
(function (global) {
  'use strict';

  var TOKEN_KEY = 'dogbuddy_token';

  function getToken() {
    try { return global.localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
  }
  function setToken(t) {
    try { global.localStorage.setItem(TOKEN_KEY, t); } catch (e) { /* 忽略 */ }
  }
  function clearToken() {
    try { global.localStorage.removeItem(TOKEN_KEY); } catch (e) { /* 忽略 */ }
  }

  function ApiError(message, status, payload) {
    this.name = 'ApiError';
    this.message = message || ('请求失败 (' + status + ')');
    this.status = status || 0;
    this.payload = payload;
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  function qs(params) {
    var parts = [];
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v === undefined || v === null || v === '') return;
      parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    });
    return parts.length ? '?' + parts.join('&') : '';
  }

  /* 核心请求：method / url / body(JSON 或 FormData) / opts {blob} */
  function request(method, url, body, opts) {
    opts = opts || {};
    var headers = { 'Accept': 'application/json' };
    var token = getToken();
    if (token) headers['X-Auth-Token'] = token;

    var init = { method: method, headers: headers, cache: 'no-store' };
    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      init.body = body; /* multipart：不手动设置 Content-Type */
    } else if (body !== undefined && body !== null) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }

    return global.fetch(url, init).then(function (res) {
      if (res.status === 401 && !opts.noAuthRedirect) {
        clearToken();
        if (global.App && typeof global.App.showLogin === 'function') {
          global.App.showLogin('登录已失效，请重新登录');
        }
        throw new ApiError('未授权（401）：请重新登录', 401);
      }
      if (opts.blob) {
        if (!res.ok) throw new ApiError('下载失败（' + res.status + '）', res.status);
        return res.blob();
      }
      if (res.status === 204) return null;
      var isJson = (res.headers.get('content-type') || '').indexOf('json') >= 0;
      var parsed = isJson ? res.json() : res.text();
      return parsed.catch(function () { return null; }).then(function (data) {
        if (!res.ok) {
          var msg = data && (data.detail || data.message || data.error || data.msg);
          if (typeof msg !== 'string' || !msg) msg = '请求失败（' + res.status + '）';
          throw new ApiError(msg, res.status, data);
        }
        return data;
      });
    }, function (err) {
      if (err instanceof ApiError) throw err;
      throw new ApiError('网络错误：' + (err && err.message ? err.message : err), 0);
    });
  }

  function enc(id) { return encodeURIComponent(id); }

  var api = {
    getToken: getToken,
    setToken: setToken,
    clearToken: clearToken,
    ApiError: ApiError,

    /* ---------------- 认证：POST /api/auth/login {"password"|"token"} → {"token"} ---------------- */
    login: function (payload) {
      return request('POST', '/api/auth/login', payload, { noAuthRedirect: true });
    },

    /* ---------------- 状态 / 总览 ---------------- */
    status: function () { return request('GET', '/api/status'); },            /* GET /api/status */
    overview: function () { return request('GET', '/api/overview'); },        /* GET /api/overview */

    /* ---------------- 控制台 ↔ 管理员对话 ---------------- */
    /* ---------------- 对话（v9：一个管理员，多个对话） ---------------- */
    chatConversations: function () { return request('GET', '/api/admin/chat/conversations'); },
    chatCreateConversation: function (title) {
      return request('POST', '/api/admin/chat/conversations', { title: title || '' });
    },
    chatRenameConversation: function (id, title) {
      return request('PATCH', '/api/admin/chat/conversations/' + enc(id), { title: title });
    },
    chatDeleteConversation: function (id) {
      return request('DELETE', '/api/admin/chat/conversations/' + enc(id));
    },    adminChatAdmins: function () { return request('GET', '/api/admin/chat/admins'); },
    adminChatMessages: function (sender, limit) {
      return request('GET', '/api/admin/chat/messages' + qs({ conversation: sender, limit: limit || 100 }));
    },
    adminChat: function (text, sender) {
      return request('POST', '/api/admin/chat', { text: text, conversation: sender || 'default' });
    },
    /* GET /api/admin/chat/usage?conversation= → {context_tokens, max_context_tokens, ratio, threshold, tokens, estimated}
       管理员会话不落 windows 表，所以**不能**走 /api/windows/<id>/usage（会 404）。 */
    adminChatUsage: function (sender) {
      return request('GET', '/api/admin/chat/usage' + qs({ conversation: sender || 'default' }));
    },
    adminChatReset: function (sender) {
      return request('POST', '/api/admin/chat/reset', { sender: sender || 'console' });
    },

    /* ---------------- 工位 ---------------- */
    windows: {
      list: function () { return request('GET', '/api/windows'); },
      create: function (body) { return request('POST', '/api/windows', body); },
      get: function (id) { return request('GET', '/api/windows/' + enc(id)); },
      update: function (id, body) { return request('PATCH', '/api/windows/' + enc(id), body); },
      /* GET /api/windows/{id}/usage → {context_tokens, max_context_tokens, ratio, threshold, tokens} */
      usage: function (id) { return request('GET', '/api/windows/' + enc(id) + '/usage'); },
      remove: function (id) { return request('DELETE', '/api/windows/' + enc(id)); },
      pause: function (id) { return request('POST', '/api/windows/' + enc(id) + '/pause'); },
      resume: function (id) { return request('POST', '/api/windows/' + enc(id) + '/resume'); },
      dispatch: function (id, prompt) {
        return request('POST', '/api/windows/' + enc(id) + '/dispatch', { prompt: prompt });
      },
      messages: function (id, limit) {
        return request('GET', '/api/windows/' + enc(id) + '/messages' + qs({ limit: limit || 100 }));
      },
      compress: function (id) { return request('POST', '/api/windows/' + enc(id) + '/context/compress'); },
      reset: function (id) { return request('POST', '/api/windows/' + enc(id) + '/context/reset'); },
      inject: function (id, text) {
        return request('POST', '/api/windows/' + enc(id) + '/context/inject', { text: text });
      }
    },

    /* ---------------- 任务 ---------------- */
    jobs: {
      list: function (windowId) { return request('GET', '/api/jobs' + qs({ window_id: windowId })); },
      create: function (body) { return request('POST', '/api/jobs', body); },
      update: function (id, body) { return request('PATCH', '/api/jobs/' + enc(id), body); },
      remove: function (id) { return request('DELETE', '/api/jobs/' + enc(id)); },
      pause: function (id) { return request('POST', '/api/jobs/' + enc(id) + '/pause'); },
      resume: function (id) { return request('POST', '/api/jobs/' + enc(id) + '/resume'); },
      runNow: function (id) { return request('POST', '/api/jobs/' + enc(id) + '/run_now'); },
      runs: function (id) { return request('GET', '/api/jobs/' + enc(id) + '/runs'); }
    },

    /* ---------------- 群组（共享工作频道） ---------------- */
    /* ---------------- 项目（ROADMAP T7.1/T7.6） ---------------- */
    projects: {
      /* GET /api/projects → {projects, summary, workspaces} */
      list: function () { return request('GET', '/api/projects'); },
      /* POST /api/projects {name,root_path,git_init,params} */
      create: function (body) { return request('POST', '/api/projects', body); },
      /* PATCH /api/projects/{id} */
      update: function (id, body) { return request('PATCH', '/api/projects/' + enc(id), body); },
      /* DELETE /api/projects/{id}（解挂工位/群组，保留目录） */
      remove: function (id) { return request('DELETE', '/api/projects/' + enc(id)); },
      /* POST /api/projects/mount {window_id,project_id,subdir} */
      mount: function (body) { return request('POST', '/api/projects/mount', body); },
      /* POST /api/projects/unmount/{window_id} */
      unmount: function (windowId) { return request('POST', '/api/projects/unmount/' + enc(windowId)); },
      /* GET /api/projects/{id}/params-chain?window_id= → 参数继承链与实际生效值 */
      chain: function (id, windowId) {
        return request('GET', '/api/projects/' + enc(id) + '/params-chain' + qs({ window_id: windowId }));
      },
      /* GET /api/projects/{id}/chain → 指挥链（管理员→主管→员工）与健康度。
         审查报告 P1-2：这个接口后端一直在（T9.5 的验收点），但**前端没有任何调用者**
         —— 全前端搜 chain 只命中上面的 params-chain。是个只能 curl 的孤儿接口。 */
      cmdChain: function (id) {
        return request('GET', '/api/projects/' + enc(id) + '/chain');
      }
    },

    /* ---------------- 项目迭代（ROADMAP T8.5/T8.6） ---------------- */
    iterations: {
      /* GET /api/iterations?project_id= → {iterations:[{...plan}], presets} */
      list: function (projectId) {
        return request('GET', '/api/iterations' + qs({ project_id: projectId }));
      },
      /* GET /api/iterations/presets → 三档预设（保守/标准/激进） */
      presets: function () { return request('GET', '/api/iterations/presets'); },
      /* POST /api/iterations {project_id,goal,mode,window_ids,speed_minutes,...} */
      create: function (body) { return request('POST', '/api/iterations', body); },
      /* GET /api/iterations/{id} → {iteration, plan, rounds} */
      detail: function (id) { return request('GET', '/api/iterations/' + enc(id)); },
      update: function (id, body) { return request('PATCH', '/api/iterations/' + enc(id), body); },
      stop: function (id, reason) {
        return request('POST', '/api/iterations/' + enc(id) + '/stop' + qs({ reason: reason }));
      },
      resume: function (id) { return request('POST', '/api/iterations/' + enc(id) + '/resume'); },
      /* 手动推一轮（不等下一个间隔） */
      runNow: function (id) { return request('POST', '/api/iterations/' + enc(id) + '/run_now'); },
      remove: function (id) { return request('DELETE', '/api/iterations/' + enc(id)); }
    },
    /* ---------------- 模型管理（目录 + 备注 + 切换） ---------------- */
    models: {
      /* GET /api/models?enabled_only= → {models:[...], current:{...}, summary:{...}} */
      list: function (enabledOnly) {
        return request('GET', '/api/models' + qs({ enabled_only: enabledOnly ? true : undefined }));
      },
      /* GET /api/models/options → {capabilities:[], speeds:[], costs:[]} */
      options: function () { return request('GET', '/api/models/options'); },
      /* POST /api/models {model,provider,base_url,display_name,note,capability,
         context_window,speed,cost} → {model, models:[...]} */
      create: function (body) { return request('POST', '/api/models', body); },
      /* PATCH /api/models/{id}（部分更新；备注走这里） */
      update: function (id, body) { return request('PATCH', '/api/models/' + enc(id), body); },
      /* DELETE /api/models/{id} */
      remove: function (id) { return request('DELETE', '/api/models/' + enc(id)); },
      /* POST /api/models/{id}/activate → 写运行时配置并落盘（换模型） */
      activate: function (id) { return request('POST', '/api/models/' + enc(id) + '/activate'); },
      /* POST /api/models/adopt-current → 把正在运行的模型收进目录 */
      adoptCurrent: function () { return request('POST', '/api/models/adopt-current'); },
      /* GET /api/models/verify-current → 当前模型在不在服务商返回的列表里 */
      /* v0.10.21：probe=1 → 再发一个 1-token 真调用，验"能不能用"（列表里有但套餐不含 = 403） */
      verifyCurrent: function (probe) {
        return request('GET', '/api/models/verify-current' + (probe ? '?probe=1' : ''));
      },
      /* POST /api/models/import {provider,base_url,api_key} → 拉取列表批量入库 */
      import: function (body) { return request('POST', '/api/models/import', body || {}); }
    },
    groups: {
      /* GET /api/groups → {groups:[{id,name,description,lead_window_id,created_at,
         updated_at,members:[{window_id,window_name,role,joined_at,exists}]}]} */
      list: function () { return request('GET', '/api/groups'); },
      /* POST /api/groups {name,description,lead_window_id} → 群组 DTO */
      create: function (body) { return request('POST', '/api/groups', body); },
      /* GET /api/groups/{ref}（ref = 组 id 或组名）→ 群组 DTO */
      get: function (ref) { return request('GET', '/api/groups/' + enc(ref)); },
      /* PATCH /api/groups/{ref} {name?,description?,lead_window_id?} */
      update: function (ref, body) { return request('PATCH', '/api/groups/' + enc(ref), body); },
      /* DELETE /api/groups/{ref} → {deleted:true} */
      remove: function (ref) { return request('DELETE', '/api/groups/' + enc(ref)); },
      /* POST /api/groups/{ref}/members {window,role:"member|lead"} → 群组 DTO */
      addMember: function (ref, window, role) {
        return request('POST', '/api/groups/' + enc(ref) + '/members',
          { window: window, role: role || 'member' });
      },
      /* DELETE /api/groups/{ref}/members/{window} → {removed:true} */
      removeMember: function (ref, window) {
        return request('DELETE', '/api/groups/' + enc(ref) + '/members/' + enc(window));
      },
      /* GET /api/groups/{ref}/channel?limit=100 →
         {messages:[{id,window_id,window_name,role,content,created_at}]} */
      channel: function (ref, limit) {
        return request('GET', '/api/groups/' + enc(ref) + '/channel' + qs({ limit: limit || 100 }));
      },
      /* POST /api/groups/{ref}/run {prompt} → {group_id,group_name,
         steps:[{window_id,window_name,is_lead,ok,summary}],summary}
         同步等待整组跑完、可能耗时：请求层不设短超时，UI 端置 loading 即可 */
      run: function (ref, prompt) {
        return request('POST', '/api/groups/' + enc(ref) + '/run', { prompt: prompt });
      }
    },

    /* ---------------- 运行时配置（kv）：approval.* 等 ---------------- */
    /* 与后端约定：GET /api/config → 配置键值对象（兼容 {config:{...}} 包装）；
       POST /api/config {key, value} 写单个键（对应后端 ConfigService.set(key,value)） */
    config: {
      get: function () { return request('GET', '/api/config'); },
      set: function (key, value) {
        return request('POST', '/api/config', { key: key, value: value });
      }
    },

    /* ---------------- 设置 ---------------- */
    settings: {
      get: function () { return request('GET', '/api/settings'); },
      update: function (body) { return request('PUT', '/api/settings', body); },
      /* GET /api/settings/models?provider=&base_url=&api_key=&profile=&model_ref= → {models, error}
         profile 可选（v1.3）：按配置套拉模型；model_ref 可选：按**目录条目**自带密钥拉 */
      models: function (provider, baseUrl, apiKey, profile, modelRef) {
        return request('GET', '/api/settings/models' + qs({
          provider: provider, base_url: baseUrl, api_key: apiKey, profile: profile,
          model_ref: modelRef
        }));
      },
      /* POST /api/settings/test_llm {provider?,base_url?,api_key?,model?,profile?} */
      testLLM: function (body) {
        return request('POST', '/api/settings/test_llm', body || {});
      },

      /* ---------------- LLM 配置套（profiles，v1.3） ---------------- */
      profiles: {
        /* GET /api/settings/profiles → {active, names: ["", ...], profiles: {name: {...llm 结构}}} */
        list: function () { return request('GET', '/api/settings/profiles'); },
        /* POST /api/settings/profiles/{name} {llm:{...}, set_active?:bool} → 全量 settings（创建/覆盖） */
        create: function (name, body) {
          return request('POST', '/api/settings/profiles/' + enc(name), body || {});
        },
        /* PATCH /api/settings/profiles/{name} 同 POST（部分更新；{set_active:true} 即"设为默认"） */
        update: function (name, body) {
          return request('PATCH', '/api/settings/profiles/' + enc(name), body || {});
        },
        /* DELETE /api/settings/profiles/{name} → {deleted, active} */
        remove: function (name) {
          return request('DELETE', '/api/settings/profiles/' + enc(name));
        }
      }
    },

    /* ---------------- 白名单 ---------------- */
    whitelist: {
      list: function () { return request('GET', '/api/whitelist'); },
      /* POST /api/whitelist {qq_id,name,role}（role 可省略，默认 admin；同 qq_id 视为更新） */
      add: function (qqId, name, role) {
        return request('POST', '/api/whitelist', {
          qq_id: String(qqId), name: name || '', role: role || 'admin'
        });
      },
      /* DELETE body 保持 {qq_id} */
      remove: function (qqId) {
        return request('DELETE', '/api/whitelist', { qq_id: String(qqId) });
      }
    },

    /* ---------------- 文件管理器（path 为工作区相对路径；workspace 可选） ---------------- */
    files: {
      tree: function (path) {
        return request('GET', '/api/files/tree' + qs({ path: path || '' }));
      },
      list: function (path) {
        return request('GET', '/api/files/list' + qs({ path: path || '' }));
      },
      read: function (path) {
        return request('GET', '/api/files/read' + qs({ path: path }));
      },
      download: function (path) {
        return request('GET', '/api/files/download' + qs({ path: path }), null, { blob: true });
      },
      image: function (path) {
        return request('GET', '/api/files/image' + qs({ path: path }), null, { blob: true });
      },
      upload: function (path, file) {
        var fd = new FormData();
        fd.append('file', file);
        fd.append('path', path || '');
        return request('POST', '/api/files/upload', fd);
      },
      mkdir: function (path, name) {
        return request('POST', '/api/files/mkdir', { path: path || '', name: name });
      },
      rename: function (path, newName) {
        return request('POST', '/api/files/rename', { path: path, new_name: newName });
      },
      remove: function (path) {
        return request('POST', '/api/files/delete', { path: path });
      },
      write: function (path, content) {
        return request('POST', '/api/files/write', { path: path, content: content });
      }
    },

    /* ---------------- 日志 ---------------- */
    logs: {
      audit: function (limit) { return request('GET', '/api/logs/audit' + qs({ limit: limit || 200 })); },
      runs: function (limit, windowId) {
        return request('GET', '/api/logs/runs' + qs({ limit: limit || 100, window_id: windowId }));
      }
    },

    /* ---------------- 待决事项（股东模式） ---------------- */
    decisions: {
      /* GET /api/decisions?status=pending|answered|timeout|cancelled|<空=全部>&limit=100
         注意：status 空串 = 全部，必须显式传 ?status=（后端缺省值是 pending） */
      list: function (status, limit) {
        var s = status === undefined || status === null ? 'pending' : status;
        return request('GET', '/api/decisions?status=' + encodeURIComponent(s) +
          '&limit=' + (Number(limit) > 0 ? Number(limit) : 100));
      },
      /* POST /api/decisions/{id}/answer {"answer": "..."} */
      answer: function (id, answer) {
        return request('POST', '/api/decisions/' + enc(id) + '/answer', { answer: answer });
      },
      /* POST /api/decisions/{id}/cancel */
      cancel: function (id) {
        return request('POST', '/api/decisions/' + enc(id) + '/cancel');
      },
      /* POST /api/decisions/sweep → {"expired": [...]} 把过期待决翻成 timeout */
      sweep: function () {
        return request('POST', '/api/decisions/sweep');
      }
    },

    /* ---------------- 账本（token 用量 / 成本） ---------------- */
    ledger: {
      /* GET /api/ledger?days=30 → {since, by_window[], by_day[], total_tokens,
         total_messages, price_per_1m, total_cost} */
      get: function (days) {
        return request('GET', '/api/ledger' + qs({ days: days || 30 }));
      },
      /* GET /api/ledger/price → {"price_per_1m": 数字} */
      price: function () { return request('GET', '/api/ledger/price'); },
      /* POST /api/ledger/price {"price_per_1m": 数字}（0=只显示 tokens 不算钱） */
      setPrice: function (pricePer1m) {
        return request('POST', '/api/ledger/price', { price_per_1m: Number(pricePer1m) || 0 });
      }
    },

    /* ---------------- SSE 实时事件（GET /api/events?token=…） ---------------- */
    /**
     * 连接 SSE 实时事件流。EventSource 无法带请求头，token 走 query
     * （与 X-Auth-Token 同值，无鉴权部署留空）。断线（onerror）后指数退避
     * 重建连接；坏帧（非 JSON / 非对象）静默忽略；回调抛错不影响连接。
     * @param {(frame: Object) => void} onEvent 每帧 JSON 对象 {type, payload, created_at} 的回调
     * @returns {{close: () => void}} 连接句柄（close 后不再重连）
     */
    events: function (onEvent) {
      var closed = false;
      var es = null;
      var attempt = 0;
      var timer = null;

      function schedule() {
        if (closed || timer) return;
        attempt += 1;
        var delay = Math.min(30000, 1000 * Math.pow(2, attempt - 1));
        timer = setTimeout(function () { timer = null; connect(); }, delay);
      }

      function connect() {
        if (closed) return;
        var url = '/api/events' + qs({ token: getToken() });
        try {
          es = new EventSource(url);
        } catch (e) {
          schedule();
          return;
        }
        es.onopen = function () { attempt = 0; };
        es.onmessage = function (ev) {
          if (closed || !ev || typeof ev.data !== 'string') return;
          var frame = null;
          try { frame = JSON.parse(ev.data); } catch (e) { return; } /* 坏帧忽略 */
          if (!frame || typeof frame !== 'object') return;
          try { onEvent(frame); } catch (e) { /* 回调异常不断连 */ }
        };
        es.onerror = function () {
          if (closed) return;
          try { es.close(); } catch (e) { /* 忽略 */ }
          es = null;
          schedule();
        };
      }

      connect();
      return {
        close: function () {
          closed = true;
          if (timer) { clearTimeout(timer); timer = null; }
          if (es) {
            try { es.close(); } catch (e) { /* 忽略 */ }
            es = null;
          }
        }
      };
    }
  };

  global.API = api;
})(window);

/* ==== js/sse.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · sse.js
   SSE 实时事件总线（松耦合层）：
     window.SSE 订阅表（事件名 → handlers，'*' 订阅全部帧）
   + 连接生命周期（start/stop，底层走 API.events 的断线重连）
   各视图在自己文件里 SSE.on('事件名', fn) 注册钩子，app.js 只负责 start。
   坏帧在 API.events 里已过滤；这里只做分发，handler 异常互不影响。
   ============================================================ */
(function (global) {
  'use strict';
  var A = global.API;

  /** @type {Object} 事件名 → 处理器数组的映射（'*' = 全部） */
  var handlers = {};
  /** @type {Object|null} 底层连接句柄 {close: function()} */
  var conn = null;

  /**
   * 订阅一类 SSE 事件。
   * @param {string} type 事件名（如 'window.status'）；'*' 订阅全部帧
   * @param {Function} fn 处理器 function(payload, frame)
   * @returns {Function} 退订函数
   */
  function on(type, fn) {
    if (typeof fn !== 'function') return function () {};
    var key = String(type || '*');
    handlers[key] = handlers[key] || [];
    handlers[key].push(fn);
    return function () { off(key, fn); };
  }

  /**
   * 退订一类 SSE 事件的某个处理器。
   * @param {string} type 事件名
   * @param {Function} fn 之前注册的处理器
   * @returns {void}
   */
  function off(type, fn) {
    var key = String(type || '*');
    var list = handlers[key];
    if (!list) return;
    var i = list.indexOf(fn);
    if (i >= 0) list.splice(i, 1);
    if (!list.length) delete handlers[key];
  }

  /**
   * 把一帧广播给订阅者（先精确事件名，再 '*'）。
   * 单个 handler 抛错不影响其他 handler。
   * @param {Object} frame 帧对象 {type, payload, created_at}
   * @returns {void}
   */
  function dispatch(frame) {
    if (!frame || typeof frame !== 'object') return;
    var type = String(frame.type || '');
    var payload = (frame.payload && typeof frame.payload === 'object') ? frame.payload : {};
    [type, '*'].forEach(function (key) {
      (handlers[key] || []).slice().forEach(function (fn) {
        try { fn(payload, frame); } catch (e) { /* handler 异常互不影响 */ }
      });
    });
    state.lastType = type;
    state.lastAt = frame.created_at || '';
    state.received += 1;
  }

  /** @type {{connected: boolean, received: number, lastType: string, lastAt: string}} 连接状态（供降级判断） */
  var state = { connected: false, received: 0, lastType: '', lastAt: '' };

  /**
   * 启动 SSE 连接（幂等：已连接则跳过）。连接/断开时广播
   * 'sse.open' / 'sse.down'（payload 为空对象），供视图做降级清理。
   * @returns {void}
   */
  function start() {
    if (conn || typeof A.events !== 'function') return;
    state.connected = true;
    conn = A.events(function (frame) {
      dispatch(frame);
    });
    dispatch({ type: 'sse.open', payload: {} });
  }

  /**
   * 停止 SSE 连接（登录失效 / 退出登录时调用），并广播 'sse.down'
   * 让视图把「正在输入…」这类半截态清理干净、退回轮询模式。
   * @returns {void}
   */
  function stop() {
    if (!conn) return;
    conn.close();
    conn = null;
    state.connected = false;
    dispatch({ type: 'sse.down', payload: {} });
  }

  global.SSE = {
    on: on,
    off: off,
    start: start,
    stop: stop,
    state: state
  };
})(window);

/* ==== js/views-main.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-main.js
   总览（统计卡 / 工位卡 / 任务概览 / 运行时间线）
   工位（列表 + 详情 + 聊天式会话流 + 手动派活 + 待办 + 上下文操作）
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  /* 通用：状态点映射 */
  var STATUS_DOT = {
    running: 'dot-run', busy: 'dot-run', working: 'dot-run',
    paused: 'dot-pause', pause: 'dot-pause',
    error: 'dot-err', failed: 'dot-err',
    idle: 'dot-idle', stopped: 'dot-idle'
  };
  var STATUS_LABEL = {
    running: '运行中', busy: '工作中', working: '工作中', paused: '已暂停',
    error: '异常', failed: '异常', idle: '空闲', stopped: '已停止'
  };
  function statusDot(st) {
    var key = String(st || 'idle').toLowerCase();
    return '<span class="dot ' + (STATUS_DOT[key] || 'dot-idle') + '"></span>';
  }
  function statusLabel(st) {
    var key = String(st || 'idle').toLowerCase();
    return STATUS_LABEL[key] || st || '空闲';
  }
  function runBadge(st) {
    var key = String(st || '').toLowerCase();
    if (key === 'ok' || key === 'success' || key === 'succeeded') return '<span class="badge badge-green">成功</span>';
    if (key === 'running') return '<span class="badge badge-blue">运行中</span>';
    if (key === 'error' || key === 'failed' || key === 'fail') return '<span class="badge badge-red">失败</span>';
    return '<span class="badge badge-grey">' + U.esc(st || '—') + '</span>';
  }
  /* 信任级徽标：params.trust == "elevated" → 金色 ELEVATED，否则「常规」 */
  function trustBadge(w) {
    var trust = w && w.params && w.params.trust;
    return trust === 'elevated'
      ? '<span class="badge badge-gold" title="信任级 elevated：可执行高危命令（仍需审批）">ELEVATED</span>'
      : '<span class="badge badge-grey" title="信任级：常规">常规</span>';
  }

  /* ============================================================
     总览
     ============================================================ */
  var Overview = {
    data: null,

    showBanner: function (llmConfigured) {
      var host = document.getElementById('ov-banner');
      if (!host) return;
      if (llmConfigured) { host.innerHTML = ''; return; }
      host.innerHTML =
        '<div class="setup-banner">' +
        '<span class="brand-mark lg">' + U.icon('wand', 'lg') + '</span>' +
        '<div style="flex:1;min-width:0">' +
        '<h3>欢迎使用 DogBuddyCn · 快速开始</h3>' +
        '<p>还没有配置 LLM 接入。7 步向导帮你完成：选择服务商 → 填写地址与密钥 → 拉取模型 → 测试连接 → 保存。全程约 1 分钟。</p>' +
        '</div>' +
        '<button class="btn btn-primary" id="ov-open-wizard">' + U.icon('wand', 'sm') + '打开设置向导</button>' +
        '</div>';
      var btn = document.getElementById('ov-open-wizard');
      if (btn) btn.addEventListener('click', function () {
        global.DogBuddyViews.Settings.openWizard();
      });
    },

    renderStats: function (st, ov) {
      var host = document.getElementById('ov-stats');
      if (!host) return;
      var runs = (ov && ov.recent_runs) || [];
      var ok = 0, total = 0;
      runs.forEach(function (r) {
        var k = String(r.status || '').toLowerCase();
        if (k === 'running') return;
        total++;
        if (k === 'ok' || k === 'success' || k === 'succeeded') ok++;
      });
      var rate = total ? Math.round(ok / total * 100) : null;
      var today = 0;
      var day = U.fmtDate(Date.now());
      runs.forEach(function (r) { if (U.fmtDate(r.started_at) === day) today++; });

      var cards = [
        { label: '工位数', value: (st && st.windows) || 0, icon: 'monitor', cls: '', hint: '执行窗口总数' },
        { label: '启用任务数', value: (st && st.jobs) || 0, icon: 'clock', cls: 'violet', hint: '调度中的任务' },
        { label: '今日运行次数', value: today, icon: 'activity', cls: 'green', hint: '按最近运行记录统计' },
        {
          label: '24h 成功率', value: rate === null ? '—' : rate, unit: rate === null ? '' : '%',
          icon: 'zap', cls: 'amber', hint: ok + ' / ' + total + ' 次成功'
        }
      ];
      host.innerHTML = cards.map(function (c) {
        return '<div class="stat-card">' +
          '<div class="stat-top"><span class="stat-label">' + c.label + '</span>' +
          '<span class="stat-icon ' + c.cls + '">' + U.icon(c.icon, 'sm') + '</span></div>' +
          '<div class="stat-value num">' + U.esc(String(c.value)) + (c.unit ? '<em>' + c.unit + '</em>' : '') + '</div>' +
          '<div class="stat-hint">' + U.esc(c.hint) + '</div>' +
          '</div>';
      }).join('');
    },

    renderWindows: function (windows) {
      var host = document.getElementById('ov-win-grid');
      if (!host) return;
      if (!windows || !windows.length) {
        host.innerHTML = U.empty({
          art: 'box', title: '还没有工位',
          desc: '工位是执行任务的智能窗口。创建第一个工位后即可派活。',
          action: '新建工位', actionId: 'new-window'
        });
        return;
      }
      host.innerHTML = windows.map(function (w) {
        var key = String(w.status || '').toLowerCase();
        return '<div class="win-card' + (key === 'running' || key === 'busy' ? ' running' : '') + '" data-win-id="' + U.esc(w.id) + '">' +
          '<div class="win-card-head">' + statusDot(w.status) +
          '<span class="win-card-name">' + U.esc(w.name) + '</span>' +
          '<span class="badge badge-grey">' + U.esc(statusLabel(w.status)) + '</span></div>' +
          '<div class="win-card-badges">' +
          '<span class="badge badge-blue">' + U.esc(w.role || 'worker') + '</span>' +
          '<span class="badge badge-violet">' + U.esc(w.mode || 'BUILD') + '</span>' +
          trustBadge(w) +
          (w.model ? '<span class="badge badge-grey mono">' + U.esc(w.model) + '</span>' : '') +
          '</div>' +
          '<div class="win-card-task"><b>当前</b>' + U.esc(w.current_task || '空闲待命') + '</div>' +
          '<div class="win-card-output">' + U.esc(w.last_output || '暂无产出') + '</div>' +
          '</div>';
      }).join('');
      U.$$('.win-card[data-win-id]', host).forEach(function (card) {
        card.addEventListener('click', function () {
          global.App.go('windows', { win: card.getAttribute('data-win-id') });
        });
      });
    },

    renderJobs: function (jobs) {
      var host = document.getElementById('ov-jobs-body');
      if (!host) return;
      if (!jobs || !jobs.length) {
        host.innerHTML = '<tr><td colspan="4">' + U.empty({
          art: 'doc', title: '暂无任务', desc: '创建定时任务，让工位自动干活。', action: '新建任务', actionId: 'new-job'
        }) + '</td></tr>';
        return;
      }
      host.innerHTML = jobs.slice(0, 8).map(function (j) {
        return '<tr>' +
          '<td><b>' + U.esc(j.name) + '</b></td>' +
          '<td class="muted">' + U.esc(j.window_name || '—') + '</td>' +
          '<td>' + (j.enabled ? '<span class="badge badge-green">启用</span>' : '<span class="badge badge-grey">停用</span>') +
          (j.fail_count > 0 ? ' <span class="fail-dot">' + j.fail_count + '</span>' : '') + '</td>' +
          '<td class="countdown num">' + U.esc(U.countdown(j.next_run_at)) + '</td>' +
          '</tr>';
      }).join('');
    },

    renderRuns: function (runs) {
      var host = document.getElementById('ov-runs');
      if (!host) return;
      if (!runs || !runs.length) {
        host.innerHTML = U.empty({ art: 'chart', title: '还没有运行记录', desc: '任务执行后会在这里形成活动流。' });
        return;
      }
      host.innerHTML = runs.map(function (r) {
        var k = String(r.status || '').toLowerCase();
        var cls = (k === 'ok' || k === 'success') ? 'ok' : (k === 'running' ? 'run' : 'err');
        var ic = cls === 'ok' ? 'check' : (cls === 'run' ? 'play' : 'alert');
        return '<div class="tl-item">' +
          '<span class="tl-icon ' + cls + '">' + U.icon(ic) + '</span>' +
          '<div class="tl-title">' + U.esc(r.job_name || r.job_id || '任务') +
          ' <span class="muted">· ' + U.esc(r.window_name || '') + '</span></div>' +
          '<div class="tl-sum">' + U.esc(r.summary || '（无摘要）') + '</div>' +
          '<div class="tl-time">' + U.esc(U.relTime(r.started_at)) + ' · ' + U.esc(U.fmtTime(r.started_at)) + '</div>' +
          '</div>';
      }).join('');
    },

    load: function () {
      var host = document.getElementById('ov-stats');
      if (host) host.innerHTML = U.skeleton('cards');
      return Promise.all([A.status(), A.overview()]).then(function (res) {
        var st = res[0], ov = res[1] || {};
        Overview.data = ov;
        Overview.showBanner(st && st.llm_configured === false);
        Overview.renderStats(st, ov);
        Overview.renderWindows(ov.windows);
        Overview.renderJobs(ov.jobs);
        Overview.renderRuns(ov.recent_runs);
      }).catch(function (e) {
        U.toast('error', '总览加载失败：' + e.message);
        if (host) host.innerHTML = '';
      });
    }
  };

  /* ============================================================
     工位：列表 + 详情 + 会话流
     ============================================================ */
  var Windows = {
    list: [],
    selectedId: null,
    detail: null,
    messages: [],
    keyword: '',

    load: function () {
      return A.windows.list().then(function (rows) {
        Windows.list = rows || [];
        Windows.renderList();
        if (!Windows.selectedId && Windows.list.length) {
          Windows.select(Windows.list[0].id);
        } else if (Windows.selectedId) {
          Windows.select(Windows.selectedId);
        } else {
          Windows.renderEmptyDetail();
        }
      }).catch(function (e) {
        U.toast('error', '工位列表加载失败：' + e.message);
      });
    },

    renderList: function () {
      var host = document.getElementById('win-list');
      if (!host) return;
      var kw = Windows.keyword.trim().toLowerCase();
      var rows = Windows.list.filter(function (w) {
        return !kw || String(w.name || '').toLowerCase().indexOf(kw) >= 0 ||
          String(w.role || '').toLowerCase().indexOf(kw) >= 0;
      });
      if (!rows.length) {
        host.innerHTML = U.empty({
          art: 'box', title: kw ? '没有匹配的工位' : '还没有工位',
          desc: kw ? '换个关键词试试。' : '点击右上角「新建工位」创建第一个执行窗口。',
          action: kw ? '' : '新建工位', actionId: 'new-window'
        });
        return;
      }
      host.innerHTML = rows.map(function (w) {
        var key = String(w.status || '').toLowerCase();
        return '<div class="win-item' + (w.id === Windows.selectedId ? ' active' : '') + '" data-win-id="' + U.esc(w.id) + '">' +
          statusDot(w.status) +
          '<div class="win-item-info">' +
          '<div class="win-item-name">' + U.esc(w.name) + '</div>' +
          '<div class="win-item-sub">' + U.esc(w.role || '') + ' · ' + U.esc(w.model || w.resolved_model || '未设模型') +
          (w.model ? '' : '（继承）') + '</div>' +
          '</div>' +
          (key === 'running' || key === 'busy' ? '<span class="badge badge-blue">运行</span>' : '') +
          trustBadge(w) +
          '</div>';
      }).join('');
      U.$$('.win-item', host).forEach(function (item) {
        item.addEventListener('click', function () {
          Windows.select(item.getAttribute('data-win-id'));
        });
      });
    },

    renderEmptyDetail: function () {
      var host = document.getElementById('win-detail');
      if (host) host.innerHTML = U.empty({
        art: 'chat', title: '选择一个工位', desc: '左侧选择工位查看会话流，或新建一个工位开始工作。'
      });
    },

    select: function (id) {
      Windows.selectedId = id;
      Windows.renderList();
      var host = document.getElementById('win-detail');
      if (!host) return;
      host.innerHTML = U.skeleton();
      Promise.all([
        A.windows.get(id),
        A.windows.messages(id, 100)
      ]).then(function (res) {
        if (Windows.selectedId !== id) return;
        Windows.detail = res[0];
        Windows.messages = (res[1] && res[1].messages) || [];
        Windows.renderDetail();
      }).catch(function (e) {
        if (Windows.selectedId !== id) return;
        host.innerHTML = '<div class="empty"><div class="empty-title">加载失败</div><div class="empty-desc">' +
          U.esc(e.message) + '</div></div>';
      });
    },

    /* ---------- 会话气泡渲染 ---------- */
    renderContent: function (text) {
      var s = String(text || '');
      var out = [], parts = s.split(/```/);
      parts.forEach(function (chunk, i) {
        if (i % 2 === 1) {
          var body = chunk.replace(/^[a-zA-Z0-9_+-]*\n/, '');
          out.push('<pre class="mono">' + U.esc(body) + '</pre>');
        } else {
          out.push(U.esc(chunk).replace(/\n/g, '<br>'));
        }
      });
      return out.join('');
    },

    /**
     * 工具消息展示名：优先 DTO.tool_name（meta 带出）→ content JSON 里的
     * tool/name/function/action → 'tool' 兜底。
     * @param {Object} m 消息 DTO（role=tool，含 tool_name / meta）
     * @returns {string} 工具名
     */
    toolName: function (m) {
      var n = String((m && m.tool_name) || ((m && m.meta) || {}).tool_name || '');
      if (n) return n;
      try {
        var obj = JSON.parse(m && m.content);
        if (obj && typeof obj === 'object') {
          n = obj.tool || obj.name || obj.function || obj.action || '';
        }
      } catch (e) { /* 非 JSON：走兜底 */ }
      return String(n || 'tool');
    },

    renderToolCard: function (content, toolName) {
      var name = String(toolName || ''), args = '', result = String(content || '');
      try {
        var obj = JSON.parse(content);
        if (obj && typeof obj === 'object') {
          if (!name) name = obj.tool || obj.name || obj.function || obj.action || '';
          var a = obj.args || obj.arguments || obj.params || obj.input || null;
          if (a !== null && a !== undefined) {
            args = typeof a === 'string' ? a : JSON.stringify(a);
          }
          var r = obj.result !== undefined ? obj.result :
            (obj.output !== undefined ? obj.output :
              (obj.content !== undefined ? obj.content : null));
          if (r !== null && r !== undefined) {
            result = typeof r === 'string' ? r : JSON.stringify(r, null, 2);
          }
          if (args === '' && result === String(content || '')) {
            result = JSON.stringify(obj, null, 2);
          }
        }
      } catch (e) { /* 非 JSON：原样展示 */ }
      return '<div class="tool-card">' +
        '<div class="tool-head">' + U.icon('chevron-r', 'ic-chevron') +
        '<span class="badge badge-blue tool-badge mono">' + U.esc(name || 'tool') + '</span>' +
        '<span class="tool-args">' + U.esc(args || '（无参数）') + '</span>' +
        '</div>' +
        '<div class="tool-body">' + U.esc(result) + '</div>' +
        '</div>';
    },

    /* ---------- 消息渲染：tool 分组（同轮多工具折叠）+ tool_name 徽标 ---------- */

    /**
     * 单条非 tool 消息 HTML（气泡 + 头像 + 时间/tokens 元信息）。
     * @param {Object} m 消息 DTO
     * @param {string} role 角色（小写）
     * @returns {string} 消息 HTML
     */
    renderMsg: function (m, role) {
      var avatar = role === 'user' ? '我' : (role === 'system' ? 'S' : 'AI');
      return '<div class="msg ' + U.esc(role) + '">' +
        '<div class="msg-avatar">' + avatar + '</div>' +
        '<div style="min-width:0">' +
        '<div class="msg-bubble">' + Windows.renderContent(m.content) + '</div>' +
        '<div class="msg-meta"><span>' + U.esc(U.fmtClock(m.created_at)) + '</span>' +
        (m.tokens ? '<span class="num">' + U.esc(m.tokens) + ' tok</span>' : '') +
        '</div></div></div>';
    },

    /**
     * 连续 role=tool 消息组的 HTML：单条直接渲染；多条折叠成
     * 「调用了 N 个工具」（可展开），每张卡带 tool_name 徽标。
     * @param {Array<Object>} group 连续的 tool 消息（按时间升序）
     * @returns {string} 消息 HTML
     */
    renderToolGroup: function (group) {
      var cards = group.map(function (m) {
        return Windows.renderToolCard(m.content, Windows.toolName(m));
      }).join('');
      var time = U.fmtClock(group[0].created_at);
      var bubble;
      if (group.length === 1) {
        bubble = '<div class="msg-bubble">工具调用' + cards + '</div>';
      } else {
        var badges = group.map(function (m) {
          return '<span class="badge badge-blue tool-badge mono">' +
            U.esc(Windows.toolName(m)) + '</span>';
        }).join('');
        bubble = '<div class="msg-bubble"><div class="tool-group">' +
          '<div class="tool-group-head">' + U.icon('chevron-r', 'ic-chevron') +
          '<span>调用了 ' + group.length + ' 个工具</span>' + badges + '</div>' +
          '<div class="tool-group-body">' + cards + '</div>' +
          '</div></div>';
      }
      return '<div class="msg tool">' +
        '<div class="msg-avatar">T</div>' +
        '<div style="min-width:0">' + bubble +
        '<div class="msg-meta"><span>' + U.esc(time) + '</span></div></div></div>';
    },

    renderMessages: function () {
      var msgs = Windows.messages.slice().sort(function (a, b) {
        return (U.parseTime(a.created_at) || 0) - (U.parseTime(b.created_at) || 0);
      });
      if (!msgs.length) {
        return U.empty({
          art: 'chat', title: '会话为空',
          desc: '在下方输入框给这位员工派第一个活，Enter 发送。'
        });
      }
      var out = [];
      var lastDay = '';
      var i = 0;
      while (i < msgs.length) {
        var m = msgs[i];
        var d = U.parseTime(m.created_at);
        var day = d ? U.fmtDate(d) : '未知日期';
        if (day !== lastDay) {
          lastDay = day;
          out.push('<div class="chat-day"><span>' + U.esc(day) + '</span></div>');
        }
        var role = String(m.role || 'assistant').toLowerCase();
        if (role === 'tool') {
          var group = [];
          while (i < msgs.length &&
                 String(msgs[i].role || 'assistant').toLowerCase() === 'tool') {
            group.push(msgs[i]);
            i++;
          }
          out.push(Windows.renderToolGroup(group));
        } else {
          out.push(Windows.renderMsg(m, role));
          i++;
        }
      }
      return out.join('');
    },

    renderTodos: function (todos) {
      if (!todos || !todos.length) {
        return '<div class="todo-panel"><h4>待办清单</h4><p class="muted small">暂无待办，由会话自动维护。</p></div>';
      }
      return '<div class="todo-panel"><h4>待办清单 · ' +
        todos.filter(function (t) { return t.done; }).length + '/' + todos.length + '</h4>' +
        todos.map(function (t) {
          return '<div class="todo-item' + (t.done ? ' done' : '') + '" data-todo-id="' + U.esc(t.id) + '">' +
            '<span class="todo-box">' + U.icon('check', 'sm') + '</span>' +
            '<span class="todo-text">' + U.esc(t.content) + '</span></div>';
        }).join('') + '</div>';
    },

    renderDetail: function () {
      var w = Windows.detail;
      var host = document.getElementById('win-detail');
      if (!host || !w) return;
      var key = String(w.status || '').toLowerCase();
      var isPaused = key === 'paused' || key === 'pause';
      host.innerHTML =
        '<div class="win-detail-head">' +
        '<div><div class="win-detail-title">' + statusDot(w.status) + U.esc(w.name) +
        '<span class="badge ' + (isPaused ? 'badge-violet' : 'badge-green') +
        '" id="win-status-badge">' +
        U.esc(statusLabel(w.status)) + '</span>' + trustBadge(w) + '</div>' +
        '<div class="card-sub" style="margin-top:4px">' + U.esc(w.workspace_path || '') + '</div></div>' +
        '<div class="win-detail-actions">' +
        '<button class="btn btn-soft btn-sm" data-act="rename">' + U.icon('edit', 'sm') + '改名</button>' +
        '<button class="btn btn-soft btn-sm" data-act="model">' + U.icon('cpu', 'sm') + '模型 / 模式</button>' +
        '<button class="btn btn-soft btn-sm" data-act="params">' + U.icon('slider', 'sm') + '参数</button>' +
        (isPaused
          ? '<button class="btn btn-primary btn-sm" data-act="resume">' + U.icon('play', 'sm') + '恢复</button>'
          : '<button class="btn btn-soft btn-sm" data-act="pause">' + U.icon('pause', 'sm') + '暂停</button>') +
        '<button class="btn btn-ghost btn-sm" data-act="compress" title="压缩上下文">压缩</button>' +
        '<button class="btn btn-ghost btn-sm" data-act="inject" title="注入系统信息">注入</button>' +
        '<button class="btn btn-ghost btn-sm" data-act="reset" title="重置上下文">重置</button>' +
        '<button class="btn btn-danger btn-sm" data-act="delete">' + U.icon('trash', 'sm') + '</button>' +
        '</div></div>' +

        '<div class="win-detail-meta">' +
        '<span class="meta-pill">' + U.icon('user', 'sm') + U.esc(w.role || 'worker') + '</span>' +
        '<span class="meta-pill">' + U.icon('layers', 'sm') + U.esc(w.mode || 'BUILD') + '</span>' +
        '<span class="meta-pill">' + U.icon('cpu', 'sm') +
        (w.model
          ? '<span class="mono">' + U.esc(w.model) + '</span><span class="hint-inline">指定</span>'
          : '<span class="mono">' + U.esc(w.resolved_model || '未设置') + '</span><span class="hint-inline">继承</span>') +
        '</span></span>' +
        (w.llm_profile ? '<span class="meta-pill">' + U.icon('layers', 'sm') + '配置套 <span class="mono">' + U.esc(w.llm_profile) + '</span></span>' : '') +
        (w.tokens ? '<span class="meta-pill">' + U.icon('activity', 'sm') + '<span class="num">' + U.esc(w.tokens) + '</span> tokens</span>' : '') +
        (w.current_task ? '<span class="meta-pill">' + U.icon('play', 'sm') + U.esc(w.current_task) + '</span>' : '') +
        '</div>' +

        '<div class="ctx-usage" id="win-usage" data-win-id="' + U.esc(w.id) + '"></div>' +

        Windows.renderTodos(w.todos) +

        '<div class="chat" id="win-chat">' + Windows.renderMessages() + '</div>' +

        '<div class="composer">' +
        '<textarea id="win-composer" rows="1" placeholder="手动派活…（Enter 发送，Shift+Enter 换行）"></textarea>' +
        '<button class="btn btn-primary" id="win-send">' + U.icon('send', 'sm') + '派活</button>' +
        '</div>';

      /* 恢复流式半截气泡（若有），再滚动到底部 */
      Windows.renderStream();
      var chat = document.getElementById('win-chat');
      if (chat) chat.scrollTop = chat.scrollHeight;

      /* 工具卡折叠 / 多工具组折叠 */
      U.$$('.tool-head, .tool-group-head', host).forEach(function (h) {
        h.addEventListener('click', function () {
          h.parentNode.classList.toggle('open');
        });
      });
      /* 待办勾选动效（展示态） */
      U.$$('.todo-box', host).forEach(function (b) {
        b.addEventListener('click', function () {
          b.parentNode.classList.toggle('done');
          U.toast('info', '待办由会话自动维护，此处仅预览状态');
        });
      });
      /* 操作按钮 */
      U.$$('.win-detail-actions [data-act]', host).forEach(function (btn) {
        btn.addEventListener('click', function () {
          Windows.action(btn.getAttribute('data-act'));
        });
      });
      /* 派活 */
      var composer = document.getElementById('win-composer');
      var send = document.getElementById('win-send');
      if (send) send.addEventListener('click', Windows.dispatch);
      if (composer) {
        composer.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            Windows.dispatch();
          }
        });
      }
      /* 上下文用量条（30s 定时刷新由 Params 模块维护） */
      var paramsMod = (global.DogBuddyViews || {}).Params;
      if (paramsMod) paramsMod.mountUsage(Windows.selectedId);
    },

    /* ---------- 打字机流式输出（SSE window.stream，多窗口隔离） ---------- */

    /** @type {Object.<string, {text: string}>} window_id → 流式缓冲（各窗口互不串屏） */
    streams: {},
    /** @type {Object.<string, {status: string, code: string}>} 最近一次运行终态（按窗口） */
    lastRun: {},
    /** @type {Object.<string, {status: string, code: string}>} 最近一次运行终态（按窗口） */
    lastRun: {},
    /** @type {number|null} 完成检测轮询定时器 */
    streamTimer: null,
    /** @type {string} 正在观察流式完成的窗口 id */
    streamWin: '',
    /** @type {number} 轮询截止时间戳（超时兜底） */
    streamDeadline: 0,
    /** @type {Object.<string, number>} dispatch 前已存在的消息 id 集合（判定新 assistant 消息） */
    streamKnown: {},

    /**
     * SSE window.stream：把 delta 拼进对应窗口的缓冲并刷新「正在输入…」气泡。
     * SSE 不可用时直接忽略（退回原轮询模式，不留半截气泡）。
     * @param {Object} payload {window_id, delta}
     * @returns {void}
     */
    onStream: function (payload) {
      payload = payload || {};
      var wid = String(payload.window_id || '');
      var delta = payload.delta;
      if (!wid || delta === undefined || delta === null) return;
      if (!global.SSE || !global.SSE.state || !global.SSE.state.connected) return;
      var st = Windows.streams[wid];
      if (!st) {
        st = { text: '' };
        Windows.streams[wid] = st;
      }
      st.text += String(delta);
      if (wid === String(Windows.selectedId)) Windows.renderStream();
    },

    /**
     * SSE `window.run_finished`：一次运行结束（成功/失败/限速暂停）。
     *
     * 为什么需要它：以前前端只能靠"轮询有没有新的 assistant 消息"判断结束；
     * LLM 掉线时落库的是 system 消息 → "正在输入…"一直挂到超时，而且**看不到错误码**
     * （owner 实测："显示的还是正在输入…但确实掉线了，也没有错误代码"）。
     * @param {Object} payload {window_id, status, error_code, error}
     * @returns {void}
     */
    onRunFinished: function (payload) {
      payload = payload || {};
      var wid = String(payload.window_id || '');
      if (!wid) return;
      var status = String(payload.status || 'ok');
      Windows.lastRun[wid] = { status: status, code: String(payload.error_code || '') };
      /* 立刻收起流式气泡（不等 180s 轮询超时） */
      if (Windows.streamWin === wid) Windows.finishStream(wid);
      else {
        delete Windows.streams[wid];
        if (wid === String(Windows.selectedId)) Windows.renderStream();
      }
      if (status !== 'ok') {
        var code = String(payload.error_code || '');
        var text = String(payload.error || '').trim();
        U.toast('error', '工位「' + (payload.window_name || wid) + '」运行失败' +
          (code ? ' [' + code + ']' : '') + (text ? '：' + text.slice(0, 120) : ''));
      }
      if (wid === String(Windows.selectedId)) {
        A.windows.messages(wid, 100).then(function (res) {
          if (Windows.streamWin === wid) return;
          Windows.messages = (res && res.messages) || Windows.messages;
          if (wid === String(Windows.selectedId)) Windows.renderChatOnly();
        }).catch(function () { /* 忽略 */ });
        Windows.select(wid);
      }
    },

    /**
     * SSE `window.run_finished`：一次运行结束（成功/失败/限速暂停）。
     *
     * 为什么需要它：以前前端只能靠"轮询有没有新的 assistant 消息"判断结束；
     * LLM 掉线时落库的是 system 消息 → "正在输入…"一直挂到超时，而且**看不到错误码**
     * （owner 实测："显示的还是正在输入…但确实掉线了，也没有错误代码"）。
     * @param {Object} payload {window_id, status, error_code, error}
     * @returns {void}
     */
    onRunFinished: function (payload) {
      payload = payload || {};
      var wid = String(payload.window_id || '');
      if (!wid) return;
      var status = String(payload.status || 'ok');
      Windows.lastRun[wid] = { status: status, code: String(payload.error_code || '') };
      /* 立刻收起流式气泡（不等 180s 轮询超时） */
      if (Windows.streamWin === wid) Windows.finishStream(wid);
      else {
        delete Windows.streams[wid];
        if (wid === String(Windows.selectedId)) Windows.renderStream();
      }
      if (status !== 'ok') {
        var code = String(payload.error_code || '');
        var text = String(payload.error || '').trim();
        U.toast('error', '工位「' + (payload.window_name || wid) + '」运行失败' +
          (code ? ' [' + code + ']' : '') + (text ? '：' + text.slice(0, 120) : ''));
      }
      if (wid === String(Windows.selectedId)) {
        A.windows.messages(wid, 100).then(function (res) {
          if (Windows.streamWin === wid) return;
          Windows.messages = (res && res.messages) || Windows.messages;
          if (wid === String(Windows.selectedId)) Windows.renderChatOnly();
        }).catch(function () { /* 忽略 */ });
        Windows.select(wid);
      }
    },

    /**
     * 渲染 / 更新 / 移除当前窗口的「正在输入…」流式气泡（追加在对话流末尾）。
     * @returns {void}
     */
    renderStream: function () {
      var chat = document.getElementById('win-chat');
      if (!chat) return;
      var st = Windows.streams[String(Windows.selectedId)];
      var node = document.getElementById('win-stream-bubble');
      if (!st || !st.text) {
        if (node && node.parentNode) node.parentNode.removeChild(node);
        return;
      }
      /* v0.10.15：**别整块重建 DOM**。以前每个 delta 都 outerHTML 换一次气泡，
         浏览器要重排+重绘整块，视口跟着抖 —— owner 的原话是"输出窗口会闪动"。
         现在只更新一个文本节点（配 CSS white-space: pre-wrap 保留换行）。 */
      if (!node) {
        chat.insertAdjacentHTML('beforeend',
          '<div class="msg assistant stream-msg" id="win-stream-bubble">' +
          '<div class="msg-avatar">AI</div>' +
          '<div style="min-width:0">' +
          '<div class="msg-bubble"><span class="stream-tag">正在输入…</span>' +
          '<span class="stream-text"></span><span class="stream-caret"></span></div>' +
          '</div></div>');
        node = document.getElementById('win-stream-bubble');
      }
      var body = node.querySelector('.stream-text');
      if (body && body.textContent !== st.text) body.textContent = st.text;
      Windows.stickToBottom(chat);
    },

    /**
     * 只有用户本来就贴在底部时才自动滚动（否则等于抢用户的滚动条，
     * 也是一种"闪"）。历史上滚 → 不动。
     * @param {HTMLElement} chat 消息容器
     * @returns {void}
     */
    stickToBottom: function (chat) {
      if (!chat) return;
      var gap = chat.scrollHeight - chat.scrollTop - chat.clientHeight;
      if (gap > 80) return;               // 用户在看上面的内容，别打扰
      chat.scrollTop = chat.scrollHeight;
    },

    /**
     * 只重渲染对话流区域（不动输入框 / 详情头部），末尾恢复流气泡。
     * @returns {void}
     */
    renderChatOnly: function () {
      var chat = document.getElementById('win-chat');
      if (!chat) return;
      chat.innerHTML = Windows.renderMessages();
      Windows.renderStream();
      chat.scrollTop = chat.scrollHeight;
    },

    /**
     * 清理某个窗口（或全部）的流式缓冲。
     * @param {string} [wid] 窗口 id；缺省清全部
     * @returns {void}
     */
    clearStream: function (wid) {
      if (wid) delete Windows.streams[String(wid)];
      else Windows.streams = {};
      Windows.renderStream();
    },

    /**
     * dispatch 后开始观察流式完成：每 2s 轮询 messages，出现「dispatch 前
     * 没有的 assistant 消息」即视为完整消息落库，用它替换半截气泡；
     * 3 分钟兜底停。SSE 不可用时这就是原轮询模式本身。
     * @param {string} id 窗口 id
     * @returns {void}
     */
    watchStream: function (id) {
      id = String(id);
      Windows.streamWin = id;
      Windows.streamDeadline = Date.now() + 180000;
      Windows.streamKnown = {};
      Windows.messages.forEach(function (m) {
        Windows.streamKnown[String(m.id)] = 1;
      });
      if (Windows.streamTimer) clearInterval(Windows.streamTimer);
      Windows.streamTimer = setInterval(function () {
        Windows.pollMessages(id);
      }, 2000);
      setTimeout(function () { Windows.pollMessages(id); }, 600);
    },

    /**
     * 轮询一次 messages：判定完整消息是否落库（见 watchStream）。
     * @param {string} id 窗口 id
     * @returns {void}
     */
    pollMessages: function (id) {
      id = String(id);
      if (Windows.streamWin !== id) return;
      if (Date.now() > Windows.streamDeadline) {
        Windows.finishStream(id, true);
        return;
      }
      A.windows.messages(id, 100).then(function (res) {
        if (Windows.streamWin !== id) return;
        var msgs = (res && res.messages) || [];
        /* v0.10.15：LLM 掉线时 loop 落库的是 **system** 消息（`[LLM error/xxx]`），
           原来只认 assistant → "正在输入…"会一直挂到 180s 超时。
           注意：瞬态重试也写 system（`[LLM 瞬态失败 … 继续重试]`）——那不是终态，
           不能收尾（否则重试期间气泡就没了，看起来像"跑完了"）。 */
        var fresh = msgs.some(function (m) {
          if (Windows.streamKnown[String(m.id)]) return false;
          var role = String(m.role || '').toLowerCase();
          if (role === 'assistant') return true;
          if (role === 'system') {
            var c = String(m.content || '');
            return c.indexOf('[LLM error/') >= 0 || c.indexOf('[限速暂停]') >= 0;
          }
          return false;
        });
        if (fresh) {
          Windows.messages = msgs;
          Windows.finishStream(id);
          if (String(Windows.selectedId) === id) Windows.renderChatOnly();
        } else if (!Windows.streams[id] && String(Windows.selectedId) === id) {
          /* 无流式气泡（SSE 不可用 / 流未开始）：保持原轮询渲染节奏 */
          Windows.messages = msgs;
          Windows.renderChatOnly();
        }
      }).catch(function () { /* 忽略 */ });
    },

    /**
     * 结束流式观察并清掉半截气泡。
     * @param {string} id 窗口 id
     * @param {boolean} [timeout] 是否因超时结束（会提示）
     * @returns {void}
     */
    finishStream: function (id, timeout) {
      if (Windows.streamTimer) {
        clearInterval(Windows.streamTimer);
        Windows.streamTimer = null;
      }
      if (id) delete Windows.streams[String(id)];
      if (Windows.streamWin === String(id)) Windows.streamWin = '';
      Windows.streamDeadline = 0;
      Windows.renderStream();
      if (timeout) U.toast('warn', '流式输出等待较久，已切回轮询刷新');
    },

    /**
     * SSE window.status：就地刷新总览工位卡 / 工位列表项 / 详情头的状态；
     * 状态回空闲且有半截气泡时立即拉 messages 判定完成。
     * @param {Object} payload {window_id, status, current_task?}
     * @returns {void}
     */
    onWindowStatus: function (payload) {
      payload = payload || {};
      var wid = String(payload.window_id || '');
      var status = String(payload.status || '');
      if (!wid || !status) return;
      var key = status.toLowerCase();
      var label = statusLabel(status);
      var paused = key.indexOf('pause') >= 0;
      var dotCls = 'dot ' + (STATUS_DOT[key] || 'dot-idle');

      /* 总览工位卡片 */
      U.$$('#ov-win-grid .win-card').forEach(function (card) {
        if (card.getAttribute('data-win-id') !== wid) return;
        var dot = U.$('.dot', card);
        if (dot) dot.className = dotCls;
        var badge = U.$('.win-card-head .badge', card);
        if (badge) badge.textContent = label;
        card.classList.toggle('running',
          key === 'running' || key === 'busy' || key === 'working');
      });
      /* 工位列表项 */
      U.$$('#win-list .win-item').forEach(function (item) {
        if (item.getAttribute('data-win-id') !== wid) return;
        var dot = U.$('.dot', item);
        if (dot) dot.className = dotCls;
        var badge = U.$('.badge', item);
        if (badge) badge.textContent = label;
      });
      /* 选中窗口的详情头 */
      if (wid === String(Windows.selectedId)) {
        var sb = document.getElementById('win-status-badge');
        if (sb) {
          sb.className = 'badge ' + (paused ? 'badge-violet' : 'badge-green');
          sb.textContent = label;
        }
        if ((key === 'idle' || key === 'stopped') && Windows.streams[wid]) {
          Windows.pollMessages(wid);
        }
      }
    },

    /**
     * SSE 断开（sse.down）：清掉半截气泡，退回原轮询模式。
     * @returns {void}
     */
    onSseDown: function () {
      Windows.streams = {};
      Windows.renderStream();
    },

    dispatch: function () {
      var input = /** @type {HTMLInputElement} */ (document.getElementById('win-composer'));
      if (!input) return;
      var text = input.value.trim();
      if (!text) return;
      var id = Windows.selectedId;
      input.value = '';
      A.windows.dispatch(id, text).then(function (r) {
        U.toast('success', (r && r.queued) ? '已加入执行队列' : '已派发');
        /* 流式气泡实时增长，轮询 messages 等完整消息落库后替换（SSE 不可用即原轮询） */
        Windows.watchStream(id);
      }).catch(function (e) {
        input.value = text;
        U.toast('error', '派活失败：' + e.message);
      });
    },

    action: function (act) {
      var w = Windows.detail;
      if (!w) return;
      var id = w.id;
      if (act === 'pause' || act === 'resume') {
        (act === 'pause' ? A.windows.pause(id) : A.windows.resume(id)).then(function () {
          U.toast('success', act === 'pause' ? '已暂停' : '已恢复');
          Windows.select(id);
        }).catch(function (e) { U.toast('error', e.message); });
      } else if (act === 'compress') {
        A.windows.compress(id).then(function () {
          U.toast('success', '上下文已压缩');
          Windows.select(id);
        }).catch(function (e) { U.toast('error', e.message); });
      } else if (act === 'reset') {
        UI.confirm({
          title: '重置上下文', danger: true, okLabel: '重置',
          text: '将清空该工位的会话上下文，确定继续？'
        }).then(function (ok) {
          if (!ok) return;
          A.windows.reset(id).then(function () {
            U.toast('success', '上下文已重置');
            Windows.select(id);
          }).catch(function (e) { U.toast('error', e.message); });
        });
      } else if (act === 'inject') {
        var m = U.modal({
          title: '注入系统信息',
          body: '<div class="field"><span class="field-label">注入内容</span>' +
            '<textarea class="textarea" id="inject-text" placeholder="将作为 system 信息注入该工位上下文…"></textarea></div>',
          buttons: [
            { label: '取消', cls: 'btn-ghost' },
            {
              label: '注入', cls: 'btn-primary',
              onClick: function (body) {
                var text = body.querySelector('#inject-text').value.trim();
                if (!text) { U.toast('warn', '请输入注入内容'); return false; }
                A.windows.inject(id, text).then(function () {
                  U.toast('success', '已注入');
                  Windows.select(id);
                }).catch(function (e) { U.toast('error', e.message); });
              }
            }
          ]
        });
        return m;
      } else if (act === 'rename') {
        U.modal({
          title: '重命名工位',
          body: '<div class="field"><span class="field-label">新名称</span>' +
            '<input class="input" id="rename-input" value="' + U.esc(w.name) + '"></div>',
          buttons: [
            { label: '取消', cls: 'btn-ghost' },
            {
              label: '保存', cls: 'btn-primary',
              onClick: function (body) {
                var name = body.querySelector('#rename-input').value.trim();
                if (!name) { U.toast('warn', '名称不能为空'); return false; }
                A.windows.update(id, { name: name }).then(function () {
                  U.toast('success', '已重命名');
                  Windows.load();
                }).catch(function (e) { U.toast('error', e.message); });
              }
            }
          ]
        });
      } else if (act === 'model') {
        Windows.openModelModal(w);
      } else if (act === 'params') {
        // 审查报告 P1-16：按钮在（data-act="params"）、渲染函数在
        // （views-params.js 的 Params.openPanel），但这里既没有分支也没有 else，
        // 点下去静默无反应 —— 典型的"有零件、没接线"。
        var V = global.DogBuddyViews || {};
        if (V.Params && typeof V.Params.openPanel === 'function') {
          V.Params.openPanel(w.id);
        } else {
          U.toast('warn', '参数面板未加载（views-params 未接线）');
        }
      } else if (act === 'chain') {
        Windows.openChainModal(w);
      } else if (act === 'delete') {
        UI.confirm({
          title: '删除工位', danger: true, okLabel: '删除',
          text: '将删除工位「' + w.name + '」及其会话，此操作不可恢复。确定继续？'
        }).then(function (ok) {
          if (!ok) return;
          A.windows.remove(id).then(function () {
            U.toast('success', '工位已删除');
            Windows.selectedId = null;
            Windows.load();
          }).catch(function (e) { U.toast('error', e.message); });
        });
      } else {
        // 未知 action 必须出声。P1-16 的根因就是"既没有分支也没有 else"，
        // 于是按钮点了静默无反应，谁也不知道是漏接线还是执行失败。
        U.toast('warn', '未知操作：' + act);
      }
    },

    openModelModal: function (w) {
      U.modal({
        title: '修改模型 / 模式',
        body:
          '<div class="field"><span class="field-label">模型（从模型目录里选）</span>' +
          '<select class="select mono" id="model-select"><option value="">（加载模型目录中…）</option></select>' +
          '<input class="input mono" id="model-input" value="' + U.esc(w.model || '') + '" placeholder="留空 = 继承管理员模型' +
          (w.resolved_model && !w.model ? '（当前 ' + U.esc(w.resolved_model) + '）' : '') + '" style="margin-top:6px">' +
          '<div class="hint">下拉里是**你已经配好的模型**（各带自己的服务商与密钥）；' +
          '留空即跟随管理员模型。</div></div>' +
          '<div class="set-row" style="border:0;padding-top:2px">' +
          '<button class="btn btn-soft btn-sm" id="model-fetch" type="button">从服务商拉取更多</button>' +
          '<span class="hint" id="model-src"></span></div>' +
          '<div class="field"><span class="field-label">模式</span>' +
          '<select class="select" id="mode-input">' +
          ['BUILD', 'PLAN', 'CHAT', 'REVIEW'].map(function (m) {
            return '<option' + (String(w.mode).toUpperCase() === m ? ' selected' : '') + '>' + m + '</option>';
          }).join('') + '</select></div>' +
          '<div class="field"><span class="field-label">LLM 配置套</span>' +
          '<select class="select" id="model-profile"><option value="">默认</option></select>' +
          '<div class="hint">空 = 使用默认配置套；更改后新消息将按该套发起。</div></div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: '保存', cls: 'btn-primary',
            onClick: function (body) {
              var model = body.querySelector('#model-input').value.trim();
              var mode = body.querySelector('#mode-input').value;
              A.windows.update(w.id, {
                model: model, mode: mode,
                llm_profile: body.querySelector('#model-profile').value
              }).then(function () {
                U.toast('success', '已更新模型 / 模式');
                Windows.select(w.id);
              }).catch(function (e) { U.toast('error', e.message); });
            }
          }
        ],
        body_after: null
      });
      /* LLM 配置套下拉（选项来自 GET /api/settings/profiles 的 names） */
      var ProfilesMod = (global.DogBuddyViews || {}).Profiles;
      if (ProfilesMod) {
        ProfilesMod.fillSelect(document.getElementById('model-profile'), w.llm_profile || '', '默认');
      }
      /* 拉取模型（用已保存的配置，可按所选配置套拉取） */
      /* v0.10.15：打开就能从**模型目录**里选。
         以前必须先点"拉取模型"，而拉取问的是服务商的**全量**列表（99 个），
         和"我自己配好的那几个"不是一回事（owner："工位点击修改模型，
         应该从模型列表里直接选配置好的模型"）。 */
      var catSel = document.getElementById('model-select');
      var catInput = document.getElementById('model-input');
      var catSrc = document.getElementById('model-src');
      if (catSel && catInput) {
        A.models.list().then(function (data) {
          var models = (data && data.models) || [];
          var cur = (data && data.current) || {};
          var opts = ['<option value="">（跟随管理员模型' +
            (cur.model ? '：' + U.esc(cur.model) : '') + '）</option>'];
          models.filter(function (m) { return m.enabled !== false; })
            .forEach(function (m) {
              var label = (m.display_name || m.model) +
                (m.provider && m.provider !== 'openai_compat' ? ' · ' + m.provider : '') +
                (m.model === cur.model ? ' · 管理员当前' : '');
              opts.push('<option value="' + U.esc(m.model) + '"' +
                (m.model === (w.model || '') ? ' selected' : '') + '>' + U.esc(label) + '</option>');
            });
          catSel.innerHTML = opts.join('');
          if (catSrc) {
            catSrc.textContent = models.length
              ? ('目录里 ' + models.length + ' 个模型')
              : '模型目录是空的 —— 去「模型管理」页拉取或新增';
          }
          catSel.addEventListener('change', function () {
            catInput.value = catSel.value || '';
          });
        }).catch(function () {
          catSel.innerHTML = '<option value="">（目录加载失败，可直接在下面手输）</option>';
        });
      }

      var fetchBtn = document.getElementById('model-fetch');
      if (fetchBtn) {
        fetchBtn.addEventListener('click', function () {
          fetchBtn.disabled = true;
          fetchBtn.textContent = '拉取中…';
          var profileSel = document.getElementById('model-profile');
          A.settings.models('', '', '', profileSel ? profileSel.value : '').then(function (r) {
            /* 全量进下拉（datalist 会按输入框已有文字过滤，所以不再用它） */
            U.fillModelSelect('model-select', r.models || [], 'model-input');
            if (r.error) U.toast('warn', '拉取模型失败：' + r.error);
            else U.toast('success', '已拉取 ' + ((r.models || []).length) + ' 个模型');
          }).catch(function (e) {
            U.toast('error', '拉取模型失败：' + e.message);
          }).then(function () {
            fetchBtn.disabled = false;
            fetchBtn.textContent = '拉取模型';
          });
        });
      }
    },

    openCreateModal: function () {
      U.modal({
        title: '新建工位',
        body:
          '<div class="field"><span class="field-label">名称 *</span>' +
          '<input class="input" id="nw-name" placeholder="如：前端开发-1"></div>' +
          '<div class="set-two">' +
          '<div class="field"><span class="field-label">角色</span>' +
          '<select class="select" id="nw-role">' +
          '<option value="worker_programmer">worker_programmer（程序员）</option>' +
          '<option value="supervisor">supervisor（主管 · 负责一个/多个项目）</option>' +
          '</select></div>' +
          '<div class="field"><span class="field-label">模式</span>' +
          '<select class="select" id="nw-mode"><option>BUILD</option><option>PLAN</option><option>CHAT</option><option>REVIEW</option></select>' +
          '</div></div>' +
          '<div class="field"><span class="field-label">工作区路径（可选）</span>' +
          '<input class="input" id="nw-ws" placeholder="留空则使用默认工作区"></div>' +
          '<div class="set-two">' +
          '<div class="field"><span class="field-label">模型（可选）</span>' +
          '<input class="input" id="nw-model" placeholder="留空 = 继承管理员模型' +
          (global.App && global.App.llmModel ? '（当前 ' + U.esc(global.App.llmModel) + '）' : '') + '">' +
          '<div class="hint">员工默认与管理员的模型保持一致；只有需要单独指定时才填。</div></div>' +
          '<div class="field"><span class="field-label">LLM 配置套</span>' +
          '<select class="select" id="nw-profile"><option value="">默认</option></select>' +
          '<div class="hint">空 = 跟随全局默认配置套</div></div>' +
          '</div>' +
          '<div class="set-two">' +
          '<div class="field"><span class="field-label">压缩阈值（0.3 ~ 0.95）</span>' +
          '<input class="input num" id="win-threshold" type="number" min="0.3" max="0.95" step="0.05"' +
          ' placeholder="留空 = 继承全局（默认 0.8）">' +
          '<div class="hint">上下文用到这个比例就自动压缩。按员工的活来定：' +
          '长任务/贵模型可以调高（0.85）省压缩，短任务可以调低（0.6）省上下文开销。</div></div>' +
          '<div class="field"><span class="field-label">上下文上限（tokens，可选）</span>' +
          '<input class="input num" id="win-maxctx" type="number" min="2000" step="1000"' +
          ' placeholder="留空 = 用模型的默认上下文">' +
          '<div class="hint">默认跟着模型走（模型页里填的上下文窗口）；只有这个工位要特殊对待时才填。</div></div>' +
          '</div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: '创建', cls: 'btn-primary',
            onClick: function (body) {
              var name = body.querySelector('#nw-name').value.trim();
              if (!name) {
                var f = body.querySelector('#nw-name');
                f.style.borderColor = 'rgba(248,113,113,0.6)';
                f.focus();
                U.toast('warn', '请填写工位名称');
                return false;
              }
              var payload = {
                name: name,
                role: body.querySelector('#nw-role').value,
                mode: body.querySelector('#nw-mode').value,
                llm_profile: body.querySelector('#nw-profile').value
              };
              var ws = body.querySelector('#nw-ws').value.trim();
              var model = body.querySelector('#nw-model').value.trim();
              if (ws) payload.workspace_path = ws;
              if (model) payload.model = model;
              /* v0.10.12：管理员建员工时明确指定压缩阈值（可选，越界后端会 400） */
              var thrRaw = body.querySelector('#win-threshold').value.trim();
              var ctxRaw = body.querySelector('#win-maxctx').value.trim();
              var params = {};
              if (thrRaw !== '') {
                var thr = Number(thrRaw);
                if (!isFinite(thr) || thr < 0.3 || thr > 0.95) {
                  U.toast('warn', '压缩阈值要在 0.3 ~ 0.95 之间');
                  return false;
                }
                params.compact_threshold = thr;
              }
              if (ctxRaw !== '') params.max_context_tokens = Number(ctxRaw);
              if (Object.keys(params).length) payload.params = params;
              A.windows.create(payload).then(function () {
                U.toast('success', '工位已创建');
                Windows.load();
              }).catch(function (e) { U.toast('error', '创建失败：' + e.message); });
            }
          }
        ]
      });
      /* LLM 配置套下拉（选项来自 GET /api/settings/profiles 的 names） */
      var ProfilesMod = (global.DogBuddyViews || {}).Profiles;
      if (ProfilesMod) {
        ProfilesMod.fillSelect(document.getElementById('nw-profile'), '', '默认');
      }
    }
  };

  global.DogBuddyViews.Overview = Overview;
  global.DogBuddyViews.Windows = Windows;
  global.DogBuddyViews._statusDot = statusDot;
  global.DogBuddyViews._statusLabel = statusLabel;
  global.DogBuddyViews._runBadge = runBadge;

  /* SSE 钩子（松耦合：事件名 → 视图方法；sse.js 先于本文件加载，缺席则自动跳过） */
  if (global.SSE) {
    global.SSE.on('window.status', function (p) { Windows.onWindowStatus(p); });
    global.SSE.on('window.stream', function (p) { Windows.onStream(p); });
    /* v0.10.15：一次运行结束的终态（有错误码）—— 收到就收起"正在输入…" */
    global.SSE.on('window.run_finished', function (p) { Windows.onRunFinished(p); });
    global.SSE.on('sse.down', function () { Windows.onSseDown(); });
  }
})(window);

/* ==== js/views-chat.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-chat.js
   对话：控制台直接与管理员 Agent 对话（建工位 / 派任务 / 查进度）
   —— v1.3 里管理员只接 QQ，控制台没有任何对话入口，这个视图补上
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var SENDER_KEY = 'dogbuddy_chat_sender';
  /* v9（v2 口径）：只有一个管理员与用户对接；用户可以开多个对话，
     每个对话是一份独立上下文。`sender` 字段保留为"对话 id"（后端同名）。 */
  var state = { sender: 'default', messages: [], admins: [], conversations: [],
                llmOk: true, sending: false, streamText: '' };

  /** 打字机缓冲：本次发送正在流式输出的文本（按对话隔离，切换对话就丢弃） */
  var stream = { conv: '', text: '', active: false };

  function sender() {
    try { return global.localStorage.getItem(SENDER_KEY) || 'default'; } catch (e) { return 'default'; }
  }
  function setSender(s) {
    state.sender = s;
    try { global.localStorage.setItem(SENDER_KEY, s); } catch (e) { /* 忽略 */ }
  }
  function currentConversation() {
    return state.conversations.filter(function (c) { return c.id === state.sender; })[0] || null;
  }

  /* ---------------- 上下文用量（管理员会话） ----------------
     管理员会话挂在内存里的合成窗口 `admin:<对话 id>`，**不在 windows 表**里，
     所以必须走 /api/admin/chat/usage；早期版本没有这个端点，界面上也就一直什么都看不到。 */
  function usageHost() { return U.$('#chat-usage'); }

  function refreshUsage(convId) {
    var host = usageHost();
    var id = convId || state.sender;
    if (host) host.setAttribute('data-conv-id', id);
    return A.adminChatUsage(id).then(function (u) {
      u = u || {};
      /* 管理员模型：管理员没有工位，它的模型就是运行时全局模型，
         在「模型管理」页点「设为当前」即可更换——这里直接显示给人看 */
      var badge = U.$('#chat-model');
      if (badge) {
        badge.textContent = '模型：' + (u.model || '(未设置)');
        badge.setAttribute('title', '管理员当前模型：' + (u.model || '(未设置)') +
          (u.base_url ? ' · ' + u.base_url : '') + '（在模型管理页点「设为当前」更换）');
      }
      if (!host) return;
      /* 异步回来时若已切换对话，丢弃这次结果 */
      if (host.getAttribute('data-conv-id') !== id) return;
      host.innerHTML = U.usageHTML(u, { label: '管理员上下文' });
    }).catch(function () {
      var badge2 = U.$('#chat-model');
      if (badge2) badge2.textContent = '模型：—';
      if (!host) return;
      if (host.getAttribute('data-conv-id') !== id) return;
      host.innerHTML = '<div class="ctx-usage-top"><span class="ctx-usage-label">' +
        U.icon('activity', 'sm') + '管理员上下文</span>' +
        '<span class="hint">用量暂时取不到</span></div>';
    });
  }

  function onNotice(payload) {
    payload = payload || {};
    var text = String(payload.text || '');
    if (!text) return;
    if (String(payload.conversation || '') !== String(state.sender)) {
      /* 汇报落在另一个对话里：提示一下，别让人以为没发生 */
      U.toast('info', '管理员有新汇报（在另一个对话里）');
      return;
    }
    state.messages.push({
      id: payload.message_id || ('notice-' + Date.now()),
      role: 'assistant', content: text,
      created_at: payload.created_at || new Date().toISOString(),
      meta: { notice: true }
    });
    render();
    refreshUsage(state.sender);
  }

  /* ---------------- 渲染 ---------------- */
  function renderSenders() {
    var sel = U.$('#chat-sender');
    if (!sel) return;
    var list = state.conversations.slice();
    if (!list.length) list = [{ id: state.sender, title: '默认对话' }];
    sel.innerHTML = list.map(function (c) {
      var label = c.title || c.id;
      var n = c.message_count || 0;
      return '<option value="' + U.esc(c.id) + '"' + (c.id === state.sender ? ' selected' : '') + '>' +
        U.esc(label) + (n ? '（' + n + '）' : '') + '</option>';
    }).join('');
  }

  function newConversation() {
    A.chatCreateConversation('').then(function (r) {
      var conv = (r && r.conversation) || {};
      state.conversations = (r && r.conversations) || state.conversations;
      if (conv.id) setSender(conv.id);
      U.toast('success', '已新建对话：' + (conv.title || conv.id || ''));
      return load();
    }).catch(function (e) { U.toast('error', '新建失败：' + (e && e.message ? e.message : e)); });
  }

  function renameConversation() {
    var conv = currentConversation();
    U.modal({
      title: '重命名对话',
      body: '<div class="field"><span class="field-label">对话名称</span>' +
        '<input class="input" id="conv-title" value="' +
        U.esc((conv && conv.title) || state.sender) + '"></div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        { label: '保存', cls: 'btn-primary', onClick: function (body) {
          var v = (body.querySelector('#conv-title').value || '').trim();
          if (!v) { U.toast('warn', '名称不能为空'); return false; }
          A.chatRenameConversation(state.sender, v).then(function (r) {
            state.conversations = (r && r.conversations) || state.conversations;
            renderSenders();
            U.toast('success', '已重命名');
          }).catch(function (e) { U.toast('error', e.message); });
        } }
      ]
    });
  }

  function deleteConversation() {
    var conv = currentConversation();
    if (state.conversations.length <= 1) { U.toast('warn', '至少要保留一个对话'); return; }
    U.confirm({
      title: '删除对话',
      text: '将删除「' + ((conv && conv.title) || state.sender) + '」及其全部消息。确定？',
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      A.chatDeleteConversation(state.sender).then(function (r) {
        state.conversations = (r && r.conversations) || [];
        if (state.conversations.length) setSender(state.conversations[0].id);
        U.toast('success', '已删除');
        return load();
      }).catch(function (e) { U.toast('error', e.message); });
    });
  }

  function bubble(m) {
    var role = m.role === 'user' ? 'user' : (m.role === 'assistant' ? 'bot' : 'tool');
    var who = role === 'user' ? '我' : (role === 'assistant' ? '管理员' : m.role);
    var time = U.relTime(m.created_at);
    var content = U.esc(m.content || '');
    /* 工具消息常是 JSON，用等宽块呈现更易读 */
    if (role === 'tool') {
      content = '<pre class="chat-pre">' + content + '</pre>';
    } else {
      content = content.replace(/\n/g, '<br>');
    }
    return '<div class="chat-msg chat-' + role + '">' +
      '<div class="chat-meta"><span class="chat-who">' + U.esc(who) + '</span>' +
      '<span class="chat-time">' + U.esc(time) + '</span></div>' +
      '<div class="chat-body">' + content + '</div></div>';
  }

  function render() {
    var log = U.$('#chat-log');
    if (!log) return;
    if (!state.messages.length && !stream.active) {
      log.innerHTML = U.empty({
        art: 'chat',
        title: state.llmOk ? '还没有对话' : 'LLM 尚未配置',
        desc: state.llmOk
          ? '试着说：每天早上 8 点推进 XX 项目的待办，完成后简要汇报。'
          : '请先到「设置」填写 base_url / model / api_key。'
      });
      return;
    }
    log.innerHTML = state.messages.map(bubble).join('');
    renderStream();
    log.scrollTop = log.scrollHeight;
  }

  /* ---------------- 打字机（SSE admin.stream） ----------------
     管理员以前没有 on_delta（只有工位有 window.stream），所以控制台上永远是
     "发送中… → 整段蹦出来"。现在按对话收 admin.stream，边收边渲染。 */
  function renderStream() {
    var log = U.$('#chat-log');
    if (!log) return;
    var node = U.$('#chat-stream-bubble');
    if (!stream.active || !stream.text) {
      if (node && node.parentNode) node.parentNode.removeChild(node);
      return;
    }
    /* v0.10.15：增量更新（只改文本节点），不再每个 delta 重建 DOM —— 消除闪动 */
    if (!node) {
      log.insertAdjacentHTML('beforeend',
        '<div class="chat-msg chat-bot" id="chat-stream-bubble">' +
        '<div class="chat-meta"><span class="chat-who">管理员</span>' +
        '<span class="chat-time">正在输入…</span></div>' +
        '<div class="chat-body"><span class="stream-text"></span>' +
        '<span class="stream-caret"></span></div></div>');
      node = U.$('#chat-stream-bubble');
    }
    var body = node ? node.querySelector('.stream-text') : null;
    if (body && body.textContent !== stream.text) body.textContent = stream.text;
    var gap = log.scrollHeight - log.scrollTop - log.clientHeight;
    if (gap <= 80) log.scrollTop = log.scrollHeight;   // 用户在看上文就别抢滚动条
  }

  function onStream(payload) {
    payload = payload || {};
    if (String(payload.conversation || '') !== String(state.sender)) return;  // 别的对话，不串屏
    if (payload.delta === undefined || payload.delta === null) return;
    if (!global.SSE || !global.SSE.state || !global.SSE.state.connected) return;
    stream.active = true;
    stream.conv = state.sender;
    stream.text += String(payload.delta);
    renderStream();
  }

  function setBusy(on) {
    state.sending = !!on;
    var btn = U.$('#chat-send');
    var ta = U.$('#chat-text');
    if (btn) {
      btn.disabled = state.sending;
      btn.innerHTML = state.sending
        ? U.icon('refresh', 'spin') + '思考中…'
        : U.icon('send') + '发送';
    }
    if (ta) ta.disabled = state.sending;
  }

  /* ---------------- 数据 ---------------- */
  function load() {
    setSender(sender());
    return A.chatConversations().then(function (info) {
      state.conversations = (info && info.conversations) || [];
      state.admins = state.conversations.map(function (c) { return c.id; });
      state.llmOk = !info || info.llm_configured !== false;
      if (!state.conversations.length) {
        return A.chatCreateConversation('').then(function (r) {
          state.conversations = (r && r.conversations) || [];
          if (state.conversations.length) setSender(state.conversations[0].id);
        });
      }
      if (!currentConversation()) setSender(state.conversations[0].id);
      renderSenders();
      return null;
    }).then(function () {
      return A.adminChatMessages(state.sender, 200);
    }).then(function (res) {
      state.messages = (res && res.messages) || [];
      render();
      refreshUsage(state.sender);
    }).catch(function (e) {
      U.toast('error', '加载对话失败：' + (e && e.message ? e.message : e));
    });
  }

  function send() {
    if (state.sending) return;
    var ta = U.$('#chat-text');
    var text = (ta && ta.value || '').trim();
    if (!text) return;

    /* 乐观追加用户气泡，失败时回滚 */
    var optimistic = { id: 'pending', role: 'user', content: text, created_at: new Date().toISOString() };
    state.messages.push(optimistic);
    stream.active = true;                 // 立刻出现"正在输入…"气泡，等第一个 delta
    stream.conv = state.sender;
    stream.text = '';
    render();
    ta.value = '';
    setBusy(true);

    A.adminChat(text, state.sender).then(function (res) {
      stream.active = false; stream.text = '';   // 整段回复到手 → 收起流式气泡，渲染正式消息
      state.messages = (res && res.messages) || state.messages;
      setBusy(false);
      render();
      refreshUsage(state.sender);
    }).catch(function (e) {
      stream.active = false; stream.text = '';
      state.messages = state.messages.filter(function (m) { return m !== optimistic; });
      var msg = (e && e.message) ? e.message : String(e);
      setBusy(false);
      render();
      U.toast(e && e.status === 409 ? 'warn' : 'error',
        e && e.status === 409 ? msg : ('发送失败：' + msg));
    });
  }

  function reset() {
    U.confirm({
      title: '清空会话',
      text: '将删除「' + (state.sender === 'console' ? '控制台会话' : state.sender) +
        '」的对话历史（工位、任务、记忆不受影响）。确定继续？',
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      A.adminChatReset(state.sender).then(function (r) {
        U.toast('success', '已清空 ' + ((r && r.dropped) || 0) + ' 条消息');
        return load();
      }).catch(function (e) {
        U.toast('error', '清空失败：' + (e && e.message ? e.message : e));
      });
    });
  }

  /* ---------------- 事件绑定（幂等） ---------------- */
  var bound = false;
  function bind() {
    if (bound) return;
    bound = true;

    /* SSE：管理员打字机（admin.stream）+ 员工汇报写入对话流（admin.notice） */
    if (global.SSE && global.SSE.on) {
      global.SSE.on('admin.stream', onStream);
      global.SSE.on('admin.notice', onNotice);
    }

    var form = U.$('#chat-send');
    if (form) form.addEventListener('click', send);

    var ta = U.$('#chat-text');
    if (ta) {
      ta.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' && !ev.shiftKey) {
          ev.preventDefault();
          send();
        }
      });
    }

    var sel = U.$('#chat-sender');
    if (sel) {
      sel.addEventListener('change', function () {
        setSender(sel.value);
        state.messages = [];
        render();
        refreshUsage(sel.value);
        A.adminChatMessages(state.sender, 200).then(function (res) {
          state.messages = (res && res.messages) || [];
          render();
        });
      });
    }

    var rf = U.$('#chat-refresh');
    if (rf) rf.addEventListener('click', load);

    var mg = U.$('#chat-model-go');
    if (mg) {
      mg.addEventListener('click', function () {
        if (global.App && global.App.go) global.App.go('models');
        else U.toast('info', '请点左侧「模型管理」');
      });
    }

    var rs = U.$('#chat-reset');
    if (rs) rs.addEventListener('click', reset);

    /* v9：对话管理 */
    var nw = U.$('#chat-new');
    if (nw) nw.addEventListener('click', newConversation);
    var rn = U.$('#chat-rename');
    if (rn) rn.addEventListener('click', renameConversation);
    var del = U.$('#chat-delete');
    if (del) del.addEventListener('click', deleteConversation);
  }

  global.DogBuddyViews.Chat = {
    load: function () { bind(); return load(); },
    send: send,
    state: state
  };
})(window);

/* ==== js/views-ops.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-ops.js
   任务（表格 / 新建编辑弹窗 / 行操作 / 历史子表 / 倒计时）
   日志（审计 + 运行记录 双 Tab / 过滤 / 展开）
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};
  var VB = global.DogBuddyViews._runBadge || function (s) { return U.esc(s || '—'); };

  /* 调度表达式：4 种格式 */
  var SCHEDULE_CHIPS = [
    { label: '间隔 30m', value: '30m' },
    { label: '每周一 9 点', value: 'every monday 9am' },
    { label: 'Cron 0 9 * * *', value: '0 9 * * *' },
    { label: '指定时间', value: '2026-02-03T14:00:00' }
  ];
  function validateSchedule(s) {
    s = String(s || '').trim();
    if (!s) return { ok: false, msg: '调度表达式不能为空' };
    if (/^\d+\s*(s|m|h|d)$/i.test(s)) return { ok: true, msg: '间隔调度：' + s };
    if (/^every\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+\d{1,2}(am|pm)$/i.test(s)) {
      return { ok: true, msg: '每周固定时刻' };
    }
    if (/^every\s+(day|hour|minute|week|month)\b/i.test(s)) return { ok: true, msg: '周期调度' };
    if (/^(\S+\s+){4}\S+$/.test(s)) return { ok: true, msg: 'Cron 表达式（分 时 日 月 周）' };
    if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$/.test(s)) return { ok: true, msg: '一次性定时' };
    return { ok: false, msg: '无法识别。示例：30m / every monday 9am / 0 9 * * * / 2026-02-03T14:00:00' };
  }

  /* ============================================================
     任务
     ============================================================ */
  var Jobs = {
    rows: [],
    windows: [],
    filterWindow: '',
    openHistory: {},

    load: function () {
      var jobsP = A.jobs.list(Jobs.filterWindow || undefined);
      var winP = Jobs.windows.length ? Promise.resolve(Jobs.windows) : A.windows.list();
      return Promise.all([jobsP, winP]).then(function (res) {
        Jobs.rows = res[0] || [];
        Jobs.windows = res[1] || [];
        Jobs.renderWindowFilter();
        Jobs.render();
      }).catch(function (e) {
        U.toast('error', '任务列表加载失败：' + e.message);
      });
    },

    renderWindowFilter: function () {
      var sel = document.getElementById('job-filter-window');
      if (!sel) return;
      var cur = Jobs.filterWindow;
      sel.innerHTML = '<option value="">全部工位</option>' + Jobs.windows.map(function (w) {
        return '<option value="' + U.esc(w.id) + '"' + (cur === w.id ? ' selected' : '') + '>' + U.esc(w.name) + '</option>';
      }).join('');
    },

    windowName: function (id) {
      var hit = Jobs.windows.filter(function (w) { return w.id === id; })[0];
      return hit ? hit.name : (id || '—');
    },

    render: function () {
      var host = document.getElementById('job-body');
      if (!host) return;
      if (!Jobs.rows.length) {
        host.innerHTML = '<tr><td colspan="8">' + U.empty({
          art: 'doc', title: '还没有任务',
          desc: '任务让工位按计划自动运行：间隔、每周固定时间、Cron 或一次性定时。',
          action: '新建任务', actionId: 'new-job'
        }) + '</td></tr>';
        return;
      }
      host.innerHTML = Jobs.rows.map(function (j) {
        var isSoon = (U.parseTime(j.next_run_at) || 0) - Date.now() < 10 * 60 * 1000;
        /* v0.10.20（圆桌实测）：一次性任务被 park 到远期绝对时间（如 2030-01-01）后
           "启用中"但永不再跑。> 30 天的下次运行时间打 ⚠，别让界面撒谎。 */
        var nextTs = U.parseTime(j.next_run_at) || 0;
        var isFar = nextTs - Date.now() > 30 * 24 * 3600 * 1000;
        var main =
          '<tr data-job-id="' + U.esc(j.id) + '">' +
          '<td><b>' + U.esc(j.name) + '</b>' + (isFar ? ' <span title="下次运行在 30 天以上——期间不会自动触发">⚠️</span>' : '') +
          '<div class="tiny muted mono">' + U.esc(j.kind || 'prompt') + '</div></td>' +
          '<td class="muted">' + U.esc(Jobs.windowName(j.window_id)) + '</td>' +
          '<td><span class="badge badge-grey mono">' + U.esc(j.schedule || '—') + '</span>' +
          '<div class="tiny muted" style="margin-top:3px">' + U.esc(j.schedule_type || '') + '</div></td>' +
          '<td><span class="badge badge-blue">' + U.esc(j.report_strategy || 'brief') + '</span></td>' +
          '<td><label class="switch" title="' + (j.enabled ? '点一下停用这个任务' : '点一下启用这个任务') + '">' +
          '<input type="checkbox" data-toggle="' + U.esc(j.id) + '"' +
          (j.enabled ? ' checked' : '') + '><i></i></label></td>' +
          '<td><span class="countdown' + (isSoon ? ' soon' : '') + '" data-countdown="' + U.esc(j.next_run_at) + '">' +
          U.esc(U.countdown(j.next_run_at)) + '</span></td>' +
          '<td><span class="fail-dot' + (j.fail_count > 0 ? '' : ' zero') + '">' + (j.fail_count || 0) + '</span></td>' +
          '<td><div class="row-actions">' +
          '<button class="icon-btn" data-act="run" title="立即执行">' + U.icon('play', 'sm') + '</button>' +
          '<button class="icon-btn" data-act="edit" title="编辑">' + U.icon('edit', 'sm') + '</button>' +
          '<button class="icon-btn" data-act="history" title="运行历史">' + U.icon('clock', 'sm') + '</button>' +
          '<button class="icon-btn danger" data-act="delete" title="删除">' + U.icon('trash', 'sm') + '</button>' +
          '</div></td></tr>';
        return main;
      }).join('');

      /* 事件绑定 */
      U.$$('#job-body [data-toggle]', host).forEach(function (sw) {
        sw.addEventListener('change', function () {
          var id = sw.getAttribute('data-toggle');
          var ok = sw.checked;
          (ok ? A.jobs.resume(id) : A.jobs.pause(id)).then(function () {
            U.toast('success', ok ? '任务已启用' : '任务已停用');
            Jobs.load();
          }).catch(function (e) {
            sw.checked = !ok;
            U.toast('error', e.message);
          });
        });
      });
      U.$$('#job-body [data-act]', host).forEach(function (btn) {
        btn.addEventListener('click', function () {
          var tr = btn.closest('tr[data-job-id]');
          var id = tr.getAttribute('data-job-id');
          var job = Jobs.rows.filter(function (j) { return j.id === id; })[0];
          Jobs.action(btn.getAttribute('data-act'), job, tr);
        });
      });
    },

    action: function (act, job, tr) {
      if (!job) return;
      if (act === 'run') {
        A.jobs.runNow(job.id).then(function () {
          U.toast('success', '已触发执行：' + job.name);
          setTimeout(function () { Jobs.load(); }, 800);
        }).catch(function (e) { U.toast('error', e.message); });
      } else if (act === 'edit') {
        Jobs.openForm(job);
      } else if (act === 'history') {
        Jobs.toggleHistory(job, tr);
      } else if (act === 'delete') {
        UI.confirm({
          title: '删除任务', danger: true, okLabel: '删除',
          text: '将删除任务「' + job.name + '」及其历史记录，确定继续？'
        }).then(function (ok) {
          if (!ok) return;
          A.jobs.remove(job.id).then(function () {
            U.toast('success', '任务已删除');
            Jobs.load();
          }).catch(function (e) { U.toast('error', e.message); });
        });
      }
    },

    toggleHistory: function (job, tr) {
      var next = tr.nextElementSibling;
      if (next && next.classList.contains('history-row')) {
        next.remove();
        return;
      }
      var row = document.createElement('tr');
      row.className = 'history-row';
      row.innerHTML = '<td colspan="8"><div class="sk sk-line" style="margin:6px 0"></div></td>';
      tr.parentNode.insertBefore(row, tr.nextSibling);
      A.jobs.runs(job.id).then(function (runs) {
        runs = runs || [];
        if (!runs.length) {
          row.innerHTML = '<td colspan="8">' + U.empty({ art: 'chart', title: '暂无运行历史', desc: '任务执行后这里会出现记录。' }) + '</td>';
          return;
        }
        row.innerHTML = '<td colspan="8"><table class="sub-table">' +
          '<thead><tr><th>状态</th><th>开始</th><th>结束</th><th>摘要</th><th>日志</th></tr></thead><tbody>' +
          runs.slice(0, 12).map(function (r) {
            return '<tr>' +
              '<td>' + VB(r.status) + '</td>' +
              '<td class="mono tiny">' + U.esc(U.fmtTime(r.started_at)) + '</td>' +
              '<td class="mono tiny">' + U.esc(U.fmtTime(r.finished_at)) + '</td>' +
              '<td>' + U.esc(r.summary || '—') + '</td>' +
              '<td>' + (r.log_path
                ? '<button class="btn btn-ghost btn-sm" data-log-path="' + U.esc(r.log_path) + '">' + U.icon('doc', 'sm') + '查看</button>'
                : '<span class="muted tiny">—</span>') + '</td>' +
              '</tr>';
          }).join('') + '</tbody></table></td>';
        U.$$('[data-log-path]', row).forEach(function (b) {
          b.addEventListener('click', function () {
            global.FileMgr.openPath(b.getAttribute('data-log-path'));
          });
        });
      }).catch(function (e) {
        row.innerHTML = '<td colspan="8"><div class="muted small">历史加载失败：' + U.esc(e.message) + '</div></td>';
      });
    },

    openForm: function (job) {
      var isEdit = !!job;
      U.modal({
        wide: true,
        title: isEdit ? '编辑任务 · ' + job.name : '新建任务',
        body:
          '<div class="set-two">' +
          '<div class="field"><span class="field-label">任务名称 *</span>' +
          '<input class="input" id="jf-name" value="' + U.esc(job ? job.name : '') + '" placeholder="如：每日晨报"></div>' +
          '<div class="field"><span class="field-label">执行工位 *</span>' +
          '<select class="select" id="jf-window">' +
          Jobs.windows.map(function (w) {
            return '<option value="' + U.esc(w.id) + '"' + (job && job.window_id === w.id ? ' selected' : '') + '>' + U.esc(w.name) + '</option>';
          }).join('') + '</select></div>' +
          '</div>' +
          '<div class="field"><span class="field-label">调度表达式 *</span>' +
          '<div class="chips" style="margin-bottom:8px" id="jf-chips">' +
          SCHEDULE_CHIPS.map(function (c) {
            return '<button type="button" class="chip mono" data-val="' + U.esc(c.value) + '">' + U.esc(c.label) + '</button>';
          }).join('') + '</div>' +
          '<input class="input mono" id="jf-schedule" value="' + U.esc(job ? job.schedule : '') + '" placeholder="30m / every monday 9am / 0 9 * * * / 2026-02-03T14:00:00">' +
          '<div class="hint" id="jf-schedule-hint">支持 4 种格式：间隔 · 每周固定时刻 · Cron · 一次性定时</div></div>' +
          '<div class="set-two">' +
          '<div class="field"><span class="field-label">任务类型</span>' +
          '<select class="select" id="jf-kind">' +
          ['prompt', 'report', 'custom'].map(function (k) {
            return '<option' + (job && job.kind === k ? ' selected' : '') + '>' + k + '</option>';
          }).join('') + '</select></div>' +
          '<div class="field"><span class="field-label">回报策略</span>' +
          '<select class="select" id="jf-strategy">' +
          ['brief', 'full', 'none'].map(function (k) {
            return '<option' + (job && job.report_strategy === k ? ' selected' : '') + '>' + k + '</option>';
          }).join('') + '</select></div>' +
          '</div>' +
          '<div class="set-two">' +
          '<div class="field"><span class="field-label">LLM 配置套</span>' +
          '<select class="select" id="jf-profile"><option value="">继承工位</option></select>' +
          '<div class="hint">空 = 继承工位的配置套</div></div>' +
          '<div class="field"><span class="field-label">回报通道</span>' +
          '<input class="input" id="jf-channel" value="' + U.esc(job && job.report_channel ? job.report_channel : '') +
          '" placeholder="留空=全部通道，如 console / webhook-ops">' +
          '<div class="hint">通道名见「设置 · 通知通道」</div></div>' +
          '</div>' +
          '<div class="field"><span class="field-label">提示词 Prompt *</span>' +
          '<textarea class="textarea" id="jf-prompt" placeholder="任务执行时派给工位的内容…" style="min-height:120px">' +
          U.esc(job ? job.prompt : '') + '</textarea></div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: isEdit ? '保存修改' : '创建任务', cls: 'btn-primary',
            onClick: function (body) {
              var name = body.querySelector('#jf-name').value.trim();
              var schedule = body.querySelector('#jf-schedule').value.trim();
              var prompt = body.querySelector('#jf-prompt').value.trim();
              var check = validateSchedule(schedule);
              if (!name || !prompt || !check.ok) {
                U.toast('warn', !name ? '请填写任务名称' : (!schedule ? '请填写调度表达式' : (!check.ok ? check.msg : '请填写提示词')));
                return false;
              }
              var payload = {
                window_id: body.querySelector('#jf-window').value,
                name: name, schedule: schedule, prompt: prompt,
                report_strategy: body.querySelector('#jf-strategy').value,
                kind: body.querySelector('#jf-kind').value,
                llm_profile: body.querySelector('#jf-profile').value,
                report_channel: body.querySelector('#jf-channel').value.trim()
              };
              var req = isEdit ? A.jobs.update(job.id, payload) : A.jobs.create(payload);
              req.then(function () {
                U.toast('success', isEdit ? '任务已更新' : '任务已创建');
                Jobs.load();
              }).catch(function (e) { U.toast('error', e.message); });
            }
          }
        ]
      });

      /* LLM 配置套下拉（选项来自 GET /api/settings/profiles 的 names） */
      var ProfilesMod = (global.DogBuddyViews || {}).Profiles;
      if (ProfilesMod) {
        ProfilesMod.fillSelect(
          document.getElementById('jf-profile'),
          job && job.llm_profile ? job.llm_profile : '',
          '继承工位'
        );
      }
      /* chips 快捷填入 + 实时校验 */
      U.$$('#jf-chips .chip').forEach(function (c) {
        c.addEventListener('click', function () {
          var input = document.getElementById('jf-schedule');
          input.value = c.getAttribute('data-val');
          input.dispatchEvent(new Event('input'));
        });
      });
      var schedInput = document.getElementById('jf-schedule');
      var hint = document.getElementById('jf-schedule-hint');
      if (schedInput && hint) {
        schedInput.addEventListener('input', U.debounce(function () {
          var v = schedInput.value.trim();
          if (!v) {
            hint.textContent = '支持 4 种格式：间隔 · 每周固定时刻 · Cron · 一次性定时';
            hint.style.color = '';
            return;
          }
          var r = validateSchedule(v);
          hint.textContent = (r.ok ? '✓ ' : '✗ ') + r.msg;
          hint.style.color = r.ok ? 'var(--ok)' : 'var(--err)';
        }, 160));
      }
    }
  };

  /* ============================================================
     日志审计
     ============================================================ */
  var Logs = {
    tab: 'audit',
    auditRows: [],
    runRows: [],
    windows: [],

    load: function () {
      var winP = Logs.windows.length ? Promise.resolve(Logs.windows) : A.windows.list();
      return Promise.all([A.logs.audit(200), A.logs.runs(100), winP]).then(function (res) {
        Logs.auditRows = res[0] || [];
        Logs.runRows = res[1] || [];
        Logs.windows = res[2] || [];
        Logs.renderWindowFilter();
        Logs.render();
      }).catch(function (e) {
        U.toast('error', '日志加载失败：' + e.message);
      });
    },

    renderWindowFilter: function () {
      var sel = document.getElementById('log-window-filter');
      if (!sel) return;
      sel.innerHTML = '<option value="">全部工位</option>' + Logs.windows.map(function (w) {
        return '<option value="' + U.esc(w.id) + '">' + U.esc(w.name) + '</option>';
      }).join('');
    },

    render: function () {
      var kw = (document.getElementById('log-filter') || {}).value || '';
      kw = kw.trim().toLowerCase();
      var winSel = (document.getElementById('log-window-filter') || {}).value || '';
      var stSel = (document.getElementById('log-status-filter') || {}).value || '';

      /* 审计 */
      var auditHost = document.getElementById('log-audit-body');
      if (auditHost) {
        var rows = Logs.auditRows.filter(function (e) {
          if (!kw) return true;
          return String(e.actor || '').toLowerCase().indexOf(kw) >= 0 ||
            String(e.action || '').toLowerCase().indexOf(kw) >= 0 ||
            String(e.detail || '').toLowerCase().indexOf(kw) >= 0;
        });
        if (!rows.length) {
          auditHost.innerHTML = '<tr><td colspan="4">' + U.empty({
            art: 'doc', title: '没有匹配的审计记录', desc: '调整过滤条件试试。'
          }) + '</td></tr>';
        } else {
          auditHost.innerHTML = rows.slice(0, 200).map(function (e, i) {
            var detail = String(e.detail || '');
            var short = detail.length > 46 ? detail.slice(0, 46) + '…' : detail;
            return '<tr>' +
              '<td class="mono tiny">' + U.esc(U.fmtTime(e.created_at)) + '</td>' +
              '<td><span class="badge badge-grey mono">' + U.esc(e.actor || '—') + '</span></td>' +
              '<td><span class="badge badge-blue mono">' + U.esc(e.action || '—') + '</span></td>' +
              '<td class="log-detail-cell">' +
              (detail
                ? '<button class="log-detail-btn" data-detail-idx="' + i + '">' + U.esc(short) + U.icon('chevron-d', 'sm') + '</button>' +
                '<div class="log-detail-full hidden" id="log-detail-' + i + '">' + U.esc(detail) + '</div>'
                : '<span class="muted tiny">—</span>') +
              '</td></tr>';
          }).join('');
          U.$$('.log-detail-btn', auditHost).forEach(function (b) {
            b.addEventListener('click', function () {
              var box = document.getElementById('log-detail-' + b.getAttribute('data-detail-idx'));
              if (box) box.classList.toggle('hidden');
            });
          });
        }
      }

      /* 运行记录 */
      var runsHost = document.getElementById('log-runs-body');
      if (runsHost) {
        var runs = Logs.runRows.filter(function (r) {
          if (winSel && r.window_id !== winSel) return false;
          if (stSel) {
            var k = String(r.status || '').toLowerCase();
            if (stSel === 'ok' && !(k === 'ok' || k === 'success' || k === 'succeeded')) return false;
            if (stSel === 'error' && !(k === 'error' || k === 'failed' || k === 'fail')) return false;
            if (stSel === 'running' && k !== 'running') return false;
          }
          return true;
        });
        if (!runs.length) {
          runsHost.innerHTML = '<tr><td colspan="6">' + U.empty({
            art: 'chart', title: '没有匹配的运行记录', desc: '调整工位 / 状态筛选试试。'
          }) + '</td></tr>';
        } else {
          runsHost.innerHTML = runs.map(function (r) {
            var name = r.job_name || r.job_id || '—';
            var wname = r.window_name || Logs.windows.filter(function (w) { return w.id === r.window_id; })[0]?.name || r.window_id || '—';
            return '<tr>' +
              '<td class="mono tiny">' + U.esc(U.fmtTime(r.started_at)) + '</td>' +
              '<td><b>' + U.esc(name) + '</b></td>' +
              '<td class="muted">' + U.esc(wname) + '</td>' +
              '<td>' + VB(r.status) + '</td>' +
              '<td class="log-detail-cell">' + U.esc(r.summary || '—') + '</td>' +
              '<td>' + (r.log_path
                ? '<button class="btn btn-ghost btn-sm" data-log-path="' + U.esc(r.log_path) + '">' + U.icon('doc', 'sm') + '查看</button>'
                : '<span class="muted tiny">—</span>') + '</td>' +
              '</tr>';
          }).join('');
          U.$$('[data-log-path]', runsHost).forEach(function (b) {
            b.addEventListener('click', function () {
              global.FileMgr.openPath(b.getAttribute('data-log-path'));
            });
          });
        }
      }
    },

    switchTab: function (tab) {
      Logs.tab = tab;
      U.$$('#log-tabs .tab').forEach(function (t) {
        t.classList.toggle('active', t.getAttribute('data-tab') === tab);
      });
      var at = document.getElementById('log-audit-table');
      var rt = document.getElementById('log-runs-table');
      if (at) at.classList.toggle('hidden', tab !== 'audit');
      if (rt) rt.classList.toggle('hidden', tab !== 'runs');
      var kw = document.getElementById('log-filter');
      var wf = document.getElementById('log-window-filter');
      var sf = document.getElementById('log-status-filter');
      if (kw) kw.parentNode.style.display = tab === 'audit' ? '' : 'none';
      if (wf) wf.style.display = tab === 'runs' ? '' : 'none';
      if (sf) sf.style.display = tab === 'runs' ? '' : 'none';
    }
  };

  global.DogBuddyViews.Jobs = Jobs;
  global.DogBuddyViews.Logs = Logs;
  global.DogBuddyViews._validateSchedule = validateSchedule;
})(window);

/* ==== js/views-settings.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-settings.js
   设置（限速重试 / 面板对接 / 网关与回报 / 通知通道 / 白名单 / 安全）
   v0.10.27（owner）：**删除 LLM 兜底配置 与 LLM 配置套 两张卡片** ——
   模型（含密钥/地址/上下文）只有一个来源：模型管理页的目录。
   配置套的后端能力与表单下拉（views-profiles.js）保留。
   首次设置向导（7 步：provider → base_url → api_key → 拉取模型 → 选模型 → 测试连接 → 保存）
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var PROVIDERS = [
    { value: 'openai_compat', label: 'OpenAI 兼容 (openai_compat)' },
    { value: 'ollama', label: 'Ollama 本地 (ollama)' }
  ];
  var PRESETS = {
    openai_compat: [
      { label: 'OpenAI', url: 'https://api.openai.com/v1' },
      { label: 'DeepSeek', url: 'https://api.deepseek.com/v1' },
      { label: '自定义', url: '' }
    ],
    ollama: [{ label: 'Ollama 默认', url: 'http://127.0.0.1:11434' }]
  };
  var STRATEGIES = ['brief', 'full', 'none'];
  var WL_ROLES = [
    { value: 'owner', label: 'owner（所有者）' },
    { value: 'admin', label: 'admin（管理员）' },
    { value: 'viewer', label: 'viewer（只读）' }
  ];

  function masked(key) { return !!key && /^\*{4,}/.test(String(key)); }

  /* ============================================================
     设置卡片
     ============================================================ */
  var Settings = {
    data: null,
    modelList: [],

    load: function () {
      var host = document.getElementById('settings-cards');
      if (host) host.innerHTML = U.skeleton('cards') + U.skeleton('cards');
      var Approval = (global.DogBuddyViews || {}).Approval;
      return Promise.all([
        A.settings.get(),
        A.whitelist.list(),
        Approval ? Approval.load() : Promise.resolve()
      ]).then(function (res) {
        Settings.data = res[0] || {};
        Settings.whitelist = res[1] || [];
        Settings.render();
      }).catch(function (e) {
        U.toast('error', '设置加载失败：' + e.message);
      });
    },

    render: function () {
      var host = document.getElementById('settings-cards');
      if (!host) return;
      var d = Settings.data || {};
      var llm = d.llm || {}, panel = d.panel || {}, gw = d.gateway || {}, rep = d.report || {};
      var Approval = (global.DogBuddyViews || {}).Approval;
      /* v0.10.27（owner）：**删掉「LLM 兜底配置」与「LLM 配置套」两张卡片** ——
         模型只有一个来源：模型管理页的目录（密钥、地址、上下文都在条目上，
         `activate()` 写运行时）。这里只剩与模型无关的运维设置。
         注：配置套（profiles）**后端能力保留**（`/api/settings/profiles`、
         工位/任务的 `llm_profile` 字段），只是不再在设置页提供编辑入口。 */
      host.innerHTML =
        Settings.cardRetry(llm.retry || {}) +
        Settings.cardPanel(panel) +
        Settings.cardGateway(gw, rep) +
        Settings.cardNotify(d.notify || {}) +
        Settings.cardWhitelist() +
        (Approval ? Approval.cardHTML() : '') +
        Settings.cardSecurity();
      Settings.bind();
      if (Approval) Approval.bind();
    },

    /* ---------- 卡片模板 ---------- */
    cardRetry: function (r) {
      return '<div class="card set-card" data-group="retry">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('refresh', 'sm') + '</span>' +
        '<div><h3>限速重试</h3><p>遇到限速 / 瞬时错误时的退避策略</p></div></div>' +
        '<div class="set-card-body">' +
        '<p class="hint" style="margin-bottom:12px">触发限速后按「基础间隔 × 倍率」递增等待，直至超过最大间隔或达到最大重试次数。' +
        '例：基础 10s、指数退避 → 10s → 20s → 40s → 80s …</p>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">base_interval（秒）</span>' +
        '<input class="input num" id="set-rbase" type="number" value="' + U.esc(r.base_interval !== undefined ? r.base_interval : 10) + '"></div>' +
        '<div class="field"><span class="field-label">max_interval（秒）</span>' +
        '<input class="input num" id="set-rmax" type="number" value="' + U.esc(r.max_interval !== undefined ? r.max_interval : 300) + '"></div>' +
        '</div><div class="set-two">' +
        '<div class="field"><span class="field-label">max_retries</span>' +
        '<input class="input num" id="set-rretries" type="number" value="' + U.esc(r.max_retries !== undefined ? r.max_retries : 5) + '"></div>' +
        '<div class="field"><span class="field-label">backoff</span>' +
        '<select class="select" id="set-rbackoff">' +
        ['exponential', 'fixed', 'linear'].map(function (b) {
          return '<option' + (r.backoff === b ? ' selected' : '') + '>' + b + '</option>';
        }).join('') + '</select></div></div>' +
        '<div class="backoff-preview" id="set-backoff-preview"></div></div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-primary btn-sm" data-save="retry">保存该组</button></div></div>';
    },

    cardPanel: function (p) {
      return '<div class="card set-card" data-group="panel">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('layers', 'sm') + '</span>' +
        '<div><h3>面板对接</h3><p>外部管理面板联动</p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="set-row"><span class="field-label">启用面板对接</span>' +
        '<label class="switch"><input type="checkbox" id="set-panel-enabled"' + (p.enabled ? ' checked' : '') + '><i></i></label></div>' +
        '<div class="field"><span class="field-label">面板 Base URL</span>' +
        '<input class="input mono" id="set-panel-url" value="' + U.esc(p.base_url || '') + '" placeholder="http://127.0.0.1:8080"></div>' +
        '<div class="field"><span class="field-label">Agent Token' +
        (p.agent_token ? '（已保存：' + U.esc(p.agent_token) + '，留空保持不变）' : '') + '</span>' +
        '<input class="input mono" id="set-panel-token" type="password" value="" placeholder="' +
        U.esc(p.agent_token ? '留空保持不变' : '从面板「Agent 接入」复制') + '"></div></div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-primary btn-sm" data-save="panel">保存该组</button></div></div>';
    },

    cardGateway: function (gw, rep) {
      return '<div class="card set-card" data-group="gateway">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('zap', 'sm') + '</span>' +
        '<div><h3>网关与回报</h3><p>消息网关 · 推送目标 · 默认回报策略</p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="set-row"><span class="field-label">启用消息网关</span>' +
        '<label class="switch"><input type="checkbox" id="set-gw-enabled"' + (gw.enabled ? ' checked' : '') + '><i></i></label></div>' +
        '<div class="field"><span class="field-label">push_target（JSON）</span>' +
        '<textarea class="textarea mono" id="set-gw-target" style="min-height:72px" placeholder=\'{"type":"group","id":"123"}\'>' +
        U.esc(gw.push_target ? JSON.stringify(gw.push_target) : '') + '</textarea>' +
        '<div class="hint">推送目标对象，按后端约定序列化为 JSON。</div></div>' +
        '<div class="field"><span class="field-label">默认回报策略</span>' +
        '<select class="select" id="set-report-strategy">' +
        STRATEGIES.map(function (s) {
          return '<option' + (rep.default_strategy === s ? ' selected' : '') + '>' + s + '</option>';
        }).join('') + '</select></div></div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-primary btn-sm" data-save="gateway">保存该组</button></div></div>';
    },

    /* 通知通道（只读展示 notify.channels；通道名供任务「回报通道」使用） */
    cardNotify: function (notify) {
      var channels = (notify && notify.channels) || [];
      var body;
      if (!channels.length) {
        body = '<div class="pf-empty">' + U.empty({
          art: 'chat', title: '当前仅控制台输出', desc: '尚未配置外部通知通道，任务回报只输出到控制台。'
        }) + '</div>';
      } else {
        body = '<div class="notify-list">' + channels.map(function (c) {
          return '<div class="notify-row' + (c.enabled ? '' : ' notify-off') + '">' +
            '<div class="notify-main">' +
            '<div class="notify-name">' +
            '<span class="badge badge-blue mono">' + U.esc(c.name || '—') + '</span>' +
            '<span class="badge badge-grey mono">' + U.esc(c.type || '—') + '</span>' +
            (c.enabled ? '<span class="badge badge-green">启用</span>' : '<span class="badge badge-amber">已停用</span>') +
            '</div>' +
            '<div class="notify-meta">' +
            (c.url ? '<span class="mono">' + U.esc(c.url) + '</span>' : '') +
            (c.target_type
              ? '<span class="muted">target ' + U.esc(c.target_type) +
              (c.target_id ? ':' + U.esc(c.target_id) : '') + '</span>'
              : '') +
            '</div></div></div>';
        }).join('') + '</div>';
      }
      return '<div class="card set-card" data-group="notify">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('send', 'sm') + '</span>' +
        '<div><h3>通知通道</h3><p>任务回报 · 消息推送的目标通道</p></div></div>' +
        '<div class="set-card-body">' + body +
        '<div class="hint" style="margin-top:10px">任务的「回报通道」请填此处的' +
        '<b>通道名</b>（如 console / webhook-ops）；留空 = 全部通道。</div></div></div>';
    },

    cardWhitelist: function () {
      var rows = (Settings.whitelist || []).map(function (w) {
        var role = w.role || 'admin';
        return '<tr><td class="mono">' + U.esc(w.qq_id) + '</td><td>' + U.esc(w.name || '—') + '</td>' +
          '<td><select class="select wl-role" data-wl-role="' + U.esc(w.qq_id) + '" data-wl-name="' + U.esc(w.name || '') + '">' +
          WL_ROLES.map(function (r) {
            return '<option value="' + r.value + '"' + (role === r.value ? ' selected' : '') + '>' + r.label + '</option>';
          }).join('') + '</select></td>' +
          '<td><div class="row-actions"><button class="icon-btn danger" data-wl-del="' + U.esc(w.qq_id) + '">' +
          U.icon('trash', 'sm') + '</button></div></td></tr>';
      }).join('');
      return '<div class="card set-card" data-group="whitelist">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('shield', 'sm') + '</span>' +
        '<div><h3>白名单</h3><p>允许通过网关交互的 QQ 号</p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="table-wrap"><table class="table">' +
        '<thead><tr><th>QQ ID</th><th>备注名</th><th>角色</th><th class="th-actions">操作</th></tr></thead>' +
        '<tbody id="wl-body">' + (rows || '<tr><td colspan="4" class="muted small" style="text-align:center;padding:18px">暂无白名单成员</td></tr>') + '</tbody>' +
        '</table></div>' +
        '<div style="display:flex;gap:8px;margin-top:12px">' +
        '<input class="input mono" id="wl-qq" placeholder="QQ 号" style="width:150px">' +
        '<input class="input" id="wl-name" placeholder="备注名（可选）">' +
        '<button class="btn btn-primary btn-sm" id="wl-add">' + U.icon('plus', 'sm') + '添加</button></div></div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-ghost btn-sm" data-save="whitelist">刷新列表</button></div></div>';
    },

    cardSecurity: function () {
      return '<div class="card set-card" data-group="security">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('lock', 'sm') + '</span>' +
        '<div><h3>安全</h3><p>控制台访问与本地会话</p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="set-row"><span class="field-label">鉴权方式</span><span class="badge badge-green">X-Auth-Token</span></div>' +
        '<div class="set-row"><span class="field-label">本地登录态</span><span class="hint mono">localStorage.dogbuddy_token</span></div>' +
        '<p class="hint" style="margin-top:10px">访问令牌 / 密码保存在服务端配置。若怀疑泄露，请在服务端更新后重新登录；' +
        '离开公用设备前建议点击下方按钮立即锁定控制台。</p></div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-danger btn-sm" id="set-lock">' + U.icon('lock', 'sm') + '锁定控制台</button></div></div>';
    },

    /* ---------- 交互绑定 ---------- */
    /* v0.10.27：LLM 兜底卡片的三个监听（高级折叠 / 打开模型管理 / 测试兜底配置）
       随卡片一起移除 —— 模型、地址、密钥都归「模型管理」页。 */
    bind: function () {},

    renderBackoff: function () {
      var host = document.getElementById('set-backoff-preview');
      if (!host) return;
      var base = Number((document.getElementById('set-rbase') || {}).value) || 10;
      var max = Number((document.getElementById('set-rmax') || {}).value) || 300;
      var mode = ((document.getElementById('set-rbackoff') || {}).value) || 'exponential';
      var vals = [], v = base;
      for (var i = 0; i < 6; i++) {
        vals.push(Math.min(Math.round(v), max));
        v = mode === 'fixed' ? base : (mode === 'linear' ? base * (i + 2) : v * 2);
      }
      var peak = Math.max.apply(null, vals) || 1;
      host.innerHTML = vals.map(function (n) {
        var h = Math.max(8, Math.round(n / peak * 64));
        return '<div class="backoff-bar"><i style="height:' + h + 'px"></i><span>' + n + 's</span></div>';
      }).join('') + '<div class="backoff-bar" style="flex:0"><i style="height:0"></i><span>→ max ' + max + 's</span></div>';
    },

    saveGroup: function (group) {
      var body = {};
      try {
        if (group === 'retry') {
          body.llm = {
            retry: {
              base_interval: Number(document.getElementById('set-rbase').value),
              max_interval: Number(document.getElementById('set-rmax').value),
              max_retries: Number(document.getElementById('set-rretries').value),
              backoff: document.getElementById('set-rbackoff').value
            }
          };
        } else if (group === 'panel') {
          body.panel = {
            enabled: document.getElementById('set-panel-enabled').checked,
            base_url: document.getElementById('set-panel-url').value.trim()
          };
          /* 密钥：留空 = 不改（服务端也拦一道，绝不把掩码存成真 token） */
          var ptoken = document.getElementById('set-panel-token').value.trim();
          if (ptoken && !masked(ptoken)) body.panel.agent_token = ptoken;
        } else if (group === 'gateway') {
          var target = document.getElementById('set-gw-target').value.trim();
          var push = {};
          if (target) {
            try { push = JSON.parse(target); }
            catch (e) { U.toast('warn', 'push_target 不是合法 JSON'); return; }
          }
          body.gateway = {
            enabled: document.getElementById('set-gw-enabled').checked,
            push_target: push
          };
          body.report = { default_strategy: document.getElementById('set-report-strategy').value };
        } else if (group === 'whitelist') {
          Settings.load();
          return;
        } else {
          return;
        }
      } catch (e) {
        U.toast('error', '表单读取失败：' + e.message);
        return;
      }
      A.settings.update(body).then(function () {
        U.toast('success', '已保存');
        A.status().then(function (st) {
          if (global.App && App.refreshStatus) App.refreshStatus();
          Settings.data = Settings.data || {};
          if (st && st.llm_configured !== undefined && global.DogBuddyViews.Overview) {
            global.DogBuddyViews.Overview.showBanner(st.llm_configured === false);
          }
        }).catch(function () { /* 忽略 */ });
      }).catch(function (e) {
        U.toast('error', '保存失败：' + e.message);
      });
    },

    /* ============================================================
       首次设置向导（全屏层）
       ============================================================ */
    wizardState: null,

    openWizard: function () {
      var state = {
        step: 0,
        provider: 'openai_compat',
        base_url: 'https://api.openai.com/v1',
        api_key: '',
        model: '',
        models: [],
        modelError: '',
        tested: null,
        saved: false
      };
      Settings.wizardState = state;
      var root = document.getElementById('wizard-root');
      root.innerHTML = '';
      var mask = U.el('<div class="wizard-mask"></div>');
      var box = U.el(
        '<div class="wizard">' +
        '<div class="wizard-head">' +
        '<span class="brand-mark">' + U.icon('wand') + '</span>' +
        '<div style="flex:1"><h2>快速开始 · LLM 接入向导</h2><p>7 步完成模型配置，随时可在「设置」中修改</p></div>' +
        '<button class="icon-btn" id="wz-close">' + U.icon('x', 'sm') + '</button>' +
        '</div>' +
        '<div class="wizard-main">' +
        '<div class="wizard-rail" id="wz-rail"></div>' +
        '<div class="wizard-body" id="wz-body"></div>' +
        '</div>' +
        '<div class="wizard-foot">' +
        '<button class="btn btn-ghost" id="wz-prev">上一步</button>' +
        '<div class="spacer"></div>' +
        '<span class="hint" id="wz-status"></span>' +
        '<button class="btn btn-primary" id="wz-next">下一步</button>' +
        '</div></div>'
      );
      mask.appendChild(box);
      root.appendChild(mask);
      mask.addEventListener('mousedown', function (e) {
        if (e.target === mask) Settings.closeWizard();
      });
      document.getElementById('wz-close').addEventListener('click', Settings.closeWizard);
      document.getElementById('wz-prev').addEventListener('click', function () {
        Settings.wizardGo(state.step - 1);
      });
      document.getElementById('wz-next').addEventListener('click', Settings.wizardNext);
      Settings.renderWizard();
    },

    closeWizard: function () {
      var root = document.getElementById('wizard-root');
      if (root) root.innerHTML = '';
    },

    WIZ_STEPS: [
      { title: '选择服务商', desc: 'OpenAI 兼容接口覆盖绝大多数云服务商；Ollama 用于本地模型。' },
      { title: '填写 Base URL', desc: '服务商的接口地址。常用服务有一键预设。' },
      { title: '填写 API Key', desc: '仅保存在服务端配置，界面以掩码显示。' },
      { title: '拉取模型', desc: '从服务商获取可用模型列表，免手输。' },
      { title: '选择模型', desc: '从列表中选一个默认模型，也可手动输入。' },
      { title: '测试连接', desc: '真实调用一次：返回延迟、模型数量与聊天样例。' },
      { title: '保存配置', desc: '写入配置后向导横幅自动消失。' }
    ],

    renderWizard: function () {
      var state = Settings.wizardState;
      if (!state) return;
      /* 左侧步骤条 */
      var rail = document.getElementById('wz-rail');
      if (rail) {
        rail.innerHTML = Settings.WIZ_STEPS.map(function (s, i) {
          return '<div class="wstep' + (i === state.step ? ' active' : '') +
            (i < state.step ? ' done' : '') + '" data-step="' + i + '">' +
            '<span class="wstep-num">' + (i < state.step ? '✓' : (i + 1)) + '</span>' +
            '<span>' + U.esc(s.title) + '</span></div>';
        }).join('');
        U.$$('.wstep', rail).forEach(function (n) {
          n.addEventListener('click', function () {
            Settings.wizardGo(Number(n.getAttribute('data-step')));
          });
        });
      }
      /* 主体 */
      var body = document.getElementById('wz-body');
      if (!body) return;
      var s = Settings.WIZ_STEPS[state.step];
      var inner = '<h3>' + (state.step + 1) + '. ' + U.esc(s.title) + '</h3><p class="desc">' + U.esc(s.desc) + '</p>';
      if (state.step === 0) {
        inner += '<div class="chips" id="wz-provider-chips">' + PROVIDERS.map(function (p) {
          return '<button type="button" class="chip' + (state.provider === p.value ? ' active' : '') +
            '" data-p="' + p.value + '">' + U.esc(p.label) + '</button>';
        }).join('') + '</div>';
      } else if (state.step === 1) {
        inner += '<div class="chips" id="wz-preset-chips" style="margin-bottom:12px">' +
          (PRESETS[state.provider] || []).map(function (p) {
            return '<button type="button" class="chip' + (state.base_url === p.url && p.url ? ' active' : '') +
              '" data-url="' + U.esc(p.url) + '">' + U.esc(p.label) + '</button>';
          }).join('') + '</div>' +
          '<div class="field"><span class="field-label">Base URL</span>' +
          '<input class="input mono" id="wz-base-url" value="' + U.esc(state.base_url) + '" placeholder="https://…"></div>';
      } else if (state.step === 2) {
        inner += '<div class="field"><span class="field-label">API Key</span>' +
          '<input class="input mono" id="wz-api-key" type="password" value="' + U.esc(state.api_key) + '" placeholder="sk-…（Ollama 可留空）"></div>' +
          '<p class="hint">密钥通过 X-Auth-Token 鉴权的后端保存，不会出现在前端日志。</p>';
      } else if (state.step === 3) {
        inner += '<button class="btn btn-primary" id="wz-fetch">' + U.icon('refresh', 'sm') + '拉取模型</button>' +
          '<div id="wz-fetch-result" style="margin-top:14px">' +
          (state.modelError
            ? '<div class="form-error">' + U.esc(state.modelError) + '</div>'
            : (state.models.length
              ? '<div class="form-ok">已拉取 ' + state.models.length + ' 个模型：' +
                U.esc(state.models.slice(0, 6).join('、')) + (state.models.length > 6 ? ' …' : '') + '</div>'
              : '<p class="hint">点击上方按钮，从服务商获取模型列表。</p>')) +
          '</div>';
      } else if (state.step === 4) {
        inner += '<div class="field"><span class="field-label">默认模型</span>' +
          '<input class="input mono" id="wz-model" list="wz-model-options" value="' + U.esc(state.model) + '" placeholder="选择或输入模型名">' +
          '<select class="select mono" id="wz-model-select" style="margin-top:6px"><option value="">（点上面「拉取模型」后在这里选）</option></select></div>' +
          (state.models.length ? '<p class="hint">来自上一步拉取的模型列表，共 ' + state.models.length + ' 个。</p>'
            : '<p class="hint">还没有拉取模型列表，可直接手输模型名。</p>');
      } else if (state.step === 5) {
        inner += '<button class="btn btn-primary" id="wz-test">' + U.icon('zap', 'sm') + '测试连接</button>' +
          '<div id="wz-test-result" style="margin-top:14px">' + Settings.wizardTestHTML(state.tested) + '</div>';
      } else {
        inner += '<div class="test-result"><div class="tr-grid">' +
          '<div class="tr-item"><b>' + U.esc(state.provider) + '</b><span>Provider</span></div>' +
          '<div class="tr-item"><b style="font-size:13px">' + U.esc(state.base_url || '（空）') + '</b><span>Base URL</span></div>' +
          '<div class="tr-item"><b style="font-size:13px">' + U.esc(state.model || '（未选）') + '</b><span>模型</span></div>' +
          '<div class="tr-item"><b>' + (state.tested && state.tested.ok ? state.tested.latency_ms + 'ms' : '—') + '</b><span>测试延迟</span></div>' +
          '</div><p class="hint">点击右下角「保存配置」写入服务端，完成后回到总览。</p></div>';
      }
      body.innerHTML = inner;

      /* 主体事件 */
      U.$$('#wz-provider-chips .chip').forEach(function (c) {
        c.addEventListener('click', function () {
          state.provider = c.getAttribute('data-p');
          state.base_url = (PRESETS[state.provider][0] || {}).url || '';
          Settings.renderWizard();
        });
      });
      U.$$('#wz-preset-chips .chip').forEach(function (c) {
        c.addEventListener('click', function () {
          var url = c.getAttribute('data-url');
          if (url) {
            state.base_url = url;
            var input = document.getElementById('wz-base-url');
            if (input) input.value = url;
            U.$$('#wz-preset-chips .chip').forEach(function (x) { x.classList.remove('active'); });
            c.classList.add('active');
          } else {
            var inp = document.getElementById('wz-base-url');
            if (inp) { inp.value = ''; inp.focus(); }
          }
        });
      });
      var bInput = document.getElementById('wz-base-url');
      if (bInput) bInput.addEventListener('input', function () { state.base_url = bInput.value.trim(); });
      var kInput = document.getElementById('wz-api-key');
      if (kInput) kInput.addEventListener('input', function () { state.api_key = kInput.value; });
      var mInput = document.getElementById('wz-model');
      if (mInput) mInput.addEventListener('input', function () { state.model = mInput.value.trim(); });

      var fetchBtn = document.getElementById('wz-fetch');
      if (fetchBtn) {
        fetchBtn.addEventListener('click', function () {
          fetchBtn.disabled = true;
          fetchBtn.textContent = '拉取中…';
          var st = document.getElementById('wz-status');
          if (st) st.textContent = '正在拉取模型列表…';
          A.settings.models(state.provider, state.base_url, state.api_key).then(function (r) {
            state.models = r.models || [];
            state.modelError = r.error || '';
            if (r.error) U.toast('warn', '拉取失败：' + r.error);
            else U.toast('success', '已拉取 ' + state.models.length + ' 个模型');
            /* v0.10.11：**不再自动填第一个** —— 那会让 datalist 只剩一项。
               现在全量进下拉，用户自己选。 */
            U.fillModelSelect('wz-model-select', state.models, 'wz-model');
          }).catch(function (e) {
            state.modelError = e.message;
            U.toast('error', '拉取失败：' + e.message);
          }).then(function () {
            if (st) st.textContent = '';
            Settings.renderWizard();
          });
        });
      }
      var testBtn = document.getElementById('wz-test');
      if (testBtn) {
        testBtn.addEventListener('click', function () {
          testBtn.disabled = true;
          testBtn.textContent = '测试中…';
          A.settings.testLLM({
            provider: state.provider,
            base_url: state.base_url,
            api_key: state.api_key || undefined,
            model: state.model || undefined
          }).then(function (r) {
            state.tested = r;
            if (r.ok) U.toast('success', '连接成功 · ' + (r.latency_ms || 0) + 'ms');
            else U.toast('error', '连接失败：' + (r.message || ''));
            Settings.renderWizard();
          }).catch(function (e) {
            state.tested = { ok: false, message: e.message };
            U.toast('error', '测试失败：' + e.message);
            Settings.renderWizard();
          });
        });
      }

      /* 底部按钮 */
      var prev = document.getElementById('wz-prev');
      var next = document.getElementById('wz-next');
      if (prev) prev.style.visibility = state.step === 0 ? 'hidden' : 'visible';
      if (next) next.textContent = state.step === Settings.WIZ_STEPS.length - 1 ? '保存配置' : '下一步';
    },

    wizardTestHTML: function (t) {
      if (!t) return '<p class="hint">点击上方按钮测试连通性，将返回延迟、模型数与聊天样例。</p>';
      if (!t.ok) {
        return '<div class="form-error">连接失败：' + U.esc(t.message || '未知错误') +
          (t.chat_error ? '<br>聊天错误：' + U.esc(t.chat_error) : '') + '</div>';
      }
      return '<div class="test-result">' +
        '<div class="tr-grid">' +
        '<div class="tr-item"><b>' + U.esc(t.latency_ms || 0) + ' ms</b><span>延迟</span></div>' +
        '<div class="tr-item"><b>' + U.esc(t.model_count || 0) + '</b><span>模型数</span></div>' +
        '</div>' +
        (t.message ? '<div class="hint" style="margin-bottom:8px">' + U.esc(t.message) + '</div>' : '') +
        (t.chat_sample ? '<div class="tr-chat">' + U.esc(String(t.chat_sample)) + '</div>'
          : (t.chat_error ? '<div class="form-error">聊天样例失败：' + U.esc(t.chat_error) + '</div>' : '')) +
        '</div>';
    },

    wizardGo: function (i) {
      var state = Settings.wizardState;
      if (!state) return;
      state.step = Math.max(0, Math.min(Settings.WIZ_STEPS.length - 1, i));
      Settings.renderWizard();
    },

    wizardNext: function () {
      var state = Settings.wizardState;
      if (!state) return;
      if (state.step === 1 && !state.base_url) {
        U.toast('warn', '请填写 Base URL');
        return;
      }
      if (state.step === Settings.WIZ_STEPS.length - 1) {
        /* v0.10.26：全局密钥框已停用，**向导也不能再往 PUT /api/settings 塞密钥**
           （服务端会 400）。改为：把向导里填的东西落成**一条模型条目**（带密钥），
           再「设为当前」—— 这跟"密钥归模型条目"的口径一致，也不会绕过停用。 */
        var btn = document.getElementById('wz-next');
        if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
        var entry = {
          provider: state.provider,
          base_url: state.base_url,
          model: state.model
        };
        if (state.api_key) entry.api_key = state.api_key;

        function saveBare() {
          /* 没选模型时退回"只存兜底地址"（不带密钥） */
          return A.settings.update({ llm: {
            provider: state.provider, base_url: state.base_url, model: state.model } });
        }
        function locate() {
          /* 409（同身份已存在）→ 找到那条，把密钥补上去（掩码/留空不动） */
          return A.models.list(false).then(function (r) {
            var host = U.endpointHost(state.base_url);
            var hit = ((r && r.models) || []).filter(function (m) {
              return m.model === state.model && (m.endpoint_host || '') === host;
            })[0];
            if (!hit) throw new Error('同名条目已存在但定位不到，请到模型管理页处理');
            var patch = state.api_key ? { api_key: state.api_key } : {};
            var chain = Object.keys(patch).length ? A.models.update(hit.id, patch) : Promise.resolve();
            return chain.then(function () { return { model: { id: hit.id } }; });
          });
        }
        var created = state.model
          ? A.models.create(entry).catch(function () { return locate(); })
          : saveBare().then(function () { return null; });

        created.then(function (r) {
          var mid = r && r.model && r.model.id;
          if (!mid) {
            U.toast('success', '兜底配置已保存（未选模型：请到「模型管理」页新增条目）');
            state.saved = true; Settings.closeWizard();
            return null;
          }
          return A.models.activate(mid).then(function (a) {
            var res = (a && a.result) || {};
            U.toast('success', '已保存并切换为 ' + (res.model || state.model) +
              (res.api_key_switched ? '（密钥已随该条目切换）' : '（沿用现有密钥）'));
            state.saved = true;
            Settings.closeWizard();
          });
        }).then(function () {
          if (global.App) {
            App.refreshStatus().then(function () {
              if (App.reloadView) App.reloadView();
            }).catch(function () { /* 忽略 */ });
          }
        }).catch(function (e) {
          U.toast('error', '保存失败：' + (e && e.message ? e.message : e));
          if (btn) { btn.disabled = false; btn.textContent = '保存配置'; }
        });
        return;
      }
      Settings.wizardGo(state.step + 1);
    }
  };

  global.DogBuddyViews.Settings = Settings;
})(window);

/* ==== js/views-profiles.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-profiles.js
   LLM 配置套（profiles）管理：列表 / 新建 / 编辑 / 复制 / 删除 / 设为默认
   弹窗内测试连接（test_llm 带 profile）· 工位与任务下拉选项统一由此提供
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var PROFILE_PROVIDERS = [
    { value: 'openai_compat', label: 'OpenAI 兼容 (openai_compat)' },
    { value: 'ollama', label: 'Ollama 本地 (ollama)' }
  ];
  var RETRY_BACKOFFS = ['exponential', 'fixed', 'linear'];
  /* api_key 掩码值不回传（沿用现有约定） */
  function maskedKey(k) { return !!k && /^\*{4,}/.test(String(k)); }
  function profileLabel(name) { return name ? name : '默认'; }
  /* keys 兼容数组 / 逗号字符串两种形态 → 文本 */
  function keysText(keys) {
    if (!keys) return '';
    if (Object.prototype.toString.call(keys) === '[object Array]') return keys.join(', ');
    return String(keys);
  }

  var Profiles = {
    data: null,        /* {active, names, profiles} */
    namesCache: null,  /* 下拉选项用的 names 缓存 */

    /* ============================================================
       卡片骨架（由 views-settings.js 的 render 挂入）
       ============================================================ */
    cardHTML: function () {
      return '<div class="card set-card" data-group="profiles">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('layers', 'sm') + '</span>' +
        '<div><h3>LLM 配置套</h3><p>多套模型接入 · 工位与任务可分别指定</p></div>' +
        '<div class="spacer"></div>' +
        '<button class="btn btn-primary btn-sm" id="pf-new">' + U.icon('plus', 'sm') + '新建套</button></div>' +
        '<div class="set-card-body"><div class="pf-list" id="pf-list">' + U.skeleton('rows') + '</div></div>' +
        '<div class="set-card-foot"><span class="hint">当前激活套用于未指定配置套的工位与任务；「默认」套不可删除。</span>' +
        '</div></div>';
    },

    /* ============================================================
       数据加载与列表渲染（加载态 / 空状态 / 错误态）
       ============================================================ */
    load: function () {
      var host = document.getElementById('pf-list');
      if (host) host.innerHTML = U.skeleton('rows');
      return A.settings.profiles.list().then(function (res) {
        Profiles.data = res || {};
        Profiles.namesCache = Profiles.data.names || [];
        Profiles.renderList();
      }).catch(function (e) {
        U.toast('error', '配置套加载失败：' + e.message);
        if (host) {
          host.innerHTML = '<div class="pf-empty">' +
            '<div class="form-error">配置套加载失败：' + U.esc(e.message) + '</div>' +
            '<button class="btn btn-soft btn-sm" id="pf-retry" style="margin-top:10px">' +
            U.icon('refresh', 'sm') + '重试</button></div>';
          var retry = document.getElementById('pf-retry');
          if (retry) retry.addEventListener('click', Profiles.load);
        }
      });
    },

    renderList: function () {
      var host = document.getElementById('pf-list');
      if (!host) return;
      var d = Profiles.data || {};
      var profiles = d.profiles || {};
      var names = d.names && d.names.length ? d.names : Object.keys(profiles);
      /* ""（默认套）恒排最前 */
      names = names.slice().sort(function (a, b) {
        if (a === '') return -1;
        if (b === '') return 1;
        return String(a).localeCompare(String(b));
      });
      if (!names.length) {
        host.innerHTML = '<div class="pf-empty">' + U.empty({
          art: 'box', title: '暂无配置套', desc: '点击右上角「新建套」创建第一套 LLM 配置。'
        }) + '</div>';
        return;
      }
      host.innerHTML = names.map(function (name) {
        var p = profiles[name] || {};
        var isActive = name === (d.active || '');
        return '<div class="pf-row' + (isActive ? ' active' : '') + '">' +
          '<div class="pf-main">' +
          '<div class="pf-name">' +
          '<span class="badge ' + (name ? 'badge-blue' : 'badge-grey') + ' mono">' + U.esc(profileLabel(name)) + '</span>' +
          (isActive ? '<span class="badge badge-green">当前激活</span>' : '') +
          '</div>' +
          '<div class="pf-meta">' +
          '<span class="badge badge-grey mono">' + U.esc(p.provider || '—') + '</span>' +
          '<span class="mono" title="' + U.esc(p.base_url || '') + '">' + U.esc(p.base_url || '未设置 base_url') + '</span>' +
          '<span class="muted">·</span>' +
          '<span class="mono">' + U.esc(p.model || '未设置模型') + '</span>' +
          '</div></div>' +
          '<div class="pf-actions">' +
          (isActive
            ? '<span class="badge badge-green tiny">默认</span>'
            : '<button class="btn btn-soft btn-sm" data-pf-act="activate" data-pf-name="' + U.esc(name) + '">设为默认</button>') +
          '<button class="btn btn-ghost btn-sm" data-pf-act="edit" data-pf-name="' + U.esc(name) + '">编辑</button>' +
          '<button class="btn btn-ghost btn-sm" data-pf-act="copy" data-pf-name="' + U.esc(name) + '">复制</button>' +
          (name
            ? '<button class="icon-btn danger" data-pf-act="delete" data-pf-name="' + U.esc(name) + '" title="删除">' +
            U.icon('trash', 'sm') + '</button>'
            : '') +
          '</div></div>';
      }).join('');

      U.$$('[data-pf-act]', host).forEach(function (btn) {
        btn.addEventListener('click', function () {
          Profiles.action(btn.getAttribute('data-pf-act'), btn.getAttribute('data-pf-name'));
        });
      });
    },

    /* ============================================================
       列表操作：设为默认 / 编辑 / 复制 / 删除（二次确认）
       ============================================================ */
    action: function (act, name) {
      if (act === 'activate') {
        A.settings.profiles.update(name, { set_active: true }).then(function () {
          U.toast('success', '已设为默认：' + profileLabel(name) + '（新工位将使用该配置套）');
          Profiles.load();
        }).catch(function (e) { U.toast('error', '设置失败：' + e.message); });
      } else if (act === 'edit') {
        Profiles.openForm('edit', name);
      } else if (act === 'copy') {
        Profiles.openForm('copy', name);
      } else if (act === 'delete') {
        if (!name) { U.toast('warn', '「默认」套不可删除'); return; }
        UI.confirm({
          title: '删除配置套', danger: true, okLabel: '删除',
          text: '将删除配置套「' + name + '」，引用它的工位与任务会回落到默认配置。确定继续？'
        }).then(function (ok) {
          if (!ok) return;
          A.settings.profiles.remove(name).then(function (r) {
            U.toast('success', '已删除：' + name + (r && r.active !== undefined ? '（当前激活：' + profileLabel(r.active) + '）' : ''));
            Profiles.load();
          }).catch(function (e) { U.toast('error', '删除失败：' + e.message); });
        });
      }
    },

    /* ============================================================
       新建 / 编辑 / 复制 弹窗
       ============================================================ */
    openForm: function (mode, name) {
      var profiles = (Profiles.data && Profiles.data.profiles) || {};
      var src = (mode === 'create' ? {} : (profiles[name] || {}));
      var title = mode === 'edit' ? '编辑配置套 · ' + profileLabel(name)
        : (mode === 'copy' ? '复制配置套 · ' + profileLabel(name) : '新建配置套');
      var m = U.modal({
        wide: true,
        title: title,
        body: Profiles.formHTML(mode, name, src),
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: mode === 'edit' ? '保存修改' : '创建配置套', cls: 'btn-primary',
            onClick: function (body) {
              return Profiles.submitForm(mode, name, body, m);
            }
          }
        ]
      });
      Profiles.bindForm(mode, name, src, m);
      return m;
    },

    formHTML: function (mode, name, src) {
      src = src || {};
      var retry = src.retry || {};
      var isNew = mode !== 'edit';
      var nameVal = mode === 'copy' ? (name ? name + '-copy' : '') : '';
      var h = '';
      h += '<p class="hint" style="margin-bottom:12px">「默认」套即全局 LLM 接入配置；工位 / 任务未指定配置套时，使用当前激活套。</p>';
      if (isNew) {
        h += '<div class="set-two">' +
          '<div class="field"><span class="field-label">配置套名称 *</span>' +
          '<input class="input mono" id="pf-name" value="' + U.esc(nameVal) + '" placeholder="如 deepseek / ollama-local"></div>' +
          '<div class="field"><span class="field-label">初值</span>' +
          '<div class="set-row" style="border:0;padding:4px 0">' +
          '<label class="switch"><input type="checkbox" id="pf-copy-default"><i></i></label>' +
          '<span class="hint">从当前默认套复制初值</span></div></div></div>';
      } else {
        h += '<div class="set-row"><span class="field-label">配置套名称</span>' +
          '<span class="badge badge-blue mono">' + U.esc(profileLabel(name)) + '</span></div>';
      }
      h += '<div class="field"><span class="field-label">服务商 Provider</span>' +
        '<select class="select" id="pf-provider">' +
        PROFILE_PROVIDERS.map(function (p) {
          return '<option value="' + p.value + '"' + (src.provider === p.value ? ' selected' : '') + '>' + p.label + '</option>';
        }).join('') + '</select></div>';
      h += '<div class="field"><span class="field-label">Base URL</span>' +
        '<input class="input mono" id="pf-base-url" value="' + U.esc(src.base_url || '') + '" placeholder="https://api.openai.com/v1"></div>';
      h += '<div class="field"><span class="field-label">API Key' +
        (src.api_key ? '（已保存：' + U.esc(src.api_key) + '，留空保持不变）' : '') + '</span>' +
        '<input class="input mono" id="pf-api-key" type="password" placeholder="sk-…"></div>';
      h += '<div class="field"><span class="field-label">模型</span>' +
        '<div style="display:flex;gap:8px">' +
        '<input class="input mono" id="pf-model" list="pf-model-options" value="' + U.esc(src.model || '') + '" placeholder="gpt-4o-mini">' +
        '<button class="btn btn-soft btn-sm" id="pf-fetch-models" type="button">拉取模型</button></div>' +
        '<select class="select mono" id="pf-model-select" style="margin-top:6px"><option value="">（点「拉取模型」后在这里选）</option></select></div>';
      h += '<div class="field"><span class="field-label">keys（多密钥，逗号分隔，可选）</span>' +
        '<input class="input mono" id="pf-keys" value="' + U.esc(keysText(src.keys)) + '" placeholder="留空不改动"></div>';
      h += '<div class="set-row" style="border:0;padding-top:2px">' +
        '<button class="btn btn-soft btn-sm" id="pf-test" type="button">' + U.icon('zap', 'sm') + '测试连接</button>' +
        '<span class="hint" id="pf-test-result">' +
        (mode === 'edit' ? '将按已保存的配置测试连通性并做一次聊天往返' : '将按当前表单填写的配置测试连通性') + '</span></div>';
      h += '<button class="adv-toggle" id="pf-adv-toggle" type="button">高级参数' + U.icon('chevron-d', 'sm') + '</button>';
      h += '<div class="hidden" id="pf-adv">' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">temperature</span>' +
        '<input class="input num" id="pf-temperature" type="number" step="0.1" value="' +
        U.esc(src.temperature !== undefined ? src.temperature : 0.7) + '"></div>' +
        '<div class="field"><span class="field-label">max_context_tokens</span>' +
        '<input class="input num" id="pf-max-ctx" type="number" value="' +
        U.esc(src.max_context_tokens || 128000) + '"></div>' +
        '</div><div class="set-two">' +
        '<div class="field"><span class="field-label">compact_threshold</span>' +
        '<input class="input num" id="pf-compact" type="number" step="0.05" value="' +
        U.esc(src.compact_threshold !== undefined ? src.compact_threshold : 0.7) + '"></div>' +
        '<div class="field"><span class="field-label">request_timeout（秒）</span>' +
        '<input class="input num" id="pf-timeout" type="number" value="' +
        U.esc(src.request_timeout || 120) + '"></div>' +
        '</div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">retry.base_interval（秒）</span>' +
        '<input class="input num" id="pf-retry-base" type="number" value="' +
        U.esc(retry.base_interval !== undefined ? retry.base_interval : 10) + '"></div>' +
        '<div class="field"><span class="field-label">retry.max_interval（秒）</span>' +
        '<input class="input num" id="pf-retry-max" type="number" value="' +
        U.esc(retry.max_interval !== undefined ? retry.max_interval : 300) + '"></div>' +
        '</div><div class="set-two">' +
        '<div class="field"><span class="field-label">retry.max_retries</span>' +
        '<input class="input num" id="pf-retry-retries" type="number" value="' +
        U.esc(retry.max_retries !== undefined ? retry.max_retries : 5) + '"></div>' +
        '<div class="field"><span class="field-label">retry.backoff</span>' +
        '<select class="select" id="pf-retry-backoff">' +
        RETRY_BACKOFFS.map(function (b) {
          return '<option' + (retry.backoff === b ? ' selected' : '') + '>' + b + '</option>';
        }).join('') + '</select></div>' +
        '</div></div>';
      if (isNew) {
        h += '<div class="set-row" style="margin-top:8px"><span class="field-label">创建后设为默认（新工位使用该套）</span>' +
          '<label class="switch"><input type="checkbox" id="pf-set-active"><i></i></label></div>';
      }
      h += '<div class="hint" style="margin-top:10px">api_key 掩码时不回传，留空即保持已保存值。</div>';
      return h;
    },

    bindForm: function (mode, name, src, m) {
      var root = m.body;
      /* 高级折叠 */
      var advBtn = root.querySelector('#pf-adv-toggle');
      if (advBtn) {
        advBtn.addEventListener('click', function () {
          advBtn.classList.toggle('open');
          var adv = root.querySelector('#pf-adv');
          if (adv) adv.classList.toggle('hidden');
        });
      }
      /* 新建：从默认套复制初值 */
      var copyBox = root.querySelector('#pf-copy-default');
      if (copyBox) {
        copyBox.addEventListener('change', function () {
          var profiles = (Profiles.data && Profiles.data.profiles) || {};
          Profiles.fillForm(root, copyBox.checked ? (profiles[''] || {}) : {});
        });
      }
      /* 拉取模型（新建用表单值；编辑按该套拉取） */
      var fetchBtn = root.querySelector('#pf-fetch-models');
      if (fetchBtn) {
        fetchBtn.addEventListener('click', function () {
          fetchBtn.disabled = true;
          fetchBtn.textContent = '拉取中…';
          var profile = mode === 'edit' ? name : '';
          var req = profile
            ? A.settings.models('', '', '', profile)
            : A.settings.models(
              root.querySelector('#pf-provider').value,
              root.querySelector('#pf-base-url').value.trim(),
              root.querySelector('#pf-api-key').value.trim()
            );
          req.then(function (r) {
            /* 全量进下拉（datalist 会按输入框已有文字过滤，所以不再用它） */
            U.fillModelSelect('pf-model-select', r.models || [], 'pf-model');
            if (r.error) U.toast('warn', '拉取失败：' + r.error);
            else U.toast('success', '已拉取 ' + ((r.models || []).length) + ' 个模型');
          }).catch(function (e) {
            U.toast('error', '拉取失败：' + e.message);
          }).then(function () {
            fetchBtn.disabled = false;
            fetchBtn.textContent = '拉取模型';
          });
        });
      }
      /* 弹窗内测试连接（test_llm 带 profile 参数） */
      var testBtn = root.querySelector('#pf-test');
      if (testBtn) {
        testBtn.addEventListener('click', function () {
          var out = root.querySelector('#pf-test-result');
          var body = {};
          if (mode === 'edit') {
            body.profile = name || '';
          } else {
            body.provider = root.querySelector('#pf-provider').value;
            var bu = root.querySelector('#pf-base-url').value.trim();
            var key = root.querySelector('#pf-api-key').value.trim();
            var model = root.querySelector('#pf-model').value.trim();
            if (bu) body.base_url = bu;
            if (key && !maskedKey(key)) body.api_key = key;
            if (model) body.model = model;
          }
          testBtn.disabled = true;
          testBtn.textContent = '测试中…';
          if (out) out.textContent = '正在测试连接…';
          A.settings.testLLM(body).then(function (r) {
            if (r.ok) {
              U.toast('success', '连接成功 · ' + (r.latency_ms || 0) + 'ms');
              if (out) {
                out.innerHTML = '<span style="color:var(--ok)">连接成功</span> · 延迟 ' +
                  U.esc(r.latency_ms || 0) + 'ms · 模型 ' + U.esc(r.model_count || 0) + ' 个' +
                  (r.chat_sample ? '<br>样例回复：' + U.esc(String(r.chat_sample).slice(0, 120)) : '');
              }
            } else {
              U.toast('error', '连接失败：' + (r.message || '未知错误'));
              if (out) {
                out.innerHTML = '<span style="color:var(--err)">失败</span> · ' +
                  U.esc(r.message || '') + (r.chat_error ? ' · ' + U.esc(r.chat_error) : '');
              }
            }
          }).catch(function (e) {
            U.toast('error', '测试失败：' + e.message);
            if (out) out.textContent = '测试失败：' + e.message;
          }).then(function () {
            testBtn.disabled = false;
            testBtn.textContent = '测试连接';
          });
        });
      }
    },

    /* 用一份 llm 值填充 / 清空表单（复制初值用） */
    fillForm: function (root, v) {
      v = v || {};
      var retry = v.retry || {};
      function setVal(id, val) {
        var n = root.querySelector('#' + id);
        if (n) n.value = val === undefined || val === null ? '' : val;
      }
      setVal('pf-provider', v.provider || 'openai_compat');
      setVal('pf-base-url', v.base_url || '');
      setVal('pf-api-key', '');
      setVal('pf-model', v.model || '');
      setVal('pf-keys', keysText(v.keys));
      setVal('pf-temperature', v.temperature !== undefined ? v.temperature : 0.7);
      setVal('pf-max-ctx', v.max_context_tokens || 128000);
      setVal('pf-compact', v.compact_threshold !== undefined ? v.compact_threshold : 0.7);
      setVal('pf-timeout', v.request_timeout || 120);
      setVal('pf-retry-base', retry.base_interval !== undefined ? retry.base_interval : 10);
      setVal('pf-retry-max', retry.max_interval !== undefined ? retry.max_interval : 300);
      setVal('pf-retry-retries', retry.max_retries !== undefined ? retry.max_retries : 5);
      setVal('pf-retry-backoff', retry.backoff || 'exponential');
    },

    /* 读取表单 → {llm:{...}}；非法数字返回 null 并 toast */
    readLlm: function (root) {
      function num(id, label) {
        var raw = root.querySelector('#' + id).value.trim();
        if (raw === '') return undefined;
        var n = Number(raw);
        if (isNaN(n)) throw new Error(label + ' 不是有效数字');
        return n;
      }
      var llm;
      try {
        llm = {
          provider: root.querySelector('#pf-provider').value,
          base_url: root.querySelector('#pf-base-url').value.trim(),
          model: root.querySelector('#pf-model').value.trim(),
          temperature: num('pf-temperature', 'temperature'),
          max_context_tokens: num('pf-max-ctx', 'max_context_tokens'),
          compact_threshold: num('pf-compact', 'compact_threshold'),
          request_timeout: num('pf-timeout', 'request_timeout'),
          retry: {
            base_interval: num('pf-retry-base', 'retry.base_interval'),
            max_interval: num('pf-retry-max', 'retry.max_interval'),
            max_retries: num('pf-retry-retries', 'retry.max_retries'),
            backoff: root.querySelector('#pf-retry-backoff').value
          }
        };
        /* api_key 掩码时不回传：留空不传 */
        var key = root.querySelector('#pf-api-key').value.trim();
        if (key && !maskedKey(key)) llm.api_key = key;
        var keysRaw = root.querySelector('#pf-keys').value.trim();
        if (keysRaw) {
          llm.keys = keysRaw.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
        }
      } catch (e) {
        U.toast('error', '表单读取失败：' + e.message);
        return null;
      }
      return llm;
    },

    submitForm: function (mode, name, root, m) {
      var isNew = mode !== 'edit';
      var newName = name;
      if (isNew) {
        newName = (root.querySelector('#pf-name').value || '').trim();
        if (!newName) {
          U.toast('warn', '请填写配置套名称');
          return false;
        }
      }
      var llm = Profiles.readLlm(root);
      if (!llm) return false;
      var payload = { llm: llm };
      if (isNew) {
        var sa = root.querySelector('#pf-set-active');
        payload.set_active = !!(sa && sa.checked);
      }
      var req = isNew
        ? A.settings.profiles.create(newName, payload)
        : A.settings.profiles.update(name, payload);
      req.then(function () {
        U.toast('success', isNew ? '配置套已创建：' + newName : '配置套已更新：' + profileLabel(name));
        Profiles.load();
      }).catch(function (e) {
        U.toast('error', (isNew ? '创建失败：' : '保存失败：') + e.message);
      });
    },

    /* ============================================================
       下拉选项支持：工位 / 任务弹窗统一取 names（空字符串 → 空标签）
       ============================================================ */
    namesList: function () {
      if (Profiles.namesCache) return Promise.resolve(Profiles.namesCache);
      return A.settings.profiles.list().then(function (res) {
        Profiles.namesCache = (res && res.names) || [];
        return Profiles.namesCache;
      });
    },

    /* fillSelect(selectEl, selected, emptyLabel)：空值项文案由调用方决定
       工位='默认' / 任务='继承工位' */
    fillSelect: function (sel, selected, emptyLabel) {
      if (!sel) return Promise.resolve();
      var empty = emptyLabel || '默认';
      sel.innerHTML = '<option value="">' + U.esc(empty) + '</option>';
      return Profiles.namesList().then(function (names) {
        var opts = names.filter(function (n) { return !!n; }).map(function (n) {
          return '<option value="' + U.esc(n) + '"' + (selected === n ? ' selected' : '') + '>' +
            U.esc(n) + '</option>';
        }).join('');
        sel.innerHTML = '<option value="">' + U.esc(empty) + '</option>' + opts;
        sel.value = selected || '';
      }).catch(function (e) {
        U.toast('warn', '配置套列表加载失败：' + e.message);
      });
    },

    /* 卡片绑定（由 views-settings.js 的 bind 后调用） */
    bind: function () {
      var btn = document.getElementById('pf-new');
      if (btn) btn.addEventListener('click', function () { Profiles.openForm('create'); });
      Profiles.load();
    }
  };

  global.DogBuddyViews.Profiles = Profiles;
})(window);

/* ==== js/views-capabilities.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-capabilities.js
   能力清单视图（T1.2）：工具卡片（权限 / 风险徽标 + 可展开参数表）
   + 权限 / 角色 / 关键词筛选 + 角色对照区（工具名 chips）
   后端契约：GET /api/capabilities → {tools, roles, tool_count, permissions}
   ============================================================ */
/* global DogBuddyViews */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var PERM_LABEL = { read: '读取', write: '写入', network: '网络', shell: '命令行' };
  var PERM_BADGE = { read: 'badge-green', write: 'badge-blue', network: 'badge-amber', shell: 'badge-red' };
  var RISK_LABEL = { low: '低风险', mid: '中风险', high: '高风险' };
  var RISK_BADGE = { low: 'badge-green', mid: 'badge-amber', high: 'badge-red' };

  /**
   * 权限徽标 HTML（read 绿 / write 蓝 / network 黄 / shell 红）。
   * @param {string} perm 权限名
   * @returns {string} 徽标 HTML
   */
  function permBadge(perm) {
    var cls = PERM_BADGE[perm] || 'badge-grey';
    var label = PERM_LABEL[perm] || perm || '未知';
    return '<span class="badge ' + cls + '" title="权限：' + U.esc(perm || '') + '">' +
      U.esc(label) + '</span>';
  }

  /**
   * 风险标签 HTML（low / mid / high → 低 / 中 / 高）。
   * @param {string} risk 风险等级
   * @returns {string} 标签 HTML
   */
  function riskBadge(risk) {
    var cls = RISK_BADGE[risk] || 'badge-grey';
    var label = RISK_LABEL[risk] || risk || '未知';
    return '<span class="badge ' + cls + '" title="风险等级">风险' + U.esc(label.replace('风险', '')) + '</span>';
  }

  /**
   * 单个工具的参数表 HTML（名称 / 类型 / 必填 / 说明 / 默认值）。
   * @param {Array<Object>} args 参数行 [{name,type,required,description,default,enum}]
   * @returns {string} 参数表 HTML
   */
  function argsTable(args) {
    if (!args || !args.length) {
      return '<p class="muted small">该工具没有参数。</p>';
    }
    var rows = args.map(function (a) {
      var desc = a.description || '';
      if (a.enum && a.enum.length) {
        desc += (desc ? ' · ' : '') + '可选值：' + a.enum.map(String).join(' / ');
      }
      var def = (a['default'] === undefined || a['default'] === null)
        ? '—' : (typeof a['default'] === 'string' ? a['default'] : JSON.stringify(a['default']));
      return '<tr>' +
        '<td class="mono">' + U.esc(a.name) + '</td>' +
        '<td class="mono">' + U.esc(a.type || 'any') + '</td>' +
        '<td>' + (a.required
          ? '<span class="badge badge-amber">必填</span>'
          : '<span class="badge badge-grey">可选</span>') + '</td>' +
        '<td>' + U.esc(desc || '—') + '</td>' +
        '<td class="mono">' + U.esc(def) + '</td>' +
        '</tr>';
    }).join('');
    return '<div class="table-wrap"><table class="table sub-table">' +
      '<thead><tr><th>名称</th><th>类型</th><th>必填</th><th>说明</th><th>默认值</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div>';
  }

  /**
   * 单张工具卡片 HTML（头部门槛点击可展开参数表）。
   * @param {Object} t 工具 DTO {name,title,description,permission,risk,args}
   * @returns {string} 卡片 HTML
   */
  function toolCardHTML(t) {
    return '<div class="cap-tool">' +
      '<div class="cap-tool-head">' + U.icon('chevron-r', 'ic-chevron') +
      '<div class="cap-tool-main">' +
      '<div class="cap-tool-name mono">' + U.esc(t.name) +
      (t.title ? '<span class="cap-tool-title">' + U.esc(t.title) + '</span>' : '') + '</div>' +
      '<div class="cap-tool-desc">' + U.esc(t.description || '暂无说明') + '</div>' +
      '</div>' +
      '<div class="cap-tool-badges">' + permBadge(t.permission) + riskBadge(t.risk) + '</div>' +
      '</div>' +
      '<div class="cap-tool-body">' + argsTable(t.args) + '</div>' +
      '</div>';
  }

  /**
   * 角色对照卡 HTML（显示名 / 模式 / 工具数 / 工具名 chips）。
   * @param {Object} r 角色 DTO {name,display_name,default_mode,is_admin,tool_names,tool_count}
   * @returns {string} 卡片 HTML
   */
  function roleCardHTML(r) {
    var chips = (r.tool_names || []).map(function (n) {
      return '<span class="chip mono" data-cap-tool="' + U.esc(n) + '" title="点击筛选该工具">' +
        U.esc(n) + '</span>';
    }).join('');
    return '<div class="card cap-role" data-cap-role="' + U.esc(r.name) + '">' +
      '<div class="card-head">' +
      '<div><h3 class="card-title">' + U.esc(r.display_name || r.name) +
      (r.is_admin
        ? '<span class="badge badge-violet" style="margin-left:8px">管理员</span>'
        : '<span class="badge badge-blue" style="margin-left:8px">员工</span>') + '</h3>' +
      '<p class="card-sub mono">' + U.esc(r.name) + ' · 模式 ' + U.esc(r.default_mode || '—') +
      ' · 工具 ' + (r.tool_count || 0) + ' 个</p></div>' +
      '<button class="btn btn-ghost btn-sm" data-cap-role-filter="' + U.esc(r.name) + '">只看该角色</button>' +
      '</div>' +
      '<div class="card-body"><div class="chips">' +
      (chips || '<span class="muted small">该角色暂无工具</span>') + '</div></div>' +
      '</div>';
  }

  var Capabilities = {
    /** @type {Object|null} GET /api/capabilities 的响应缓存 */
    data: null,
    /** @type {{perm: string, role: string, kw: string}} 当前筛选条件 */
    filters: { perm: '', role: '', kw: '' },
    /** @type {boolean} 筛选控件是否已绑定事件 */
    bound: false,

    /**
     * 视图入口：拉取能力清单并渲染（可反复调用，筛选状态保留）。
     * @returns {Promise<void>}
     */
    load: function () {
      Capabilities.ensureBound();
      var grid = document.getElementById('cap-grid');
      if (grid) grid.innerHTML = U.skeleton('rows');
      return A.capabilities().then(function (data) {
        Capabilities.data = data || { tools: [], roles: [], permissions: [] };
        Capabilities.fillRoleOptions();
        Capabilities.render();
      }).catch(function (e) {
        if (grid) {
          grid.innerHTML = '<div class="empty"><div class="empty-title">能力清单加载失败</div>' +
            '<div class="empty-desc">' + U.esc(e.message) + '</div></div>';
        }
        U.toast('error', '能力清单加载失败：' + e.message);
      });
    },

    /**
     * 绑定筛选控件事件（幂等，只绑一次）。
     * @returns {void}
     */
    ensureBound: function () {
      if (Capabilities.bound) return;
      Capabilities.bound = true;
      var perm = /** @type {HTMLSelectElement|null} */ (document.getElementById('cap-perm-filter'));
      var role = /** @type {HTMLSelectElement|null} */ (document.getElementById('cap-role-filter'));
      var kw = /** @type {HTMLInputElement|null} */ (document.getElementById('cap-search'));
      if (perm) {
        perm.addEventListener('change', function () {
          Capabilities.filters.perm = perm.value;
          Capabilities.render();
        });
      }
      if (role) {
        role.addEventListener('change', function () {
          Capabilities.filters.role = role.value;
          Capabilities.render();
        });
      }
      if (kw) {
        kw.addEventListener('input', U.debounce(function () {
          Capabilities.filters.kw = kw.value.trim().toLowerCase();
          Capabilities.render();
        }, 150));
      }
    },

    /**
     * 填充「按角色筛选」下拉框选项。
     * @returns {void}
     */
    fillRoleOptions: function () {
      var sel = /** @type {HTMLSelectElement|null} */ (document.getElementById('cap-role-filter'));
      if (!sel) return;
      var roles = (Capabilities.data && Capabilities.data.roles) || [];
      sel.innerHTML = '<option value="">全部角色</option>' + roles.map(function (r) {
        return '<option value="' + U.esc(r.name) + '">' +
          U.esc(r.display_name || r.name) + '（' + (r.tool_count || 0) + '）</option>';
      }).join('');
      sel.value = Capabilities.filters.role;
    },

    /**
     * 按筛选条件过滤工具列表。
     * @returns {Array<Object>} 过滤后的工具 DTO 数组
     */
    filteredTools: function () {
      var data = Capabilities.data || {};
      var tools = data.tools || [];
      var f = Capabilities.filters;
      var roleNames = null;
      if (f.role) {
        var role = (data.roles || []).filter(function (r) { return r.name === f.role; })[0];
        roleNames = role ? (role.tool_names || []) : [];
      }
      return tools.filter(function (t) {
        if (f.perm && t.permission !== f.perm) return false;
        if (roleNames && roleNames.indexOf(t.name) < 0) return false;
        if (f.kw) {
          var hay = (String(t.name || '') + ' ' + String(t.description || '') +
            ' ' + String(t.title || '')).toLowerCase();
          if (hay.indexOf(f.kw) < 0) return false;
        }
        return true;
      });
    },

    /**
     * 渲染工具区 + 角色对照区，并绑定卡片交互。
     * @returns {void}
     */
    render: function () {
      var grid = document.getElementById('cap-grid');
      var roleHost = document.getElementById('cap-roles');
      var tools = Capabilities.filteredTools();
      var data = Capabilities.data || {};

      if (grid) {
        if (!tools.length) {
          grid.innerHTML = U.empty({
            art: 'box', title: '没有匹配的工具',
            desc: '换个关键词，或把权限 / 角色筛选调回「全部」试试。'
          });
        } else {
          grid.innerHTML = tools.map(toolCardHTML).join('');
        }
        /* 参数表折叠 */
        U.$$('.cap-tool-head', grid).forEach(function (h) {
          h.addEventListener('click', function () {
            h.parentNode.classList.toggle('open');
          });
        });
      }

      if (roleHost) {
        roleHost.innerHTML = (data.roles || []).map(roleCardHTML).join('') ||
          '<p class="muted small">暂无角色。</p>';
        /* 角色卡：只看该角色 */
        U.$$('[data-cap-role-filter]', roleHost).forEach(function (btn) {
          btn.addEventListener('click', function (e) {
            e.stopPropagation();
            Capabilities.filters.role = btn.getAttribute('data-cap-role-filter');
            var sel = /** @type {HTMLSelectElement|null} */ (document.getElementById('cap-role-filter'));
            if (sel) sel.value = Capabilities.filters.role;
            Capabilities.render();
            U.toast('info', '只看角色工具：' + Capabilities.filters.role);
          });
        });
        /* 工具 chips：点击按工具名筛选 */
        U.$$('[data-cap-tool]', roleHost).forEach(function (chip) {
          chip.addEventListener('click', function () {
            var name = chip.getAttribute('data-cap-tool');
            Capabilities.filters.kw = String(name).toLowerCase();
            var kw = /** @type {HTMLInputElement|null} */ (document.getElementById('cap-search'));
            if (kw) kw.value = name;
            Capabilities.render();
          });
        });
      }
    }
  };

  /* 挂进共享命名空间（可变共享状态，文件头已用 global 指令声明） */
  DogBuddyViews.Capabilities = Capabilities;
})(window);

/* ==== js/views-projects.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-projects.js
   项目管理（ROADMAP T7.6）：项目=根目录+默认参数，工位挂载(可带子目录)，
   群组挂项目共享工作区；并把「参数继承链」摊开给人看
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var state = { projects: [], summary: {}, windows: [], iterations: [], bound: false, keyword: '' };

  var MODE_LABEL = { conservative: '保守', standard: '标准', aggressive: '激进' };

  function iterOf(projectId) {
    return state.iterations.filter(function (x) { return x.project_id === projectId; })[0];
  }

  function iterBlock(p) {
    var it = iterOf(p.id);
    if (!it) {
      return '<div class="prj-iter"><span class="card-sub">尚无迭代计划</span>' +
        '<button class="btn btn-soft btn-sm" data-prj="iter-start" data-id="' + U.esc(p.id) + '">' +
        '开始迭代…</button></div>';
    }
    var act = it.status === 'active';
    var prog = it.last_progress === '' || it.last_progress === undefined ? '—' : (it.last_progress + '%');
    var derived = (it.plan && it.plan.derived) || {};
    var why = [];
    if (derived.model_speed) why.push('模型' + derived.model_speed);
    if (derived.staff_speed) why.push('工位' + derived.staff_speed);
    if (derived.rate_limit) why.push('限速' + derived.rate_limit);
    return '<div class="prj-iter">' +
      '<div class="prj-iter-head">' +
      '<span class="chip ' + (act ? 'chip-on' : 'chip-grey') + '">迭代' +
      (act ? '进行中' : (it.status === 'done' ? '已完成' : '已停止')) + '</span>' +
      '<span class="chip chip-grey">' + U.esc(MODE_LABEL[it.mode] || it.mode) + '档</span>' +
      '<span class="card-sub">第 ' + (it.round || 0) + '/' + ((it.plan && it.plan.max_rounds) || '?') + ' 轮</span>' +
      '<span class="card-sub">进度 ' + U.esc(String(prog)) + '</span>' +
      '<span class="card-sub">每 ' + ((it.plan && it.plan.interval_minutes) || '?') + ' 分钟一轮</span>' +
      (act && it.next_run_at ? '<span class="card-sub">下次 ' + U.esc(String(it.next_run_at).slice(5, 16)) + '</span>' : '') +
      (it.stop_reason ? '<span class="card-sub">停止原因：' + U.esc(it.stop_reason) + '</span>' : '') +
      '</div>' +
      '<div class="prj-iter-goal">目标：' + U.esc(it.goal || '') + '</div>' +
      (why.length ? '<div class="card-sub">节奏来源：' + U.esc(why.join(' · ')) +
        (derived.interval === 'manual' ? '（间隔为手动覆盖）' : '') + '</div>' : '') +
      '<div class="prj-actions">' +
      '<button class="btn btn-soft btn-sm" data-prj="iter-run" data-id="' + U.esc(p.id) + '">推一轮</button>' +
      '<button class="btn btn-soft btn-sm" data-prj="iter-chain" data-id="' + U.esc(p.id) + '">轮次记录</button>' +
      (act
        ? '<button class="btn btn-ghost btn-sm" data-prj="iter-stop" data-id="' + U.esc(p.id) + '">停止</button>'
        : '<button class="btn btn-soft btn-sm" data-prj="iter-resume" data-id="' + U.esc(p.id) + '">恢复</button>') +
      '<button class="btn btn-danger btn-sm" data-prj="iter-del" data-id="' + U.esc(p.id) + '">' +
      U.icon('trash', 'sm') + '</button>' +
      '</div></div>';
  }

  var SOURCE_LABEL = {
    window: { text: '工位覆盖', cls: 'chip-on' },
    project: { text: '项目覆盖', cls: 'chip-on' },
    inherited: { text: '继承中', cls: 'chip-grey' }
  };

  /* ---------------- 渲染 ---------------- */
  function renderSummary() {
    var host = U.$('#prj-summary');
    if (!host) return;
    var s = state.summary || {};
    host.innerHTML = [
      ['项目', s.total || 0],
      ['已挂工位', s.mounted_windows || 0],
      ['可见目录', (A.projects._roots || state.roots || []).length]
    ].map(function (kv) {
      return '<div class="stat"><div class="stat-num">' + U.esc(String(kv[1])) + '</div>' +
        '<div class="stat-label">' + U.esc(kv[0]) + '</div></div>';
    }).join('');
  }

  function projectCard(p) {
    var wins = (p.windows || []).map(function (w) {
      return '<span class="chip">' + U.esc(w.name) +
        (w.subdir ? '<span class="mono chip-sub">/' + U.esc(w.subdir) + '</span>' : '') +
        ' <button class="chip-x" data-unmount="' + U.esc(w.id) + '" title="解挂">×</button></span>';
    }).join(' ') || '<span class="card-sub">还没有工位挂载</span>';
    var groups = (p.groups || []).map(function (g) {
      return '<span class="chip chip-grey">群组 ' + U.esc(g.name) + '</span>';
    }).join(' ');
    var pcount = Object.keys(p.params || {}).length;
    return '<div class="card prj-card">' +
      '<div class="card-body">' +
      '<div class="prj-head">' +
      '<div><div class="prj-name">' + U.esc(p.name) + '</div>' +
      '<div class="card-sub mono">' + U.esc(p.root_path) + '</div></div>' +
      '<div class="prj-actions">' +
      '<button class="btn btn-soft btn-sm" data-prj="chain" data-id="' + U.esc(p.id) + '" title="工位/项目/配置套/全局的参数继承">参数链</button>' +
      '<button class="btn btn-soft btn-sm" data-prj="cmd-chain" data-id="' + U.esc(p.id) + '" title="管理员→主管→员工 的指挥链与健康度">指挥链</button>' +
      '<button class="btn btn-soft btn-sm" data-prj="mount" data-id="' + U.esc(p.id) + '">挂载工位</button>' +
      '<button class="btn btn-soft btn-sm" data-prj="edit" data-id="' + U.esc(p.id) + '">编辑</button>' +
      '<button class="btn btn-danger btn-sm" data-prj="del" data-id="' + U.esc(p.id) + '">' + U.icon('trash', 'sm') + '</button>' +
      '</div></div>' +
      '<div class="prj-meta">' +
      '<span class="card-sub">默认参数 ' + pcount + ' 项</span>' +
      '<span class="card-sub">工位 ' + (p.window_count || 0) + ' 个</span>' +
      (groups ? '<span class="card-sub">' + groups + '</span>' : '') +
      '</div>' +
      '<div class="prj-wins">' + wins + '</div>' +
      iterBlock(p) +
      '</div></div>';
  }

  function render() {
    var host = U.$('#prj-list');
    if (!host) return;
    var kw = state.keyword.trim().toLowerCase();
    var rows = state.projects.filter(function (p) {
      if (!kw) return true;
      return ((p.name || '') + (p.root_path || '')).toLowerCase().indexOf(kw) >= 0;
    });
    if (!rows.length) {
      host.innerHTML = U.empty({
        art: 'box',
        title: state.projects.length ? '没有匹配的项目' : '还没有项目',
        desc: state.projects.length ? '换个关键词试试。'
          : '项目 = 一个根目录 + 一组默认参数。建好之后把工位挂上来（可指定子目录），多工位就能在同一份代码上协作，群组也能共享这个目录。'
      });
      return;
    }
    host.innerHTML = rows.map(projectCard).join('');
    U.$$('[data-prj]', host).forEach(function (b) {
      b.addEventListener('click', function () {
        action(b.getAttribute('data-prj'), b.getAttribute('data-id'));
      });
    });
    U.$$('[data-unmount]', host).forEach(function (b) {
      b.addEventListener('click', function () { unmount(b.getAttribute('data-unmount')); });
    });
  }

  /* ---------------- 动作 ---------------- */
  function load() {
    ensureBound();
    return Promise.all([A.projects.list(), A.windows.list(), A.iterations.list()])
      .then(function (res) {
      var d = res[0] || {};
      state.projects = d.projects || [];
      state.summary = d.summary || {};
      state.roots = d.workspaces || [];
      A.projects._roots = state.roots;
      state.windows = Array.isArray(res[1]) ? res[1] : (res[1].windows || []);
      state.iterations = (res[2] && res[2].iterations) || [];
      state.presets = (res[2] && res[2].presets) || [];
      renderSummary();
      render();
    }).catch(function (e) {
      U.toast('error', '加载项目失败：' + (e && e.message ? e.message : e));
    });
  }

  function action(kind, id) {
    var p = state.projects.filter(function (x) { return x.id === id; })[0];
    if (!p) return;
    if (kind === 'edit') return editProject(p);
    if (kind === 'mount') return mountWindow(p);
    if (kind === 'chain') return showChain(p);
    if (kind === 'cmd-chain') return showCmdChain(p);
    if (kind === 'iter-start') return startIteration(p);
    if (kind === 'iter-run') return runIteration(p, true);
    if (kind === 'iter-chain') return showRounds(p);
    if (kind === 'iter-delete' || kind === 'iter-del') {
      var it = iterOf(p.id);
      if (!it) return;
      return U.confirm({ title: '删除迭代计划', text: '只删计划与轮次记录，不影响项目文件。确定？', danger: true })
        .then(function (ok) {
          if (!ok) return;
          return A.iterations.remove(it.id).then(function () {
            U.toast('success', '已删除迭代'); return load();
          }).catch(function (e) { U.toast('error', e.message); });
        });
    }
    if (kind === 'iter-stop') {
      var it2 = iterOf(p.id);
      if (!it2) return;
      return A.iterations.stop(it2.id, '管理员手动停止').then(function () {
        U.toast('success', '已停止迭代'); return load();
      }).catch(function (e) { U.toast('error', e.message); });
    }
    if (kind === 'iter-resume') {
      var it3 = iterOf(p.id);
      if (!it3) return;
      return A.iterations.resume(it3.id).then(function () {
        U.toast('success', '已恢复迭代'); return load();
      }).catch(function (e) { U.toast('error', e.message); });
    }
    if (kind === 'del') {
      return U.confirm({
        title: '删除项目',
        text: '只会删除项目记录并解挂工位/群组，**工作目录和文件会保留**。确定？',
        danger: true
      }).then(function (ok) {
        if (!ok) return;
        return A.projects.remove(id).then(function () {
          U.toast('success', '已删除（目录已保留）');
          return load();
        }).catch(function (e) { U.toast('error', e.message); });
      });
    }
  }

  /* ---------------- 迭代（T8.5/T8.6） ---------------- */
  function startIteration(p) {
    var presets = state.presets || [];
    var mounted = (p.windows || []).map(function (w) { return w.id; });
    U.modal({
      title: '开始项目迭代：' + p.name,
      wide: true,
      body:
        '<div class="field"><span class="field-label">迭代目标 *（要具体、可判定"达成没"）</span>' +
        '<textarea class="textarea" id="it-goal" rows="3" ' +
        'placeholder="例如：让 /api/models 的备注字段支持批量导入，并补齐 12 个单测。"></textarea></div>' +
        '<div class="field"><span class="field-label">档位（三档预设；下面的手动值会覆盖它）</span>' +
        '<select class="select" id="it-mode">' + presets.map(function (x) {
          return '<option value="' + U.esc(x.mode) + '"' + (x.mode === 'standard' ? ' selected' : '') +
            '>' + U.esc(x.label) + ' —— ' + U.esc(x.hint) + '</option>';
        }).join('') + '</select></div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">迭代间隔（分钟，0=自动推）</span>' +
        '<input class="input" id="it-speed" type="number" min="0" value="0"></div>' +
        '<div class="field"><span class="field-label">次数上限（0=用档位默认）</span>' +
        '<input class="input" id="it-max" type="number" min="0" value="0"></div>' +
        '</div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">无进展几轮就停（0=用档位默认）</span>' +
        '<input class="input" id="it-npl" type="number" min="0" value="0"></div>' +
        '<div class="field"><span class="field-label">预算上限 tokens（可选，0=不限）</span>' +
        '<input class="input" id="it-budget" type="number" min="0" value="0"></div>' +
        '</div>' +
        '<div class="card-sub">参与工位：' + (mounted.length
          ? U.esc((p.windows || []).map(function (w) { return w.name; }).join('、'))
          : '<b>该项目还没有挂工位</b>——先去挂工位再来开迭代') + '</div>' +
        '<div class="hint">系统会按<b>员工画像 + 模型能力 + 当前限速严重度</b>自动推节奏：' +
        '模型越慢、工位越保守、正在被限速 → 迭代越慢（避免排队雪崩）。</div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        {
          label: '开始', cls: 'btn-primary',
          onClick: function (body) {
            var goal = body.querySelector('#it-goal').value.trim();
            if (!goal) { U.toast('warn', '迭代目标不能为空'); return false; }
            A.iterations.create({
              project_id: p.id, goal: goal,
              mode: body.querySelector('#it-mode').value,
              window_ids: mounted,
              speed_minutes: Number(body.querySelector('#it-speed').value) || 0,
              max_rounds: Number(body.querySelector('#it-max').value) || 0,
              no_progress_limit: Number(body.querySelector('#it-npl').value) || 0,
              budget_tokens: Number(body.querySelector('#it-budget').value) || 0
            }).then(function (r) {
              var plan = (r && r.plan) || {};
              U.toast('success', '迭代已开始：每 ' + plan.interval_minutes + ' 分钟一轮，最多 ' +
                plan.max_rounds + ' 轮');
              return load();
            }).catch(function (e) { U.toast('error', e.message); });
          }
        }
      ]
    });
  }

  function runIteration(p, confirmFirst) {
    var it = iterOf(p.id);
    if (!it) return;
    var go = function () {
      U.toast('info', '正在推一轮…（可能要几分钟）');
      return A.iterations.runNow(it.id).then(function (r) {
        var res = (r && r.result) || {};
        U.toast(res.stopped ? 'warn' : 'success',
          res.stopped ? ('本轮后停止：' + (res.stop_reason || ''))
                      : ('第 ' + res.round + ' 轮完成，进度 ' +
                         (res.progress === null || res.progress === undefined ? '未汇报' : res.progress + '%')));
        return load();
      }).catch(function (e) { U.toast('error', '推轮失败：' + e.message); });
    };
    if (confirmFirst) {
      return U.confirm({ title: '立即推一轮',
        text: '会立刻让项目团队在共享工作区跑一轮（消耗模型额度）。确定？' })
        .then(function (ok) { if (ok) return go(); });
    }
    return go();
  }

  function showRounds(p) {
    var it = iterOf(p.id);
    if (!it) return;
    A.iterations.detail(it.id).then(function (d) {
      var rounds = d.rounds || [];
      var rows = rounds.map(function (x) {
        return '<tr><td class="mono">#' + x.round + '</td>' +
          '<td>' + U.esc(String(x.progress)) + '%' + (x.goal_achieved ? ' <span class="badge badge-green">达成</span>' : '') + '</td>' +
          '<td>' + U.esc(x.status) + '</td>' +
          '<td class="mdl-note">' + U.esc((x.summary || '').slice(0, 400)) + '</td></tr>';
      }).join('');
      U.modal({
        title: '迭代轮次记录',
        wide: true,
        body: '<div class="card-sub" style="margin-bottom:8px">目标：' + U.esc(it.goal) +
          '　·　计划：每 ' + d.plan.interval_minutes + ' 分钟一轮，最多 ' + d.plan.max_rounds +
          ' 轮，连续 ' + d.plan.no_progress_limit + ' 轮无进展即停' +
          (d.plan.budget_tokens ? '，预算 ' + d.plan.budget_tokens + ' tokens' : '') + '</div>' +
          (rounds.length
            ? '<div class="table-wrap"><table class="table"><thead><tr><th>轮次</th>' +
              '<th>进度</th><th>状态</th><th>本轮做了什么</th></tr></thead><tbody>' +
              rows + '</tbody></table></div>'
            : '<div class="card-sub">还没有轮次记录。</div>'),
        buttons: [{ label: '关闭', cls: 'btn-ghost' }]
      });
    }).catch(function (e) { U.toast('error', e.message); });
  }

  /* 主管候选要现拉一次（可能刚建了主管工位）——P1-2：以前没有这个字段，
     owner_supervisor_id 只能手工 PATCH。 */
  function editProject(p) {
    A.windows.list().then(function (d) {
      openEditModal(p, d.windows || []);
    }).catch(function () { openEditModal(p, []); });
  }

  function openEditModal(p, allWindows) {
    var isNew = !p;
    p = p || { name: '', root_path: '', params: {} };
    U.modal({
      title: isNew ? '新建项目' : ('编辑项目：' + p.name),
      wide: true,
      body:
        '<div class="field"><span class="field-label">项目名 *</span>' +
        '<input class="input" id="prj-f-name" value="' + U.esc(p.name || '') + '" placeholder="如 DogBuddyCn 主仓库"></div>' +
        '<div class="field"><span class="field-label">项目主管（谁负责这个项目：拆活、盯进度、裁决审批）</span>' +
        '<select class="select" id="prj-f-sup">' +
        '<option value="">（无主管 —— 事件直接连管理员）</option>' +
        (allWindows || []).filter(function (w) { return w.role === 'supervisor'; })
          .map(function (w) {
            var sel = (p.owner_supervisor_id === w.id) ? ' selected' : '';
            return '<option value="' + U.esc(w.id) + '"' + sel + '>' +
              U.esc(w.name) + ' (' + U.esc(w.id) + ')</option>';
          }).join('') +
        '</select>' +
        '<div class="hint">没有可选的主管？先到「工位」页新建一个角色为 supervisor 的工位。</div></div>' +
        '<div class="field"><span class="field-label">根目录（留空=在工作区根下用项目名建）</span>' +
        '<input class="input mono" id="prj-f-root" value="' + U.esc(p.root_path || '') + '" placeholder="/opt/dogbuddy/workspaces/xxx"></div>' +
        (isNew ? '<label class="mdl-check"><input type="checkbox" id="prj-f-git" checked>初始化 git 仓库</label>' : '') +
        '<div class="field"><span class="field-label">默认参数（JSON，工位可覆盖）</span>' +
        '<textarea class="textarea mono" id="prj-f-params" rows="4" placeholder=\'{"max_iterations": 30, "max_context_tokens": 64000, "retry": {"max_retries": 3}}\'>' +
        U.esc(JSON.stringify(p.params || {}, null, 2)) + '</textarea>' +
        '<div class="hint">这一层比工位弱：项目定默认，工位里写的值仍然优先。</div></div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        {
          label: isNew ? '创建' : '保存', cls: 'btn-primary',
          onClick: function (body) {
            var params = {};
            var raw = body.querySelector('#prj-f-params').value.trim();
            if (raw) {
              try { params = JSON.parse(raw); } catch (e) { U.toast('warn', '参数不是合法 JSON：' + e.message); return false; }
            }
            var payload = {
              name: body.querySelector('#prj-f-name').value.trim(),
              root_path: body.querySelector('#prj-f-root').value.trim(),
              params: params
            };
            var supEl = body.querySelector('#prj-f-sup');
            if (supEl) payload.owner_supervisor_id = supEl.value;
            if (!payload.name) { U.toast('warn', '项目名不能为空'); return false; }
            if (isNew) payload.git_init = !!body.querySelector('#prj-f-git').checked;
            var req = isNew ? A.projects.create(payload) : A.projects.update(p.id, payload);
            req.then(function () { U.toast('success', isNew ? '项目已创建' : '已保存'); return load(); })
              .catch(function (e) { U.toast('error', e.message); });
          }
        }
      ]
    });
  }

  function mountWindow(p) {
    var free = state.windows.filter(function (w) {
      return (w.project_id || '') !== p.id;
    });
    if (!free.length) { U.toast('warn', '没有可挂载的工位'); return; }
    U.modal({
      title: '把工位挂到「' + p.name + '」',
      body:
        '<div class="field"><span class="field-label">选择工位</span>' +
        '<select class="select" id="prj-m-win">' + free.map(function (w) {
          var tag = w.project_id ? '（现挂：' + (w.project_name || w.project_id) + '）' : '';
          return '<option value="' + U.esc(w.id) + '">' + U.esc(w.name) + tag + '</option>';
        }).join('') + '</select></div>' +
        '<div class="field"><span class="field-label">子目录（可选）</span>' +
        '<input class="input mono" id="prj-m-sub" placeholder="如 services/api —— 留空=用项目根">' +
        '<div class="hint">填了子目录，这个工位的文件与 shell 边界就锁在子目录里，避免和其他工位互相踩。</div></div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        {
          label: '挂载', cls: 'btn-primary',
          onClick: function (body) {
            A.projects.mount({
              window_id: body.querySelector('#prj-m-win').value,
              project_id: p.id,
              subdir: body.querySelector('#prj-m-sub').value.trim()
            }).then(function () { U.toast('success', '已挂载'); return load(); })
              .catch(function (e) { U.toast('error', e.message); });
          }
        }
      ]
    });
  }

  function unmount(windowId) {
    A.projects.unmount(windowId).then(function () {
      U.toast('success', '已解挂');
      return load();
    }).catch(function (e) { U.toast('error', e.message); });
  }

  function showChain(p) {
    var wid = (p.windows && p.windows[0]) ? p.windows[0].id : '';
    var req = wid ? A.projects.chain(p.id, wid) : A.projects.chain(p.id);
    req.then(function (d) {
      var eff = d.effective || {}, src = d.sources || {};
      var keys = Object.keys(eff);
      var rows = keys.map(function (k) {
        var s = SOURCE_LABEL[src[k]] || SOURCE_LABEL.inherited;
        var v = eff[k];
        if (v && typeof v === 'object') v = JSON.stringify(v);
        return '<tr><td class="mono">' + U.esc(k) + '</td>' +
          '<td class="mono">' + U.esc(String(v)) + '</td>' +
          '<td><span class="chip ' + s.cls + '">' + U.esc(s.text) + '</span></td></tr>';
      }).join('');
      U.modal({
        title: '参数继承链',
        wide: true,
        body:
          '<div class="card-sub" style="margin-bottom:8px">' +
          '优先级：<b>工位</b> &gt; 项目 &gt; 绑定配置套 &gt; 全局默认。' +
          (d.window ? '当前展示：工位「' + U.esc(d.window.name) + '」' : '（未指定工位，仅看项目层）') +
          '</div>' +
          '<div class="table-wrap"><table class="table"><thead><tr>' +
          '<th>参数</th><th>生效值</th><th>来源</th></tr></thead><tbody>' + rows + '</tbody></table></div>',
        buttons: [{ label: '关闭', cls: 'btn-ghost' }]
      });
    }).catch(function (e) { U.toast('error', e.message); });
  }

  /* 指挥链一览（P1-2）：后端 `/api/projects/{id}/chain` 原来没有前端调用者。 */
  function showCmdChain(p) {
    A.projects.cmdChain(p.id).then(function (d) {
      var sup = d.supervisor;
      var supRow = sup
        ? '<div class="card-sub">主管工位：<b>' + U.esc(sup.name) + '</b> ' +
          '<span class="mono">' + U.esc(sup.id) + '</span> ' +
          '<span class="chip">scope=' + U.esc(sup.scope) + '</span></div>'
        : '<div class="card-sub">主管工位：<b>没有</b>（事件直接连管理员，兼容单管理员部署）</div>';
      var workers = (d.workers || []).map(function (w) {
        return '<tr><td class="mono">' + U.esc(w.id) + '</td><td>' + U.esc(w.name) + '</td>' +
          '<td class="mono">' + U.esc(w.role) + '</td>' +
          '<td><span class="chip">' + U.esc(w.status || '-') + '</span></td></tr>';
      }).join('') || '<tr><td colspan="4" class="card-sub">该项目还没有员工工位</td></tr>';
      var warn = (d.health || '').indexOf('ok') === 0 ? '' :
        '<div class="hint" style="margin-top:8px">⚠ ' + U.esc(d.health) + '</div>';
      U.modal({
        title: '指挥链 · ' + U.esc(d.project.name),
        wide: true,
        body:
          '<div class="card-sub" style="margin-bottom:8px">' +
          '管理员 <b>system</b> → 主管 <b>project</b> → 员工。寻址：' +
          '<span class="mono">' + U.esc(d.addressing) + '</span></div>' +
          supRow + warn +
          '<div class="table-wrap" style="margin-top:8px"><table class="table"><thead><tr>' +
          '<th>工位 id</th><th>名称</th><th>角色</th><th>状态</th></tr></thead><tbody>' +
          workers + '</tbody></table></div>',
        buttons: [{ label: '关闭', cls: 'btn-ghost' }]
      });
    }).catch(function (e) { U.toast('error', e.message); });
  }

  function ensureBound() {
    if (state.bound) return;
    state.bound = true;
    var add = U.$('#prj-add');
    if (add) add.addEventListener('click', function () { editProject(null); });
    var rf = U.$('#prj-refresh');
    if (rf) rf.addEventListener('click', load);
    var kw = U.$('#prj-search');
    if (kw) kw.addEventListener('input', U.debounce(function () {
      state.keyword = kw.value; render();
    }, 120));
  }

  global.DogBuddyViews.Projects = { load: load, state: state };
})(window);

/* ==== js/views-models.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-models.js
   模型管理页：目录 + 备注（能力与限制）+ 一键切换当前模型
   —— 以前换模型只能改配置文件，换之前也没地方记下模型的脾气
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var state = { models: [], current: {}, summary: {}, options: {}, keyword: '', onlyEnabled: false, bound: false };

  /* v0.10.23：与后端 `model_service.endpoint_of().split('/')[0]` 同口径的 host[:port]。
     用来在**提交前**判断"这条会不会撞上同身份（同名+同端点+同 key）的既有条目"。
     v0.10.26：实现搬到 `U.endpointHost`（设置向导也要用），这里只留个别名。 */
  var endpointHostOf = U.endpointHost;

  /* ---------------- 渲染 ---------------- */
  function curLine() {
    var c = state.current || {};
    var tag = state.summary && state.summary.current_in_catalog ? '' :
      ' <span class="badge badge-amber">未纳入目录</span>';
    return '<div class="card"><div class="card-body mdl-current">' +
      '<span class="mdl-cur-label" title="管理员就是你正在对话的那个 Agent；它在模型管理页点「设为当前」即可更换">管理员当前模型</span>' +
      '<span class="mono mdl-cur-model">' + U.esc(c.model || '(未设置)') + '</span>' + tag +
      '<span class="card-sub mono">' + U.esc(c.base_url || '') + '</span>' +
      '<span class="card-sub">上下文 ' + U.esc(String(c.max_context_tokens || 0)) + ' tokens</span>' +
      '<span class="card-sub">活跃配置套 ' + U.esc(c.active_profile || '(默认)') + '</span>' +
      '</div>' +
      ((state.summary && state.summary.current_in_catalog === false)
        ? '<div class="card-sub" style="margin-top:6px;color:var(--warn)">' +
          '⚠️ 这个模型**不在下面的目录里** —— 页面上看不到它、也没法写备注。' +
          '<button class="btn btn-soft btn-sm" data-mdl="adopt-current" style="margin-left:8px">把当前模型纳入目录</button>' +
          '</div>'
        : '') +
      '<div class="card-sub" style="margin-top:6px">下面对话那头（管理员）用的就是这条模型；' +
      '工位没有单独指定模型时也继承它。点某一行的「设为管理员模型」即可切换。' +
      '<button class="btn btn-soft btn-sm" data-mdl="verify-current" style="margin-left:8px">' +
      '校验它在服务商列表里</button></div>' +
      '<div class="card-sub" id="mdl-verify-out" style="margin-top:4px"></div>' +
      '</div>';
  }

  function noteCell(m) {
    if ((m.note || '').trim()) {
      return '<div class="mdl-note">' + U.esc(m.note) + '</div>';
    }
    return '<span class="mdl-note mdl-note-empty">（未写备注——建议写清能力与限制）</span>';
  }

  function caps(m) {
    var out = [];
    (m.capability || '').split(/[,，\s]+/).forEach(function (t) {
      if (t) out.push('<span class="chip">' + U.esc(t) + '</span>');
    });
    if (m.speed) out.push('<span class="chip chip-grey">速度 ' + U.esc(m.speed) + '</span>');
    if (m.cost) out.push('<span class="chip chip-grey">成本 ' + U.esc(m.cost) + '</span>');
    return out.join(' ') || '<span class="card-sub">—</span>';
  }

  function row(m) {
    var isCur = m.is_current;
    /* v0.10.21：同名模型可以共存（不同端点各一条）。同名时把**端点**显式标出来，
       否则两条重名条目在列表里看起来一模一样（owner：「新增的会覆盖掉原来的」）。 */
    var dup = (state.dupNames && state.dupNames[m.model]) > 1;
    return '<tr' + (isCur ? ' class="mdl-row-cur"' : '') + '>' +
      '<td><div class="mdl-name">' + U.esc(m.display_name || m.model) +
      (isCur ? ' <span class="badge badge-green">当前</span>' : '') +
      (m.enabled ? '' : ' <span class="badge badge-grey">已停用</span>') + '</div>' +
      (dup ? '<span class="badge badge-amber" title="目录里有同名的另一条，靠端点区分">同名 · ' +
        U.esc(m.endpoint_host || m.endpoint || '?') + '</span> ' : '') +
      '<div class="card-sub mono">' + U.esc(m.id) + '</div></td>' +
      '<td>' + U.esc(m.provider || '') + '<div class="card-sub mono">' + U.esc(m.base_url || '—') + '</div>' +
      (m.has_api_key
        ? '<span class="badge badge-green" title="该条目自带密钥，「设为当前」时会一起切换">自带密钥</span>'
        : '<span class="badge badge-amber" title="设置页的全局密钥框已停用；给这条填一把自己的密钥最稳妥">用全局密钥（已停用）</span>') + '</td>' +
      '<td>' + noteCell(m) + '</td>' +
      '<td>' + caps(m) + '</td>' +
      '<td class="num">' + (m.context_window ? U.esc(String(m.context_window)) : '—') + '</td>' +
      '<td class="mdl-actions">' +
      '<button class="btn btn-soft btn-sm" data-mdl="note" data-id="' + U.esc(m.id) + '">' + U.icon('edit', 'sm') + '备注</button>' +
      (isCur ? '' : '<button class="btn btn-primary btn-sm" data-mdl="activate" data-id="' + U.esc(m.id) + '" title="管理员与（未单独指定模型的）工位都会用它">设为管理员模型</button>') +
      '<button class="btn btn-ghost btn-sm" data-mdl="toggle" data-id="' + U.esc(m.id) + '">' + (m.enabled ? '停用' : '启用') + '</button>' +
      '<button class="btn btn-danger btn-sm" data-mdl="del" data-id="' + U.esc(m.id) + '">' + U.icon('trash', 'sm') + '</button>' +
      '</td></tr>';
  }

  function render() {
    var host = U.$('#mdl-list');
    if (!host) return;
    var kw = state.keyword.trim().toLowerCase();
    var rows = state.models.filter(function (m) {
      if (state.onlyEnabled && !m.enabled) return false;
      if (!kw) return true;
      return ((m.model || '') + (m.display_name || '') + (m.note || '') + (m.capability || ''))
        .toLowerCase().indexOf(kw) >= 0;
    });
    if (!rows.length) {
      host.innerHTML = U.empty({
        art: 'box',
        title: state.models.length ? '没有匹配的模型' : '模型目录是空的',
        desc: state.models.length
          ? '换个关键词，或清掉"只看启用"。'
          : '点右上角「拉取模型」从当前 LLM 服务商导入，或手动「新增模型」——记得给每个模型写备注（能力/限制/限速）。'
      });
      return;
    }
    /* 同名计数：给 row() 用来决定是否显示「同名 · 端点」标签 */
    state.dupNames = {};
    state.models.forEach(function (m) {
      state.dupNames[m.model] = (state.dupNames[m.model] || 0) + 1;
    });
    host.innerHTML = '<div class="table-wrap"><table class="table mdl-table">' +
      '<thead><tr><th>模型</th><th>来源</th><th>备注（能力与限制）</th>' +
      '<th>标签</th><th>上下文</th><th class="th-actions">操作</th></tr></thead>' +
      '<tbody>' + rows.map(row).join('') + '</tbody></table></div>';
    U.$$('[data-mdl]', host).forEach(function (b) {
      b.addEventListener('click', function () { action(b.getAttribute('data-mdl'), b.getAttribute('data-id')); });
    });
  }

  function renderSummary() {
    var host = U.$('#mdl-summary');
    if (!host) return;
    var s = state.summary || {};
    host.innerHTML = [
      ['目录内模型', s.total || 0],
      ['启用', s.enabled || 0],
      ['已写备注', s.noted || 0],
      ['用全局密钥（设置页已停用）', (state.models || []).filter(function (m) {
        return m.has_api_key === false;
      }).length],
      ['当前模型', s.current_model || '—']
    ].map(function (kv) {
      return '<div class="stat"><div class="stat-num">' + U.esc(String(kv[1])) + '</div>' +
        '<div class="stat-label">' + U.esc(kv[0]) + '</div></div>';
    }).join('');
  }

  /* ---------------- 动作 ---------------- */
  function apiModels() { return A.models; }

  function load() {
    ensureBound();
    return Promise.all([apiModels().list(state.onlyEnabled), apiModels().options()])
      .then(function (res) {
        var data = res[0] || {};
        state.models = data.models || [];
        state.current = data.current || {};
        state.summary = data.summary || {};
        state.options = res[1] || {};
        renderSummary();
        var cur = U.$('#mdl-current');
        if (cur) {
          cur.innerHTML = curLine();
          U.$$('[data-mdl]', cur).forEach(function (b) {
            b.addEventListener('click', function () {
              action(b.getAttribute('data-mdl'), b.getAttribute('data-id'));
            });
          });
        }
        render();
      }).catch(function (e) {
        U.toast('error', '加载模型目录失败：' + (e && e.message ? e.message : e));
      });
  }

  function action(kind, id) {
    if (kind === 'adopt-current') {
      A.models.adoptCurrent().then(function (r) {
        U.toast('success', '已把当前模型纳入目录：' + ((r.model && r.model.model) || ''));
        return load();
      }).catch(function (e) { U.toast('error', '纳入失败：' + e.message); });
      return;
    }
    if (kind === 'verify-current') {
      var out = U.$('#mdl-verify-out');
      if (out) out.textContent = '正在向服务商校验（并真发一次 1-token 调用）…';
      /* v0.10.21：以前只查"在不在列表"→ "列表里有但套餐不含"会显示绿灯，
         真跑任务才 403（owner 实测 ds4.1）。现在顺带验一次真调用。 */
      A.models.verifyCurrent(true).then(function (r) {
        if (!out) return;
        if (r.error) { out.textContent = '校验失败：' + r.error; return; }
        var probe = r.probe || {};
        if (r.in_provider_list && r.usable === false) {
          out.innerHTML = '<span style="color:var(--warn,#fcd34d)">⚠️ 在服务商列表里，但真调用被拒：</span>' +
            U.esc(probe.error || '') +
            '<div class="card-sub">列表校验看不出这种情况（套餐/额度不含该模型）。' +
            '要么在服务商侧开通，要么换个模型。</div>';
          return;
        }
        out.innerHTML = r.in_provider_list
          ? '<span style="color:var(--ok)">✅ 服务商列表里有这个模型</span>（共 ' + r.model_count + ' 个模型）' +
            (r.usable ? '，真调用也通了' + (probe.latency_ms ? '（' + probe.latency_ms + 'ms）' : '')
                      : '')
          : '<span style="color:var(--danger)">❌ 服务商列表里**没有** ' + U.esc(r.model) + '</span>' +
            (r.similar && r.similar.length
              ? '（相近的：' + r.similar.map(U.esc).join('、') + '）'
              : '（共 ' + r.model_count + ' 个模型；可能是拼错或已下线）');
      }).catch(function (e) { if (out) out.textContent = '校验失败：' + e.message; });
      return;
    }
    var m = state.models.filter(function (x) { return x.id === id; })[0];
    if (!m) return;
    if (kind === 'note') return editModel(m, true);
    if (kind === 'toggle') {
      return apiModels().update(id, { enabled: !m.enabled })
        .then(function () { U.toast('success', m.enabled ? '已停用' : '已启用'); return load(); })
        .catch(function (e) { U.toast('error', e.message); });
    }
    if (kind === 'del') {
      return U.confirm({ title: '删除模型', text: '只从目录删除，不影响当前运行配置。确定？', danger: true })
        .then(function (ok) {
          if (!ok) return;
          return apiModels().remove(id).then(function () { U.toast('success', '已删除'); return load(); })
            .catch(function (e) { U.toast('error', e.message); });
        });
    }
    if (kind === 'activate') {
      return apiModels().activate(id).then(function (r) {
        var res = (r && r.result) || {};
        U.toast('success', '管理员模型已切换为 ' + (res.model || id) + '（已写盘，重启后仍生效）');
        return load();
      }).catch(function (e) { U.toast('error', '切换失败：' + e.message); });
    }
  }

  function chipInput(id, value, options, label) {
    var chosen = String(value || '').split(/[,，\s]+/).filter(Boolean);
    return '<div class="field"><span class="field-label">' + U.esc(label) + '</span>' +
      '<div class="mdl-chips" id="' + id + '-chips">' +
      (options || []).map(function (o) {
        var on = chosen.indexOf(o) >= 0;
        return '<button type="button" class="chip chip-btn' + (on ? ' chip-on' : '') +
          '" data-chip="' + U.esc(o) + '">' + U.esc(o) + '</button>';
      }).join('') +
      '</div><input class="input" id="' + id + '" value="' + U.esc(value || '') +
      '" placeholder="也可以手动输入，逗号分隔"></div>';
  }

  /* ---------------- 新增/编辑模型（v12：一条表单就能从零接入一家服务商） ----------------
     以前的表单要求手打 provider、手打地址、9 个字段一次填完，而且**没有密钥栏**——
     跨服务商换模型时用的还是全局那把 key（401），只能再回设置页手改。
     现在：预设一键带入地址 → 填密钥 → 拉取模型列表选名字 → 测试连接 → 保存。 */
  function presetChips() {
    return (state.options.providers || []).map(function (p) {
      return '<button type="button" class="chip chip-btn" data-preset="' + U.esc(p.provider) +
        '" data-url="' + U.esc(p.url) + '">' + U.esc(p.label) + '</button>';
    }).join(' ') || '<span class="card-sub">（无预设）</span>';
  }

  function probeOut(msg, kind) {
    var el = U.$('#mdl-f-probe-out');
    if (!el) return;
    el.className = 'hint' + (kind === 'ok' ? ' form-ok' : (kind === 'err' ? ' form-error' : ''));
    el.textContent = msg || '';
  }

  function editModel(m, focusNote) {
    var isNew = !m;
    /* v0.10.12：新条目**不预填 128k**——上下文窗口跟着服务商/模型走，
       服务商没给就留空（0 = 继承），别替它编一个数。 */
    m = m || { model: '', provider: state.current.provider || 'openai_compat',
               base_url: state.current.base_url || '', note: '', capability: '',
               context_window: 0, speed: '', cost: '' };
    var keyHint = m.has_api_key
      ? ('已保存 ' + (m.api_key || '********') + '，留空保持不变')
      : '留空 = 用全局密钥（设置页那把）';
    U.modal({
      title: isNew ? '新增模型' : ('模型：' + (m.display_name || m.model)),
      wide: true,
      body:
        '<div class="field"><span class="field-label">服务商预设（点一下带出地址）</span>' +
        '<div class="mdl-chips" id="mdl-f-presets">' + presetChips() + '</div></div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">模型名 *</span>' +
        '<input class="input mono" id="mdl-f-model" value="' +
        U.esc(m.model || '') + '" placeholder="也可以直接在下面的下拉里选"></div>' +
        '<div class="field"><span class="field-label">API 地址（base_url）</span>' +
        '<input class="input mono" id="mdl-f-base" value="' + U.esc(m.base_url || '') +
        '" placeholder="https://…/v1"></div>' +
        '</div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">API Key</span>' +
        '<input class="input mono" id="mdl-f-key" type="password" value="" placeholder="' +
        U.esc(keyHint) + '"></div>' +
        '<div class="field"><span class="field-label">provider</span>' +
        '<input class="input mono" id="mdl-f-provider" value="' +
        U.esc(m.provider || 'openai_compat') + '"></div>' +
        '</div>' +
        '<div class="mdl-probe-row">' +
        '<button class="btn btn-soft btn-sm" id="mdl-f-fetch" type="button">' + U.icon('refresh', 'sm') + '拉取模型列表</button>' +
        '<button class="btn btn-soft btn-sm" id="mdl-f-test" type="button">' + U.icon('activity', 'sm') + '测试连接</button>' +
        '<span class="hint" id="mdl-f-probe-out"></span></div>' +
        /* 拉取结果放进真正的下拉：<datalist> 会**按输入框里已有的文字过滤**，
           所以"自动填第一个"会让下拉里只剩那一个（实测就是这样：99 个只能选到 1 个）。 */
        '<div class="field" id="mdl-f-picker">' +
        '<span class="field-label">从拉取到的列表里选（<span id="mdl-f-count">0</span> 个）</span>' +
        '<select class="select mono" id="mdl-f-select"><option value="">（点上面「拉取模型列表」后在这里选）</option></select>' +
        '<div class="hint">这里列的是**全量**模型，选中即填入上面的「模型名」；不拉取也可以手输。</div></div>' +
        '<div class="field"><span class="field-label">备注：能力与限制（建议写清）</span>' +
        '<textarea class="textarea" id="mdl-f-note" rows="3" placeholder="例如：工具调用稳定；429 限速很凶，并发别超 2；中文好；不支持 vision。">' +
        U.esc(m.note || '') + '</textarea></div>' +
        chipInput('mdl-f-cap', m.capability, state.options.capabilities, '能力标签') +
        '<details class="mdl-adv"' + (focusNote ? '' : '') + '>' +
        '<summary>更多字段（显示名 / 上下文 / 速度 / 成本）</summary>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">显示名（可选）</span>' +
        '<input class="input" id="mdl-f-name" value="' + U.esc(m.display_name || '') + '"></div>' +
        '<div class="field"><span class="field-label">上下文窗口（tokens）</span>' +
        '<input class="input" id="mdl-f-ctx" type="number" min="0" value="' +
        U.esc(String(m.context_window || 0)) + '" placeholder="0 / 留空 = 用服务商默认">' +
        '<div class="hint" id="mdl-f-ctx-hint">拉取后如果服务商报了上下文，会自动填在这里；' +
        '没报就留 0（= 继承全局，不瞎填）。这个值会在「设为当前」时写进模型的运行时上下文。</div></div>' +
        '</div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">速度档</span>' +
        '<select class="select" id="mdl-f-speed">' + (state.options.speeds || ['']).map(function (s) {
          return '<option value="' + U.esc(s) + '"' + (s === (m.speed || '') ? ' selected' : '') + '>' +
            U.esc(s || '（未填）') + '</option>';
        }).join('') + '</select></div>' +
        '<div class="field"><span class="field-label">成本档</span>' +
        '<select class="select" id="mdl-f-cost">' + (state.options.costs || ['']).map(function (s) {
          return '<option value="' + U.esc(s) + '"' + (s === (m.cost || '') ? ' selected' : '') + '>' +
            U.esc(s || '（未填）') + '</option>';
        }).join('') + '</select></div>' +
        '</div></details>' +
        (isNew
          ? '<label class="mdl-chk"><input type="checkbox" id="mdl-f-activate" checked> 保存后立即设为当前模型</label>'
          : ''),
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        {
          label: isNew ? '创建' : '保存', cls: 'btn-primary',
          onClick: function (body) {
            var payload = {
              model: body.querySelector('#mdl-f-model').value.trim(),
              display_name: body.querySelector('#mdl-f-name').value.trim(),
              provider: body.querySelector('#mdl-f-provider').value.trim(),
              base_url: body.querySelector('#mdl-f-base').value.trim(),
              note: body.querySelector('#mdl-f-note').value.trim(),
              capability: body.querySelector('#mdl-f-cap').value.trim(),
              context_window: Number(body.querySelector('#mdl-f-ctx').value) || 0,
              speed: body.querySelector('#mdl-f-speed').value,
              cost: body.querySelector('#mdl-f-cost').value
            };
            /* 密钥：只在真的录入了新值时才发（掩码/空 = 保持原样，服务端也拦一道） */
            var typed = body.querySelector('#mdl-f-key').value.trim();
            if (typed) payload.api_key = typed;
            if (!payload.model) { U.toast('warn', '模型名不能为空'); return false; }
            /* v0.10.23：身份 = 模型名 + 端点 + **密钥**。只有"完全同身份"才拦；
               同端点换 key 是允许的（同一个服务商、同一个模型、两把 key 各存一条）——
               这正是用户要的，别再拦错。 */
            if (isNew) {
              var keyTail = payload.api_key
                ? String(payload.api_key).replace(/[^A-Za-z0-9]/g, '').slice(-4) : '';
              var clash = (state.models || []).filter(function (x) {
                if (x.model !== payload.model) return false;
                if ((x.endpoint_host || '') !== endpointHostOf(payload.base_url)) return false;
                if (!keyTail) return true;  /* 没填新 key = 沿用全局 → 同身份 */
                var tail = String(x.api_key || '').replace(/[^A-Za-z0-9]/g, '').slice(-4);
                return tail === keyTail;
              });
              if (clash.length) {
                U.toast('error', '已有完全同身份的条目「' + clash[0].id +
                  '」（同名 + 同端点 + 同一把 key）。新增不会覆盖它：' +
                  '要改请点那一行的「备注」；要用另一把 key，就把上面的 API Key 换成另一把。');
                return false;
              }
            }
            var wantActivate = isNew && body.querySelector('#mdl-f-activate') &&
              body.querySelector('#mdl-f-activate').checked;
            var p = isNew ? apiModels().create(payload) : apiModels().update(m.id, payload);
            p.then(function (r) {
              var saved = (r && r.model) || {};
              /* v0.10.23：同名但**身份不同**（换了端点或换了 key）→ 服务端另存了一条，
                 两条共存。把这件事说清楚，别让用户以为又是覆盖。 */
              if (isNew && saved.endpoint_conflict) {
                U.toast('success', '已加入目录：同名条目已有别的身份（端点或密钥不同），' +
                  '已另存为「' + (saved.id || '') + '」，原来那条不受影响');
              } else {
                U.toast('success', isNew ? '已加入目录' : '已保存');
              }
              if (wantActivate && saved.id) {
                return apiModels().activate(saved.id).then(function (a) {
                  var res = (a && a.result) || {};
                  U.toast('success', '已切换为 ' + (res.model || payload.model) +
                    (res.api_key_switched ? '（已同时切换为该模型自带的密钥）' : '（沿用全局密钥）'));
                });
              }
              return null;
            }).then(function () { return load(); })
              .catch(function (e) { U.toast('error', e.message); });
          }
        }
      ]
    });

    /* 预设：一键带入 provider + 地址 */
    var presets = U.$('#mdl-f-presets');
    if (presets) {
      U.$$('[data-preset]', presets).forEach(function (b) {
        b.addEventListener('click', function () {
          var prov = U.$('#mdl-f-provider');
          var base = U.$('#mdl-f-base');
          if (prov) prov.value = b.getAttribute('data-preset') || 'openai_compat';
          if (base) base.value = b.getAttribute('data-url') || '';
          U.$$('[data-preset]', presets).forEach(function (x) { x.classList.remove('chip-on'); });
          b.classList.add('chip-on');
        });
      });
    }

    /* 读当前表单（拉取/测试共用） */
    function formArgs() {
      var out = {
        provider: (U.$('#mdl-f-provider') || {}).value || '',
        base_url: (U.$('#mdl-f-base') || {}).value.trim(),
        model: (U.$('#mdl-f-model') || {}).value.trim()
      };
      var typed = (U.$('#mdl-f-key') || {}).value;
      if (typed && typed.trim()) out.api_key = typed.trim();
      else if (!isNew && m.has_api_key) out.model_ref = m.id;   /* 用条目里已保存的那把 */
      return out;
    }

    /* 拉取的上下文长度缓存：id → tokens（0 = 服务商没说） */
    var mdlCtx = {};
    function applyCtx(name) {
      var inp = U.$('#mdl-f-ctx');
      var hint = U.$('#mdl-f-ctx-hint');
      if (!inp) return;
      var ctx = Number(mdlCtx[name] || 0);
      if (ctx > 0) {
        if (!Number(inp.value)) inp.value = String(ctx);
        if (hint) hint.textContent = '服务商报的上下文是 ' + ctx + ' tokens（已填入，可改）。';
      } else if (!Number(inp.value) && hint) {
        hint.textContent = '这家服务商没给上下文长度 → 留 0 = 继承全局（不替它瞎填）。';
      }
    }

    var fetchBtn = U.$('#mdl-f-fetch');
    if (fetchBtn) {
      fetchBtn.addEventListener('click', function () {
        var a = formArgs();
        probeOut('拉取中…');
        A.settings.models(a.provider, a.base_url, a.api_key, undefined, a.model_ref).then(function (r) {
          var list = (r && r.models) || [];
          if (r && r.error) { probeOut('拉取失败：' + r.error, 'err'); return; }
          /* 唯一的选择入口：select（全量、不按输入框过滤） */
          U.fillModelSelect('mdl-f-select', list, 'mdl-f-model');
          /* 服务商愿意给上下文就记下来，选中时自动填进「上下文窗口」 */
          mdlCtx = {};
          (r.details || []).forEach(function (d) {
            if (d && d.id) mdlCtx[d.id] = Number(d.context_window || 0);
          });
          var ctxCount = Object.keys(mdlCtx).filter(function (k2) { return mdlCtx[k2] > 0; }).length;
          var cnt = U.$('#mdl-f-count');
          if (cnt) cnt.textContent = String(list.length);
          probeOut(list.length
            ? ('拉到 ' + list.length + ' 个模型，在下面下拉里选' +
               (ctxCount ? '（其中 ' + ctxCount + ' 个服务商给了上下文长度，选中会自动填）'
                         : '（这家没报上下文长度，条目留 0 = 继承）'))
            : '没拉到模型（检查地址与密钥）', list.length ? 'ok' : 'err');
        }).catch(function (e) { probeOut('拉取失败：' + e.message, 'err'); });
      });
    }

    /* 选中下拉项 → 填「模型名」（ui.fillModelSelect 已绑），并顺手带出服务商给的上下文 */
    var ctxSel = U.$('#mdl-f-select');
    if (ctxSel) {
      ctxSel.addEventListener('change', function () {
        if (ctxSel.value) applyCtx(ctxSel.value);
      });
    }

    var testBtn = U.$('#mdl-f-test');
    if (testBtn) {
      testBtn.addEventListener('click', function () {
        var a = formArgs();
        if (!a.model) { U.toast('warn', '先填模型名再测'); return; }
        probeOut('连接中…');
        A.settings.testLLM(a).then(function (r) {
          var ok = r && r.ok !== false && !r.error;
          probeOut((ok ? '✅ 通了' : '❌ 失败') +
            (r && r.latency_ms ? ' · ' + r.latency_ms + 'ms' : '') +
            (r && r.model_count ? ' · 该服务商 ' + r.model_count + ' 个模型' : '') +
            (r && r.error ? ' · ' + r.error : ''), ok ? 'ok' : 'err');
        }).catch(function (e) { probeOut('测试失败：' + e.message, 'err'); });
      });
    }

    // 标签 chip 交互
    var chips = U.$('#mdl-f-cap-chips');
    var input = U.$('#mdl-f-cap');
    if (chips && input) {
      U.$$('[data-chip]', chips).forEach(function (b) {
        b.addEventListener('click', function () {
          b.classList.toggle('chip-on');
          var on = U.$$('.chip-on', chips).map(function (x) { return x.getAttribute('data-chip'); });
          input.value = on.join(',');
        });
      });
    }
    if (focusNote) {
      var n = U.$('#mdl-f-note');
      if (n) n.focus();
    }
  }

  function importModels() {
    /* v0.10.21：拉取**过多**是真的（SiliconFlow 一次回 99 条），而且留空会静默
       拿"当前/全局 key"去拉 —— 于是"顺手把默认 key 那家的模型也拉进来了"。
       改成两步：先拉（只看不写），再勾选要导入的；顶部明说这次用的端点与密钥来源。 */
    var fetched = [];
    U.modal({
      title: '拉取并导入模型',
      wide: true,
      body: '<div class="card-sub" style="margin-bottom:10px">' +
        '<b>第一步「拉取列表」只看不写</b>；第二步勾选要进目录的，点「只导入勾选的」。' +
        '已存在条目<b>不会被改写</b>（备注、地址、密钥都保留）；同名但地址不同的模型会<b>另存一条</b>。</div>' +
        '<div class="field"><span class="field-label">服务商预设</span>' +
        '<div class="mdl-chips" id="mdl-i-presets">' + presetChips() + '</div></div>' +
        '<div class="field"><span class="field-label">API 地址（留空 = 用当前生效的地址）</span>' +
        '<input class="input mono" id="mdl-i-base" value=""></div>' +
        '<div class="field"><span class="field-label">API Key（留空 = 用全局密钥）</span>' +
        '<input class="input mono" id="mdl-i-key" type="password" value="" placeholder="另一家服务商就填这里"></div>' +
        '<div class="field"><span class="field-label">provider（留空 = 用当前）</span>' +
        '<input class="input mono" id="mdl-i-provider" value=""></div>' +
        '<div class="mdl-chips" style="align-items:center;margin-bottom:6px">' +
        '<button class="btn btn-soft btn-sm" id="mdl-i-fetch" type="button">' + U.icon('refresh', 'sm') + '拉取列表（只看不写）</button>' +
        '<input class="input mono" id="mdl-i-q" style="max-width:220px" placeholder="过滤：如 deepseek / qwen">' +
        '<button class="btn btn-ghost btn-sm" id="mdl-i-all" type="button">全选</button>' +
        '<button class="btn btn-ghost btn-sm" id="mdl-i-none" type="button">清空</button>' +
        '</div>' +
        '<div class="hint" id="mdl-i-info">还没拉取。留空地址会拿「当前生效」的端点与密钥去拉。</div>' +
        '<div id="mdl-i-list" class="mdl-pick-list"></div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        {
          label: '只导入勾选的', cls: 'btn-primary',
          onClick: function (body) {
            var picked = U.$$('#mdl-i-list input[type=checkbox]').filter(function (c) { return c.checked; })
              .map(function (c) { return c.getAttribute('data-model'); });
            var payload = {
              provider: body.querySelector('#mdl-i-provider').value.trim(),
              base_url: body.querySelector('#mdl-i-base').value.trim()
            };
            var key = body.querySelector('#mdl-i-key').value.trim();
            if (key) payload.api_key = key;
            if (!picked.length) { U.toast('warn', '先拉取列表并勾选至少一个模型'); return false; }
            payload.only = picked;
            apiModels().import(payload).then(function (r) {
              var res = (r && r.result) || {};
              var msg = '导入 ' + picked.length + ' 个：新增 ' + ((res.added || []).length) +
                '，已存在保留 ' + (res.refreshed || []).length +
                (res.kept_notes ? '（其中 ' + res.kept_notes + ' 条备注原样保留）' : '');
              if ((res.conflicts || []).length) {
                msg += '；' + res.conflicts.length + ' 个同名不同地址 → 已另存新条目';
              }
              U.toast('success', msg);
              return load();
            }).catch(function (e) { U.toast('error', e.message); return false; });
          }
        }
      ]
    });
    var presets = U.$('#mdl-i-presets');
    if (presets) {
      U.$$('[data-preset]', presets).forEach(function (b) {
        b.addEventListener('click', function () {
          var prov = U.$('#mdl-i-provider');
          var base = U.$('#mdl-i-base');
          if (prov) prov.value = b.getAttribute('data-preset') || '';
          if (base) base.value = b.getAttribute('data-url') || '';
          U.$$('[data-preset]', presets).forEach(function (x) { x.classList.remove('chip-on'); });
          b.classList.add('chip-on');
        });
      });
    }

    function renderPick() {
      var host = U.$('#mdl-i-list');
      if (!host) return;
      var q = (U.$('#mdl-i-q').value || '').trim().toLowerCase();
      var rows = fetched.filter(function (d) { return !q || d.id.toLowerCase().indexOf(q) >= 0; });
      if (!rows.length) { host.innerHTML = ''; return; }
      host.innerHTML = rows.map(function (d) {
        return '<label class="mdl-pick"><input type="checkbox" data-model="' + U.esc(d.id) + '">' +
          '<span class="mono">' + U.esc(d.id) + '</span>' +
          (Number(d.context_window) > 0
            ? '<span class="badge badge-grey">ctx ' + U.esc(String(d.context_window)) + '</span>' : '') +
          '</label>';
      }).join('');
    }

    var fetchBtn = U.$('#mdl-i-fetch');
    if (fetchBtn) {
      fetchBtn.addEventListener('click', function () {
        var info = U.$('#mdl-i-info');
        var base = U.$('#mdl-i-base').value.trim();
        var provider = U.$('#mdl-i-provider').value.trim();
        var key = U.$('#mdl-i-key').value.trim();
        if (info) info.textContent = '拉取中…（用的端点：' + (base || '当前生效的地址') + '）';
        /* 只看不写：走 /settings/models，不落库 */
        A.settings.models(provider || undefined, base || undefined, key || undefined)
          .then(function (r) {
            if (r && r.error) {
              if (info) info.textContent = '拉取失败：' + r.error;
              U.toast('error', '拉取失败：' + r.error);
              return;
            }
            fetched = (r && r.details) || [];
            renderPick();
            var ctxCount = fetched.filter(function (d) { return Number(d.context_window) > 0; }).length;
            if (info) {
              info.textContent = '共 ' + fetched.length + ' 个模型' +
                (ctxCount ? '（' + ctxCount + ' 个带上下文长度，导入时自动填入）' : '（这家没报上下文长度）') +
                ' · 校验通过：端点 ' + (r.used_endpoint || base || '当前生效') +
                ' · 密钥来源：' + (key ? '本次填写的' : '全局密钥');
            }
            if (!fetched.length) U.toast('warn', '没拉到模型：检查地址与密钥（本地部署还要确认同网段）');
          })
          .catch(function (e) { if (info) info.textContent = '拉取失败：' + e.message; });
      });
    }
    ['#mdl-i-q'].forEach(function (sel) {
      var q = U.$(sel);
      if (q) q.addEventListener('input', renderPick);
    });
    var all = U.$('#mdl-i-all');
    if (all) all.addEventListener('click', function () {
      U.$$('#mdl-i-list input[type=checkbox]').forEach(function (c) { c.checked = true; });
    });
    var none = U.$('#mdl-i-none');
    if (none) none.addEventListener('click', function () {
      U.$$('#mdl-i-list input[type=checkbox]').forEach(function (c) { c.checked = false; });
    });
  }

  function ensureBound() {
    if (state.bound) return;
    state.bound = true;
    var imp = U.$('#mdl-import'); if (imp) imp.addEventListener('click', importModels);
    var add = U.$('#mdl-add'); if (add) add.addEventListener('click', function () { editModel(null); });
    var rf = U.$('#mdl-refresh'); if (rf) rf.addEventListener('click', load);
    var kw = U.$('#mdl-search');
    if (kw) kw.addEventListener('input', U.debounce(function () {
      state.keyword = kw.value; render();
    }, 120));
    var only = U.$('#mdl-only-enabled');
    if (only) only.addEventListener('change', function () {
      state.onlyEnabled = only.checked; load();
    });
  }

  global.DogBuddyViews.Models = { load: load, state: state, editModel: editModel };
})(window);

/* ==== js/views-params.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-params.js
   工位参数面板（T1.1）：工位级参数覆盖（继承中 / 已覆盖）
   + 上下文用量条（ratio 填充 · 超阈值变橙 · 超 0.95 变红 · 30s 刷新）
   后端契约：
     GET  /api/windows/{id}         → params / effective_params / param_sources
     PATCH /api/windows/{id}        → {"params": {...}} 整体替换（{} 清空全部覆盖）
     GET  /api/windows/{id}/usage   → {context_tokens, max_context_tokens, ratio, threshold}
   ============================================================ */
/* global DogBuddyViews */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  /**
   * 单个参数行的描述（渲染 + 校验共用一份定义）。
   * @typedef {Object} ParamField
   * @property {string} key      覆盖键（支持 "retry.max_retries" 这类嵌套路径）
   * @property {string} label    中文标签
   * @property {string} unit     单位后缀（无单位给 ''）
   * @property {'int'|'float'} type 值类型
   * @property {number} min      允许最小值
   * @property {number} max      允许最大值
   * @property {number} step     输入框步进
   * @property {string} hint     一句话说明
   */

  /** @type {ParamField[]} 6 组参数（重试组含 max_retries / base_interval 两个键） */
  var FIELDS = [
    { key: 'max_context_tokens', label: '上下文上限', unit: 'tokens', type: 'int',
      min: 2000, max: 10000000, step: 1000,
      hint: '该工位上下文窗口的最大 token 数，超过即触发压缩' },
    { key: 'compact_threshold', label: '压缩阈值', unit: '', type: 'float',
      min: 0.3, max: 0.95, step: 0.05,
      hint: '上下文用量达到该比例时自动压缩（0.30 ~ 0.95）' },
    { key: 'temperature', label: '温度', unit: '', type: 'float',
      min: 0, max: 2, step: 0.1,
      hint: '采样温度，越高越发散（0 ~ 2）' },
    { key: 'request_timeout', label: '请求超时', unit: '秒', type: 'float',
      min: 5, max: 3600, step: 5,
      hint: '单次 LLM 请求的超时时间' },
    { key: 'max_iterations', label: '迭代上限', unit: '轮', type: 'int',
      min: 1, max: 200, step: 1,
      hint: '单次任务最多工具迭代轮数（1 ~ 200）' },
    { key: 'task_timeout', label: '单轮等待上限', unit: '秒', type: 'float',
      min: 30, max: 86400, step: 30,
      hint: 'cron 触发后等待单轮结束的上限（默认 1800）；超时不算失败，任务继续在后台跑' },
    { key: 'retry.max_retries', label: '重试次数', unit: '次', type: 'int',
      min: 0, max: 50, step: 1,
      hint: '限速 / 失败后的最大重试次数（0 ~ 50）' },
    { key: 'retry.base_interval', label: '重试基础间隔', unit: '秒', type: 'float',
      min: 0.1, max: 3600, step: 0.5,
      hint: '首次重试等待秒数（指数退避基数）' }
  ];

  /* ---------------- 嵌套覆盖键读写（"retry.max_retries" ↔ params.retry.max_retries） ---------------- */

  /**
   * 读取窗口覆盖字典里某个键的值（嵌套路径）。
   * @param {Object} params 窗口覆盖字典（window.params）
   * @param {string} key 形如 "max_context_tokens" 或 "retry.max_retries"
   * @returns {*} 覆盖值；未覆盖返回 undefined
   */
  function getOverride(params, key) {
    var parts = key.split('.');
    var cur = params || {};
    for (var i = 0; i < parts.length; i++) {
      if (cur === null || typeof cur !== 'object' || !(parts[i] in cur)) return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  }

  /**
   * 设置窗口覆盖字典里某个键的值（嵌套路径，按需创建中间对象）。
   * @param {Object} params 窗口覆盖字典（会被原地修改）
   * @param {string} key 形如 "retry.max_retries"
   * @param {*} value 覆盖值
   * @returns {void}
   */
  function setOverride(params, key, value) {
    var parts = key.split('.');
    var cur = params;
    for (var i = 0; i < parts.length - 1; i++) {
      if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) cur[parts[i]] = {};
      cur = cur[parts[i]];
    }
    cur[parts[parts.length - 1]] = value;
  }

  /**
   * 删除窗口覆盖字典里某个键（回落继承），并顺手清掉空掉的 retry 子对象。
   * @param {Object} params 窗口覆盖字典（会被原地修改）
   * @param {string} key 形如 "retry.base_interval"
   * @returns {void}
   */
  function delOverride(params, key) {
    var parts = key.split('.');
    var cur = params;
    for (var i = 0; i < parts.length - 1; i++) {
      if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) return;
      cur = cur[parts[i]];
    }
    delete cur[parts[parts.length - 1]];
    if (parts.length > 1 && Object.keys(cur).length === 0) delete params[parts[0]];
  }

  /**
   * 判定某行当前是「已覆盖」还是「继承中」。
   * 顶层键直接看 param_sources；retry 组 param_sources 只到 "retry" 一级，
   * 子键按 window.params.retry 里是否存在该键精确判定（只盖 max_retries 时
   * base_interval 仍是继承）。
   * @param {Object} w 窗口 DTO（含 params / param_sources）
   * @param {ParamField} field 参数行定义
   * @returns {'window'|'inherited'} "window"=已覆盖 / "inherited"=继承中
   */
  function sourceOf(w, field) {
    var src = (w.param_sources || {})[field.key];
    if (src === 'window' || src === 'inherited') return src;
    /* retry 子键：param_sources 只到 "retry" 一级，按 window.params.retry 里
       是否存在该键精确判定（只盖 max_retries 时 base_interval 仍是继承） */
    var val = getOverride(w.params, field.key);
    return (val !== undefined && val !== null) ? 'window' : 'inherited';
  }

  /**
   * 千分位格式化整数（45,230 这种）。
   * @param {number|string} n 数值
   * @returns {string} 格式化后的字符串；非数值返回 '—'
   */
  function fmtInt(n) {
    var v = Number(n);
    if (!isFinite(v)) return '—';
    return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  /**
   * 「继承中 / 已覆盖」徽标 HTML。
   * @param {'window'|'inherited'} src 参数来源
   * @returns {string} 徽标 HTML
   */
  function sourceBadge(src) {
    return src === 'window'
      ? '<span class="badge badge-blue param-badge">已覆盖</span>'
      : '<span class="badge badge-grey param-badge">继承中</span>';
  }

  /**
   * 单行参数的输入区 HTML（继承中：空输入框 + effective 值灰色占位）。
   * @param {Object} w 窗口 DTO
   * @param {ParamField} field 参数行定义
   * @returns {string} 行 HTML
   */
  function rowHTML(w, field) {
    var src = sourceOf(w, field);
    var override = getOverride(w.params, field.key);
    var effective = getOverride(w.effective_params, field.key);
    var placeholder = '继承 ' + (effective === undefined || effective === null
      ? '—' : String(effective) + (field.unit ? ' ' + field.unit : ''));
    return '<div class="param-row' + (field.key.indexOf('retry.') === 0 ? ' retry-sub' : '') + '"' +
      ' data-field-row="' + U.esc(field.key) + '">' +
      '<div class="param-row-label">' +
      '<span class="param-name">' + U.esc(field.label) + '</span>' +
      '<span class="hint">' + U.esc(field.hint) + '</span>' +
      '</div>' +
      '<div class="param-row-input">' +
      '<input class="input param-input" type="number"' +
      ' data-field="' + U.esc(field.key) + '"' +
      ' step="' + field.step + '" min="' + field.min + '" max="' + field.max + '"' +
      ' value="' + (override === undefined || override === null ? '' : U.esc(override)) + '"' +
      ' placeholder="' + U.esc(placeholder) + '">' +
      (field.unit ? '<span class="param-unit">' + U.esc(field.unit) + '</span>' : '') +
      '</div>' +
      '<div class="param-row-side">' + sourceBadge(src) +
      (src === 'window'
        ? '<button type="button" class="btn btn-ghost btn-sm param-reset" data-reset="' +
          U.esc(field.key) + '" title="删掉本行覆盖，回落继承值">恢复继承</button>'
        : '') +
      '</div>' +
      '</div>';
  }

  /**
   * 参数面板主体 HTML。
   * @param {Object} w 窗口 DTO
   * @returns {string} 面板 HTML
   */
  function panelHTML(w) {
    var rows = FIELDS.map(function (f) {
      var head = f.key === 'retry.max_retries'
        ? '<div class="param-group-title">重试（retry）</div>' : '';
      return head + rowHTML(w, f);
    }).join('');
    return '<p class="hint param-tip">只把被你改过的键存为工位覆盖，没动过的键保持继承；' +
      '「恢复继承」或清空输入框 = 删掉该键覆盖。保存采用整体替换语义。</p>' +
      '<div class="param-panel">' + rows + '</div>';
  }

  /**
   * 校验一行输入值。
   * @param {ParamField} field 参数行定义
   * @param {string} raw 输入框原始文本
   * @returns {{ok: boolean, value?: number, msg?: string}} 校验结果
   */
  function validateField(field, raw) {
    var n = field.type === 'int' ? Number(raw) : parseFloat(raw);
    if (raw === '' || !isFinite(n)) return { ok: false, msg: field.label + '：请输入数字' };
    if (field.type === 'int' && Math.floor(n) !== n) return { ok: false, msg: field.label + '：必须是整数' };
    if (n < field.min || n > field.max) {
      return { ok: false, msg: field.label + '：取值需在 ' + field.min + ' ~ ' + field.max + ' 之间' };
    }
    return { ok: true, value: n };
  }

  /**
   * 汇总面板输入 → PATCH 用的 params 整体替换字典。
   * 语义：原覆盖未动 → 保留；本次改动 → 覆盖；输入框清空 / 点「恢复继承」→ 删除该键回落继承。
   * @param {Object} w 窗口 DTO（打开面板时的快照）
   * @param {HTMLElement} body 面板 DOM
   * @returns {{ok: boolean, params?: Object, msg?: string}} 汇总结果
   */
  function collectParams(w, body) {
    var params = JSON.parse(JSON.stringify(w.params || {}));
    for (var i = 0; i < FIELDS.length; i++) {
      var field = FIELDS[i];
      var input = /** @type {HTMLInputElement|null} */ (
        body.querySelector('[data-field="' + field.key + '"]'));
      if (!input) continue;
      var raw = String(input.value || '').trim();
      var original = getOverride(params, field.key);
      if (raw === '') {
        if (original !== undefined) delOverride(params, field.key);
        continue;
      }
      var r = validateField(field, raw);
      if (!r.ok) return { ok: false, msg: r.msg };
      if (r.value !== original) setOverride(params, field.key, r.value);
    }
    return { ok: true, params: params };
  }

  /* ---------------- 上下文用量条 ---------------- */
  var usageTimer = null;
  var usageWinId = null;

  /**
   * 用量条 HTML —— 实现已抽到 `ui.js`（`U.usageHTML`），供对话视图共用；
   * 这里保留本地包装是为了兼容旧调用点与老 bundle。
   * @param {Object} u GET /api/windows/{id}/usage 的响应
   * @returns {string} 用量条 HTML
   */
  function usageHTML(u) {
    if (typeof U.usageHTML === 'function') return U.usageHTML(u);
    var ratio = Math.max(0, Math.min(1, Number(u.ratio) || 0));
    var level = ratio > 0.95 ? 'crit'
      : (Number(u.threshold) > 0 && ratio > Number(u.threshold) ? 'warn' : '');
    var pct = Math.round(ratio * 100);
    return '<div class="ctx-usage-top">' +
      '<span class="ctx-usage-label">' + U.icon('activity', 'sm') + '上下文用量</span>' +
      '<span class="ctx-usage-text num">' + fmtInt(u.context_tokens) + ' / ' +
      fmtInt(u.max_context_tokens) + ' tokens</span>' +
      '</div>' +
      '<div class="ctx-usage-track"><div class="ctx-usage-fill ' + level +
      '" style="width:' + pct + '%"></div></div>' +
      '<div class="ctx-usage-foot"><span>' + pct + '%</span>' +
      '<span class="hint">压缩阈值 ' + Math.round((Number(u.threshold) || 0) * 100) + '%</span></div>';
  }

  /**
   * 拉取一次用量并就地刷新用量条（DOM 不存在时静默跳过）。
   * @returns {Promise<void>}
   */
  function refreshUsage() {
    if (!usageWinId) return Promise.resolve();
    var id = usageWinId;
    return A.windows.usage(id).then(function (u) {
      var host = document.getElementById('win-usage');
      if (!host || host.getAttribute('data-win-id') !== id) return;
      host.innerHTML = usageHTML(u || {});
    }).catch(function () { /* 用量条失败不影响主界面 */ });
  }

  var Params = {
    /**
     * 在窗口详情里挂载上下文用量条，并启动 30s 定时刷新。
     * @param {string} windowId 工位 id
     * @returns {void}
     */
    mountUsage: function (windowId) {
      var host = document.getElementById('win-usage');
      if (!host) return;
      usageWinId = windowId;
      host.setAttribute('data-win-id', windowId);
      host.innerHTML = '<div class="ctx-usage-top"><span class="ctx-usage-label">' +
        U.icon('activity', 'sm') + '上下文用量</span><span class="hint">加载中…</span></div>';
      refreshUsage();
      if (!usageTimer) usageTimer = setInterval(refreshUsage, 30000);
    },

    /**
     * 打开工位参数编辑面板（弹窗）。
     * @param {string} windowId 工位 id
     * @returns {Promise<void>}
     */
    openPanel: function (windowId) {
      return A.windows.get(windowId).then(function (w) {
        var body = U.el('<div></div>');
        body.innerHTML = panelHTML(w);

        /* 行内「恢复继承」：直接把该行输入清空（保存时按删除覆盖处理） */
        U.$$('.param-reset', body).forEach(function (btn) {
          btn.addEventListener('click', function () {
            var key = btn.getAttribute('data-reset');
            var input = /** @type {HTMLInputElement|null} */ (
              body.querySelector('[data-field="' + key + '"]'));
            if (input) input.value = '';
            btn.parentNode.removeChild(btn);
            U.toast('info', '已标记恢复继承，保存后生效');
          });
        });

        U.modal({
          title: '工位参数 · ' + w.name,
          wide: true,
          body: body,
          buttons: [
            {
              label: '全部恢复继承', cls: 'btn-ghost', keepOpen: true,
              onClick: function () {
                U.confirm({
                  title: '清空全部覆盖', danger: true, okLabel: '清空',
                  text: '将删掉该工位的全部参数覆盖（整体传 {}），全部回落继承值。确定继续？'
                }).then(function (ok) {
                  if (!ok) return;
                  A.windows.update(windowId, { params: {} }).then(function () {
                    U.toast('success', '已全部恢复继承');
                    U.closeTopModal();
                    Params.reloadDetail(windowId);
                  }).catch(function (e) { U.toast('error', e.message); });
                });
              }
            },
            { label: '取消', cls: 'btn-ghost' },
            {
              label: '保存', cls: 'btn-primary',
              onClick: function (bodyEl) {
                var r = collectParams(w, bodyEl);
                if (!r.ok) {
                  U.toast('error', r.msg || '参数校验失败');
                  return false; /* 校验不过不关弹窗 */
                }
                A.windows.update(windowId, { params: r.params }).then(function () {
                  U.toast('success', '工位参数已保存');
                  Params.reloadDetail(windowId);
                }).catch(function (e) { U.toast('error', '保存失败：' + e.message); });
                return true;
              }
            }
          ]
        });
      }).catch(function (e) {
        U.toast('error', '参数面板打开失败：' + e.message);
      });
    },

    /**
     * 保存 / 清空后刷新窗口详情（复用 views-main 的选中重绘）。
     * @param {string} windowId 工位 id
     * @returns {void}
     */
    reloadDetail: function (windowId) {
      var wins = global.DogBuddyViews && global.DogBuddyViews.Windows;
      if (wins && typeof wins.select === 'function') wins.select(windowId);
    }
  };

  /* 挂进共享命名空间（可变共享状态，文件头已用 global 指令声明） */
  DogBuddyViews.Params = Params;
})(window);

/* ==== js/views-decisions.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-decisions.js
   待决事项（股东模式核心，T2.3）：员工向老板提问的收件箱
   —— 决定，而不是盯着看。待决卡片实时插入，已回答/超时进「历史」折叠区。
   后端契约：
     GET  /api/decisions?status=&limit=  → {decisions:[{id,window_id,question,options,
           answer,status,source,created_at,answered_at,expires_at}], pending_count}
     POST /api/decisions/{id}/answer {"answer"} / {id}/cancel / decisions/sweep
     SSE  decision.created / decision.answered / decision.timeout
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var STATUS_BADGE = {
    pending: '<span class="badge badge-red">待决</span>',
    answered: '<span class="badge badge-green">已回答</span>',
    timeout: '<span class="badge badge-amber">已超时</span>',
    cancelled: '<span class="badge badge-grey">已取消</span>'
  };

  var Decisions = {
    /** @type {Array<Object>} 待决列表（status=pending） */
    pending: [],
    /** @type {Array<Object>} 历史（answered / timeout / cancelled） */
    history: [],
    /** @type {Object.<string,string>} 工位 id → 名称 */
    names: {},
    loaded: false,

    /* ---------------- 工位名解析 ---------------- */

    /**
     * 工位显示名：能查到用名称，查不到原样显示 window_id。
     * @param {string} wid 工位 id
     * @returns {string} 工位显示名
     */
    winName: function (wid) {
      return Decisions.names[wid] || wid || '—';
    },

    /**
     * 拉取工位名映射（失败静默，回退显示 window_id）。
     * @returns {Promise<void>} 完成即 resolve
     */
    loadNames: function () {
      return A.windows.list().then(function (rows) {
        (rows || []).forEach(function (w) {
          if (w && w.id) Decisions.names[w.id] = w.name || w.id;
        });
      }).catch(function () { /* 名称映射失败不影响主流程 */ });
    },

    /* ---------------- 数据 ---------------- */

    /**
     * 加载待决 + 历史，并刷新红点。
     * @returns {Promise<void>} 渲染完成
     */
    load: function () {
      var host = document.getElementById('dec-list');
      if (host && !Decisions.loaded) host.innerHTML = U.skeleton();
      return Promise.all([
        A.decisions.list('pending', 100),
        A.decisions.list('', 100),
        Decisions.loadNames()
      ]).then(function (res) {
        var pendingRes = (res[0] && res[0].decisions) || [];
        var all = (res[1] && res[1].decisions) || [];
        Decisions.pending = pendingRes;
        Decisions.history = all.filter(function (d) {
          return String(d.status || '') !== 'pending';
        });
        Decisions.loaded = true;
        Decisions.updateBadge((res[0] && res[0].pending_count) || pendingRes.length);
        Decisions.render();
      }).catch(function (e) {
        if (host) {
          host.innerHTML = '<div class="empty"><div class="empty-title">加载失败</div>' +
            '<div class="empty-desc">' + U.esc(e && e.message ? e.message : e) + '</div></div>';
        }
      });
    },

    /* ---------------- 红点 ---------------- */

    /**
     * 更新侧边栏「待决」导航红点（0 隐藏）。
     * @param {number} n 待决数量
     * @returns {void}
     */
    updateBadge: function (n) {
      var dot = document.getElementById('nav-decisions-badge');
      if (!dot) return;
      var count = Number(n) || 0;
      dot.textContent = count > 99 ? '99+' : String(count);
      dot.classList.toggle('hidden', count <= 0);
    },

    /* ---------------- 渲染 ---------------- */

    /**
     * 单张待决卡片 HTML。按 source 分两种：
     * source=confirm →「⚠ 高危操作待审批」卡（命令等宽显示，批准执行/拒绝 二选一）；
     * 其余（ask_user 等）→ 普通提问卡（问题 / 选项按钮 / 回答框）。
     * @param {Object} d 待决 DTO
     * @returns {string} 卡片 HTML
     */
    card: function (d) {
      var head = '<div class="dec-head">' +
        (STATUS_BADGE[d.status] || STATUS_BADGE.pending) +
        '<span class="dec-from">来自 <b>' + U.esc(Decisions.winName(d.window_id)) + '</b></span>' +
        '<span class="dec-time">提出 ' + U.esc(U.relTime(d.created_at)) +
        (d.expires_at ? ' · 超时 ' + U.esc(U.countdown(d.expires_at)) : ' · 不超时') + '</span>' +
        '</div>';
      if (String(d.source || '') === 'confirm') {
        /* 高危操作待审批（source=confirm）：命令等宽显示，批准/拒绝二选一 */
        return '<div class="dec-card dec-card-danger" data-dec-id="' + U.esc(d.id) + '">' +
          head.replace(STATUS_BADGE[d.status] || STATUS_BADGE.pending,
            '<span class="badge badge-red">⚠ 高危操作待审批</span>') +
          '<div class="dec-cmd-label">待执行命令</div>' +
          '<pre class="dec-cmd">' + U.esc(d.question || '（空命令）') + '</pre>' +
          '<div class="dec-answer-row">' +
          '<button class="btn btn-danger btn-sm dec-approve">' + U.icon('check', 'sm') + '批准执行</button>' +
          '<button class="btn btn-ghost btn-sm dec-reject">' + U.icon('x', 'sm') + '拒绝</button>' +
          '<span class="hint">批准 = 放行执行；拒绝 = 告诉员工不要做。</span>' +
          '</div></div>';
      }
      var options = Array.isArray(d.options) ? d.options : [];
      return head +
        '<div class="dec-question">' + U.esc(d.question || '（空问题）') + '</div>' +
        (options.length
          ? '<div class="chips dec-options">' + options.map(function (o) {
              return '<button class="chip dec-opt" data-opt="' + U.esc(o) + '">' + U.esc(o) + '</button>';
            }).join('') + '</div>'
          : '') +
        '<div class="dec-answer-row">' +
        '<input class="input dec-input" placeholder="输入回答…（可点上方选项填入）" autocomplete="off">' +
        '<button class="btn btn-primary btn-sm dec-answer">' + U.icon('check', 'sm') + '回答</button>' +
        '<button class="btn btn-ghost btn-sm dec-cancel" title="跳过该待决事项">跳过（取消）</button>' +
        '</div></div>';
    },

    /**
     * 单条历史条目 HTML（已回答/超时/取消，只读）。
     * @param {Object} d 决策 DTO
     * @returns {string} 条目 HTML
     */
    historyItem: function (d) {
      return '<div class="dec-hist-item">' +
        '<div class="dec-hist-head">' +
        (String(d.source || '') === 'confirm'
          ? '<span class="badge badge-red">⚠ 高危</span>' : '') +
        (STATUS_BADGE[d.status] || '') +
        '<span class="dec-from">来自 <b>' + U.esc(Decisions.winName(d.window_id)) + '</b></span>' +
        '<span class="dec-time">' + U.esc(U.relTime(d.answered_at || d.created_at)) + '</span>' +
        '</div>' +
        '<div class="dec-hist-q">' + U.esc(d.question || '') + '</div>' +
        (d.answer
          ? '<div class="dec-hist-a">回答：' + U.esc(d.answer) + '</div>'
          : '<div class="dec-hist-a muted">（无回答）</div>') +
        '</div>';
    },

    /**
     * 渲染整页：待决卡片列表 + 历史折叠区 + 空态。
     * @returns {void}
     */
    render: function () {
      var host = document.getElementById('dec-list');
      if (!host) return;
      var pend = Decisions.pending;
      var hist = Decisions.history;

      if (!pend.length) {
        host.innerHTML = U.empty({
          art: 'chat',
          title: '没有待决事项——员工们正在自主推进。',
          desc: '员工遇到需要你拍板的问题时，会实时出现在这里（含建议选项，可一键采纳）。'
        });
      } else {
        host.innerHTML = pend.map(Decisions.card).join('');
      }

      var histHost = document.getElementById('dec-history');
      if (histHost) {
        histHost.innerHTML = '<details class="dec-hist"' + (pend.length ? '' : ' open') + '>' +
          '<summary>' + U.icon('chevron-r', 'ic-chevron') + '历史（' + hist.length + '）</summary>' +
          (hist.length
            ? '<div class="dec-hist-list">' + hist.map(Decisions.historyItem).join('') + '</div>'
            : '<p class="muted small dec-hist-empty">暂无历史记录。</p>') +
          '</details>';
      }

      var meta = document.getElementById('dec-meta');
      if (meta) {
        meta.textContent = '待决 ' + pend.length + ' · 历史 ' + hist.length;
      }

      Decisions.bind();
    },

    /* ---------------- 交互（每次 render 后重绑，幂等） ---------------- */

    /**
     * 绑定卡片内交互：选项按钮填入 / 回答 / 取消。
     * @returns {void}
     */
    bind: function () {
      U.$$('.dec-card').forEach(function (card) {
        var id = Number(card.getAttribute('data-dec-id'));
        var input = U.$('.dec-input', card);
        U.$$('.dec-opt', card).forEach(function (b) {
          b.addEventListener('click', function () {
            if (input) {
              input.value = b.getAttribute('data-opt') || '';
              input.focus();
            }
          });
        });
        var answerBtn = U.$('.dec-answer', card);
        if (answerBtn) {
          answerBtn.addEventListener('click', function () {
            Decisions.answer(id, (input && input.value || '').trim(), answerBtn);
          });
        }
        if (input) {
          input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
              e.preventDefault();
              Decisions.answer(id, input.value.trim(), answerBtn);
            }
          });
        }
        var cancelBtn = U.$('.dec-cancel', card);
        if (cancelBtn) {
          cancelBtn.addEventListener('click', function () {
            Decisions.cancel(id, cancelBtn);
          });
        }
        /* 高危审批卡（source=confirm）：批准执行 / 拒绝 → answer 文本固定 */
        var approveBtn = U.$('.dec-approve', card);
        if (approveBtn) {
          approveBtn.addEventListener('click', function () {
            Decisions.answer(id, '批准', approveBtn);
          });
        }
        var rejectBtn = U.$('.dec-reject', card);
        if (rejectBtn) {
          rejectBtn.addEventListener('click', function () {
            Decisions.answer(id, '拒绝', rejectBtn);
          });
        }
      });
    },

    /**
     * 回答一条待决（空回答拒绝；成功后刷新列表）。
     * @param {number} id 决策 id
     * @param {string} text 回答文本
     * @param {HTMLButtonElement|null} btn 触发按钮（置灰防重复提交）
     * @returns {void}
     */
    answer: function (id, text, btn) {
      if (!text) {
        U.toast('warn', '请先输入回答（或点建议选项填入）');
        return;
      }
      if (btn) btn.disabled = true;
      A.decisions.answer(id, text).then(function () {
        U.toast('success', '已回答，员工继续推进');
        return Decisions.load();
      }).catch(function (e) {
        if (btn) btn.disabled = false;
        U.toast('error', '回答失败：' + (e && e.message ? e.message : e));
      });
    },

    /**
     * 跳过（取消）一条待决。
     * @param {number} id 决策 id
     * @param {HTMLButtonElement|null} btn 触发按钮
     * @returns {void}
     */
    cancel: function (id, btn) {
      if (btn) btn.disabled = true;
      A.decisions.cancel(id).then(function () {
        U.toast('info', '已取消该待决事项');
        return Decisions.load();
      }).catch(function (e) {
        if (btn) btn.disabled = false;
        U.toast('error', '取消失败：' + (e && e.message ? e.message : e));
      });
    },

    /* ---------------- SSE 钩子 ---------------- */

    /**
     * decision.created：红点 +1、提示条、待决卡片实时插入（视图已打开时）。
     * payload 至少含 {id, window_id, question}；source=confirm 走红色高危提示。
     * 缺字段时回退整页刷新。
     * @param {Object} payload 事件负载
     * @returns {void}
     */
    onCreated: function (payload) {
      payload = payload || {};
      var dot = document.getElementById('nav-decisions-badge');
      var cur = parseInt(String((dot && dot.textContent) || '0'), 10);
      if (!isFinite(cur)) cur = 0;
      Decisions.updateBadge(cur + 1);
      if (String(payload.source || '') === 'confirm') {
        /* 高危命令待审批：红色高亮提示（红点在导航「待决」上） */
        U.toast('error', '⚠ 高危操作待审批（来自 ' + U.esc(Decisions.winName(payload.window_id)) +
          '）——去「待决」批准或拒绝', 8000);
      } else {
        U.toast('warn', '新待决：' + String(payload.question || '员工有问题要问你') +
          '（来自 ' + U.esc(Decisions.winName(payload.window_id)) + '）', 6000);
      }

      var host = document.getElementById('dec-list');
      var view = document.getElementById('view-decisions');
      if (!host || !view || !view.classList.contains('active')) return;

      if (payload.id && payload.question) {
        var d = {
          id: payload.id,
          window_id: payload.window_id || '',
          question: payload.question,
          options: Array.isArray(payload.options) ? payload.options : [],
          status: 'pending',
          source: payload.source || '',
          created_at: payload.created_at || new Date().toISOString(),
          expires_at: payload.expires_at || ''
        };
        if (Decisions.pending.some(function (x) { return x.id === d.id; })) return;
        Decisions.pending.unshift(d);
        if (!Decisions.pending.length) Decisions.render();
        else {
          var emptyNode = U.$('.empty', host);
          if (emptyNode) Decisions.render();
          else host.insertAdjacentHTML('afterbegin', Decisions.card(d));
          Decisions.bind();
        }
      } else {
        Decisions.load(); /* payload 不全：整页刷新兜底 */
      }
    },

    /**
     * decision.answered / decision.timeout：待决移入历史、红点同步。
     * @param {Object} payload 事件负载
     * @returns {void}
     */
    onClosed: function (payload) {
      payload = payload || {};
      var dot = document.getElementById('nav-decisions-badge');
      var cur = parseInt(String((dot && dot.textContent) || '0'), 10);
      if (!isFinite(cur)) cur = 0;
      Decisions.updateBadge(Math.max(0, cur - 1));
      var view = document.getElementById('view-decisions');
      if (view && view.classList.contains('active')) Decisions.load();
    }
  };

  /* SSE 松耦合注册：事件名 → 视图钩子（sse.js 先于本文件加载） */
  if (global.SSE) {
    global.SSE.on('decision.created', function (p) { Decisions.onCreated(p); });
    global.SSE.on('decision.answered', function (p) { Decisions.onClosed(p); });
    global.SSE.on('decision.timeout', function (p) { Decisions.onClosed(p); });
  }

  global.DogBuddyViews.Decisions = Decisions;
})(window);

/* ==== js/views-groups.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-groups.js
   群组（共享工作频道，ROADMAP T3）：多个工位一起干活的协作空间
   —— 列表卡（成员 chips · 组长 👑）/ 成员管理 / 共享频道消息流 / 任务派发时间线
   后端契约（routes/groups.py）：
     GET    /api/groups                        → {groups:[GroupDTO]}
     POST   /api/groups {name,description,lead_window_id} → GroupDTO
     GET    /api/groups/{ref}                  → GroupDTO（ref=组 id 或组名）
     PATCH  /api/groups/{ref} {name?,description?,lead_window_id?}
     DELETE /api/groups/{ref}                  → {deleted:true}
     POST   /api/groups/{ref}/members {window,role} → GroupDTO
     DELETE /api/groups/{ref}/members/{window} → {removed:true}
     GET    /api/groups/{ref}/channel?limit=   → {messages:[ChannelMsg]}
     POST   /api/groups/{ref}/run {prompt}     → {group_id,group_name,steps[],summary}
   GroupDTO = {id,name,description,lead_window_id,created_at,updated_at,
               members:[{window_id,window_name,role:"lead|member",joined_at,exists}]}
   ChannelMsg = {id,window_id,window_name,role:"user|assistant|tool|system",content,created_at}
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var ROLE_BADGE = {
    lead: '<span class="badge badge-amber" title="组长：负责收口最终结论">👑 组长</span>',
    member: '<span class="badge badge-blue">成员</span>'
  };

  /**
   * 频道消息角色 → 徽标 HTML。
   * @param {string} role 消息角色（user|assistant|tool|system）
   * @returns {string} 徽标 HTML
   */
  function roleBadge(role) {
    if (role === 'user') return '<span class="badge badge-green">用户</span>';
    if (role === 'assistant') return '<span class="badge badge-blue">员工</span>';
    if (role === 'tool') return '<span class="badge badge-amber">工具</span>';
    return '<span class="badge badge-grey">系统</span>';
  }

  /**
   * 由窗口 id 稳定散列一个色相（头像色点：同一工位恒定同色）。
   * @param {string} id 窗口 id
   * @returns {number} 色相 0-359
   */
  function hueOf(id) {
    var s = String(id || ''), h = 0;
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return h;
  }

  /**
   * 头像色点 HTML（首字母 + 稳定色相）。
   * @param {string} name 窗口名
   * @param {string} id 窗口 id（取色）
   * @returns {string} 头像 HTML
   */
  function avatar(name, id) {
    var h = hueOf(id || name);
    var ch = String(name || '?').trim().charAt(0) || '?';
    return '<span class="grp-avatar" style="color:hsl(' + h + ',80%,72%);' +
      'background:hsla(' + h + ',70%,55%,0.16);border-color:hsla(' + h + ',70%,55%,0.35)">' +
      U.esc(ch) + '</span>';
  }

  var Groups = {
    /** @type {Array<Object>} 群组 DTO 列表 */
    list: [],
    /** @type {Array<Object>} 工位列表（下拉数据源，GET /api/windows） */
    windows: [],
    /** @type {string|null} 当前打开的群组 id */
    selectedId: null,
    /** @type {Object|null} 当前群组 DTO */
    current: null,
    /** @type {Array<Object>} 共享频道消息（最新在前） */
    channel: [],
    /** @type {Object|null} 最近一次任务派发结果 {group_id,group_name,steps,summary} */
    runResult: null,
    /** @type {number|null} 频道自动刷新句柄（30s） */
    channelTimer: null,
    /** @type {boolean} 频道拉取进行中（防重入） */
    channelBusy: false,

    /* ---------------- 数据加载 ---------------- */

    /**
     * 加载群组列表 + 工位下拉源；若已打开某群组则顺带刷新其频道。
     * @returns {Promise<void>} 渲染完成
     */
    load: function () {
      var host = document.getElementById('grp-list');
      if (host && !Groups.list.length) host.innerHTML = U.skeleton('cards');
      return Promise.all([A.groups.list(), A.windows.list()]).then(function (res) {
        Groups.list = (res[0] && res[0].groups) || [];
        Groups.windows = res[1] || [];
        Groups.renderList();
        if (Groups.selectedId) {
          Groups.renderDetail();
          return Groups.loadChannel();
        }
        var detail = document.getElementById('grp-detail');
        if (detail) detail.innerHTML = '';
        Groups.startAutoRefresh();
      }).catch(function (e) {
        if (host) {
          host.innerHTML = '<div class="empty"><div class="empty-title">群组加载失败</div>' +
            '<div class="empty-desc">' + U.esc(e && e.message ? e.message : e) + '</div></div>';
        }
      });
    },

    /**
     * 打开某群组详情：拉详情 + 频道。
     * @param {string} gid 群组 id
     * @returns {Promise<void>} 渲染完成
     */
    select: function (gid) {
      Groups.selectedId = gid;
      Groups.runResult = null;
      Groups.renderList();
      var detail = document.getElementById('grp-detail');
      if (detail) detail.innerHTML = U.skeleton();
      return Promise.all([A.groups.get(gid), A.groups.channel(gid, 100)]).then(function (res) {
        if (Groups.selectedId !== gid) return;
        Groups.current = res[0] || null;
        Groups.channel = (res[1] && res[1].messages) || [];
        Groups.renderDetail();
        Groups.startAutoRefresh();
      }).catch(function (e) {
        if (Groups.selectedId !== gid) return;
        if (detail) {
          detail.innerHTML = '<div class="empty"><div class="empty-title">打开群组失败</div>' +
            '<div class="empty-desc">' + U.esc(e && e.message ? e.message : e) + '</div></div>';
        }
      });
    },

    /**
     * 仅刷新共享频道（30s 自动刷新 / 手动刷新共用，静默失败）。
     * @returns {Promise<void>} 拉取完成
     */
    loadChannel: function () {
      if (!Groups.selectedId || Groups.channelBusy) return Promise.resolve();
      Groups.channelBusy = true;
      return A.groups.channel(Groups.selectedId, 100).then(function (r) {
        Groups.channel = (r && r.messages) || [];
        Groups.renderChannel();
      }).catch(function () { /* 频道刷新失败不打断页面 */ })
        .then(function () { Groups.channelBusy = false; });
    },

    /**
     * 启动 30s 自动刷新（幂等；仅在「群组」视图打开时真正发请求）。
     * @returns {void}
     */
    startAutoRefresh: function () {
      if (Groups.channelTimer) return;
      Groups.channelTimer = setInterval(function () {
        if (!Groups.selectedId) return;
        if (global.App && global.App.view !== 'groups') return;
        Groups.loadChannel();
      }, 30000);
    },

    /* ---------------- 渲染：列表 ---------------- */

    /**
     * 群组列表卡（组名 / 描述 / 成员 chips，组长 👑）；空态给引导。
     * @returns {void}
     */
    renderList: function () {
      var host = document.getElementById('grp-list');
      if (!host) return;
      if (!Groups.list.length) {
        host.innerHTML = U.empty({
          art: 'chat',
          title: '还没有群组',
          desc: '共享频道让几个员工一起干活——建个组，把工位拉进来，派一个任务试试。',
          action: '新建群组', actionId: 'new-group'
        });
        return;
      }
      host.innerHTML = '<div class="grp-grid">' + Groups.list.map(function (g) {
        var members = Array.isArray(g.members) ? g.members : [];
        return '<div class="card grp-card' + (g.id === Groups.selectedId ? ' active' : '') +
          '" data-grp-id="' + U.esc(g.id) + '">' +
          '<div class="grp-card-head">' +
          '<span class="grp-card-icon">' + U.icon('layers', 'sm') + '</span>' +
          '<div><div class="grp-card-name">' + U.esc(g.name) + '</div>' +
          '<div class="grp-card-desc">' + U.esc(g.description || '（无描述）') + '</div></div>' +
          '</div>' +
          '<div class="chips grp-member-chips">' + (members.length
            ? members.map(function (m) {
                return '<span class="chip' + (m.role === 'lead' ? ' chip-lead' : '') + '">' +
                  (m.role === 'lead' ? '👑 ' : '') + U.esc(m.window_name || m.window_id) +
                  (m.exists === false ? ' <em class="muted">已删除</em>' : '') + '</span>';
              }).join('')
            : '<span class="muted small">暂无成员</span>') + '</div>' +
          '<div class="grp-card-foot">' +
          '<span class="muted small">' + members.length + ' 名成员 · 更新 ' +
          U.esc(U.relTime(g.updated_at || g.created_at)) + '</span>' +
          '<button class="btn btn-soft btn-sm grp-open">打开</button>' +
          '</div></div>';
      }).join('') + '</div>';

      U.$$('.grp-card', host).forEach(function (card) {
        card.addEventListener('click', function () {
          Groups.select(card.getAttribute('data-grp-id'));
        });
      });
    },

    /* ---------------- 渲染：详情 ---------------- */

    /**
     * 群组详情：头部操作 + 成员管理 + 任务派发 + 共享频道。
     * @returns {void}
     */
    renderDetail: function () {
      var host = document.getElementById('grp-detail');
      var g = Groups.current;
      if (!host) return;
      if (!g) { host.innerHTML = ''; return; }

      host.innerHTML =
        '<div class="card grp-detail">' +
        '<div class="grp-detail-head">' +
        '<div><h2 class="card-title">' + U.esc(g.name) + '</h2>' +
        '<p class="card-sub">' + U.esc(g.description || '（无描述）') + '</p></div>' +
        '<div class="toolbar-actions">' +
        '<button class="btn btn-soft btn-sm" id="grp-edit">' + U.icon('edit', 'sm') + '编辑</button>' +
        '<button class="btn btn-soft btn-sm" id="grp-chan-refresh">' + U.icon('refresh', 'sm') + '刷新频道</button>' +
        '<button class="btn btn-danger btn-sm" id="grp-delete">' + U.icon('trash', 'sm') + '删除群组</button>' +
        '</div></div>' +
        '<div class="grp-cols">' +
        '<div class="card grp-side">' + Groups.memberHTML(g) + '</div>' +
        '<div class="grp-main">' + Groups.runHTML() + Groups.channelHTML() + '</div>' +
        '</div></div>';

      Groups.bindDetail();
      Groups.renderChannel();
      Groups.renderRun();
    },

    /**
     * 成员管理区 HTML（添加成员下拉 + 成员行：设组长 / 移除）。
     * @param {Object} g 群组 DTO
     * @returns {string} 成员区 HTML
     */
    memberHTML: function (g) {
      var members = Array.isArray(g.members) ? g.members : [];
      var inGroup = {};
      members.forEach(function (m) { inGroup[m.window_id] = true; });
      var options = Groups.windows.filter(function (w) { return !inGroup[w.id]; });
      return '<div class="grp-side-head">' + U.icon('user', 'sm') + '成员管理</div>' +
        '<div class="grp-add-row">' +
        '<select class="select select-sm" id="grp-member-sel"' + (options.length ? '' : ' disabled') + '>' +
        (options.length
          ? options.map(function (w) {
              return '<option value="' + U.esc(w.id) + '">' + U.esc(w.name) + '</option>';
            }).join('')
          : '<option value="">（全部工位都已在组内）</option>') +
        '</select>' +
        '<select class="select select-sm" id="grp-member-role">' +
        '<option value="member">成员</option><option value="lead">组长</option></select>' +
        '<button class="btn btn-primary btn-sm" id="grp-member-add"' +
        (options.length ? '' : ' disabled') + '>' + U.icon('plus', 'sm') + '添加</button>' +
        '</div>' +
        '<div class="grp-member-list">' + (members.length
          ? members.map(function (m) {
              return '<div class="grp-member-row" data-win-id="' + U.esc(m.window_id) + '">' +
                avatar(m.window_name || m.window_id, m.window_id) +
                '<div class="grp-member-info">' +
                '<div class="grp-member-name">' + U.esc(m.window_name || m.window_id) +
                (m.exists === false ? ' <span class="badge badge-grey">工位已删除</span>' : '') + '</div>' +
                '<div class="muted small">加入 ' + U.esc(U.relTime(m.joined_at)) + '</div></div>' +
                (ROLE_BADGE[m.role] || ROLE_BADGE.member) +
                '<div class="row-actions">' +
                (m.role === 'lead' ? '' :
                  '<button class="icon-btn" data-act="set-lead" title="设为组长">' + U.icon('arrow-up', 'sm') + '</button>') +
                (m.exists === false ? '' :
                  '<button class="icon-btn" data-act="chat" title="查看该成员的会话（他们在这里的发言）">' + U.icon('chat', 'sm') + '</button>') +
                '<button class="icon-btn danger" data-act="remove" title="移出群组">' + U.icon('trash', 'sm') + '</button>' +
                '</div></div>';
            }).join('')
          : '<p class="muted small" style="padding:8px 2px">暂无成员——从上方下拉把工位拉进来。</p>') +
        '</div>';
    },

    /**
     * 任务派发区 HTML（prompt 输入 + 派发按钮 + 结果容器）。
     * @returns {string} 派发区 HTML
     */
    runHTML: function () {
      return '<div class="card grp-run">' +
        '<div class="grp-side-head">' + U.icon('send', 'sm') + '任务派发</div>' +
        '<div class="grp-run-row">' +
        '<textarea class="textarea" id="grp-prompt" rows="2" placeholder="给这个小组派个活，例如：调研竞品定价并给出一份对比结论。"></textarea>' +
        '<button class="btn btn-primary" id="grp-run-btn">' + U.icon('play', 'sm') + '派发任务</button>' +
        '</div>' +
        '<div class="hint">同步等待小组逐个工位执行，可能耗时——派发中按钮会置灰，请耐心等结果。</div>' +
        '<div id="grp-run-result"></div>' +
        '</div>';
    },

    /**
     * 共享频道区 HTML（消息容器 + 头部刷新）。
     * @returns {string} 频道区 HTML
     */
    channelHTML: function () {
      return '<div class="card grp-chan">' +
        '<div class="grp-side-head">' + U.icon('chat', 'sm') + '共享频道' +
        '<span class="muted small grp-chan-note">每 30 秒自动刷新</span></div>' +
        '<div class="grp-chan-list" id="grp-chan-list"></div>' +
        '</div>';
    },

    /**
     * 渲染频道消息流（头像色点 + 工位名 + 角色徽标 + 时间 + 内容）。
     * @returns {void}
     */
    renderChannel: function () {
      var host = document.getElementById('grp-chan-list');
      if (!host) return;
      if (!Groups.channel.length) {
        host.innerHTML = '<p class="muted small grp-chan-empty">频道暂无消息——派个任务，员工们会在这里留下协作记录。</p>';
        return;
      }
      host.innerHTML = Groups.channel.map(function (m) {
        return '<div class="grp-msg grp-msg-' + U.esc(m.role || 'system') + '">' +
          avatar(m.window_name || m.window_id, m.window_id) +
          '<div class="grp-msg-body">' +
          '<div class="grp-msg-meta">' +
          '<span class="grp-msg-name">' + U.esc(m.window_name || m.window_id) + '</span>' +
          roleBadge(m.role) +
          '<span class="grp-msg-time">' + U.esc(U.relTime(m.created_at)) + '</span>' +
          '</div>' +
          '<div class="grp-msg-content">' + U.esc(m.content || '') + '</div>' +
          '</div></div>';
      }).join('');
    },

    /**
     * 渲染任务派发结果：steps 时间线（工位名 / 成功失败 / 摘要）+ 最终 summary 高亮卡。
     * @returns {void}
     */
    renderRun: function () {
      var host = document.getElementById('grp-run-result');
      var r = Groups.runResult;
      if (!host) return;
      if (!r) { host.innerHTML = ''; return; }
      var steps = Array.isArray(r.steps) ? r.steps : [];
      host.innerHTML =
        '<div class="grp-steps">' + (steps.length
          ? steps.map(function (s) {
              return '<div class="grp-step ' + (s.ok ? 'ok' : 'err') + '">' +
                '<span class="grp-step-dot">' + U.icon(s.ok ? 'check' : 'x', 'sm') + '</span>' +
                '<div class="grp-step-body">' +
                '<div class="grp-step-head"><b>' + U.esc(s.window_name || s.window_id) + '</b>' +
                (s.is_lead ? ' <span class="badge badge-amber">组长收口</span>' : '') +
                (s.ok ? ' <span class="badge badge-green">成功</span>' : ' <span class="badge badge-red">失败</span>') +
                '</div>' +
                '<div class="grp-step-sum">' + U.esc(s.summary || '（无摘要）') + '</div>' +
                '</div></div>';
            }).join('')
          : '<p class="muted small">本次派发没有产生步骤。</p>') + '</div>' +
        '<div class="grp-summary">' +
        '<div class="grp-summary-label">' + U.icon('check', 'sm') + '最终结论</div>' +
        '<div class="grp-summary-text">' + U.esc(r.summary || '（无总结）') + '</div>' +
        '</div>';
    },

    /* ---------------- 交互 ---------------- */

    /**
     * 详情区交互绑定：编辑 / 删除 / 刷新频道 / 成员操作 / 派发。
     * @returns {void}
     */
    bindDetail: function () {
      var g = Groups.current;
      if (!g) return;
      var edit = document.getElementById('grp-edit');
      if (edit) edit.addEventListener('click', function () { Groups.openEdit(); });
      var del = document.getElementById('grp-delete');
      if (del) {
        del.addEventListener('click', function () {
          U.confirm({
            title: '删除群组', danger: true, okLabel: '删除',
            text: '确定删除群组「' + g.name + '」？成员关联会一并解除（工位本身不受影响）。'
          }).then(function (ok) {
            if (!ok) return;
            A.groups.remove(g.id).then(function () {
              U.toast('success', '群组已删除');
              Groups.selectedId = null;
              Groups.current = null;
              Groups.load();
            }).catch(function (e) { U.toast('error', '删除失败：' + e.message); });
          });
        });
      }
      var refresh = document.getElementById('grp-chan-refresh');
      if (refresh) {
        refresh.addEventListener('click', function () {
          Groups.loadChannel().then(function () { U.toast('info', '频道已刷新'); });
        });
      }
      var add = document.getElementById('grp-member-add');
      if (add) {
        add.addEventListener('click', function () {
          var sel = document.getElementById('grp-member-sel');
          var roleSel = document.getElementById('grp-member-role');
          if (!sel || !sel.value) { U.toast('warn', '请选择要添加的工位'); return; }
          add.disabled = true;
          A.groups.addMember(g.id, sel.value, roleSel ? roleSel.value : 'member')
            .then(function (dto) {
              U.toast('success', '已加入群组');
              Groups.current = dto || Groups.current;
              return Groups.loadChannel();
            })
            .catch(function (e) { U.toast('error', '添加失败：' + e.message); })
            .then(function () {
              Groups.renderDetail();
              Groups.load();
            });
        });
      }
      U.$$('.grp-member-row').forEach(function (row) {
        var wid = row.getAttribute('data-win-id');
        var lead = U.$('[data-act="set-lead"]', row);
        if (lead) {
          lead.addEventListener('click', function () {
            lead.disabled = true;
            A.groups.update(g.id, { lead_window_id: wid }).then(function (dto) {
              U.toast('success', '已设为组长');
              Groups.current = dto || Groups.current;
              Groups.renderDetail();
              Groups.load();
            }).catch(function (e) {
              lead.disabled = false;
              U.toast('error', '设组长失败：' + e.message);
            });
          });
        }
        var rm = U.$('[data-act="remove"]', row);
        if (rm) {
          rm.addEventListener('click', function () {
            rm.disabled = true;
            A.groups.removeMember(g.id, wid).then(function () {
              U.toast('success', '已移出群组');
              Groups.renderDetail();
              Groups.load();
            }).catch(function (e) {
              rm.disabled = false;
              U.toast('error', '移除失败：' + e.message);
            });
          });
        }
        // v0.10.20：「他们的沟通」要看得见——成员行加会话入口，跳到该工位的
        // 会话流（圆桌测试时工人的发言只散在各窗口会话和工作区文件里）。
        var chat = U.$('[data-act="chat"]', row);
        if (chat) {
          chat.addEventListener('click', function () {
            if (global.App && global.App.go) global.App.go('windows', { win: wid });
          });
        }
      });
      var runBtn = document.getElementById('grp-run-btn');
      if (runBtn) {
        runBtn.addEventListener('click', function () {
          var input = document.getElementById('grp-prompt');
          var prompt = (input && input.value || '').trim();
          if (!prompt) { U.toast('warn', '先写下要派发的任务'); return; }
          Groups.dispatch(prompt, runBtn);
        });
      }
    },

    /**
     * 派发群组任务：按钮置 loading 直到同步 run 返回，展示 steps + summary。
     * @param {string} prompt 任务描述
     * @param {HTMLButtonElement} btn 派发按钮（置灰 + 文案「派发中…」）
     * @returns {void}
     */
    dispatch: function (prompt, btn) {
      var gid = Groups.selectedId;
      btn.disabled = true;
      btn.textContent = '派发中…（小组在干活）';
      var result = document.getElementById('grp-run-result');
      if (result) result.innerHTML = U.skeleton();
      A.groups.run(gid, prompt).then(function (r) {
        if (Groups.selectedId !== gid) return;
        Groups.runResult = r || null;
        Groups.renderRun();
        U.toast('success', '任务派发完成');
        Groups.loadChannel();
      }).catch(function (e) {
        if (result) {
          result.innerHTML = '<div class="empty"><div class="empty-title">派发失败</div>' +
            '<div class="empty-desc">' + U.esc(e && e.message ? e.message : e) + '</div></div>';
        }
        U.toast('error', '派发失败：' + (e && e.message ? e.message : e));
      }).then(function () {
        btn.disabled = false;
        btn.innerHTML = U.icon('play', 'sm') + '派发任务';
      });
    },

    /* ---------------- 新建 / 编辑 ---------------- */

    /**
     * 「新建群组」弹窗：名称 / 描述 / 下选组长（数据来自 GET /api/windows）。
     * @returns {void}
     */
    openCreate: function () {
      var winOpts = Groups.windows.map(function (w) {
        return '<option value="' + U.esc(w.id) + '">' + U.esc(w.name) + '</option>';
      }).join('');
      var m = U.modal({
        title: '新建群组',
        body:
          '<div class="field"><span class="field-label">群组名称 *</span>' +
          '<input class="input" id="grp-new-name" placeholder="例如：定价调研小组"></div>' +
          '<div class="field"><span class="field-label">描述</span>' +
          '<textarea class="textarea" id="grp-new-desc" rows="2" placeholder="这个小组负责什么？"></textarea></div>' +
          '<div class="field"><span class="field-label">组长（负责收口结论）</span>' +
          '<select class="select" id="grp-new-lead">' +
          '<option value="">（暂不指定）</option>' + winOpts + '</select>' +
          '<div class="hint">组长会汇总各成员产出，给出最终结论。</div></div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: '创建', cls: 'btn-primary', keepOpen: true,
            onClick: function (body, api) {
              var name = body.querySelector('#grp-new-name').value.trim();
              if (!name) { U.toast('warn', '请填写群组名称'); return false; }
              A.groups.create({
                name: name,
                description: body.querySelector('#grp-new-desc').value.trim(),
                lead_window_id: body.querySelector('#grp-new-lead').value || ''
              }).then(function (g) {
                api.close();
                U.toast('success', '群组已创建');
                Groups.selectedId = (g && g.id) || null;
                Groups.load();
              }).catch(function (e) { U.toast('error', '创建失败：' + e.message); });
            }
          }
        ]
      });
      return m;
    },

    /**
     * 「编辑群组」弹窗：改名称 / 描述 / 组长（PATCH /api/groups/{ref}）。
     * @returns {void}
     */
    openEdit: function () {
      var g = Groups.current;
      if (!g) return;
      var members = Array.isArray(g.members) ? g.members : [];
      var leadOpts = '<option value="">（暂不指定）</option>' + members.map(function (m) {
        return '<option value="' + U.esc(m.window_id) + '"' +
          (m.window_id === g.lead_window_id ? ' selected' : '') + '>' +
          U.esc(m.window_name || m.window_id) + '</option>';
      }).join('');
      U.modal({
        title: '编辑群组',
        body:
          '<div class="field"><span class="field-label">群组名称 *</span>' +
          '<input class="input" id="grp-edit-name" value="' + U.esc(g.name) + '"></div>' +
          '<div class="field"><span class="field-label">描述</span>' +
          '<textarea class="textarea" id="grp-edit-desc" rows="2">' + U.esc(g.description || '') + '</textarea></div>' +
          '<div class="field"><span class="field-label">组长</span>' +
          '<select class="select" id="grp-edit-lead">' + leadOpts + '</select></div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: '保存', cls: 'btn-primary', keepOpen: true,
            onClick: function (body, api) {
              var name = body.querySelector('#grp-edit-name').value.trim();
              if (!name) { U.toast('warn', '请填写群组名称'); return false; }
              A.groups.update(g.id, {
                name: name,
                description: body.querySelector('#grp-edit-desc').value.trim(),
                lead_window_id: body.querySelector('#grp-edit-lead').value || ''
              }).then(function (dto) {
                api.close();
                U.toast('success', '群组已更新');
                Groups.current = dto || Groups.current;
                Groups.renderDetail();
                Groups.load();
              }).catch(function (e) { U.toast('error', '保存失败：' + e.message); });
            }
          }
        ]
      });
    }
  };

  global.DogBuddyViews.Groups = Groups;
})(window);

/* ==== js/views-approval.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-approval.js
   审批设置（高危命令审批，ROADMAP T4）：
   —— 放行清单（approval.allowlist：每条 = 一条允许的 shell 命令前缀/模式）
   —— 托管代审开关（approval.escalation_review：管理员托管模式，派独立 LLM 审查）
   以「设置卡片」形式挂进设置视图（同 views-profiles.js 的注入模式）。
   后端契约（后端并行开发中，字段以此为准）：
     GET  /api/config                → 配置键值（兼容 {config:{...}} 包装 / 平铺两种形状）
     POST /api/config {key, value}   → 写单键（对应 ConfigService.set(key,value)）
   读到的形状不确定时按「取不到 = 默认值」降级，绝不让审批卡拖垮设置页。
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var KEY_ALLOWLIST = 'approval.allowlist';
  var KEY_ESCALATION = 'approval.escalation_review';

  var Approval = {
    /** @type {Array<string>} 放行清单（允许执行、免审批的 shell 命令前缀/模式） */
    allowlist: [],
    /** @type {boolean} 托管代审开关（true = 管理员托管模式） */
    escalationReview: false,
    /** @type {boolean} 配置是否已从后端读到（读不到保持默认值） */
    loaded: false,

    /* ---------------- 配置读取（多形状兼容） ---------------- */

    /**
     * 从 GET /api/config 的原始响应里取一个配置键。
     * 兼容形状：{config:{key:value,...}} / {values:{...}} / 平铺 {key:value} /
     * {keys:[{key,value}]}；取不到返回 undefined。
     * @param {*} raw GET /api/config 的原始响应
     * @param {string} key 配置键（如 approval.allowlist）
     * @returns {*} 键对应值（取不到为 undefined）
     */
    pick: function (raw, key) {
      if (!raw || typeof raw !== 'object') return undefined;
      var bags = [raw, raw.config, raw.values, raw.settings];
      for (var i = 0; i < bags.length; i++) {
        var bag = bags[i];
        if (bag && typeof bag === 'object' && key in bag) return bag[key];
      }
      if (Array.isArray(raw.keys)) {
        for (var j = 0; j < raw.keys.length; j++) {
          var item = raw.keys[j];
          if (item && item.key === key) return item.value;
        }
      }
      return undefined;
    },

    /**
     * 把 allowlist 值规整成字符串数组（数组原样去空；字符串按行拆）。
     * @param {*} v 原始值
     * @returns {Array<string>} 字符串数组
     */
    normList: function (v) {
      var arr = Array.isArray(v) ? v : (typeof v === 'string' ? v.split('\n') : []);
      return arr.map(function (s) { return String(s).trim(); })
        .filter(function (s) { return !!s; });
    },

    /**
     * 拉取 approval.* 配置（失败静默用默认值，永不 reject）。
     * @returns {Promise<void>} 完成即 resolve
     */
    load: function () {
      return A.config.get().then(function (raw) {
        Approval.allowlist = Approval.normList(Approval.pick(raw, KEY_ALLOWLIST));
        var esc = Approval.pick(raw, KEY_ESCALATION);
        Approval.escalationReview = esc === true || esc === 'true' || esc === 1;
        Approval.loaded = true;
      }).catch(function () {
        /* 后端 /api/config 未就绪：保持默认值，卡片照常展示 */
        Approval.loaded = false;
      });
    },

    /* ---------------- 卡片模板（由 views-settings.js 注入） ---------------- */

    /**
     * 审批设置卡片 HTML：放行清单编辑 + 托管代审开关。
     * @returns {string} 卡片 HTML
     */
    cardHTML: function () {
      return '<div class="card set-card" data-group="approval">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('shield', 'sm') + '</span>' +
        '<div><h3>审批设置</h3><p>高危命令审批 · 放行清单 · 托管代审</p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="field"><span class="field-label">放行清单（approval.allowlist）</span>' +
        '<textarea class="textarea mono" id="appr-allowlist" rows="5" ' +
        'placeholder="每行一条允许的 shell 命令前缀 / 模式，例如：&#10;git status&#10;ls&#10;npm run build"></textarea>' +
        '<div class="hint">每条 = 一条允许的 shell 命令前缀/模式。命中放行清单的命令免审批直接执行；' +
        '清单之外的非常规命令会进入「待决」等你审批。</div></div>' +
        '<div class="set-row appr-esc-row">' +
        '<div><span class="field-label">托管代审（approval.escalation_review）</span>' +
        '<div class="hint">开启后非常规命令先由独立审查代理把关；不可逆命令永远需要你亲自批准。</div></div>' +
        '<label class="switch"><input type="checkbox" id="appr-esc"' +
        (Approval.escalationReview ? ' checked' : '') + '><i></i></label>' +
        '</div>' +
        (Approval.loaded ? '' :
          '<div class="hint appr-offline">⚠ 未读到后端 /api/config 当前值，展示的是默认值；保存后即按新值生效。</div>') +
        '</div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-primary btn-sm" id="appr-save">' + U.icon('save', 'sm') + '保存审批设置</button>' +
        '</div></div>';
    },

    /* ---------------- 交互 ---------------- */

    /**
     * 绑定保存按钮（每次卡片渲染后调用，幂等）。
     * @returns {void}
     */
    bind: function () {
      var ta = document.getElementById('appr-allowlist');
      if (ta) ta.value = Approval.allowlist.join('\n');
      var btn = document.getElementById('appr-save');
      if (btn) btn.addEventListener('click', function () { Approval.save(btn); });
    },

    /**
     * 保存两项配置：先写 allowlist，再写 escalation 开关（逐键 POST /api/config）。
     * @param {HTMLButtonElement} btn 保存按钮（置灰防重复提交）
     * @returns {void}
     */
    save: function (btn) {
      var ta = document.getElementById('appr-allowlist');
      var sw = document.getElementById('appr-esc');
      var list = Approval.normList(ta ? ta.value : '');
      var esc = !!(sw && sw.checked);
      if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
      A.config.set(KEY_ALLOWLIST, list).then(function () {
        return A.config.set(KEY_ESCALATION, esc);
      }).then(function () {
        Approval.allowlist = list;
        Approval.escalationReview = esc;
        Approval.loaded = true;
        U.toast('success', '审批设置已保存（放行 ' + list.length + ' 条 · 托管代审' +
          (esc ? '开' : '关') + '）');
      }).catch(function (e) {
        U.toast('error', '保存失败：' + (e && e.message ? e.message : e));
      }).then(function () {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = U.icon('save', 'sm') + '保存审批设置';
        }
      });
    }
  };

  global.DogBuddyViews.Approval = Approval;
})(window);

/* ==== js/views-ledger.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · views-ledger.js
   用量账本（T2.4）：token 用量按工位/按天聚合，可选单价换算成本
   —— 数据只收集不汇报，用户自己打开账本看；通知永不推账单。
   后端契约：
     GET  /api/ledger?days=30 → {since, by_window:[{window_id,window_name,messages,
           tokens,cost}], by_day:[{day,messages,tokens,cost}], total_tokens,
           total_messages, price_per_1m, total_cost}
     GET  /api/ledger/price   → {price_per_1m}
     POST /api/ledger/price   {"price_per_1m": 数字}（0=只显示 tokens 不算钱）
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  /**
   * 千分位格式化整数（45,230 这种）。
   * @param {number|string} n 数值
   * @returns {string} 格式化后的字符串；非数值返回 '—'
   */
  function fmtInt(n) {
    var v = Number(n);
    if (!isFinite(v)) return '—';
    return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  /**
   * 成本显示：未设单价（0）时提示「未设单价」，否则保留两位小数。
   * @param {number} cost 成本
   * @param {number} price 单价（每 1M tokens）
   * @returns {string} 展示文本
   */
  function fmtCost(cost, price) {
    if (!Number(price)) return '未设单价';
    var v = Number(cost) || 0;
    return v.toFixed(2);
  }

  var Ledger = {
    /** @type {number} 统计天数（7/30/90） */
    days: 30,
    /** @type {Object|null} GET /api/ledger 的返回 */
    data: null,

    /* ---------------- 数据 ---------------- */

    /**
     * 加载账本数据并渲染。
     * @returns {Promise<void>} 渲染完成
     */
    load: function () {
      var host = document.getElementById('led-summary');
      if (host && !Ledger.data) {
        host.innerHTML = U.skeleton('cards');
      }
      return A.ledger.get(Ledger.days).then(function (data) {
        Ledger.data = data || {};
        Ledger.render();
      }).catch(function (e) {
        var body = document.getElementById('led-by-window');
        if (body) {
          body.innerHTML = '<div class="empty"><div class="empty-title">加载失败</div>' +
            '<div class="empty-desc">' + U.esc(e && e.message ? e.message : e) + '</div></div>';
        }
      });
    },

    /* ---------------- 渲染 ---------------- */

    /**
     * 渲染整页：汇总卡 / 按工位条形 / 按天柱状 / 单价设置 / 天数切换态。
     * @returns {void}
     */
    render: function () {
      var d = Ledger.data || {};
      var price = Number(d.price_per_1m) || 0;

      /* 天数切换态 */
      U.$$('#led-days .seg-btn').forEach(function (b) {
        b.classList.toggle('active', Number(b.getAttribute('data-days')) === Ledger.days);
      });

      /* 顶部汇总卡 */
      var sum = document.getElementById('led-summary');
      if (sum) {
        var cards = [
          { label: '总 tokens', value: fmtInt(d.total_tokens), unit: '', icon: 'activity',
            cls: 'green', hint: '最近 ' + Ledger.days + ' 天累计' },
          { label: '总消息数', value: fmtInt(d.total_messages), unit: '条', icon: 'chat',
            cls: 'violet', hint: 'user / assistant / tool 合计' },
          { label: '总成本', value: fmtCost(d.total_cost, price), unit: '',
            icon: 'zap', cls: 'amber',
            hint: price ? ('按单价 ' + price + ' / 1M tokens 估算') : '到单价设置里填价格后自动换算' }
        ];
        sum.innerHTML = cards.map(function (c) {
          return '<div class="stat-card">' +
            '<div class="stat-top"><span class="stat-label">' + c.label + '</span>' +
            '<span class="stat-icon ' + c.cls + '">' + U.icon(c.icon, 'sm') + '</span></div>' +
            '<div class="stat-value num">' + U.esc(String(c.value)) +
            (c.unit ? '<em>' + c.unit + '</em>' : '') + '</div>' +
            '<div class="stat-hint">' + U.esc(c.hint) + '</div></div>';
        }).join('');
      }

      /* 按工位：tokens 条形（纯 CSS 宽度百分比） */
      var winHost = document.getElementById('led-by-window');
      if (winHost) {
        var byWin = d.by_window || [];
        if (!byWin.length) {
          winHost.innerHTML = U.empty({
            art: 'chart', title: '暂无用量',
            desc: '员工开始干活后，这里会按工位显示 token 消耗。'
          });
        } else {
          var maxWin = 0;
          byWin.forEach(function (r) { maxWin = Math.max(maxWin, Number(r.tokens) || 0); });
          winHost.innerHTML = byWin.map(function (r) {
            var pct = maxWin ? Math.max(2, Math.round((Number(r.tokens) || 0) / maxWin * 100)) : 0;
            return '<div class="led-row">' +
              '<div class="led-row-top">' +
              '<span class="led-name">' + U.esc(r.window_name || r.window_id || '—') + '</span>' +
              '<span class="led-num num">' + fmtInt(r.tokens) + ' tok</span>' +
              '</div>' +
              '<div class="led-bar"><div class="led-bar-fill" style="width:' + pct + '%"></div></div>' +
              '<div class="led-row-sub muted tiny">' +
              U.esc(String(r.messages || 0)) + ' 条消息 · 成本 ' +
              U.esc(fmtCost(r.cost, price)) + '</div>' +
              '</div>';
          }).join('');
        }
      }

      /* 按天：纯 CSS div 柱状 */
      var dayHost = document.getElementById('led-by-day');
      if (dayHost) {
        var byDay = d.by_day || [];
        if (!byDay.length) {
          dayHost.innerHTML = U.empty({
            art: 'chart', title: '暂无按天数据',
            desc: '有运行记录后按天展示用量趋势。'
          });
        } else {
          var maxDay = 0;
          byDay.forEach(function (r) { maxDay = Math.max(maxDay, Number(r.tokens) || 0); });
          dayHost.innerHTML = '<div class="led-days">' + byDay.map(function (r) {
            var h = maxDay ? Math.max(2, Math.round((Number(r.tokens) || 0) / maxDay * 100)) : 2;
            var day = String(r.day || '');
            var short = day.length >= 10 ? day.slice(5) : day; /* MM-DD */
            return '<div class="led-day-col" title="' + U.esc(day) + ' · ' + fmtInt(r.tokens) +
              ' tok · ' + fmtInt(r.messages) + ' 条消息">' +
              '<div class="led-day-bar" style="height:' + h + '%"></div>' +
              '<div class="led-day-label tiny">' + U.esc(short) + '</div></div>';
          }).join('') + '</div>' +
          '<p class="muted tiny led-days-hint">纵轴为当日 tokens（峰值 ' + fmtInt(maxDay) +
          '），悬停看明细。</p>';
        }
      }

      /* 单价设置 */
      var priceHost = document.getElementById('led-price');
      if (priceHost) {
        priceHost.innerHTML =
          '<div class="field"><span class="field-label">单价（每 1M tokens）</span>' +
          '<div class="dec-answer-row">' +
          '<input class="input" id="led-price-input" type="number" min="0" step="0.01" value="' +
          U.esc(String(price)) + '" placeholder="例如 30">' +
          '<button class="btn btn-primary btn-sm" id="led-price-save">保存</button>' +
          '</div>' +
          '<p class="muted tiny" style="margin-top:6px">0=只显示用量，不算钱。货币单位自便（元 / 美元均可）。</p>' +
          '</div>';
        var save = /** @type {HTMLButtonElement} */ (document.getElementById('led-price-save'));
        if (save) {
          save.addEventListener('click', function () {
            var input = /** @type {HTMLInputElement} */ (document.getElementById('led-price-input'));
            var value = Number(input && input.value) || 0;
            save.disabled = true;
            A.ledger.setPrice(value).then(function () {
              U.toast('success', value ? ('单价已保存：' + value + ' / 1M tokens') : '已改为只显示用量（0）');
              return Ledger.load();
            }).catch(function (e) {
              save.disabled = false;
              U.toast('error', '保存失败：' + (e && e.message ? e.message : e));
            });
          });
        }
      }
    },

    /**
     * 切换统计天数（7 / 30 / 90）。
     * @param {number} days 天数
     * @returns {void}
     */
    switchDays: function (days) {
      var n = Number(days);
      if (n !== 7 && n !== 30 && n !== 90) return;
      if (Ledger.days === n) return;
      Ledger.days = n;
      Ledger.load();
    }
  };

  global.DogBuddyViews.Ledger = Ledger;
})(window);

/* ==== js/filemanager.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · filemanager.js
   Windows 资源管理器式三区布局：左树 / 中列表 / 右预览
   上传（含拖拽）· 新建 · 重命名 · 删除 · 预览↔编辑（Ctrl+S）· 下载
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;

  var IMG_EXT = /\.(png|jpe?g|gif|webp|bmp|svg|ico)$/i;
  var CODE_EXT = /\.(js|ts|jsx|tsx|py|go|rs|java|c|cc|cpp|h|hpp|cs|rb|php|sh|bash|zsh|json|ya?ml|toml|ini|cfg|conf|xml|html?|css|scss|less|md|txt|sql|vue|svelte|log)$/i;

  var FileMgr = {
    path: '',
    entries: [],
    breadcrumbs: [],
    keyword: '',
    sortKey: 'name',
    sortDir: 1,
    selected: null,
    preview: null,   /* {path, name, mode:'preview'|'edit', content, language, size, isImage} */
    treeData: [],

    /* ---------- 加载 ---------- */
    load: function () {
      FileMgr.loadList();
      FileMgr.loadTree();
    },

    loadList: function () {
      var host = document.getElementById('fm-list');
      if (host) host.innerHTML = U.skeleton('rows');
      return A.files.list(FileMgr.path).then(function (data) {
        FileMgr.breadcrumbs = (data && data.breadcrumbs) || [];
        FileMgr.entries = (data && data.entries) || [];
        FileMgr.renderCrumb();
        FileMgr.renderList();
      }).catch(function (e) {
        if (host) {
          host.innerHTML = '<div class="empty"><div class="empty-title">无法读取目录</div>' +
            '<div class="empty-desc">' + U.esc(e.message) + '</div></div>';
        }
        U.toast('error', '目录读取失败：' + e.message);
      });
    },

    loadTree: function () {
      return A.files.tree('').then(function (rows) {
        FileMgr.treeData = rows || [];
        FileMgr.renderTree();
      }).catch(function () { /* 树失败不打扰 */ });
    },

    /* ---------- 渲染：面包屑 ---------- */
    renderCrumb: function () {
      var host = document.getElementById('fm-crumb');
      if (!host) return;
      var crumbs = FileMgr.breadcrumbs.slice();
      if (!crumbs.length || crumbs[crumbs.length - 1].path !== FileMgr.path) {
        crumbs.push({ name: '根目录', path: FileMgr.path });
      }
      host.innerHTML = crumbs.map(function (c, i) {
        var isLast = i === crumbs.length - 1;
        return (i ? U.icon('chevron-r', 'sm') : '') +
          '<button class="' + (isLast ? 'current' : '') + '" data-path="' + U.esc(c.path) + '">' +
          U.esc(c.name || '根目录') + '</button>';
      }).join('');
      U.$$('button', host).forEach(function (b) {
        b.addEventListener('click', function () {
          FileMgr.navigate(b.getAttribute('data-path'));
        });
      });
    },

    /* ---------- 渲染：左树 ---------- */
    renderTree: function () {
      var host = document.getElementById('fm-tree');
      if (!host) return;
      function walk(rows, depth) {
        return (rows || []).map(function (n) {
          var isDir = n.type === 'dir' || n.type === 'directory' || n.children;
          if (!isDir) return '';
          var active = FileMgr.path === n.path;
          var line = '<div class="fm-tree-item' + (active ? ' active' : '') +
            '" data-path="' + U.esc(n.path) + '" style="padding-left:' + (9 + depth * 14) + 'px">' +
            U.icon('folder', 'sm') + '<span>' + U.esc(n.name) + '</span></div>';
          return line + (n.children ? walk(n.children, depth + 1) : '');
        }).join('');
      }
      host.innerHTML = '<div class="fm-tree-item' + (FileMgr.path === '' ? ' active' : '') +
        '" data-path="">' + U.icon('folder', 'sm') + '<span>工作区根目录</span></div>' +
        walk(FileMgr.treeData, 1);
      U.$$('.fm-tree-item', host).forEach(function (n) {
        n.addEventListener('click', function () {
          FileMgr.navigate(n.getAttribute('data-path'));
        });
      });
    },

    /* ---------- 渲染：中列表 ---------- */
    sortedEntries: function () {
      var kw = FileMgr.keyword.trim().toLowerCase();
      var rows = FileMgr.entries.filter(function (e) {
        return !kw || String(e.name || '').toLowerCase().indexOf(kw) >= 0;
      });
      var key = FileMgr.sortKey, dir = FileMgr.sortDir;
      return rows.slice().sort(function (a, b) {
        /* 目录优先 */
        var ad = a.type === 'dir' || a.type === 'directory' ? 0 : 1;
        var bd = b.type === 'dir' || b.type === 'directory' ? 0 : 1;
        if (ad !== bd) return ad - bd;
        if (key === 'size') return (Number(a.size) - Number(b.size)) * dir;
        if (key === 'mtime') return (U.parseTime(a.mtime) - U.parseTime(b.mtime)) * dir;
        return String(a.name).localeCompare(String(b.name)) * dir;
      });
    },

    renderList: function () {
      var host = document.getElementById('fm-list');
      if (!host) return;
      var rows = FileMgr.sortedEntries();
      if (!rows.length) {
        host.innerHTML = U.empty({
          art: 'folder', title: FileMgr.keyword ? '没有匹配的文件' : '空目录',
          desc: FileMgr.keyword ? '换个关键词试试。' : '拖拽文件到此处上传，或使用上方工具条新建。',
          action: FileMgr.keyword ? '' : '上传文件', actionId: 'fm-upload'
        });
        FileMgr.bindDropZone();
        return;
      }
      host.innerHTML = '<table class="fm-table"><thead><tr>' +
        '<th data-sort="name">名称' + FileMgr.sortMark('name') + '</th>' +
        '<th data-sort="size" style="width:90px">大小' + FileMgr.sortMark('size') + '</th>' +
        '<th data-sort="mtime" style="width:150px">修改时间' + FileMgr.sortMark('mtime') + '</th>' +
        '<th style="width:96px"></th>' +
        '</tr></thead><tbody>' +
        rows.map(function (e) {
          var isDir = e.type === 'dir' || e.type === 'directory';
          var sel = FileMgr.selected && FileMgr.selected.path === e.path;
          return '<tr data-path="' + U.esc(e.path) + '" data-type="' + U.esc(e.type) + '" class="' + (sel ? 'selected' : '') + '">' +
            '<td><div class="fm-name">' +
            U.icon(isDir ? 'folder' : (IMG_EXT.test(e.name) ? 'image' : (CODE_EXT.test(e.name) ? 'code' : 'doc')), isDir ? 'folder' : '') +
            '<span>' + U.esc(e.name) + '</span></div></td>' +
            '<td class="fm-size">' + (isDir ? '—' : U.esc(U.fmtSize(e.size))) + '</td>' +
            '<td class="fm-time">' + U.esc(U.fmtTime(e.mtime)) + '</td>' +
            '<td><div class="fm-row-actions">' +
            '<button class="icon-btn" data-op="rename" title="重命名">' + U.icon('edit', 'sm') + '</button>' +
            (isDir ? '' : '<button class="icon-btn" data-op="download" title="下载">' + U.icon('download', 'sm') + '</button>') +
            '<button class="icon-btn danger" data-op="delete" title="删除">' + U.icon('trash', 'sm') + '</button>' +
            '</div></td></tr>';
        }).join('') + '</tbody></table>';

      /* 表头排序 */
      U.$$('.fm-table th[data-sort]', host).forEach(function (th) {
        th.addEventListener('click', function () {
          var k = th.getAttribute('data-sort');
          if (FileMgr.sortKey === k) FileMgr.sortDir = -FileMgr.sortDir;
          else { FileMgr.sortKey = k; FileMgr.sortDir = 1; }
          var sel = document.getElementById('fm-sort');
          if (sel) sel.value = k;
          FileMgr.renderList();
        });
      });
      /* 行交互 */
      U.$$('.fm-table tbody tr', host).forEach(function (tr) {
        tr.addEventListener('click', function (e) {
          if (e.target.closest('[data-op]')) return;
          var entry = FileMgr.findEntry(tr.getAttribute('data-path'));
          FileMgr.selected = entry;
          FileMgr.renderList();
          if (entry && entry.type !== 'dir' && entry.type !== 'directory') FileMgr.previewFile(entry);
        });
        tr.addEventListener('dblclick', function () {
          var entry = FileMgr.findEntry(tr.getAttribute('data-path'));
          if (!entry) return;
          if (entry.type === 'dir' || entry.type === 'directory') FileMgr.navigate(entry.path);
          else FileMgr.previewFile(entry);
        });
      });
      U.$$('[data-op]', host).forEach(function (btn) {
        btn.addEventListener('click', function (e) {
          e.stopPropagation();
          var tr = btn.closest('tr');
          var entry = FileMgr.findEntry(tr.getAttribute('data-path'));
          if (!entry) return;
          var op = btn.getAttribute('data-op');
          if (op === 'rename') FileMgr.renameEntry(entry);
          else if (op === 'delete') FileMgr.deleteEntry(entry);
          else if (op === 'download') FileMgr.download(entry.path);
        });
      });
      FileMgr.bindDropZone();
    },

    sortMark: function (key) {
      if (FileMgr.sortKey !== key) return '';
      return FileMgr.sortDir > 0 ? ' ↑' : ' ↓';
    },

    findEntry: function (path) {
      return FileMgr.entries.filter(function (e) { return e.path === path; })[0] || null;
    },

    /* ---------- 拖拽上传 ---------- */
    bindDropZone: function () {
      var host = document.getElementById('fm-list');
      if (!host || host._dropBound) return;
      host._dropBound = true;
      ['dragenter', 'dragover'].forEach(function (ev) {
        host.addEventListener(ev, function (e) {
          e.preventDefault();
          host.classList.add('drop-over');
        });
      });
      ['dragleave', 'drop'].forEach(function (ev) {
        host.addEventListener(ev, function (e) {
          e.preventDefault();
          if (ev === 'dragleave' && host.contains(e.relatedTarget)) return;
          host.classList.remove('drop-over');
        });
      });
      host.addEventListener('drop', function (e) {
        var files = e.dataTransfer && e.dataTransfer.files;
        if (files && files.length) FileMgr.uploadFiles(files);
      });
    },

    uploadFiles: function (files) {
      var list = Array.prototype.slice.call(files);
      var total = list.length, done = 0, failed = 0;
      list.forEach(function (f) {
        A.files.upload(FileMgr.path, f).then(function () {
          done++;
          if (done === total) {
            U.toast(failed ? 'warn' : 'success', failed ? (failed + ' 个文件上传失败') : ('已上传 ' + total + ' 个文件'));
            FileMgr.load();
          }
        }).catch(function (e) {
          failed++; done++;
          U.toast('error', '上传失败：' + e.message);
          if (done === total) FileMgr.load();
        });
      });
    },

    /* ---------- 导航 ---------- */
    navigate: function (path) {
      FileMgr.path = path || '';
      FileMgr.selected = null;
      FileMgr.keyword = '';
      var s = document.getElementById('fm-search');
      if (s) s.value = '';
      FileMgr.loadList();
      FileMgr.renderTree();
    },

    parentPath: function () {
      var p = FileMgr.path || '';
      if (!p) return '';
      var i = p.lastIndexOf('/');
      return i < 0 ? '' : p.slice(0, i);
    },

    /* ---------- 预览 / 编辑 ---------- */
    previewFile: function (entry) {
      var host = document.getElementById('fm-preview');
      if (!host) return;
      host.innerHTML = '<div class="fm-preview-head"><span class="name">' + U.esc(entry.name) + '</span></div>' +
        '<div class="fm-preview-body">' + U.skeleton() + '</div>';
      if (window.innerWidth <= 720) host.classList.add('show');

      if (IMG_EXT.test(entry.name)) {
        A.files.image(entry.path).then(function (blob) {
          var url = URL.createObjectURL(blob);
          FileMgr.preview = { path: entry.path, name: entry.name, isImage: true, size: entry.size };
          FileMgr.renderPreview(null, url);
        }).catch(function () {
          /* 回退：用 read 的 image_base64 */
          A.files.read(entry.path).then(function (data) {
            var url = data.image_base64 ? ('data:image/*;base64,' + data.image_base64) : '';
            FileMgr.preview = { path: entry.path, name: entry.name, isImage: true, size: entry.size };
            FileMgr.renderPreview(null, url);
          }).catch(function (e) {
            FileMgr.renderLoadError(entry, e);
          });
        });
        return;
      }

      A.files.read(entry.path).then(function (data) {
        FileMgr.preview = {
          path: entry.path, name: entry.name, mode: 'preview',
          content: data.content || '', language: data.language || '',
          size: data.size !== undefined ? data.size : entry.size, isImage: false
        };
        FileMgr.renderPreview();
      }).catch(function (e) {
        FileMgr.renderLoadError(entry, e);
      });
    },

    renderLoadError: function (entry, e) {
      var host = document.getElementById('fm-preview');
      if (!host) return;
      var isBig = e.status === 413;
      host.innerHTML =
        '<div class="fm-preview-head"><span class="name">' + U.esc(entry.name) + '</span>' +
        '<button class="btn btn-soft btn-sm" id="fmp-download">' + U.icon('download', 'sm') + '下载</button></div>' +
        '<div class="fm-preview-body">' +
        '<div class="fm-notice">' + U.icon('alert', 'sm') +
        '<div><b>' + (isBig ? '文件过大，无法在线预览' : '无法预览该文件') + '</b><br>' +
        U.esc(isBig ? '超过 200KB 的文本不提供在线预览，可下载后查看。' : e.message) + '</div></div></div>';
      var btn = document.getElementById('fmp-download');
      if (btn) btn.addEventListener('click', function () { FileMgr.download(entry.path); });
    },

    renderPreview: function (overrideContent, imageUrl) {
      var p = FileMgr.preview;
      var host = document.getElementById('fm-preview');
      if (!host || !p) return;
      var head =
        '<div class="fm-preview-head">' +
        '<span class="name">' + U.esc(p.name) + '</span>' +
        (p.isImage ? '' :
          '<button class="btn btn-soft btn-sm" id="fmp-edit-toggle">' +
          U.icon(p.mode === 'edit' ? 'eye' : 'edit', 'sm') + (p.mode === 'edit' ? '预览' : '编辑') + '</button>' +
          (p.mode === 'edit' ? '<button class="btn btn-primary btn-sm" id="fmp-save">' + U.icon('save', 'sm') + '保存</button>' : '')) +
        '<button class="btn btn-soft btn-sm" id="fmp-download">' + U.icon('download', 'sm') + '下载</button>' +
        '</div>';

      var bodyHTML;
      if (p.isImage) {
        bodyHTML = '<div class="fm-img">' +
          (imageUrl ? '<img src="' + imageUrl + '" alt="' + U.esc(p.name) + '">'
            : '<div class="fm-notice">' + U.icon('alert', 'sm') + '图片加载失败</div>') + '</div>';
      } else if (p.mode === 'edit') {
        bodyHTML = '<textarea class="fm-edit-area" id="fmp-editor" spellcheck="false">' +
          U.esc(overrideContent !== undefined && overrideContent !== null ? overrideContent : p.content) + '</textarea>';
      } else {
        bodyHTML = '<pre>' + U.highlightCode(overrideContent !== undefined && overrideContent !== null ? overrideContent : p.content, p.language) + '</pre>' +
          (p.size > 200 * 1024 ? '<div class="fm-notice" style="margin-top:10px">' + U.icon('info', 'sm') +
            '<div>文件较大（' + U.esc(U.fmtSize(p.size)) + '），预览可能截断。</div></div>' : '');
      }
      host.innerHTML = head + '<div class="fm-preview-body">' + bodyHTML + '</div>';

      var toggle = document.getElementById('fmp-edit-toggle');
      if (toggle) {
        toggle.addEventListener('click', function () {
          var editor = document.getElementById('fmp-editor');
          if (p.mode === 'edit' && editor) p.content = editor.value;
          p.mode = p.mode === 'edit' ? 'preview' : 'edit';
          FileMgr.renderPreview();
        });
      }
      var save = document.getElementById('fmp-save');
      if (save) save.addEventListener('click', FileMgr.saveEdit);
      var dl = document.getElementById('fmp-download');
      if (dl) dl.addEventListener('click', function () { FileMgr.download(p.path); });
      var editor = document.getElementById('fmp-editor');
      if (editor) {
        editor.addEventListener('keydown', function (e) {
          if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
            e.preventDefault();
            FileMgr.saveEdit();
          }
        });
      }
    },

    saveEdit: function () {
      var p = FileMgr.preview;
      var editor = document.getElementById('fmp-editor');
      if (!p || !editor) return;
      A.files.write(p.path, editor.value).then(function () {
        p.content = editor.value;
        U.toast('success', '已保存：' + p.name);
      }).catch(function (e) {
        U.toast('error', '保存失败：' + e.message);
      });
    },

    download: function (path) {
      A.files.download(path).then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = path.split('/').pop() || 'download';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
      }).catch(function (e) {
        U.toast('error', '下载失败：' + e.message);
      });
    },

    /* ---------- 新建 / 重命名 / 删除 ---------- */
    promptName: function (title, value, cb) {
      U.modal({
        title: title,
        body: '<div class="field"><span class="field-label">名称</span>' +
          '<input class="input" id="fm-name-input" value="' + U.esc(value || '') + '"></div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: '确定', cls: 'btn-primary',
            onClick: function (body) {
              var name = body.querySelector('#fm-name-input').value.trim();
              if (!name) { U.toast('warn', '名称不能为空'); return false; }
              cb(name);
            }
          }
        ]
      });
    },

    mkdir: function () {
      FileMgr.promptName('新建文件夹', '新建文件夹', function (name) {
        A.files.mkdir(FileMgr.path, name).then(function () {
          U.toast('success', '已创建文件夹：' + name);
          FileMgr.load();
        }).catch(function (e) { U.toast('error', e.message); });
      });
    },

    mkfile: function () {
      FileMgr.promptName('新建文件', 'untitled.txt', function (name) {
        var path = FileMgr.path ? (FileMgr.path + '/' + name) : name;
        A.files.write(path, '').then(function () {
          U.toast('success', '已创建文件：' + name);
          FileMgr.load();
        }).catch(function (e) { U.toast('error', e.message); });
      });
    },

    renameEntry: function (entry) {
      FileMgr.promptName('重命名 · ' + entry.name, entry.name, function (name) {
        if (name === entry.name) return;
        A.files.rename(entry.path, name).then(function () {
          U.toast('success', '已重命名为：' + name);
          if (FileMgr.preview && FileMgr.preview.path === entry.path) FileMgr.preview = null;
          FileMgr.load();
          var pv = document.getElementById('fm-preview');
          if (pv && FileMgr.preview === null) {
            pv.innerHTML = U.empty({ art: 'doc', title: '选择文件以预览', desc: '左键单击预览，双击打开目录或文件。' });
          }
        }).catch(function (e) { U.toast('error', e.message); });
      });
    },

    deleteEntry: function (entry) {
      UI.confirm({
        title: '删除确认', danger: true, okLabel: '删除',
        text: '确定删除「' + entry.name + '」？此操作不可恢复。'
      }).then(function (ok) {
        if (!ok) return;
        A.files.remove(entry.path).then(function () {
          U.toast('success', '已删除：' + entry.name);
          if (FileMgr.preview && FileMgr.preview.path === entry.path) {
            FileMgr.preview = null;
            var pv = document.getElementById('fm-preview');
            if (pv) pv.innerHTML = U.empty({ art: 'doc', title: '选择文件以预览', desc: '左键单击预览，双击打开目录或文件。' });
          }
          FileMgr.load();
        }).catch(function (e) { U.toast('error', e.message); });
      });
    },

    /* ---------- 从日志跳转预览 ---------- */
    openPath: function (path) {
      var parts = String(path || '').split(/[\\/]/);
      var name = parts.pop();
      var dir = parts.join('/');
      global.App.go('files');
      FileMgr.path = '';
      FileMgr.loadList().then(function () {
        FileMgr.path = dir;
        return FileMgr.loadList();
      }).then(function () {
        var entry = FileMgr.entries.filter(function (e) { return e.name === name; })[0];
        if (entry) {
          FileMgr.selected = entry;
          FileMgr.previewFile(entry);
          FileMgr.renderList();
        } else {
          U.toast('warn', '未在目录中找到：' + name);
        }
      }).catch(function () { /* 已有提示 */ });
    },

    /* ---------- 工具条绑定 ---------- */
    bindToolbar: function () {
      var bind = function (id, fn) {
        var n = document.getElementById(id);
        if (n) n.addEventListener('click', fn);
      };
      bind('fm-up', function () { FileMgr.navigate(FileMgr.parentPath()); });
      bind('fm-refresh', function () { FileMgr.load(); });
      bind('fm-new-folder', FileMgr.mkdir);
      bind('fm-new-file', FileMgr.mkfile);
      bind('fm-upload', function () {
        var input = document.getElementById('fm-upload-input');
        if (input) input.click();
      });
      var input = document.getElementById('fm-upload-input');
      if (input) {
        input.addEventListener('change', function () {
          if (input.files && input.files.length) FileMgr.uploadFiles(input.files);
          input.value = '';
        });
      }
      var search = document.getElementById('fm-search');
      if (search) {
        search.addEventListener('input', U.debounce(function () {
          FileMgr.keyword = search.value;
          FileMgr.renderList();
        }, 150));
      }
      var sort = document.getElementById('fm-sort');
      if (sort) {
        sort.addEventListener('change', function () {
          FileMgr.sortKey = sort.value;
          FileMgr.sortDir = 1;
          FileMgr.renderList();
        });
      }
    },

    init: function () {
      FileMgr.bindToolbar();
      var pv = document.getElementById('fm-preview');
      if (pv) pv.innerHTML = U.empty({ art: 'doc', title: '选择文件以预览', desc: '左键单击预览，双击打开目录或文件。' });
    }
  };

  global.FileMgr = FileMgr;
})(window);

/* ==== js/app.js ==== */
/* ============================================================
   DogBuddyCn 控制台 · app.js
   应用外壳：登录 / 路由 / 侧边栏 / 顶栏 / 状态轮询 / 快捷键 / 空状态动作
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  var V = global.DogBuddyViews;

  var TITLES = {
    overview: '总览', chat: '对话', decisions: '待决', windows: '工位', jobs: '任务',
    groups: '群组', capabilities: '能力', projects: '项目', models: '模型', ledger: '账本', files: '文件管理器',
    logs: '日志审计', settings: '设置'
  };

  var App = {
    view: 'overview',
    statusTimer: null,
    tickTimer: null,
    booted: false,

    /* ---------------- 登录层 ---------------- */
    loginMode: 'token',

    showLogin: function (msg) {
      var mask = document.getElementById('login-mask');
      var shell = document.getElementById('app-shell');
      if (mask) mask.classList.remove('hidden');
      if (shell) shell.classList.add('hidden');
      if (App.statusTimer) { clearInterval(App.statusTimer); App.statusTimer = null; }
      if (msg) {
        var err = document.getElementById('login-error');
        if (err) { err.textContent = msg; err.classList.remove('hidden'); }
      }
    },

    hideLogin: function () {
      var mask = document.getElementById('login-mask');
      var shell = document.getElementById('app-shell');
      if (mask) mask.classList.add('hidden');
      if (shell) shell.classList.remove('hidden');
    },

    logout: function () {
      A.clearToken();
      App.showLogin('已退出登录');
    },

    bindLogin: function () {
      var tabs = document.getElementById('login-tabs');
      if (tabs) {
        U.$$('.seg-btn', tabs).forEach(function (b) {
          b.addEventListener('click', function () {
            U.$$('.seg-btn', tabs).forEach(function (x) { x.classList.remove('active'); });
            b.classList.add('active');
            App.loginMode = b.getAttribute('data-mode');
            var label = document.getElementById('login-label');
            var input = document.getElementById('login-input');
            if (label) {
              label.textContent = App.loginMode === 'token'
                ? '访问令牌 (X-Auth-Token)' : '控制台密码';
            }
            if (input) {
              input.placeholder = App.loginMode === 'token' ? '粘贴 token' : '输入密码';
              input.value = '';
            }
          });
        });
      }
      var form = document.getElementById('login-form');
      if (form) {
        form.addEventListener('submit', function (e) {
          e.preventDefault();
          var input = document.getElementById('login-input');
          var btn = document.getElementById('btn-login');
          var errBox = document.getElementById('login-error');
          var value = (input && input.value || '').trim();
          if (!value) {
            if (errBox) { errBox.textContent = '请输入' + (App.loginMode === 'token' ? '访问令牌' : '密码'); errBox.classList.remove('hidden'); }
            return;
          }
          if (errBox) errBox.classList.add('hidden');
          if (btn) { btn.disabled = true; btn.textContent = '验证中…'; }
          var payload = App.loginMode === 'token' ? { token: value } : { password: value };
          A.login(payload).then(function (r) {
            if (r && r.token) A.setToken(r.token);
            else A.setToken(value); /* 兼容只返回 ok 的部署：直接用输入值作 token */
            if (btn) { btn.disabled = false; btn.textContent = '进入控制台'; }
            App.hideLogin();
            App.boot();
            U.toast('success', '欢迎回来');
          }).catch(function (e) {
            if (btn) { btn.disabled = false; btn.textContent = '进入控制台'; }
            if (errBox) {
              errBox.textContent = e.status === 401 ? '凭据错误，请重试' : (e.message || '登录失败');
              errBox.classList.remove('hidden');
            }
          });
        });
      }
    },

    /* ---------------- 路由 ---------------- */
    go: function (view, params) {
      if (!TITLES[view]) view = 'overview';
      App.view = view;
      U.$$('.view').forEach(function (v) {
        v.classList.toggle('active', v.id === 'view-' + view);
      });
      U.$$('#nav-main .nav-item').forEach(function (n) {
        n.classList.toggle('active', n.getAttribute('data-view') === view);
      });
      var title = document.getElementById('page-title');
      if (title) title.textContent = TITLES[view];
      var side = document.getElementById('sidebar');
      if (side) side.classList.remove('open');
      App.renderView(params);
    },

    renderView: function (params) {
      switch (App.view) {
        case 'overview':
          V.Overview.load();
          break;
        case 'chat':
          if (V.Chat) V.Chat.load();
          break;
        case 'windows':
          if (params && params.win) V.Windows.selectedId = params.win;
          V.Windows.load();
          break;
        case 'jobs':
          V.Jobs.load();
          break;
        case 'groups':
          if (V.Groups) V.Groups.load();
          break;
        case 'capabilities':
          if (V.Capabilities) V.Capabilities.load();
          break;
        case 'decisions':
          if (V.Decisions) V.Decisions.load();
          break;
        case 'ledger':
          if (V.Ledger) V.Ledger.load();
          break;
        case 'projects':

          if (V.Projects) V.Projects.load();

          break;

        case 'models':
          if (V.Models) V.Models.load();
          break;
        case 'files':
          global.FileMgr.load();
          break;
        case 'logs':
          V.Logs.load();
          break;
        case 'settings':
          V.Settings.load();
          break;
        default:
          break;
      }
    },

    reloadView: function () {
      App.renderView();
    },

    /* ---------------- 系统状态 ---------------- */
    llmModel: '',
    refreshStatus: function () {
      return A.status().then(function (st) {
        App.llmModel = st.llm_model || '';
        var dot = document.getElementById('sys-dot');
        var title = document.getElementById('sys-running');
        var rate = document.getElementById('sys-rate');
        var wins = document.getElementById('sys-wins');
        var jobs = document.getElementById('sys-jobs');
        if (dot) {
          dot.className = 'dot ' + (st.running ? 'dot-run pulse' : 'dot-idle');
        }
        if (title) title.textContent = st.running ? '运行中' : '已停止';
        if (rate) {
          var waiting = st.rate_limit && st.rate_limit.waiting;
          rate.classList.toggle('hidden', !waiting);
          if (waiting) {
            rate.textContent = '限速等待' + (st.rate_limit.attempt ? ' · 第 ' + st.rate_limit.attempt + ' 次' : '');
          }
        }
        /* 顶栏限速徽标：SSE 实时置位，这里兜底清除（限速结束没有专门事件） */
        var topRate = document.getElementById('top-rate');
        if (topRate && !(st.rate_limit && st.rate_limit.waiting)) {
          topRate.classList.add('hidden');
        }
        if (wins) wins.textContent = st.windows || 0;
        if (jobs) jobs.textContent = st.jobs || 0;
        return st;
      }).catch(function () { return null; });
    },

    /* ---------------- 快捷键与全局交互 ---------------- */
    bindGlobal: function () {
      /* 导航 */
      U.$$('#nav-main .nav-item').forEach(function (n) {
        n.addEventListener('click', function () {
          App.go(n.getAttribute('data-view'));
        });
      });
      /* 顶栏 */
      var refresh = document.getElementById('btn-refresh');
      if (refresh) {
        refresh.addEventListener('click', function () {
          App.reloadView();
          App.refreshStatus();
          U.toast('info', '已刷新');
        });
      }
      var logout = document.getElementById('btn-logout');
      if (logout) {
        logout.addEventListener('click', function () {
          UI.confirm({ title: '退出登录', okLabel: '退出', text: '确定退出当前会话？' })
            .then(function (ok) { if (ok) App.logout(); });
        });
      }
      var toggle = document.getElementById('btn-sidebar-toggle');
      if (toggle) {
        toggle.addEventListener('click', function () {
          var side = document.getElementById('sidebar');
          if (side) side.classList.toggle('open');
        });
      }
      /* 全局搜索：跳转到工位页并过滤 */
      var search = document.getElementById('global-search');
      if (search) {
        search.addEventListener('keydown', function (e) {
          if (e.key !== 'Enter') return;
          var kw = search.value.trim();
          if (!kw) return;
          V.Windows.keyword = kw;
          var winSearch = document.getElementById('win-search');
          if (winSearch) winSearch.value = kw;
          App.go('windows');
          U.toast('info', '在工位中搜索：' + kw);
        });
      }
      /* 视图内跳转按钮 */
      document.addEventListener('click', function (e) {
        var goto = e.target.closest && e.target.closest('[data-goto]');
        if (goto) {
          App.go(goto.getAttribute('data-goto'));
          return;
        }
        var act = e.target.closest && e.target.closest('[data-empty-action]');
        if (act) {
          var id = act.getAttribute('data-empty-action');
          if (id === 'new-window') V.Windows.openCreateModal();
          else if (id === 'new-job') V.Jobs.openForm(null);
          else if (id === 'new-group' && V.Groups) V.Groups.openCreate();
          else if (id === 'fm-upload') {
            var input = document.getElementById('fm-upload-input');
            if (input) input.click();
          }
        }
      });
      /* 工位搜索 */
      var winSearch = document.getElementById('win-search');
      if (winSearch) {
        winSearch.addEventListener('input', U.debounce(function () {
          V.Windows.keyword = winSearch.value;
          V.Windows.renderList();
        }, 150));
      }
      /* 账本天数切换（7 / 30 / 90） */
      var ledDays = document.getElementById('led-days');
      if (ledDays) {
        U.$$('.seg-btn', ledDays).forEach(function (b) {
          b.addEventListener('click', function () {
            if (V.Ledger) V.Ledger.switchDays(b.getAttribute('data-days'));
          });
        });
      }

      /* SSE：限速退避徽标（顶栏实时显示「限速等待中 第N次退避」） */
      if (global.SSE) {
        global.SSE.on('rate_limit.status', function (p) {
          var topRate = document.getElementById('top-rate');
          if (!topRate) return;
          var attempt = Number(p && p.attempt) || 0;
          topRate.textContent = '限速等待中 ' + (attempt ? '第' + attempt + '次退避' : '退避中');
          topRate.classList.remove('hidden');
        });
      }

      /* 新建按钮 */
      var nw = document.getElementById('btn-new-window');
      if (nw) nw.addEventListener('click', function () { V.Windows.openCreateModal(); });
      var nj = document.getElementById('btn-new-job');
      if (nj) nj.addEventListener('click', function () { V.Jobs.openForm(null); });
      var gn = document.getElementById('grp-new-btn');
      if (gn) gn.addEventListener('click', function () {
        if (V.Groups) V.Groups.openCreate();
      });

      /* 任务筛选 */
      var jf = document.getElementById('job-filter-window');
      if (jf) {
        jf.addEventListener('change', function () {
          V.Jobs.filterWindow = jf.value;
          V.Jobs.load();
        });
      }

      /* 日志 Tab / 过滤 */
      var logTabs = document.getElementById('log-tabs');
      if (logTabs) {
        U.$$('.tab', logTabs).forEach(function (t) {
          t.addEventListener('click', function () {
            V.Logs.switchTab(t.getAttribute('data-tab'));
          });
        });
      }
      var logFilter = document.getElementById('log-filter');
      if (logFilter) {
        logFilter.addEventListener('input', U.debounce(function () { V.Logs.render(); }, 150));
      }
      ['log-window-filter', 'log-status-filter'].forEach(function (id) {
        var n = document.getElementById(id);
        if (n) n.addEventListener('change', function () { V.Logs.render(); });
      });
      var logRefresh = document.getElementById('log-refresh');
      if (logRefresh) {
        logRefresh.addEventListener('click', function () {
          V.Logs.load();
          U.toast('info', '日志已刷新');
        });
      }
      var decRefresh = document.getElementById('dec-refresh');
      if (decRefresh) {
        decRefresh.addEventListener('click', function () {
          if (V.Decisions) V.Decisions.load();
        });
      }

      /* 键盘快捷键 */
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
          if (U.closeTopModal()) return;
          var wiz = document.getElementById('wizard-root');
          if (wiz && wiz.innerHTML) { V.Settings.closeWizard(); }
          return;
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
          e.preventDefault();
          var s = document.getElementById('global-search');
          if (s) s.focus();
          return;
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
          /* 文件编辑器 Ctrl+S 由 filemanager 处理；这里仅兜底阻止默认保存 */
          var editor = document.getElementById('fmp-editor');
          if (editor) e.preventDefault();
        }
      });

      /* 倒计时刷新（任务页） */
      App.tickTimer = setInterval(function () {
        if (App.view !== 'jobs') return;
        U.$$('[data-countdown]').forEach(function (n) {
          n.textContent = U.countdown(n.getAttribute('data-countdown'));
        });
      }, 1000);
    },

    /* ---------------- 启动 ---------------- */
    boot: function () {
      /* SSE 实时事件接上（断线自动重连；退订/分发见 sse.js 与各视图钩子） */
      if (global.SSE) global.SSE.start();
      /* 待决红点初值（后续由 SSE decision.* 实时增减） */
      if (V.Decisions) {
        A.decisions.list('pending', 1).then(function (r) {
          V.Decisions.updateBadge((r && r.pending_count) || 0);
        }).catch(function () { /* 忽略 */ });
      }
      App.refreshStatus().then(function (st) {
        if (st && st.llm_configured === false) {
          /* 首次设置：总览横幅 + 自动打开向导 */
          V.Overview.showBanner(false);
          if (!App.booted) V.Settings.openWizard();
        }
      }).catch(function () { /* 忽略 */ });
      App.statusTimer = setInterval(function () {
        App.refreshStatus();
      }, 15000);
      App.go(App.view || 'overview');
      App.booted = true;
    },

    init: function () {
      App.bindLogin();
      App.bindGlobal();
      global.FileMgr.init();
      if (A.getToken()) {
        App.hideLogin();
        App.boot();
      } else {
        App.showLogin();
      }
    }
  };

  global.App = App;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { App.init(); });
  } else {
    App.init();
  }
})(window);
