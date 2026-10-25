/* ============================================================
   DogBuddyCn 控制台 · views-costs.js（v0.11.0）
   成本页：每一次 LLM 调用的钱 · 按工位/模型/天 · 单价可改（历史按当时价）
   —— 工业自动化的第一步：先把"钱"变成可查的数字
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var state = { days: 7, summary: {}, prices: [], loaded: false };

  function money(v) {
    var n = Number(v || 0);
    if (!n) return '0';
    return n < 0.01 ? n.toFixed(4) : n.toFixed(2);
  }

  function stat(label, value, sub) {
    return '<div class="card stat"><div class="card-body">' +
      '<div class="stat-label">' + U.esc(label) + '</div>' +
      '<div class="stat-value mono">' + U.esc(String(value)) + '</div>' +
      (sub ? '<div class="stat-sub">' + U.esc(sub) + '</div>' : '') +
      '</div></div>';
  }

  function table(headers, rows, empty) {
    if (!rows.length) return '<p class="muted">' + U.esc(empty || '暂无数据') + '</p>';
    return '<table class="table"><thead><tr>' +
      headers.map(function (h) { return '<th>' + U.esc(h) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + rows.join('') + '</tbody></table>';
  }

  function renderStats() {
    var host = U.$('#cost-stats');
    if (!host) return;
    var s = state.summary || {};
    var today = s.today || {}, total = s.total || {};
    host.innerHTML =
      stat('今日', money(today.amount), (today.calls || 0) + ' 次调用') +
      stat('近 ' + (s.days || state.days) + ' 天', money(total.amount),
           (total.calls || 0) + ' 次调用') +
      stat('Token（近 ' + (s.days || state.days) + ' 天）',
           ((total.in_tokens || 0) + (total.out_tokens || 0)).toLocaleString(),
           '缓存命中 ' + (total.cache_tokens || 0).toLocaleString()) +
      stat('已配单价', (s.priced_models || 0) + ' 条', '改价留审计，历史不回算');
  }

  function renderWindows() {
    var host = U.$('#cost-windows');
    if (!host) return;
    var rows = (state.summary.by_window || []).map(function (b) {
      return '<tr><td class="mono">' + U.esc(b.bucket || '（未知）') + '</td>' +
        '<td>' + (b.calls || 0) + '</td>' +
        '<td class="mono">' + ((b.in_tokens || 0) + (b.out_tokens || 0)).toLocaleString() + '</td>' +
        '<td class="mono">' + U.esc(money(b.amount)) + '</td></tr>';
    });
    host.innerHTML = '<h3 class="card-title">按工位</h3>' +
      table(['工位', '调用', 'Token', '金额'], rows, '这段时间还没有记账');
  }

  function renderPrices() {
    var host = U.$('#cost-models');
    if (!host) return;
    var rows = (state.prices || []).map(function (p) {
      var pending = /待校准/.test(String(p.note || ''));
      return '<tr><td class="mono">' + U.esc(p.id || p.model) + '</td>' +
        '<td class="mono">' + U.esc(String(p.in_price || 0)) + '</td>' +
        '<td class="mono">' + U.esc(String(p.out_price || 0)) + '</td>' +
        '<td class="mono">' + U.esc(String(p.cache_price || 0)) + '</td>' +
        '<td>' + (pending ? '<span class="badge badge-amber">待校准</span>' : '') +
        (p.enabled ? '' : ' <span class="badge badge-grey">停用</span>') + '</td>' +
        '<td><button class="btn btn-soft btn-sm" data-price="' + U.esc(p.id) + '">改价</button></td></tr>';
    });
    host.innerHTML = '<h3 class="card-title">单价表 <span class="card-sub">（每 1K token；' +
      '改价只影响之后的调用）</span></h3>' +
      table(['条目', '输入', '输出', '缓存命中', '状态', ''], rows,
            '单价表是空的 —— 点右上角「按目录生成占位价」，或先只统计用量');
    U.$$('#cost-models [data-price]').forEach(function (btn) {
      btn.addEventListener('click', function () { editPrice(btn.getAttribute('data-price')); });
    });
  }

  function editPrice(id) {
    var p = (state.prices || []).filter(function (x) { return String(x.id) === String(id); })[0] || {};
    U.modal({
      title: '改单价 · ' + (p.id || id),
      body: '<div class="card-sub" style="margin-bottom:8px">单位：每 1K token（视觉模型 ' +
        'unit=image 时输入价=每张图）。' +
        '<b>改价不影响历史账本</b>——每条记录都存了当时的单价。</div>' +
        '<div class="field"><span class="field-label">输入价</span>' +
        '<input class="input mono" id="cost-in" value="' + U.esc(String(p.in_price || 0)) + '"></div>' +
        '<div class="field"><span class="field-label">输出价</span>' +
        '<input class="input mono" id="cost-out" value="' + U.esc(String(p.out_price || 0)) + '"></div>' +
        '<div class="field"><span class="field-label">缓存命中价（可 0）</span>' +
        '<input class="input mono" id="cost-cache" value="' + U.esc(String(p.cache_price || 0)) + '"></div>' +
        '<div class="field"><span class="field-label">备注</span>' +
        '<input class="input" id="cost-note" value="' + U.esc(String(p.note || '')) + '"></div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        {
          label: '保存', cls: 'btn-primary',
          onClick: function (body) {
            var payload = {
              provider: p.provider || '', model: p.model || id,
              unit: p.unit || 'token',
              in_price: Number(body.querySelector('#cost-in').value || 0),
              out_price: Number(body.querySelector('#cost-out').value || 0),
              cache_price: Number(body.querySelector('#cost-cache').value || 0),
              note: body.querySelector('#cost-note').value || '',
              enabled: true
            };
            if (payload.in_price < 0 || payload.out_price < 0 || payload.cache_price < 0) {
              U.toast('error', '单价不能为负');
              return false;
            }
            A.costs.putPrice(id, payload).then(function () {
              U.toast('success', '已保存（改价已留审计）');
              return load();
            }).catch(function (e) { U.toast('error', e.message); return false; });
            return true;
          }
        }
      ]
    });
  }

  function load() {
    A.costs.summary(state.days).then(function (s) {
      state.summary = s || {};
      state.loaded = true;
      renderStats();
      renderWindows();
      var hint = U.$('#cost-hint');
      if (hint) {
        hint.innerHTML = (s && s.priced_models)
          ? '金额 = miss×输入价 + 命中×(输入价−缓存价) + 输出×输出价（每 1K token）；' +
            '单价若标了「待校准」，请按真实账单校准一次。'
          : '⚠️ 单价表为空，金额会一直是 0（token 用量照常记录）。点右上角「按目录生成占位价」先建立贵/便宜排序。';
      }
    }).catch(function (e) { U.toast('error', e.message); });
    A.costs.prices().then(function (r) {
      state.prices = (r && r.prices) || [];
      renderPrices();
    }).catch(function () { /* 成本页不因为单价表失败而整页白 */ });
  }

  function seed() {
    A.costs.seed().then(function (r) {
      U.toast('success', '生成占位价 新增 ' + ((r || {}).created || 0) +
        ' 条，跳过 ' + ((r || {}).skipped || 0) + ' 条（已存在不覆盖）', 6000);
      load();
    }).catch(function (e) { U.toast('error', e.message); });
  }

  function bind() {
    var rf = U.$('#cost-refresh'); if (rf) rf.addEventListener('click', load);
    var sd = U.$('#cost-seed'); if (sd) sd.addEventListener('click', seed);
  }

  global.DogBuddyViews.Costs = { load: load, bind: bind, state: state, seed: seed };
})(window);
