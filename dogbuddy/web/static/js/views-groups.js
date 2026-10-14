/* ============================================================
   DogBuddyCn 控制台 · views-groups.js
   群组（共享工作频道，ROADMAP T3）：多个工位一起干活的协作空间
   —— 列表卡（成员 chips · 组长 👑）/ 成员管理 / 共享频道消息流 / 任务派发时间线
   后端契约（routes/groups.py）：
     GET    /api/groups                        → {groups:[GroupDTO]}
     POST   /api/groups {name,description,lead_window_id} → GroupDTO
     GET    /api/groups/{ref}                  → GroupDTO（ref=组 id 或组名）
     PATCH  /api/groups/{ref} {name?,description?,lead_window_id?}
     DELETE /api/groups/{ref}                  → {deleted:true}
     POST   /api/groups/{ref}/members {window,role} → GroupDTO
     DELETE /api/groups/{ref}/members/{window} → {removed:true}
     GET    /api/groups/{ref}/channel?limit=   → {messages:[ChannelMsg]}
     POST   /api/groups/{ref}/run {prompt}     → {group_id,group_name,steps[],summary}
   GroupDTO = {id,name,description,lead_window_id,created_at,updated_at,
               members:[{window_id,window_name,role:"lead|member",joined_at,exists}]}
   ChannelMsg = {id,window_id,window_name,role:"user|assistant|tool|system",content,created_at}
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var ROLE_BADGE = {
    lead: '<span class="badge badge-amber" title="组长：负责收口最终结论">👑 组长</span>',
    member: '<span class="badge badge-blue">成员</span>'
  };

  /**
   * 频道消息角色 → 徽标 HTML。
   * @param {string} role 消息角色（user|assistant|tool|system）
   * @returns {string} 徽标 HTML
   */
  function roleBadge(role) {
    if (role === 'user') return '<span class="badge badge-green">用户</span>';
    if (role === 'assistant') return '<span class="badge badge-blue">员工</span>';
    if (role === 'tool') return '<span class="badge badge-amber">工具</span>';
    return '<span class="badge badge-grey">系统</span>';
  }

  /**
   * 由窗口 id 稳定散列一个色相（头像色点：同一工位恒定同色）。
   * @param {string} id 窗口 id
   * @returns {number} 色相 0-359
   */
  function hueOf(id) {
    var s = String(id || ''), h = 0;
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return h;
  }

  /**
   * 头像色点 HTML（首字母 + 稳定色相）。
   * @param {string} name 窗口名
   * @param {string} id 窗口 id（取色）
   * @returns {string} 头像 HTML
   */
  function avatar(name, id) {
    var h = hueOf(id || name);
    var ch = String(name || '?').trim().charAt(0) || '?';
    return '<span class="grp-avatar" style="color:hsl(' + h + ',80%,72%);' +
      'background:hsla(' + h + ',70%,55%,0.16);border-color:hsla(' + h + ',70%,55%,0.35)">' +
      U.esc(ch) + '</span>';
  }

  var Groups = {
    /** @type {Array<Object>} 群组 DTO 列表 */
    list: [],
    /** @type {Array<Object>} 工位列表（下拉数据源，GET /api/windows） */
    windows: [],
    /** @type {string|null} 当前打开的群组 id */
    selectedId: null,
    /** @type {Object|null} 当前群组 DTO */
    current: null,
    /** @type {Array<Object>} 共享频道消息（最新在前） */
    channel: [],
    /** @type {Object|null} 最近一次任务派发结果 {group_id,group_name,steps,summary} */
    runResult: null,
    /** @type {number|null} 频道自动刷新句柄（30s） */
    channelTimer: null,
    /** @type {boolean} 频道拉取进行中（防重入） */
    channelBusy: false,

    /* ---------------- 数据加载 ---------------- */

    /**
     * 加载群组列表 + 工位下拉源；若已打开某群组则顺带刷新其频道。
     * @returns {Promise<void>} 渲染完成
     */
    load: function () {
      var host = document.getElementById('grp-list');
      if (host && !Groups.list.length) host.innerHTML = U.skeleton('cards');
      return Promise.all([A.groups.list(), A.windows.list()]).then(function (res) {
        Groups.list = (res[0] && res[0].groups) || [];
        Groups.windows = res[1] || [];
        Groups.renderList();
        if (Groups.selectedId) {
          Groups.renderDetail();
          return Groups.loadChannel();
        }
        var detail = document.getElementById('grp-detail');
        if (detail) detail.innerHTML = '';
        Groups.startAutoRefresh();
      }).catch(function (e) {
        if (host) {
          host.innerHTML = '<div class="empty"><div class="empty-title">群组加载失败</div>' +
            '<div class="empty-desc">' + U.esc(e && e.message ? e.message : e) + '</div></div>';
        }
      });
    },

    /**
     * 打开某群组详情：拉详情 + 频道。
     * @param {string} gid 群组 id
     * @returns {Promise<void>} 渲染完成
     */
    select: function (gid) {
      Groups.selectedId = gid;
      Groups.runResult = null;
      Groups.renderList();
      var detail = document.getElementById('grp-detail');
      if (detail) detail.innerHTML = U.skeleton();
      return Promise.all([A.groups.get(gid), A.groups.channel(gid, 100)]).then(function (res) {
        if (Groups.selectedId !== gid) return;
        Groups.current = res[0] || null;
        Groups.channel = (res[1] && res[1].messages) || [];
        Groups.renderDetail();
        Groups.startAutoRefresh();
      }).catch(function (e) {
        if (Groups.selectedId !== gid) return;
        if (detail) {
          detail.innerHTML = '<div class="empty"><div class="empty-title">打开群组失败</div>' +
            '<div class="empty-desc">' + U.esc(e && e.message ? e.message : e) + '</div></div>';
        }
      });
    },

    /**
     * 仅刷新共享频道（30s 自动刷新 / 手动刷新共用，静默失败）。
     * @returns {Promise<void>} 拉取完成
     */
    loadChannel: function () {
      if (!Groups.selectedId || Groups.channelBusy) return Promise.resolve();
      Groups.channelBusy = true;
      return A.groups.channel(Groups.selectedId, 100).then(function (r) {
        Groups.channel = (r && r.messages) || [];
        Groups.renderChannel();
      }).catch(function () { /* 频道刷新失败不打断页面 */ })
        .then(function () { Groups.channelBusy = false; });
    },

    /**
     * 启动 30s 自动刷新（幂等；仅在「群组」视图打开时真正发请求）。
     * @returns {void}
     */
    startAutoRefresh: function () {
      if (Groups.channelTimer) return;
      Groups.channelTimer = setInterval(function () {
        if (!Groups.selectedId) return;
        if (global.App && global.App.view !== 'groups') return;
        Groups.loadChannel();
      }, 30000);
    },

    /* ---------------- 渲染：列表 ---------------- */

    /**
     * 群组列表卡（组名 / 描述 / 成员 chips，组长 👑）；空态给引导。
     * @returns {void}
     */
    renderList: function () {
      var host = document.getElementById('grp-list');
      if (!host) return;
      if (!Groups.list.length) {
        host.innerHTML = U.empty({
          art: 'chat',
          title: '还没有群组',
          desc: '共享频道让几个员工一起干活——建个组，把工位拉进来，派一个任务试试。',
          action: '新建群组', actionId: 'new-group'
        });
        return;
      }
      host.innerHTML = '<div class="grp-grid">' + Groups.list.map(function (g) {
        var members = Array.isArray(g.members) ? g.members : [];
        return '<div class="card grp-card' + (g.id === Groups.selectedId ? ' active' : '') +
          '" data-grp-id="' + U.esc(g.id) + '">' +
          '<div class="grp-card-head">' +
          '<span class="grp-card-icon">' + U.icon('layers', 'sm') + '</span>' +
          '<div><div class="grp-card-name">' + U.esc(g.name) + '</div>' +
          '<div class="grp-card-desc">' + U.esc(g.description || '（无描述）') + '</div></div>' +
          '</div>' +
          '<div class="chips grp-member-chips">' + (members.length
            ? members.map(function (m) {
                return '<span class="chip' + (m.role === 'lead' ? ' chip-lead' : '') + '">' +
                  (m.role === 'lead' ? '👑 ' : '') + U.esc(m.window_name || m.window_id) +
                  (m.exists === false ? ' <em class="muted">已删除</em>' : '') + '</span>';
              }).join('')
            : '<span class="muted small">暂无成员</span>') + '</div>' +
          '<div class="grp-card-foot">' +
          '<span class="muted small">' + members.length + ' 名成员 · 更新 ' +
          U.esc(U.relTime(g.updated_at || g.created_at)) + '</span>' +
          '<button class="btn btn-soft btn-sm grp-open">打开</button>' +
          '</div></div>';
      }).join('') + '</div>';

      U.$$('.grp-card', host).forEach(function (card) {
        card.addEventListener('click', function () {
          Groups.select(card.getAttribute('data-grp-id'));
        });
      });
    },

    /* ---------------- 渲染：详情 ---------------- */

    /**
     * 群组详情：头部操作 + 成员管理 + 任务派发 + 共享频道。
     * @returns {void}
     */
    renderDetail: function () {
      var host = document.getElementById('grp-detail');
      var g = Groups.current;
      if (!host) return;
      if (!g) { host.innerHTML = ''; return; }

      host.innerHTML =
        '<div class="card grp-detail">' +
        '<div class="grp-detail-head">' +
        '<div><h2 class="card-title">' + U.esc(g.name) + '</h2>' +
        '<p class="card-sub">' + U.esc(g.description || '（无描述）') + '</p></div>' +
        '<div class="toolbar-actions">' +
        '<button class="btn btn-soft btn-sm" id="grp-edit">' + U.icon('edit', 'sm') + '编辑</button>' +
        '<button class="btn btn-soft btn-sm" id="grp-chan-refresh">' + U.icon('refresh', 'sm') + '刷新频道</button>' +
        '<button class="btn btn-danger btn-sm" id="grp-delete">' + U.icon('trash', 'sm') + '删除群组</button>' +
        '</div></div>' +
        '<div class="grp-cols">' +
        '<div class="card grp-side">' + Groups.memberHTML(g) + '</div>' +
        '<div class="grp-main">' + Groups.runHTML() + Groups.channelHTML() + '</div>' +
        '</div></div>';

      Groups.bindDetail();
      Groups.renderChannel();
      Groups.renderRun();
    },

    /**
     * 成员管理区 HTML（添加成员下拉 + 成员行：设组长 / 移除）。
     * @param {Object} g 群组 DTO
     * @returns {string} 成员区 HTML
     */
    memberHTML: function (g) {
      var members = Array.isArray(g.members) ? g.members : [];
      var inGroup = {};
      members.forEach(function (m) { inGroup[m.window_id] = true; });
      var options = Groups.windows.filter(function (w) { return !inGroup[w.id]; });
      return '<div class="grp-side-head">' + U.icon('user', 'sm') + '成员管理</div>' +
        '<div class="grp-add-row">' +
        '<select class="select select-sm" id="grp-member-sel"' + (options.length ? '' : ' disabled') + '>' +
        (options.length
          ? options.map(function (w) {
              return '<option value="' + U.esc(w.id) + '">' + U.esc(w.name) + '</option>';
            }).join('')
          : '<option value="">（全部工位都已在组内）</option>') +
        '</select>' +
        '<select class="select select-sm" id="grp-member-role">' +
        '<option value="member">成员</option><option value="lead">组长</option></select>' +
        '<button class="btn btn-primary btn-sm" id="grp-member-add"' +
        (options.length ? '' : ' disabled') + '>' + U.icon('plus', 'sm') + '添加</button>' +
        '</div>' +
        '<div class="grp-member-list">' + (members.length
          ? members.map(function (m) {
              return '<div class="grp-member-row" data-win-id="' + U.esc(m.window_id) + '">' +
                avatar(m.window_name || m.window_id, m.window_id) +
                '<div class="grp-member-info">' +
                '<div class="grp-member-name">' + U.esc(m.window_name || m.window_id) +
                (m.exists === false ? ' <span class="badge badge-grey">工位已删除</span>' : '') + '</div>' +
                '<div class="muted small">加入 ' + U.esc(U.relTime(m.joined_at)) + '</div></div>' +
                (ROLE_BADGE[m.role] || ROLE_BADGE.member) +
                '<div class="row-actions">' +
                (m.role === 'lead' ? '' :
                  '<button class="icon-btn" data-act="set-lead" title="设为组长">' + U.icon('arrow-up', 'sm') + '</button>') +
                '<button class="icon-btn danger" data-act="remove" title="移出群组">' + U.icon('trash', 'sm') + '</button>' +
                '</div></div>';
            }).join('')
          : '<p class="muted small" style="padding:8px 2px">暂无成员——从上方下拉把工位拉进来。</p>') +
        '</div>';
    },

    /**
     * 任务派发区 HTML（prompt 输入 + 派发按钮 + 结果容器）。
     * @returns {string} 派发区 HTML
     */
    runHTML: function () {
      return '<div class="card grp-run">' +
        '<div class="grp-side-head">' + U.icon('send', 'sm') + '任务派发</div>' +
        '<div class="grp-run-row">' +
        '<textarea class="textarea" id="grp-prompt" rows="2" placeholder="给这个小组派个活，例如：调研竞品定价并给出一份对比结论。"></textarea>' +
        '<button class="btn btn-primary" id="grp-run-btn">' + U.icon('play', 'sm') + '派发任务</button>' +
        '</div>' +
        '<div class="hint">同步等待小组逐个工位执行，可能耗时——派发中按钮会置灰，请耐心等结果。</div>' +
        '<div id="grp-run-result"></div>' +
        '</div>';
    },

    /**
     * 共享频道区 HTML（消息容器 + 头部刷新）。
     * @returns {string} 频道区 HTML
     */
    channelHTML: function () {
      return '<div class="card grp-chan">' +
        '<div class="grp-side-head">' + U.icon('chat', 'sm') + '共享频道' +
        '<span class="muted small grp-chan-note">每 30 秒自动刷新</span></div>' +
        '<div class="grp-chan-list" id="grp-chan-list"></div>' +
        '</div>';
    },

    /**
     * 渲染频道消息流（头像色点 + 工位名 + 角色徽标 + 时间 + 内容）。
     * @returns {void}
     */
    renderChannel: function () {
      var host = document.getElementById('grp-chan-list');
      if (!host) return;
      if (!Groups.channel.length) {
        host.innerHTML = '<p class="muted small grp-chan-empty">频道暂无消息——派个任务，员工们会在这里留下协作记录。</p>';
        return;
      }
      host.innerHTML = Groups.channel.map(function (m) {
        return '<div class="grp-msg grp-msg-' + U.esc(m.role || 'system') + '">' +
          avatar(m.window_name || m.window_id, m.window_id) +
          '<div class="grp-msg-body">' +
          '<div class="grp-msg-meta">' +
          '<span class="grp-msg-name">' + U.esc(m.window_name || m.window_id) + '</span>' +
          roleBadge(m.role) +
          '<span class="grp-msg-time">' + U.esc(U.relTime(m.created_at)) + '</span>' +
          '</div>' +
          '<div class="grp-msg-content">' + U.esc(m.content || '') + '</div>' +
          '</div></div>';
      }).join('');
    },

    /**
     * 渲染任务派发结果：steps 时间线（工位名 / 成功失败 / 摘要）+ 最终 summary 高亮卡。
     * @returns {void}
     */
    renderRun: function () {
      var host = document.getElementById('grp-run-result');
      var r = Groups.runResult;
      if (!host) return;
      if (!r) { host.innerHTML = ''; return; }
      var steps = Array.isArray(r.steps) ? r.steps : [];
      host.innerHTML =
        '<div class="grp-steps">' + (steps.length
          ? steps.map(function (s) {
              return '<div class="grp-step ' + (s.ok ? 'ok' : 'err') + '">' +
                '<span class="grp-step-dot">' + U.icon(s.ok ? 'check' : 'x', 'sm') + '</span>' +
                '<div class="grp-step-body">' +
                '<div class="grp-step-head"><b>' + U.esc(s.window_name || s.window_id) + '</b>' +
                (s.is_lead ? ' <span class="badge badge-amber">组长收口</span>' : '') +
                (s.ok ? ' <span class="badge badge-green">成功</span>' : ' <span class="badge badge-red">失败</span>') +
                '</div>' +
                '<div class="grp-step-sum">' + U.esc(s.summary || '（无摘要）') + '</div>' +
                '</div></div>';
            }).join('')
          : '<p class="muted small">本次派发没有产生步骤。</p>') + '</div>' +
        '<div class="grp-summary">' +
        '<div class="grp-summary-label">' + U.icon('check', 'sm') + '最终结论</div>' +
        '<div class="grp-summary-text">' + U.esc(r.summary || '（无总结）') + '</div>' +
        '</div>';
    },

    /* ---------------- 交互 ---------------- */

    /**
     * 详情区交互绑定：编辑 / 删除 / 刷新频道 / 成员操作 / 派发。
     * @returns {void}
     */
    bindDetail: function () {
      var g = Groups.current;
      if (!g) return;
      var edit = document.getElementById('grp-edit');
      if (edit) edit.addEventListener('click', function () { Groups.openEdit(); });
      var del = document.getElementById('grp-delete');
      if (del) {
        del.addEventListener('click', function () {
          U.confirm({
            title: '删除群组', danger: true, okLabel: '删除',
            text: '确定删除群组「' + g.name + '」？成员关联会一并解除（工位本身不受影响）。'
          }).then(function (ok) {
            if (!ok) return;
            A.groups.remove(g.id).then(function () {
              U.toast('success', '群组已删除');
              Groups.selectedId = null;
              Groups.current = null;
              Groups.load();
            }).catch(function (e) { U.toast('error', '删除失败：' + e.message); });
          });
        });
      }
      var refresh = document.getElementById('grp-chan-refresh');
      if (refresh) {
        refresh.addEventListener('click', function () {
          Groups.loadChannel().then(function () { U.toast('info', '频道已刷新'); });
        });
      }
      var add = document.getElementById('grp-member-add');
      if (add) {
        add.addEventListener('click', function () {
          var sel = document.getElementById('grp-member-sel');
          var roleSel = document.getElementById('grp-member-role');
          if (!sel || !sel.value) { U.toast('warn', '请选择要添加的工位'); return; }
          add.disabled = true;
          A.groups.addMember(g.id, sel.value, roleSel ? roleSel.value : 'member')
            .then(function (dto) {
              U.toast('success', '已加入群组');
              Groups.current = dto || Groups.current;
              return Groups.loadChannel();
            })
            .catch(function (e) { U.toast('error', '添加失败：' + e.message); })
            .then(function () {
              Groups.renderDetail();
              Groups.load();
            });
        });
      }
      U.$$('.grp-member-row').forEach(function (row) {
        var wid = row.getAttribute('data-win-id');
        var lead = U.$('[data-act="set-lead"]', row);
        if (lead) {
          lead.addEventListener('click', function () {
            lead.disabled = true;
            A.groups.update(g.id, { lead_window_id: wid }).then(function (dto) {
              U.toast('success', '已设为组长');
              Groups.current = dto || Groups.current;
              Groups.renderDetail();
              Groups.load();
            }).catch(function (e) {
              lead.disabled = false;
              U.toast('error', '设组长失败：' + e.message);
            });
          });
        }
        var rm = U.$('[data-act="remove"]', row);
        if (rm) {
          rm.addEventListener('click', function () {
            rm.disabled = true;
            A.groups.removeMember(g.id, wid).then(function () {
              U.toast('success', '已移出群组');
              Groups.renderDetail();
              Groups.load();
            }).catch(function (e) {
              rm.disabled = false;
              U.toast('error', '移除失败：' + e.message);
            });
          });
        }
      });
      var runBtn = document.getElementById('grp-run-btn');
      if (runBtn) {
        runBtn.addEventListener('click', function () {
          var input = document.getElementById('grp-prompt');
          var prompt = (input && input.value || '').trim();
          if (!prompt) { U.toast('warn', '先写下要派发的任务'); return; }
          Groups.dispatch(prompt, runBtn);
        });
      }
    },

    /**
     * 派发群组任务：按钮置 loading 直到同步 run 返回，展示 steps + summary。
     * @param {string} prompt 任务描述
     * @param {HTMLButtonElement} btn 派发按钮（置灰 + 文案「派发中…」）
     * @returns {void}
     */
    dispatch: function (prompt, btn) {
      var gid = Groups.selectedId;
      btn.disabled = true;
      btn.textContent = '派发中…（小组在干活）';
      var result = document.getElementById('grp-run-result');
      if (result) result.innerHTML = U.skeleton();
      A.groups.run(gid, prompt).then(function (r) {
        if (Groups.selectedId !== gid) return;
        Groups.runResult = r || null;
        Groups.renderRun();
        U.toast('success', '任务派发完成');
        Groups.loadChannel();
      }).catch(function (e) {
        if (result) {
          result.innerHTML = '<div class="empty"><div class="empty-title">派发失败</div>' +
            '<div class="empty-desc">' + U.esc(e && e.message ? e.message : e) + '</div></div>';
        }
        U.toast('error', '派发失败：' + (e && e.message ? e.message : e));
      }).then(function () {
        btn.disabled = false;
        btn.innerHTML = U.icon('play', 'sm') + '派发任务';
      });
    },

    /* ---------------- 新建 / 编辑 ---------------- */

    /**
     * 「新建群组」弹窗：名称 / 描述 / 下选组长（数据来自 GET /api/windows）。
     * @returns {void}
     */
    openCreate: function () {
      var winOpts = Groups.windows.map(function (w) {
        return '<option value="' + U.esc(w.id) + '">' + U.esc(w.name) + '</option>';
      }).join('');
      var m = U.modal({
        title: '新建群组',
        body:
          '<div class="field"><span class="field-label">群组名称 *</span>' +
          '<input class="input" id="grp-new-name" placeholder="例如：定价调研小组"></div>' +
          '<div class="field"><span class="field-label">描述</span>' +
          '<textarea class="textarea" id="grp-new-desc" rows="2" placeholder="这个小组负责什么？"></textarea></div>' +
          '<div class="field"><span class="field-label">组长（负责收口结论）</span>' +
          '<select class="select" id="grp-new-lead">' +
          '<option value="">（暂不指定）</option>' + winOpts + '</select>' +
          '<div class="hint">组长会汇总各成员产出，给出最终结论。</div></div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: '创建', cls: 'btn-primary', keepOpen: true,
            onClick: function (body, api) {
              var name = body.querySelector('#grp-new-name').value.trim();
              if (!name) { U.toast('warn', '请填写群组名称'); return false; }
              A.groups.create({
                name: name,
                description: body.querySelector('#grp-new-desc').value.trim(),
                lead_window_id: body.querySelector('#grp-new-lead').value || ''
              }).then(function (g) {
                api.close();
                U.toast('success', '群组已创建');
                Groups.selectedId = (g && g.id) || null;
                Groups.load();
              }).catch(function (e) { U.toast('error', '创建失败：' + e.message); });
            }
          }
        ]
      });
      return m;
    },

    /**
     * 「编辑群组」弹窗：改名称 / 描述 / 组长（PATCH /api/groups/{ref}）。
     * @returns {void}
     */
    openEdit: function () {
      var g = Groups.current;
      if (!g) return;
      var members = Array.isArray(g.members) ? g.members : [];
      var leadOpts = '<option value="">（暂不指定）</option>' + members.map(function (m) {
        return '<option value="' + U.esc(m.window_id) + '"' +
          (m.window_id === g.lead_window_id ? ' selected' : '') + '>' +
          U.esc(m.window_name || m.window_id) + '</option>';
      }).join('');
      U.modal({
        title: '编辑群组',
        body:
          '<div class="field"><span class="field-label">群组名称 *</span>' +
          '<input class="input" id="grp-edit-name" value="' + U.esc(g.name) + '"></div>' +
          '<div class="field"><span class="field-label">描述</span>' +
          '<textarea class="textarea" id="grp-edit-desc" rows="2">' + U.esc(g.description || '') + '</textarea></div>' +
          '<div class="field"><span class="field-label">组长</span>' +
          '<select class="select" id="grp-edit-lead">' + leadOpts + '</select></div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: '保存', cls: 'btn-primary', keepOpen: true,
            onClick: function (body, api) {
              var name = body.querySelector('#grp-edit-name').value.trim();
              if (!name) { U.toast('warn', '请填写群组名称'); return false; }
              A.groups.update(g.id, {
                name: name,
                description: body.querySelector('#grp-edit-desc').value.trim(),
                lead_window_id: body.querySelector('#grp-edit-lead').value || ''
              }).then(function (dto) {
                api.close();
                U.toast('success', '群组已更新');
                Groups.current = dto || Groups.current;
                Groups.renderDetail();
                Groups.load();
              }).catch(function (e) { U.toast('error', '保存失败：' + e.message); });
            }
          }
        ]
      });
    }
  };

  global.DogBuddyViews.Groups = Groups;
})(window);
