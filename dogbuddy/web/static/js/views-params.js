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
   * 用量条 HTML：ratio 填充，超过 threshold 变橙，超过 0.95 变红。
   * @param {Object} u GET /api/windows/{id}/usage 的响应
   * @returns {string} 用量条 HTML
   */
  function usageHTML(u) {
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
