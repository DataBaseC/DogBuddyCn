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

  /* v0.10.23：与后端 `model_service.endpoint_of().split('/')[0]` 同口径的 host[:port]。
     用来在**提交前**判断"这条会不会撞上同名同端点的既有条目"（撞上会被服务端 409 拒绝）。 */
  function endpointHostOf(url) {
    var s = String(url || '').trim();
    if (!s) return '';
    var m = s.match(/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\/([^/]*)/);
    return (m ? m[1] : s.split('/')[0]).toLowerCase();
  }

  /* ---------------- 渲染 ---------------- */
  function curLine() {
    var c = state.current || {};
    var tag = state.summary && state.summary.current_in_catalog ? '' :
      ' <span class="badge badge-amber">未纳入目录</span>';
    return '<div class="card"><div class="card-body mdl-current">' +
      '<span class="mdl-cur-label" title="管理员就是你正在对话的那个 Agent；它在模型管理页点「设为当前」即可更换">管理员当前模型</span>' +
      '<span class="mono mdl-cur-model">' + U.esc(c.model || '(未设置)') + '</span>' + tag +
      '<span class="card-sub mono">' + U.esc(c.base_url || '') + '</span>' +
      '<span class="card-sub">上下文 ' + U.esc(String(c.max_context_tokens || 0)) + ' tokens</span>' +
      '<span class="card-sub">活跃配置套 ' + U.esc(c.active_profile || '(默认)') + '</span>' +
      '</div>' +
      ((state.summary && state.summary.current_in_catalog === false)
        ? '<div class="card-sub" style="margin-top:6px;color:var(--warn)">' +
          '⚠️ 这个模型**不在下面的目录里** —— 页面上看不到它、也没法写备注。' +
          '<button class="btn btn-soft btn-sm" data-mdl="adopt-current" style="margin-left:8px">把当前模型纳入目录</button>' +
          '</div>'
        : '') +
      '<div class="card-sub" style="margin-top:6px">下面对话那头（管理员）用的就是这条模型；' +
      '工位没有单独指定模型时也继承它。点某一行的「设为管理员模型」即可切换。' +
      '<button class="btn btn-soft btn-sm" data-mdl="verify-current" style="margin-left:8px">' +
      '校验它在服务商列表里</button></div>' +
      '<div class="card-sub" id="mdl-verify-out" style="margin-top:4px"></div>' +
      '</div>';
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
    /* v0.10.21：同名模型可以共存（不同端点各一条）。同名时把**端点**显式标出来，
       否则两条重名条目在列表里看起来一模一样（owner：「新增的会覆盖掉原来的」）。 */
    var dup = (state.dupNames && state.dupNames[m.model]) > 1;
    return '<tr' + (isCur ? ' class="mdl-row-cur"' : '') + '>' +
      '<td><div class="mdl-name">' + U.esc(m.display_name || m.model) +
      (isCur ? ' <span class="badge badge-green">当前</span>' : '') +
      (m.enabled ? '' : ' <span class="badge badge-grey">已停用</span>') + '</div>' +
      (dup ? '<span class="badge badge-amber" title="目录里有同名的另一条，靠端点区分">同名 · ' +
        U.esc(m.endpoint_host || m.endpoint || '?') + '</span> ' : '') +
      '<div class="card-sub mono">' + U.esc(m.id) + '</div></td>' +
      '<td>' + U.esc(m.provider || '') + '<div class="card-sub mono">' + U.esc(m.base_url || '—') + '</div>' +
      (m.has_api_key
        ? '<span class="badge badge-green" title="该条目自带密钥，「设为当前」时会一起切换">自带密钥</span>'
        : '<span class="badge badge-grey" title="跟设置页里的全局密钥共用">用全局密钥</span>') + '</td>' +
      '<td>' + noteCell(m) + '</td>' +
      '<td>' + caps(m) + '</td>' +
      '<td class="num">' + (m.context_window ? U.esc(String(m.context_window)) : '—') + '</td>' +
      '<td class="mdl-actions">' +
      '<button class="btn btn-soft btn-sm" data-mdl="note" data-id="' + U.esc(m.id) + '">' + U.icon('edit', 'sm') + '备注</button>' +
      (isCur ? '' : '<button class="btn btn-primary btn-sm" data-mdl="activate" data-id="' + U.esc(m.id) + '" title="管理员与（未单独指定模型的）工位都会用它">设为管理员模型</button>') +
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
    /* 同名计数：给 row() 用来决定是否显示「同名 · 端点」标签 */
    state.dupNames = {};
    state.models.forEach(function (m) {
      state.dupNames[m.model] = (state.dupNames[m.model] || 0) + 1;
    });
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
        if (cur) {
          cur.innerHTML = curLine();
          U.$$('[data-mdl]', cur).forEach(function (b) {
            b.addEventListener('click', function () {
              action(b.getAttribute('data-mdl'), b.getAttribute('data-id'));
            });
          });
        }
        render();
      }).catch(function (e) {
        U.toast('error', '加载模型目录失败：' + (e && e.message ? e.message : e));
      });
  }

  function action(kind, id) {
    if (kind === 'adopt-current') {
      A.models.adoptCurrent().then(function (r) {
        U.toast('success', '已把当前模型纳入目录：' + ((r.model && r.model.model) || ''));
        return load();
      }).catch(function (e) { U.toast('error', '纳入失败：' + e.message); });
      return;
    }
    if (kind === 'verify-current') {
      var out = U.$('#mdl-verify-out');
      if (out) out.textContent = '正在向服务商校验（并真发一次 1-token 调用）…';
      /* v0.10.21：以前只查"在不在列表"→ "列表里有但套餐不含"会显示绿灯，
         真跑任务才 403（owner 实测 ds4.1）。现在顺带验一次真调用。 */
      A.models.verifyCurrent(true).then(function (r) {
        if (!out) return;
        if (r.error) { out.textContent = '校验失败：' + r.error; return; }
        var probe = r.probe || {};
        if (r.in_provider_list && r.usable === false) {
          out.innerHTML = '<span style="color:var(--warn,#fcd34d)">⚠️ 在服务商列表里，但真调用被拒：</span>' +
            U.esc(probe.error || '') +
            '<div class="card-sub">列表校验看不出这种情况（套餐/额度不含该模型）。' +
            '要么在服务商侧开通，要么换个模型。</div>';
          return;
        }
        out.innerHTML = r.in_provider_list
          ? '<span style="color:var(--ok)">✅ 服务商列表里有这个模型</span>（共 ' + r.model_count + ' 个模型）' +
            (r.usable ? '，真调用也通了' + (probe.latency_ms ? '（' + probe.latency_ms + 'ms）' : '')
                      : '')
          : '<span style="color:var(--danger)">❌ 服务商列表里**没有** ' + U.esc(r.model) + '</span>' +
            (r.similar && r.similar.length
              ? '（相近的：' + r.similar.map(U.esc).join('、') + '）'
              : '（共 ' + r.model_count + ' 个模型；可能是拼错或已下线）');
      }).catch(function (e) { if (out) out.textContent = '校验失败：' + e.message; });
      return;
    }
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
        U.toast('success', '管理员模型已切换为 ' + (res.model || id) + '（已写盘，重启后仍生效）');
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

  /* ---------------- 新增/编辑模型（v12：一条表单就能从零接入一家服务商） ----------------
     以前的表单要求手打 provider、手打地址、9 个字段一次填完，而且**没有密钥栏**——
     跨服务商换模型时用的还是全局那把 key（401），只能再回设置页手改。
     现在：预设一键带入地址 → 填密钥 → 拉取模型列表选名字 → 测试连接 → 保存。 */
  function presetChips() {
    return (state.options.providers || []).map(function (p) {
      return '<button type="button" class="chip chip-btn" data-preset="' + U.esc(p.provider) +
        '" data-url="' + U.esc(p.url) + '">' + U.esc(p.label) + '</button>';
    }).join(' ') || '<span class="card-sub">（无预设）</span>';
  }

  function probeOut(msg, kind) {
    var el = U.$('#mdl-f-probe-out');
    if (!el) return;
    el.className = 'hint' + (kind === 'ok' ? ' form-ok' : (kind === 'err' ? ' form-error' : ''));
    el.textContent = msg || '';
  }

  function editModel(m, focusNote) {
    var isNew = !m;
    /* v0.10.12：新条目**不预填 128k**——上下文窗口跟着服务商/模型走，
       服务商没给就留空（0 = 继承），别替它编一个数。 */
    m = m || { model: '', provider: state.current.provider || 'openai_compat',
               base_url: state.current.base_url || '', note: '', capability: '',
               context_window: 0, speed: '', cost: '' };
    var keyHint = m.has_api_key
      ? ('已保存 ' + (m.api_key || '********') + '，留空保持不变')
      : '留空 = 用全局密钥（设置页那把）';
    U.modal({
      title: isNew ? '新增模型' : ('模型：' + (m.display_name || m.model)),
      wide: true,
      body:
        '<div class="field"><span class="field-label">服务商预设（点一下带出地址）</span>' +
        '<div class="mdl-chips" id="mdl-f-presets">' + presetChips() + '</div></div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">模型名 *</span>' +
        '<input class="input mono" id="mdl-f-model" value="' +
        U.esc(m.model || '') + '" placeholder="也可以直接在下面的下拉里选"></div>' +
        '<div class="field"><span class="field-label">API 地址（base_url）</span>' +
        '<input class="input mono" id="mdl-f-base" value="' + U.esc(m.base_url || '') +
        '" placeholder="https://…/v1"></div>' +
        '</div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">API Key</span>' +
        '<input class="input mono" id="mdl-f-key" type="password" value="" placeholder="' +
        U.esc(keyHint) + '"></div>' +
        '<div class="field"><span class="field-label">provider</span>' +
        '<input class="input mono" id="mdl-f-provider" value="' +
        U.esc(m.provider || 'openai_compat') + '"></div>' +
        '</div>' +
        '<div class="mdl-probe-row">' +
        '<button class="btn btn-soft btn-sm" id="mdl-f-fetch" type="button">' + U.icon('refresh', 'sm') + '拉取模型列表</button>' +
        '<button class="btn btn-soft btn-sm" id="mdl-f-test" type="button">' + U.icon('activity', 'sm') + '测试连接</button>' +
        '<span class="hint" id="mdl-f-probe-out"></span></div>' +
        /* 拉取结果放进真正的下拉：<datalist> 会**按输入框里已有的文字过滤**，
           所以"自动填第一个"会让下拉里只剩那一个（实测就是这样：99 个只能选到 1 个）。 */
        '<div class="field" id="mdl-f-picker">' +
        '<span class="field-label">从拉取到的列表里选（<span id="mdl-f-count">0</span> 个）</span>' +
        '<select class="select mono" id="mdl-f-select"><option value="">（点上面「拉取模型列表」后在这里选）</option></select>' +
        '<div class="hint">这里列的是**全量**模型，选中即填入上面的「模型名」；不拉取也可以手输。</div></div>' +
        '<div class="field"><span class="field-label">备注：能力与限制（建议写清）</span>' +
        '<textarea class="textarea" id="mdl-f-note" rows="3" placeholder="例如：工具调用稳定；429 限速很凶，并发别超 2；中文好；不支持 vision。">' +
        U.esc(m.note || '') + '</textarea></div>' +
        chipInput('mdl-f-cap', m.capability, state.options.capabilities, '能力标签') +
        '<details class="mdl-adv"' + (focusNote ? '' : '') + '>' +
        '<summary>更多字段（显示名 / 上下文 / 速度 / 成本）</summary>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">显示名（可选）</span>' +
        '<input class="input" id="mdl-f-name" value="' + U.esc(m.display_name || '') + '"></div>' +
        '<div class="field"><span class="field-label">上下文窗口（tokens）</span>' +
        '<input class="input" id="mdl-f-ctx" type="number" min="0" value="' +
        U.esc(String(m.context_window || 0)) + '" placeholder="0 / 留空 = 用服务商默认">' +
        '<div class="hint" id="mdl-f-ctx-hint">拉取后如果服务商报了上下文，会自动填在这里；' +
        '没报就留 0（= 继承全局，不瞎填）。这个值会在「设为当前」时写进模型的运行时上下文。</div></div>' +
        '</div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">速度档</span>' +
        '<select class="select" id="mdl-f-speed">' + (state.options.speeds || ['']).map(function (s) {
          return '<option value="' + U.esc(s) + '"' + (s === (m.speed || '') ? ' selected' : '') + '>' +
            U.esc(s || '（未填）') + '</option>';
        }).join('') + '</select></div>' +
        '<div class="field"><span class="field-label">成本档</span>' +
        '<select class="select" id="mdl-f-cost">' + (state.options.costs || ['']).map(function (s) {
          return '<option value="' + U.esc(s) + '"' + (s === (m.cost || '') ? ' selected' : '') + '>' +
            U.esc(s || '（未填）') + '</option>';
        }).join('') + '</select></div>' +
        '</div></details>' +
        (isNew
          ? '<label class="mdl-chk"><input type="checkbox" id="mdl-f-activate" checked> 保存后立即设为当前模型</label>'
          : ''),
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
            /* 密钥：只在真的录入了新值时才发（掩码/空 = 保持原样，服务端也拦一道） */
            var typed = body.querySelector('#mdl-f-key').value.trim();
            if (typed) payload.api_key = typed;
            if (!payload.model) { U.toast('warn', '模型名不能为空'); return false; }
            /* v0.10.23：身份 = 模型名 + 端点 + **密钥**。只有"完全同身份"才拦；
               同端点换 key 是允许的（同一个服务商、同一个模型、两把 key 各存一条）——
               这正是用户要的，别再拦错。 */
            if (isNew) {
              var keyTail = payload.api_key
                ? String(payload.api_key).replace(/[^A-Za-z0-9]/g, '').slice(-4) : '';
              var clash = (state.models || []).filter(function (x) {
                if (x.model !== payload.model) return false;
                if ((x.endpoint_host || '') !== endpointHostOf(payload.base_url)) return false;
                if (!keyTail) return true;  /* 没填新 key = 沿用全局 → 同身份 */
                var tail = String(x.api_key || '').replace(/[^A-Za-z0-9]/g, '').slice(-4);
                return tail === keyTail;
              });
              if (clash.length) {
                U.toast('error', '已有完全同身份的条目「' + clash[0].id +
                  '」（同名 + 同端点 + 同一把 key）。新增不会覆盖它：' +
                  '要改请点那一行的「备注」；要用另一把 key，就把上面的 API Key 换成另一把。');
                return false;
              }
            }
            var wantActivate = isNew && body.querySelector('#mdl-f-activate') &&
              body.querySelector('#mdl-f-activate').checked;
            var p = isNew ? apiModels().create(payload) : apiModels().update(m.id, payload);
            p.then(function (r) {
              var saved = (r && r.model) || {};
              /* v0.10.23：同名但**身份不同**（换了端点或换了 key）→ 服务端另存了一条，
                 两条共存。把这件事说清楚，别让用户以为又是覆盖。 */
              if (isNew && saved.endpoint_conflict) {
                U.toast('success', '已加入目录：同名条目已有别的身份（端点或密钥不同），' +
                  '已另存为「' + (saved.id || '') + '」，原来那条不受影响');
              } else {
                U.toast('success', isNew ? '已加入目录' : '已保存');
              }
              if (wantActivate && saved.id) {
                return apiModels().activate(saved.id).then(function (a) {
                  var res = (a && a.result) || {};
                  U.toast('success', '已切换为 ' + (res.model || payload.model) +
                    (res.api_key_switched ? '（已同时切换为该模型自带的密钥）' : '（沿用全局密钥）'));
                });
              }
              return null;
            }).then(function () { return load(); })
              .catch(function (e) { U.toast('error', e.message); });
          }
        }
      ]
    });

    /* 预设：一键带入 provider + 地址 */
    var presets = U.$('#mdl-f-presets');
    if (presets) {
      U.$$('[data-preset]', presets).forEach(function (b) {
        b.addEventListener('click', function () {
          var prov = U.$('#mdl-f-provider');
          var base = U.$('#mdl-f-base');
          if (prov) prov.value = b.getAttribute('data-preset') || 'openai_compat';
          if (base) base.value = b.getAttribute('data-url') || '';
          U.$$('[data-preset]', presets).forEach(function (x) { x.classList.remove('chip-on'); });
          b.classList.add('chip-on');
        });
      });
    }

    /* 读当前表单（拉取/测试共用） */
    function formArgs() {
      var out = {
        provider: (U.$('#mdl-f-provider') || {}).value || '',
        base_url: (U.$('#mdl-f-base') || {}).value.trim(),
        model: (U.$('#mdl-f-model') || {}).value.trim()
      };
      var typed = (U.$('#mdl-f-key') || {}).value;
      if (typed && typed.trim()) out.api_key = typed.trim();
      else if (!isNew && m.has_api_key) out.model_ref = m.id;   /* 用条目里已保存的那把 */
      return out;
    }

    /* 拉取的上下文长度缓存：id → tokens（0 = 服务商没说） */
    var mdlCtx = {};
    function applyCtx(name) {
      var inp = U.$('#mdl-f-ctx');
      var hint = U.$('#mdl-f-ctx-hint');
      if (!inp) return;
      var ctx = Number(mdlCtx[name] || 0);
      if (ctx > 0) {
        if (!Number(inp.value)) inp.value = String(ctx);
        if (hint) hint.textContent = '服务商报的上下文是 ' + ctx + ' tokens（已填入，可改）。';
      } else if (!Number(inp.value) && hint) {
        hint.textContent = '这家服务商没给上下文长度 → 留 0 = 继承全局（不替它瞎填）。';
      }
    }

    var fetchBtn = U.$('#mdl-f-fetch');
    if (fetchBtn) {
      fetchBtn.addEventListener('click', function () {
        var a = formArgs();
        probeOut('拉取中…');
        A.settings.models(a.provider, a.base_url, a.api_key, undefined, a.model_ref).then(function (r) {
          var list = (r && r.models) || [];
          if (r && r.error) { probeOut('拉取失败：' + r.error, 'err'); return; }
          /* 唯一的选择入口：select（全量、不按输入框过滤） */
          U.fillModelSelect('mdl-f-select', list, 'mdl-f-model');
          /* 服务商愿意给上下文就记下来，选中时自动填进「上下文窗口」 */
          mdlCtx = {};
          (r.details || []).forEach(function (d) {
            if (d && d.id) mdlCtx[d.id] = Number(d.context_window || 0);
          });
          var ctxCount = Object.keys(mdlCtx).filter(function (k2) { return mdlCtx[k2] > 0; }).length;
          var cnt = U.$('#mdl-f-count');
          if (cnt) cnt.textContent = String(list.length);
          probeOut(list.length
            ? ('拉到 ' + list.length + ' 个模型，在下面下拉里选' +
               (ctxCount ? '（其中 ' + ctxCount + ' 个服务商给了上下文长度，选中会自动填）'
                         : '（这家没报上下文长度，条目留 0 = 继承）'))
            : '没拉到模型（检查地址与密钥）', list.length ? 'ok' : 'err');
        }).catch(function (e) { probeOut('拉取失败：' + e.message, 'err'); });
      });
    }

    /* 选中下拉项 → 填「模型名」（ui.fillModelSelect 已绑），并顺手带出服务商给的上下文 */
    var ctxSel = U.$('#mdl-f-select');
    if (ctxSel) {
      ctxSel.addEventListener('change', function () {
        if (ctxSel.value) applyCtx(ctxSel.value);
      });
    }

    var testBtn = U.$('#mdl-f-test');
    if (testBtn) {
      testBtn.addEventListener('click', function () {
        var a = formArgs();
        if (!a.model) { U.toast('warn', '先填模型名再测'); return; }
        probeOut('连接中…');
        A.settings.testLLM(a).then(function (r) {
          var ok = r && r.ok !== false && !r.error;
          probeOut((ok ? '✅ 通了' : '❌ 失败') +
            (r && r.latency_ms ? ' · ' + r.latency_ms + 'ms' : '') +
            (r && r.model_count ? ' · 该服务商 ' + r.model_count + ' 个模型' : '') +
            (r && r.error ? ' · ' + r.error : ''), ok ? 'ok' : 'err');
        }).catch(function (e) { probeOut('测试失败：' + e.message, 'err'); });
      });
    }

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
    /* v0.10.21：拉取**过多**是真的（SiliconFlow 一次回 99 条），而且留空会静默
       拿"当前/全局 key"去拉 —— 于是"顺手把默认 key 那家的模型也拉进来了"。
       改成两步：先拉（只看不写），再勾选要导入的；顶部明说这次用的端点与密钥来源。 */
    var fetched = [];
    U.modal({
      title: '拉取并导入模型',
      wide: true,
      body: '<div class="card-sub" style="margin-bottom:10px">' +
        '<b>第一步「拉取列表」只看不写</b>；第二步勾选要进目录的，点「只导入勾选的」。' +
        '已存在条目<b>不会被改写</b>（备注、地址、密钥都保留）；同名但地址不同的模型会<b>另存一条</b>。</div>' +
        '<div class="field"><span class="field-label">服务商预设</span>' +
        '<div class="mdl-chips" id="mdl-i-presets">' + presetChips() + '</div></div>' +
        '<div class="field"><span class="field-label">API 地址（留空 = 用当前生效的地址）</span>' +
        '<input class="input mono" id="mdl-i-base" value=""></div>' +
        '<div class="field"><span class="field-label">API Key（留空 = 用全局密钥）</span>' +
        '<input class="input mono" id="mdl-i-key" type="password" value="" placeholder="另一家服务商就填这里"></div>' +
        '<div class="field"><span class="field-label">provider（留空 = 用当前）</span>' +
        '<input class="input mono" id="mdl-i-provider" value=""></div>' +
        '<div class="mdl-chips" style="align-items:center;margin-bottom:6px">' +
        '<button class="btn btn-soft btn-sm" id="mdl-i-fetch" type="button">' + U.icon('refresh', 'sm') + '拉取列表（只看不写）</button>' +
        '<input class="input mono" id="mdl-i-q" style="max-width:220px" placeholder="过滤：如 deepseek / qwen">' +
        '<button class="btn btn-ghost btn-sm" id="mdl-i-all" type="button">全选</button>' +
        '<button class="btn btn-ghost btn-sm" id="mdl-i-none" type="button">清空</button>' +
        '</div>' +
        '<div class="hint" id="mdl-i-info">还没拉取。留空地址会拿「当前生效」的端点与密钥去拉。</div>' +
        '<div id="mdl-i-list" class="mdl-pick-list"></div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        {
          label: '只导入勾选的', cls: 'btn-primary',
          onClick: function (body) {
            var picked = U.$$('#mdl-i-list input[type=checkbox]').filter(function (c) { return c.checked; })
              .map(function (c) { return c.getAttribute('data-model'); });
            var payload = {
              provider: body.querySelector('#mdl-i-provider').value.trim(),
              base_url: body.querySelector('#mdl-i-base').value.trim()
            };
            var key = body.querySelector('#mdl-i-key').value.trim();
            if (key) payload.api_key = key;
            if (!picked.length) { U.toast('warn', '先拉取列表并勾选至少一个模型'); return false; }
            payload.only = picked;
            apiModels().import(payload).then(function (r) {
              var res = (r && r.result) || {};
              var msg = '导入 ' + picked.length + ' 个：新增 ' + ((res.added || []).length) +
                '，已存在保留 ' + (res.refreshed || []).length +
                (res.kept_notes ? '（其中 ' + res.kept_notes + ' 条备注原样保留）' : '');
              if ((res.conflicts || []).length) {
                msg += '；' + res.conflicts.length + ' 个同名不同地址 → 已另存新条目';
              }
              U.toast('success', msg);
              return load();
            }).catch(function (e) { U.toast('error', e.message); return false; });
          }
        }
      ]
    });
    var presets = U.$('#mdl-i-presets');
    if (presets) {
      U.$$('[data-preset]', presets).forEach(function (b) {
        b.addEventListener('click', function () {
          var prov = U.$('#mdl-i-provider');
          var base = U.$('#mdl-i-base');
          if (prov) prov.value = b.getAttribute('data-preset') || '';
          if (base) base.value = b.getAttribute('data-url') || '';
          U.$$('[data-preset]', presets).forEach(function (x) { x.classList.remove('chip-on'); });
          b.classList.add('chip-on');
        });
      });
    }

    function renderPick() {
      var host = U.$('#mdl-i-list');
      if (!host) return;
      var q = (U.$('#mdl-i-q').value || '').trim().toLowerCase();
      var rows = fetched.filter(function (d) { return !q || d.id.toLowerCase().indexOf(q) >= 0; });
      if (!rows.length) { host.innerHTML = ''; return; }
      host.innerHTML = rows.map(function (d) {
        return '<label class="mdl-pick"><input type="checkbox" data-model="' + U.esc(d.id) + '">' +
          '<span class="mono">' + U.esc(d.id) + '</span>' +
          (Number(d.context_window) > 0
            ? '<span class="badge badge-grey">ctx ' + U.esc(String(d.context_window)) + '</span>' : '') +
          '</label>';
      }).join('');
    }

    var fetchBtn = U.$('#mdl-i-fetch');
    if (fetchBtn) {
      fetchBtn.addEventListener('click', function () {
        var info = U.$('#mdl-i-info');
        var base = U.$('#mdl-i-base').value.trim();
        var provider = U.$('#mdl-i-provider').value.trim();
        var key = U.$('#mdl-i-key').value.trim();
        if (info) info.textContent = '拉取中…（用的端点：' + (base || '当前生效的地址') + '）';
        /* 只看不写：走 /settings/models，不落库 */
        A.settings.models(provider || undefined, base || undefined, key || undefined)
          .then(function (r) {
            if (r && r.error) {
              if (info) info.textContent = '拉取失败：' + r.error;
              U.toast('error', '拉取失败：' + r.error);
              return;
            }
            fetched = (r && r.details) || [];
            renderPick();
            var ctxCount = fetched.filter(function (d) { return Number(d.context_window) > 0; }).length;
            if (info) {
              info.textContent = '共 ' + fetched.length + ' 个模型' +
                (ctxCount ? '（' + ctxCount + ' 个带上下文长度，导入时自动填入）' : '（这家没报上下文长度）') +
                ' · 校验通过：端点 ' + (r.used_endpoint || base || '当前生效') +
                ' · 密钥来源：' + (key ? '本次填写的' : '全局密钥');
            }
            if (!fetched.length) U.toast('warn', '没拉到模型：检查地址与密钥（本地部署还要确认同网段）');
          })
          .catch(function (e) { if (info) info.textContent = '拉取失败：' + e.message; });
      });
    }
    ['#mdl-i-q'].forEach(function (sel) {
      var q = U.$(sel);
      if (q) q.addEventListener('input', renderPick);
    });
    var all = U.$('#mdl-i-all');
    if (all) all.addEventListener('click', function () {
      U.$$('#mdl-i-list input[type=checkbox]').forEach(function (c) { c.checked = true; });
    });
    var none = U.$('#mdl-i-none');
    if (none) none.addEventListener('click', function () {
      U.$$('#mdl-i-list input[type=checkbox]').forEach(function (c) { c.checked = false; });
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
