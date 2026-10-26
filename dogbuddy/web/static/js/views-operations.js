/* ============================================================
   DogBuddyCn 控制台 · views-operations.js（v0.14.0）
   运营：目标 → 回合 → 日志 → 实验
   —— 每一轮都能回答"假设是什么、做了什么、效果如何、下一步换不换"
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var state = { goals: [], journal: [], experiments: [], loaded: false };

  function num(v) {
    if (v === null || v === undefined || v === '') return '—';
    return Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 });
  }

  function rel(ts) { return ts ? U.relTime(ts) : '—'; }

  function stat(label, value, sub) {
    return '<div class="card stat"><div class="card-body">' +
      '<div class="stat-label">' + U.esc(label) + '</div>' +
      '<div class="stat-value mono">' + U.esc(String(value)) + '</div>' +
      (sub ? '<div class="stat-sub">' + U.esc(sub) + '</div>' : '') +
      '</div></div>';
  }

  function renderStats() {
    var host = U.$('#ops-stats');
    if (!host) return;
    var running = state.journal.filter(function (j) { return j.status === 'running'; }).length;
    var keeps = state.journal.filter(function (j) { return j.verdict === 'keep'; }).length;
    var blocked = state.journal.filter(function (j) { return j.status === 'blocked'; }).length;
    var wins = state.experiments.filter(function (e) { return e.status === 'won'; }).length;
    host.innerHTML =
      stat('目标', state.goals.length,
           state.goals.filter(function (g) { return g.enabled; }).length + ' 个启用') +
      stat('回合', state.journal.length, running + ' 个在跑') +
      stat('判定为有效', keeps, blocked + ' 个被预算/执行拦下') +
      stat('实验', state.experiments.length, wins + ' 个已达标');
  }

  function goalButtons(g) {
    return '<button class="btn btn-soft btn-sm" data-goal-plan="' + U.esc(g.id) + '">计划</button> ' +
      '<button class="btn btn-primary btn-sm" data-goal-run="' + U.esc(g.id) + '">跑一轮</button> ' +
      '<button class="btn btn-ghost btn-sm" data-goal-edit="' + U.esc(g.id) + '">改</button> ' +
      '<button class="btn btn-ghost btn-sm" data-goal-del="' + U.esc(g.id) + '">删</button>';
  }

  function renderGoals() {
    var host = U.$('#ops-goals');
    if (!host) return;
    var rows = (state.goals || []).map(function (g) {
      var metric = g.metric_name
        ? U.esc(g.metric_name) + (g.metric_unit ? '（' + U.esc(g.metric_unit) + '）' : '')
        : '<span class="badge badge-amber">未设指标</span>';
      var target = (g.target === null || g.target === undefined || g.target === '')
        ? '—' : num(g.target);
      return '<tr>' +
        '<td><b>' + U.esc(g.name) + '</b>' +
        (g.description ? '<div class="card-sub">' + U.esc(g.description) + '</div>' : '') + '</td>' +
        '<td>' + metric + '</td>' +
        '<td class="mono">' + (g.direction === 'down' ? '↓' : '↑') + ' ' + target + '</td>' +
        '<td class="mono">' + U.esc(g.pipeline_id || '—') + '</td>' +
        '<td>' + U.esc(g.cadence || 'manual') + '</td>' +
        '<td class="hint">' + rel(g.last_round_at) + '</td>' +
        '<td>' + goalButtons(g) + '</td></tr>';
    });
    host.innerHTML = '<div class="card-head"><div><h3 class="card-title">运营目标</h3>' +
      '<p class="card-sub">目标只描述"要什么"；具体动作由每一轮计划给出，执行交给流水线。</p></div>' +
      '<button class="btn btn-primary btn-sm" id="ops-new-goal">新建目标</button></div>' +
      (rows.length
        ? '<table class="table"><thead><tr><th>目标</th><th>指标</th><th>目标值</th>' +
          '<th>执行流水线</th><th>节奏</th><th>上一轮</th><th></th></tr></thead><tbody>' +
          rows.join('') + '</tbody></table>'
        : '<p class="muted">还没有目标。先建一个（例如"站点周访问量"），再配一条执行流水线。</p>');

    var add = U.$('#ops-new-goal');
    if (add) add.addEventListener('click', function () { editGoal(''); });
    U.$$('#ops-goals [data-goal-plan]').forEach(function (b) {
      b.addEventListener('click', function () { showPlan(b.getAttribute('data-goal-plan')); });
    });
    U.$$('#ops-goals [data-goal-run]').forEach(function (b) {
      b.addEventListener('click', function () { runRound(b.getAttribute('data-goal-run')); });
    });
    U.$$('#ops-goals [data-goal-edit]').forEach(function (b) {
      b.addEventListener('click', function () { editGoal(b.getAttribute('data-goal-edit')); });
    });
    U.$$('#ops-goals [data-goal-del]').forEach(function (b) {
      b.addEventListener('click', function () {
        var id = b.getAttribute('data-goal-del');
        if (!window.confirm('删除目标 ' + id + '？日志不会删（留作历史）。')) return;
        A.ops.deleteGoal(id).then(function () { U.toast('success', '已删除'); load(); })
          .catch(function (e) { U.toast('error', e.message); });
      });
    });
  }

  function renderJournal() {
    var host = U.$('#ops-journal');
    if (!host) return;
    var rows = (state.journal || []).map(function (j) {
      var v = j.verdict || '—';
      var cls = v === 'keep' ? 'badge-green'
        : (v === 'adjust' ? 'badge-amber' : (v === 'unknown' ? 'badge-grey' : 'badge-red'));
      return '<tr><td class="hint">' + rel(j.started_at) + '</td>' +
        '<td class="mono">' + U.esc(j.goal_id) + '</td>' +
        '<td class="mono">#' + (j.round_no || 0) + '</td>' +
        '<td><span class="badge ' + (j.status === 'done' ? 'badge-green' : 'badge-amber') + '">' +
        U.esc(j.status) + '</span></td>' +
        '<td><span class="badge ' + cls + '">' + U.esc(v) + '</span></td>' +
        '<td class="mono">' + num(j.metric_before) + ' → ' + num(j.metric_after) + '</td>' +
        '<td class="mono">' + (j.delta === null || j.delta === undefined ? '—'
          : (j.delta > 0 ? '+' : '') + num(j.delta)) + '</td>' +
        '<td title="' + U.esc(j.notes || '') + '">' + U.esc((j.action || '').slice(0, 42)) +
        ((j.action || '').length > 42 ? '…' : '') + '</td></tr>';
    });
    host.innerHTML = '<h3 class="card-title">回合日志 <span class="card-sub">（预期 → 实测 → 判定；' +
      '读不到指标就记 unknown，不拿 0 冒充）</span></h3>' +
      (rows.length
        ? '<table class="table"><thead><tr><th>时间</th><th>目标</th><th>轮次</th>' +
          '<th>状态</th><th>判定</th><th>指标</th><th>Δ</th><th>动作</th></tr></thead><tbody>' +
          rows.join('') + '</tbody></table>'
        : '<p class="muted">还没有回合。点目标行的「计划」看它准备做什么。</p>');
  }

  function renderExperiments() {
    var host = U.$('#ops-experiments');
    if (!host) return;
    var rows = (state.experiments || []).map(function (e) {
      var cls = e.status === 'won' ? 'badge-green'
        : (e.status === 'lost' ? 'badge-red' : 'badge-amber');
      return '<tr><td class="mono">' + U.esc(e.id) + '</td>' +
        '<td class="mono">' + U.esc(e.goal_id) + '</td>' +
        '<td class="mono">v' + (e.variant || 1) + '</td>' +
        '<td><span class="badge ' + cls + '">' + U.esc(e.status) + '</span></td>' +
        '<td class="mono">' + num(e.metric_before) + ' → ' + num(e.metric_after) + '</td>' +
        '<td class="hint">' + U.esc((e.hypothesis || '').slice(0, 40)) + '</td></tr>';
    });
    host.innerHTML = '<h3 class="card-title">实验（变体）<span class="card-sub">' +
      '（keep 继续放大，adjust/stop 判负并换新变体）</span></h3>' +
      (rows.length
        ? '<table class="table"><thead><tr><th>实验</th><th>目标</th><th>变体</th>' +
          '<th>状态</th><th>指标</th><th>假设</th></tr></thead><tbody>' +
          rows.join('') + '</tbody></table>'
        : '<p class="muted">还没有实验记录。</p>');
  }

  function render() { renderStats(); renderGoals(); renderJournal(); renderExperiments(); }

  /* ---------------- 计划 / 开跑 ---------------- */
  function showPlan(gid) {
    A.ops.plan(gid).then(function (p) {
      var g = p.goal || {};
      U.modal({
        title: '第 ' + (p.round_no || 1) + ' 轮计划 · ' + (g.name || gid),
        wide: true,
        body: '<div class="card-sub" style="margin-bottom:10px">' +
          '当前指标：<b>' + U.esc(g.metric_name || '未设') + '</b> = ' +
          U.esc(num(p.metric_before)) + '；目标 ' + U.esc(num(g.target)) +
          '（' + (g.direction === 'down' ? '越低越好' : '越高越好') + '）</div>' +
          '<div class="field"><span class="field-label">假设</span>' +
          '<div>' + U.esc(p.hypothesis) + '</div></div>' +
          '<div class="field"><span class="field-label">这一轮的动作</span>' +
          '<div>' + U.esc(p.action) + '</div></div>' +
          '<div class="field"><span class="field-label">验收口径</span>' +
          '<div>' + U.esc(p.acceptance) + '</div></div>' +
          '<div class="hint">开跑后由流水线执行；指标变化后自动判定 keep / adjust，' +
          '读不到指标就记 unknown。</div>',
        buttons: [
          { label: '关掉', cls: 'btn-ghost' },
          {
            label: '按此开跑', cls: 'btn-primary',
            onClick: function () {
              A.ops.startRound(gid, true).then(function (r) {
                var j = (r || {}).journal || {};
                U.toast(j.status === 'blocked' ? 'warn' : 'success',
                        j.status === 'blocked' ? '预算拦下：' + (j.notes || '')
                          : '回合已开始（' + (j.status || '') + '）', 7000);
                load();
                return true;
              }).catch(function (e) { U.toast('error', e.message); return false; });
            }
          }
        ]
      });
    }).catch(function (e) { U.toast('error', e.message); });
  }

  function runRound(gid) {
    if (!window.confirm('立刻跑一轮？会消耗预算（受预算闸门约束）。')) return;
    A.ops.startRound(gid, true).then(function (r) {
      var j = (r || {}).journal || {};
      U.toast(j.status === 'blocked' ? 'warn' : 'success',
              j.status === 'blocked' ? '预算拦下：' + (j.notes || '')
                : '已发起：' + (j.status || ''), 7000);
      load();
    }).catch(function (e) { U.toast('error', e.message); });
  }

  /* ---------------- 目标编辑 ---------------- */
  function editGoal(gid) {
    var g = (state.goals || []).filter(function (x) { return String(x.id) === String(gid); })[0] || {};
    U.modal({
      title: gid ? '改目标 · ' + gid : '新建运营目标',
      wide: true,
      body: '<div class="field"><span class="field-label">名称</span>' +
        '<input class="input" id="og-name" value="' + U.esc(g.name || '') + '"></div>' +
        '<div class="field"><span class="field-label">说明（可选）</span>' +
        '<input class="input" id="og-desc" value="' + U.esc(g.description || '') + '"></div>' +
        '<div class="field"><span class="field-label">指标名（边缘上报的名字，如 site.visits）</span>' +
        '<input class="input mono" id="og-metric" value="' + U.esc(g.metric_name || '') + '"></div>' +
        '<div class="field"><span class="field-label">单位</span>' +
        '<input class="input mono" id="og-unit" value="' + U.esc(g.metric_unit || '') + '"></div>' +
        '<div class="field"><span class="field-label">方向</span>' +
        '<select class="select" id="og-dir">' +
        '<option value="up"' + (g.direction === 'down' ? '' : ' selected') + '>越高越好（up）</option>' +
        '<option value="down"' + (g.direction === 'down' ? ' selected' : '') + '>越低越好（down）</option>' +
        '</select></div>' +
        '<div class="field"><span class="field-label">目标值</span>' +
        '<input class="input mono" id="og-target" value="' +
        U.esc(g.target === null || g.target === undefined ? '' : g.target) + '"></div>' +
        '<div class="field"><span class="field-label">节奏（只做标记，不会自动开跑）</span>' +
        '<select class="select" id="og-cadence">' +
        ['manual', 'daily', 'weekly'].map(function (c) {
          return '<option value="' + c + '"' + ((g.cadence || 'manual') === c ? ' selected' : '') +
            '>' + c + '</option>';
        }).join('') + '</select></div>' +
        '<div class="field"><span class="field-label">执行流水线 id（可留空：只记计划）</span>' +
        '<input class="input mono" id="og-pipe" value="' + U.esc(g.pipeline_id || '') + '"></div>' +
        '<div class="hint">动作里的 <code>{goal_name}</code> / <code>{action}</code> / ' +
        '<code>{round_no}</code> 会被替换成这一轮的内容。</div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        {
          label: '保存', cls: 'btn-primary',
          onClick: function (body) {
            var id = gid || ('goal-' + Date.now().toString(36));
            var body_ = {
              name: body.querySelector('#og-name').value.trim(),
              description: body.querySelector('#og-desc').value.trim(),
              metric_name: body.querySelector('#og-metric').value.trim(),
              metric_unit: body.querySelector('#og-unit').value.trim(),
              direction: body.querySelector('#og-dir').value,
              target: body.querySelector('#og-target').value,
              cadence: body.querySelector('#og-cadence').value,
              pipeline_id: body.querySelector('#og-pipe').value.trim(),
              enabled: true
            };
            if (!body_.name) { U.toast('error', '名称必填'); return false; }
            A.ops.putGoal(id, body_).then(function () {
              U.toast('success', '已保存');
              load();
              return true;
            }).catch(function (e) { U.toast('error', e.message); return false; });
          }
        }
      ]
    });
  }

  /* ---------------- 入口 ---------------- */
  function load() {
    A.ops.goals().then(function (r) {
      state.goals = (r && r.goals) || [];
      state.loaded = true;
      render();
    }).catch(function (e) { U.toast('error', e.message); });
    A.ops.journal(40).then(function (r) {
      state.journal = (r && r.journal) || [];
      render();
    }).catch(function () { /* 日志挂了不影响目标表 */ });
    A.ops.experiments(40).then(function (r) {
      state.experiments = (r && r.experiments) || [];
      render();
    }).catch(function () {});
  }

  function bind() {
    var rf = U.$('#ops-refresh');
    if (rf) rf.addEventListener('click', load);
    var nt = U.$('#ops-new-goal');
    if (nt) nt.addEventListener('click', function () { editGoal(''); });
  }

  global.DogBuddyViews.Operations = { load: load, bind: bind, state: state };
})(window);
