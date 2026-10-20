/* DogBuddyCn — 运行台账指示器（P0，v0.10.37）
 * Copyright (C) 2026 DogBuddyCn contributors — AGPL-3.0
 *
 * owner 实测："你在哪跑呢，我咋没看见"。以前控制台**没有任何地方**显示"此刻谁在跑什么"
 * （群组甚至跑起来时工位状态还是 idle），用 API/脚本触发的运行完全不可见。
 *
 * 这里做最小可用的一半：
 *   · 顶栏常驻指示器（有几个在跑 / 有几个在限速暂停）
 *   · 点开抽屉：列出每个运行的工位或群组、类型、阶段、状态、已跑多久、限速暂停次数
 *   · 每行可**取消**（幂等，不算失败，产物保留）
 * 数据来源：进页面拉一次 `GET /api/runs?active=1`，之后靠 SSE `run.*` 事件实时更新。
 */
(function (global) {
  'use strict';
  var U = global.U;
  var A = global.A;

  var Runs = {
    active: [],
    recent: [],
    timer: null,

    /** 进页面时拉一次全量活跃运行（SSE 断线时也有底）。
        v0.10.40：同时拉最近结束的运行——"异常停下后要能唤醒"，用户得先看见它。 */
    refresh: function () {
      if (!A || !A.runs) return Promise.resolve();
      return Promise.all([A.runs.list(true), A.runs.list(false, 8)]).then(function (res) {
        Runs.active = (res[0] && res[0].runs) || [];
        Runs.recent = (res[1] && res[1].runs) || [];
        Runs.render();
      }).catch(function () { /* 台账不可用就当没有（不要打扰用户） */ });
    },

    /** 周期性兜底刷新：SSE 断线/丢帧时不至于一直显示旧状态 */
    startPolling: function (ms) {
      if (Runs.timer) return;
      Runs.timer = setInterval(function () {
        if (global.SSE && global.SSE.state && global.SSE.state.connected) return;
        Runs.refresh();
      }, ms || 15000);
    },

    /** SSE `run.started` / `run.updated` / `run.finished` */
    onEvent: function (etype, p) {
      p = p || {};
      var id = String(p.id || '');
      if (!id) return;
      var idx = -1;
      for (var i = 0; i < Runs.active.length; i++) {
        if (String(Runs.active[i].id) === id) { idx = i; break; }
      }
      var live = p.live !== false && String(p.status) !== 'done'
        && String(p.status) !== 'failed' && String(p.status) !== 'cancelled'
        && String(p.status) !== 'timeout' && String(p.status) !== 'interrupted';
      if (live) {
        if (idx >= 0) Runs.active[idx] = p; else Runs.active.push(p);
      } else if (idx >= 0) {
        Runs.active.splice(idx, 1);
      }
      Runs.render();
    },

    /** 人类可读的时长（运行中的用 started_at 算到现在） */
    elapsed: function (r) {
      var t = U.parseTime(r.started_at);
      if (!t) return '';
      var s = Math.max(0, Math.round((Date.now() - t.getTime()) / 1000));
      if (s < 60) return s + 's';
      if (s < 3600) return Math.floor(s / 60) + 'm' + (s % 60) + 's';
      return Math.floor(s / 3600) + 'h' + Math.floor((s % 3600) / 60) + 'm';
    },

    kindLabel: function (k) {
      return {
        manual: '人工派单', group: '群组', coordinated: '组长派活',
        group_step: '群组步骤', scheduled: '定时任务'
      }[String(k)] || (k || '运行');
    },

    statusLabel: function (s) {
      return {
        queued: '排队中', running: '运行中', paused: '限速暂停',
        cancelling: '正在取消', done: '已完成', failed: '失败',
        cancelled: '已取消', timeout: '超时（后台仍在跑）', interrupted: '进程重启中断',
        /* v0.10.38：有步骤没完成 / v0.10.40：撞迭代上限也是 partial */
        partial: '部分完成（未跑完，可重试）'
      }[String(s)] || (s || '-');
    },

    /** 这些终态可以"唤醒"（v0.10.40：异常停下后管理员能续跑） */
    retryable: function (s) {
      return ['failed', 'timeout', 'interrupted', 'partial', 'cancelled', 'paused']
        .indexOf(String(s)) >= 0;
    },

    /** 指示器 + 抽屉一起重画 */
    render: function () {
      var badge = document.getElementById('top-runs');
      if (badge) {
        var n = Runs.active.length;
        var paused = Runs.active.filter(function (r) { return r.status === 'paused'; }).length;
        if (!n) {
          badge.classList.add('hidden');
        } else {
          badge.classList.remove('hidden');
          badge.textContent = (paused ? '⏸ ' : '▶ ') + n + ' 个任务在跑'
            + (paused ? '（' + paused + ' 个限速暂停）' : '');
        }
      }
      var box = document.getElementById('runs-drawer');
      if (box && !box.classList.contains('hidden')) Runs.renderDrawer();
    },

    renderDrawer: function () {
      var box = document.getElementById('runs-drawer');
      if (!box) return;
      var html = '';
      if (!Runs.active.length) {
        html += '<div class="runs-empty">当前没有正在跑的任务。</div>';
      } else {
        html += Runs.active.map(function (r) {
          var who = r.window_name || r.group_name || r.window_id || r.group_id || '-';
          var pauseInfo = r.pause_count
            ? ' · 限速暂停 ' + r.pause_count + ' 次' + (r.paused_seconds ? '（共 ' + Math.round(r.paused_seconds) + 's）' : '')
            : '';
          return '<div class="run-row" data-run="' + U.esc(r.id) + '">' +
            '<div class="run-main">' +
            '<div class="run-title"><b>' + U.esc(who) + '</b>' +
            '<span class="chip chip-grey">' + U.esc(Runs.kindLabel(r.kind)) + '</span>' +
            '<span class="chip ' + (r.status === 'paused' ? 'chip-amber' : 'chip-blue') + '">' +
            U.esc(Runs.statusLabel(r.status)) + '</span>' +
            (r.phase ? '<span class="chip">' + U.esc(r.phase) + '</span>' : '') +
            '</div>' +
            '<div class="run-sub mono">' + U.esc(r.id) + ' · ' + Runs.elapsed(r) +
            pauseInfo + ' · ' + U.esc((r.summary || '').slice(0, 80)) + '</div>' +
            '</div>' +
            '<button class="btn btn-danger btn-sm" data-run-cancel="' + U.esc(r.id) + '">取消</button>' +
            '</div>';
        }).join('');
      }
      /* v0.10.40：最近结束的运行——"异常停下后要能唤醒"，先让它看得见 */
      var recent = (Runs.recent || []).filter(function (r) {
        return String(r.status) !== 'done';
      }).slice(0, 6);
      if (recent.length) {
        html += '<div class="runs-empty" style="margin-top:8px">最近结束（可唤醒）</div>' +
          recent.map(function (r) {
            var who = r.window_name || r.group_name || r.window_id || r.group_id || '-';
            var canRetry = Runs.retryable(r.status);
            return '<div class="run-row" data-run="' + U.esc(r.id) + '">' +
              '<div class="run-main">' +
              '<div class="run-title"><b>' + U.esc(who) + '</b>' +
              '<span class="chip chip-grey">' + U.esc(Runs.kindLabel(r.kind)) + '</span>' +
              '<span class="chip chip-amber">' + U.esc(Runs.statusLabel(r.status)) + '</span>' +
              '</div>' +
              '<div class="run-sub mono">' + U.esc(r.id) + ' · ' +
              U.esc((r.summary || '').slice(0, 90)) + '</div></div>' +
              (canRetry
                ? '<button class="btn btn-soft btn-sm" data-run-retry="' + U.esc(r.id) + '">重试</button>'
                : '') +
              '</div>';
          }).join('');
      }
      box.innerHTML = html;
    },

    toggleDrawer: function () {
      var box = document.getElementById('runs-drawer');
      if (!box) return;
      box.classList.toggle('hidden');
      if (!box.classList.contains('hidden')) {
        Runs.refresh().then(Runs.renderDrawer);
      }
    },

    cancel: function (id, btn) {
      if (!A || !A.runs) return;
      if (btn) { btn.disabled = true; btn.textContent = '取消中…'; }
      A.runs.cancel(id).then(function () {
        U.toast('ok', '已取消 ' + id + '（产物保留，不算失败）');
        return Runs.refresh();
      }).catch(function (e) {
        U.toast('warn', '取消失败：' + (e && e.message ? e.message : e));
        if (btn) { btn.disabled = false; btn.textContent = '取消'; }
      });
    },

    /** 挂事件（启动时调用一次） */
    bind: function () {
      var badge = document.getElementById('top-runs');
      if (badge) badge.addEventListener('click', Runs.toggleDrawer);
      var box = document.getElementById('runs-drawer');
      if (box) {
        box.addEventListener('click', function (ev) {
          var c = ev.target.closest('[data-run-cancel]');
          if (c) { Runs.cancel(c.getAttribute('data-run-cancel'), c); return; }
          /* v0.10.40：唤醒未完成的运行（异常停下之后） */
          var r = ev.target.closest('[data-run-retry]');
          if (r) Runs.retry(r.getAttribute('data-run-retry'), r);
        });
      }
      Runs.refresh();
      Runs.startPolling(15000);
    },

    /* v0.10.40：重试 = 先停掉卡住的那次，再用**落库的原始任务文本**重跑一次 */
    retry: function (id, btn) {
      if (!A || !A.runs || !A.runs.retry) return;
      if (btn) { btn.disabled = true; btn.textContent = '唤醒中…'; }
      A.runs.retry(id).then(function (d) {
        var note = (d && d.note) ? d.note : '已重新发起';
        U.toast('ok', note);
        return Runs.refresh();
      }).catch(function (e) {
        U.toast('warn', '唤醒失败：' + (e && e.message ? e.message : e));
        if (btn) { btn.disabled = false; btn.textContent = '重试'; }
      });
    }
  };

  // 视图命名空间约定（test_frontend_wiring 会校验）：
  global.DogBuddyViews = global.DogBuddyViews || {};
  global.DogBuddyViews.Runs = Runs;
  global.Runs = Runs;          // 短名（app.js 里直接用它绑事件）
})(window);
