/* DogBuddyCn — review records page (0.26.1)
 * Copyright (C) DogBuddyCn contributors — AGPL-3.0
 */
(function (global) {
  'use strict';
  var U = global.U;
  var A = global.A;

  var Reviews = {
    rows: [],

    load: function () {
      if (!A || !A.reviews) return Promise.resolve();
      return A.reviews.list('', 100).then(function (d) {
        Reviews.rows = (d && d.reviews) || [];
        Reviews.render(d && d.summary);
      }).catch(function (e) {
        Reviews.renderError(e);
      });
    },

    label: function (r) {
      return {
        queued: '排队', running: '审核中', waiting_human: '待人工',
        passed: '通过', failed: '未通过', error: '审核异常',
        overridden: '人工放行'
      }[String(r.status)] || (r.status || '-');
    },

    render: function (summary) {
      var box = document.getElementById('reviews-list');
      var meta = document.getElementById('reviews-meta');
      if (meta) {
        meta.textContent = '审核 ' + ((summary && summary.total) || Reviews.rows.length) +
          ' · 待人工 ' + ((summary && summary.waiting) || 0);
      }
      if (!box) return;
      if (!Reviews.rows.length) {
        box.innerHTML = '<div class="card"><div class="card-sub">还没有审核记录。' +
          '开启 review.mode=shadow 并配置 reviewer 后，任务结束会在这里出现。</div></div>';
        return;
      }
      box.innerHTML = Reviews.rows.map(function (r) {
        var canOverride = ['failed', 'error', 'waiting_human'].indexOf(String(r.status)) >= 0;
        var verdict = r.verdict ? (' · ' + U.esc(r.verdict)) : '';
        return '<div class="card" style="margin-bottom:8px">' +
          '<div class="run-row">' +
          '<div class="run-main">' +
          '<div class="run-title"><b>' + U.esc(r.work_item_id || r.id) + '</b>' +
          '<span class="chip chip-grey">' + U.esc(Reviews.label(r)) + verdict + '</span></div>' +
          '<div class="run-sub mono">' + U.esc(r.id) + ' · run ' + U.esc(r.run_id || '-') +
          ' · ' + U.esc(r.created_at || '') + '</div>' +
          '<div class="run-sub">' + U.esc((r.summary || '').slice(0, 300)) + '</div>' +
          '</div>' +
          (canOverride
            ? '<button class="btn btn-soft btn-sm" data-review-override="' + U.esc(r.id) + '">人工放行</button>'
            : '') +
          '</div></div>';
      }).join('');
    },

    renderError: function (e) {
      var box = document.getElementById('reviews-list');
      if (box) box.innerHTML = '<div class="card"><div class="card-sub">' +
        '审核记录加载失败：' + U.esc(e && e.message ? e.message : e) + '</div></div>';
    },

    override: function (id, btn) {
      var reason = global.prompt('人工放行原因（会写入审计）：');
      if (!reason || !String(reason).trim()) return;
      if (btn) { btn.disabled = true; btn.textContent = '放行中…'; }
      A.reviews.override(id, String(reason).trim()).then(function () {
        U.toast('success', '已人工放行 ' + id);
        return Reviews.load();
      }).catch(function (e) {
        U.toast('warn', '放行失败：' + (e && e.message ? e.message : e));
        if (btn) { btn.disabled = false; btn.textContent = '人工放行'; }
      });
    },

    bind: function () {
      var refresh = document.getElementById('reviews-refresh');
      if (refresh) refresh.addEventListener('click', function () { Reviews.load(); });
      var box = document.getElementById('reviews-list');
      if (box) {
        box.addEventListener('click', function (ev) {
          var b = ev.target.closest('[data-review-override]');
          if (b) Reviews.override(b.getAttribute('data-review-override'), b);
        });
      }
    }
  };

  global.DogBuddyViews = global.DogBuddyViews || {};
  global.DogBuddyViews.Reviews = Reviews;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', Reviews.bind);
  } else {
    Reviews.bind();
  }
})(window);
