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
      '<button class="btn btn-soft btn-sm" data-prj="chain" data-id="' + U.esc(p.id) + '">参数链</button>' +
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

  function editProject(p) {
    var isNew = !p;
    p = p || { name: '', root_path: '', params: {} };
    U.modal({
      title: isNew ? '新建项目' : ('编辑项目：' + p.name),
      wide: true,
      body:
        '<div class="field"><span class="field-label">项目名 *</span>' +
        '<input class="input" id="prj-f-name" value="' + U.esc(p.name || '') + '" placeholder="如 DogBuddyCn 主仓库"></div>' +
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
