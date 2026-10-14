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

  /* ---------------- 渲染 ---------------- */
  function curLine() {
    var c = state.current || {};
    var tag = state.summary && state.summary.current_in_catalog ? '' :
      ' <span class="badge badge-amber">未纳入目录</span>';
    return '<div class="card"><div class="card-body mdl-current">' +
      '<span class="mdl-cur-label">当前生效</span>' +
      '<span class="mono mdl-cur-model">' + U.esc(c.model || '(未设置)') + '</span>' + tag +
      '<span class="card-sub mono">' + U.esc(c.base_url || '') + '</span>' +
      '<span class="card-sub">上下文 ' + U.esc(String(c.max_context_tokens || 0)) + ' tokens</span>' +
      '<span class="card-sub">活跃配置套 ' + U.esc(c.active_profile || '(默认)') + '</span>' +
      '</div></div>';
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
    return '<tr' + (isCur ? ' class="mdl-row-cur"' : '') + '>' +
      '<td><div class="mdl-name">' + U.esc(m.display_name || m.model) +
      (isCur ? ' <span class="badge badge-green">当前</span>' : '') +
      (m.enabled ? '' : ' <span class="badge badge-grey">已停用</span>') + '</div>' +
      '<div class="card-sub mono">' + U.esc(m.id) + '</div></td>' +
      '<td>' + U.esc(m.provider || '') + '<div class="card-sub mono">' + U.esc(m.base_url || '—') + '</div></td>' +
      '<td>' + noteCell(m) + '</td>' +
      '<td>' + caps(m) + '</td>' +
      '<td class="num">' + (m.context_window ? U.esc(String(m.context_window)) : '—') + '</td>' +
      '<td class="mdl-actions">' +
      '<button class="btn btn-soft btn-sm" data-mdl="note" data-id="' + U.esc(m.id) + '">' + U.icon('edit', 'sm') + '备注</button>' +
      (isCur ? '' : '<button class="btn btn-primary btn-sm" data-mdl="activate" data-id="' + U.esc(m.id) + '">设为当前</button>') +
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
        if (cur) cur.innerHTML = curLine();
        render();
      }).catch(function (e) {
        U.toast('error', '加载模型目录失败：' + (e && e.message ? e.message : e));
      });
  }

  function action(kind, id) {
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
        U.toast('success', '已切换为 ' + (res.model || id) + '（已写盘，重启后仍生效）');
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

  function editModel(m, focusNote) {
    var isNew = !m;
    m = m || { model: '', provider: state.current.provider || 'openai_compat',
               base_url: state.current.base_url || '', note: '', capability: '',
               context_window: state.current.max_context_tokens || 0, speed: '', cost: '' };
    U.modal({
      title: isNew ? '新增模型' : ('模型：' + (m.display_name || m.model)),
      wide: true,
      body:
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">模型名 *</span>' +
        '<input class="input mono" id="mdl-f-model" value="' + U.esc(m.model || '') + '" placeholder="如 deepseek-v4-flash"></div>' +
        '<div class="field"><span class="field-label">显示名（可选）</span>' +
        '<input class="input" id="mdl-f-name" value="' + U.esc(m.display_name || '') + '" placeholder="给管理员看的名字"></div>' +
        '</div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">provider</span>' +
        '<input class="input mono" id="mdl-f-provider" value="' + U.esc(m.provider || 'openai_compat') + '"></div>' +
        '<div class="field"><span class="field-label">API 地址（留空=用全局）</span>' +
        '<input class="input mono" id="mdl-f-base" value="' + U.esc(m.base_url || '') + '"></div>' +
        '</div>' +
        '<div class="field"><span class="field-label">备注：能力与限制 *（最重要的一栏）</span>' +
        '<textarea class="textarea" id="mdl-f-note" rows="4" placeholder="例如：工具调用稳定；429 限速很凶，并发别超 2；中文好；不支持 vision；上下文 128k 但实际 32k 后明显变笨。">' +
        U.esc(m.note || '') + '</textarea>' +
        '<div class="hint">写给未来的自己/管理员看：擅长什么、不能干什么、限速与坑。</div></div>' +
        chipInput('mdl-f-cap', m.capability, state.options.capabilities, '能力标签') +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">上下文窗口（tokens）</span>' +
        '<input class="input" id="mdl-f-ctx" type="number" min="0" value="' + U.esc(String(m.context_window || 0)) + '"></div>' +
        '<div class="field"><span class="field-label">速度档</span>' +
        '<select class="select" id="mdl-f-speed">' + (state.options.speeds || ['']).map(function (s) {
          return '<option value="' + U.esc(s) + '"' + (s === (m.speed || '') ? ' selected' : '') + '>' +
            U.esc(s || '（未填）') + '</option>';
        }).join('') + '</select></div>' +
        '</div>' +
        '<div class="field"><span class="field-label">成本档</span>' +
        '<select class="select" id="mdl-f-cost">' + (state.options.costs || ['']).map(function (s) {
          return '<option value="' + U.esc(s) + '"' + (s === (m.cost || '') ? ' selected' : '') + '>' +
            U.esc(s || '（未填）') + '</option>';
        }).join('') + '</select></div>',
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
            if (!payload.model) { U.toast('warn', '模型名不能为空'); return false; }
            var p = isNew ? apiModels().create(payload) : apiModels().update(m.id, payload);
            p.then(function () {
              U.toast('success', isNew ? '已加入目录' : '已保存');
              return load();
            }).catch(function (e) { U.toast('error', e.message); });
          }
        }
      ]
    });
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
    U.modal({
      title: '拉取并导入模型',
      body: '<div class="card-sub" style="margin-bottom:10px">从当前（或指定）LLM 服务商拉取模型列表，' +
        '批量加入目录。<b>已存在条目的备注不会被覆盖。</b></div>' +
        '<div class="field"><span class="field-label">provider（留空=用当前）</span>' +
        '<input class="input mono" id="mdl-i-provider" value=""></div>' +
        '<div class="field"><span class="field-label">API 地址（留空=用当前）</span>' +
        '<input class="input mono" id="mdl-i-base" value=""></div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        {
          label: '拉取', cls: 'btn-primary',
          onClick: function (body) {
            var payload = {
              provider: body.querySelector('#mdl-i-provider').value.trim(),
              base_url: body.querySelector('#mdl-i-base').value.trim()
            };
            apiModels().import(payload).then(function (r) {
              var res = (r && r.result) || {};
              U.toast('success', '拉取 ' + (res.fetched || 0) + ' 个，新增 ' + ((res.added || []).length) +
                ' 个，保留备注 ' + (res.kept_notes || 0) + ' 条');
              return load();
            }).catch(function (e) { U.toast('error', e.message); });
          }
        }
      ]
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
