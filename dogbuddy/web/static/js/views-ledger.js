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
