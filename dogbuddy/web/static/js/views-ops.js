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
      /* v0.10.43：拉 1000 条（原来 200）——只在浏览器里过滤 200 条时，更早的
         记录永远筛不到（owner："筛选不管用"） */
      return Promise.all([A.logs.audit(1000), A.logs.runs(200), winP]).then(function (res) {
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
          /* v0.10.43：按工位筛选也作用于审计（以前只作用于运行记录）；
             关键字同时匹配工位名（后端已把 detail 里的窗口 id 解析成名字） */
          if (winSel) {
            var wname = (Logs.windows.filter(function (w) { return w.id === winSel; })[0] || {}).name || '';
            var blob0 = (e.actor_label || '') + ' ' + (e.detail || '') + ' ' + (e.actor || '');
            if (blob0.indexOf(winSel) < 0 && (!wname || blob0.indexOf(wname) < 0)) return false;
          }
          if (!kw) return true;
          return String(e.actor_label || '').toLowerCase().indexOf(kw) >= 0 ||
            String(e.actor || '').toLowerCase().indexOf(kw) >= 0 ||
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
              /* v0.10.43：显示工位名（后端解析），原始 actor 放 tooltip */
              '<td><span class="badge badge-grey" title="' + U.esc(e.actor || '') + '">' +
              U.esc(e.actor_label || e.actor || '—') + '</span></td>' +
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
