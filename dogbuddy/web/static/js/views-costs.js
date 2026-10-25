/* ============================================================
   DogBuddyCn 控制台 · views-costs.js（v0.11.0）
   成本页：每一次 LLM 调用的钱 · 按工位/模型/天 · 单价可改（历史按当时价）
   —— 工业自动化的第一步：先把"钱"变成可查的数字
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var state = { days: 7, summary: {}, prices: [], budgets: [], loaded: false };

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

  /* ---------------- v0.11.1 预算 ---------------- */
  var SCOPE_LABEL = { global: '全局', group: '群组', job: '定时任务',
                      window: '工位', run: '单次运行' };

  function bar(ratio, level) {
    var pct = Math.min(100, Math.round((ratio || 0) * 100));
    var cls = level === 'exceeded' ? 'err' : (level === 'warn' ? 'warn' : 'ok');
    return '<div class="cost-bar"><div class="cost-bar-fill cost-bar-' + cls +
      '" style="width:' + pct + '%"></div></div>';
  }

  function renderBudgets() {
    var host = U.$('#cost-budgets');
    if (!host) return;
    var rows = (state.budgets || []).map(function (b) {
      return '<tr><td>' + U.esc(SCOPE_LABEL[b.scope_type] || b.scope_type) +
        (b.scope_id ? ' <span class="mono card-sub">' + U.esc(b.scope_id) + '</span>' : '') +
        '</td>' +
        '<td class="mono">' + U.esc(String(b.limit_amount)) + '</td>' +
        '<td class="mono">' + U.esc(money(b.spent)) + '</td>' +
        '<td style="min-width:120px">' + bar(b.ratio, b.level) + '</td>' +
        '<td>' + U.esc(b.strategy) + ' · ' + U.esc(b.period) + '</td>' +
        '<td><button class="btn btn-soft btn-sm" data-budget="' + U.esc(b.id) + '">改</button> ' +
        '<button class="btn btn-ghost btn-sm" data-budget-del="' + U.esc(b.id) + '">删</button></td></tr>';
    });
    host.innerHTML = '<h3 class="card-title">预算 <span class="card-sub">（超限走 降档→排队→暂停，' +
      '任何降级都留审计）</span></h3>' +
      (rows.length
        ? '<table class="table"><thead><tr><th>作用域</th><th>上限</th><th>已用</th>' +
          '<th>进度</th><th>策略</th><th></th></tr></thead><tbody>' + rows.join('') +
          '</tbody></table>'
        : '<p class="muted">还没设预算 —— 不设就是不限（金额照常记账）。</p>');
    U.$$('#cost-budgets [data-budget]').forEach(function (btn) {
      btn.addEventListener('click', function () { editBudget(btn.getAttribute('data-budget')); });
    });
    U.$$('#cost-budgets [data-budget-del]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-budget-del');
        if (!window.confirm('删除预算 ' + id + '？')) return;
        A.costs.deleteBudget(id).then(function () { U.toast('success', '已删除'); load(); })
          .catch(function (e) { U.toast('error', e.message); });
      });
    });
  }

  function editBudget(id) {
    var b = (state.budgets || []).filter(function (x) { return String(x.id) === String(id); })[0] || {};
    var scopes = Object.keys(SCOPE_LABEL);
    U.modal({
      title: '预算 · ' + (id || '新增'),
      body: '<div class="card-sub" style="margin-bottom:8px">' +
        '超限后：<b>downgrade</b> 换更便宜的模型（单价表里挑）→ 没有更便宜的就停；' +
        '<b>queue</b> 排队到下个周期；<b>pause</b> 直接暂停本轮。' +
        '只有 <b>用完</b> 才拦，到 80% 只在成本页提示。</div>' +
        '<div class="field"><span class="field-label">作用域</span>' +
        '<select class="select" id="bg-scope">' + scopes.map(function (s) {
          return '<option value="' + s + '"' + (b.scope_type === s ? ' selected' : '') +
            '>' + SCOPE_LABEL[s] + '（' + s + '）</option>';
        }).join('') + '</select></div>' +
        '<div class="field"><span class="field-label">scope_id（global 留空）</span>' +
        '<input class="input mono" id="bg-sid" value="' + U.esc(b.scope_id || '') + '"></div>' +
        '<div class="field"><span class="field-label">周期</span>' +
        '<select class="select" id="bg-period">' +
        '<option value="day"' + (b.period === 'day' ? ' selected' : '') + '>每天</option>' +
        '<option value="week"' + (b.period === 'week' ? ' selected' : '') + '>每周</option>' +
        '</select></div>' +
        '<div class="field"><span class="field-label">上限（金额）</span>' +
        '<input class="input mono" id="bg-limit" value="' + U.esc(String(b.limit_amount || 1)) + '"></div>' +
        '<div class="field"><span class="field-label">超限策略</span>' +
        '<select class="select" id="bg-strategy">' +
        ['downgrade', 'queue', 'pause'].map(function (s) {
          return '<option value="' + s + '"' + (b.strategy === s ? ' selected' : '') + '>' + s + '</option>';
        }).join('') + '</select></div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        {
          label: '保存', cls: 'btn-primary',
          onClick: function (body) {
            var scope = body.querySelector('#bg-scope').value;
            var sid = body.querySelector('#bg-sid').value.trim();
            var payload = {
              scope_type: scope, scope_id: scope === 'global' ? '' : sid,
              period: body.querySelector('#bg-period').value,
              limit_amount: Number(body.querySelector('#bg-limit').value || 0),
              strategy: body.querySelector('#bg-strategy').value,
              enabled: true
            };
            if (scope !== 'global' && !payload.scope_id) {
              U.toast('error', '这个作用域必须填 scope_id');
              return false;
            }
            if (!(payload.limit_amount > 0)) { U.toast('error', '上限要大于 0'); return false; }
            var bid = id || (scope + ':' + (payload.scope_id || '*'));
            A.costs.putBudget(bid, payload).then(function () {
              U.toast('success', '已保存（预算变更已留审计）');
              return load();
            }).catch(function (e) { U.toast('error', e.message); return false; });
            return true;
          }
        }
      ]
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
    A.costs.budgets().then(function (r) {
      state.budgets = (r && r.budgets) || [];
      renderBudgets();
    }).catch(function () { /* 预算表失败也不拦页面 */ });
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
