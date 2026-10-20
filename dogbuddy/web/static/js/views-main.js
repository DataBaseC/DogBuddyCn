/* ============================================================
   DogBuddyCn 控制台 · views-main.js
   总览（统计卡 / 工位卡 / 任务概览 / 运行时间线）
   工位（列表 + 详情 + 聊天式会话流 + 手动派活 + 待办 + 上下文操作）
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  /* 通用：状态点映射 */
  var STATUS_DOT = {
    running: 'dot-run', busy: 'dot-run', working: 'dot-run',
    paused: 'dot-pause', pause: 'dot-pause',
    error: 'dot-err', failed: 'dot-err',
    idle: 'dot-idle', stopped: 'dot-idle'
  };
  var STATUS_LABEL = {
    running: '运行中', busy: '工作中', working: '工作中', paused: '已暂停',
    error: '异常', failed: '异常', idle: '空闲', stopped: '已停止'
  };
  function statusDot(st) {
    var key = String(st || 'idle').toLowerCase();
    return '<span class="dot ' + (STATUS_DOT[key] || 'dot-idle') + '"></span>';
  }
  function statusLabel(st) {
    var key = String(st || 'idle').toLowerCase();
    return STATUS_LABEL[key] || st || '空闲';
  }
  function runBadge(st) {
    var key = String(st || '').toLowerCase();
    if (key === 'ok' || key === 'success' || key === 'succeeded') return '<span class="badge badge-green">成功</span>';
    if (key === 'running') return '<span class="badge badge-blue">运行中</span>';
    if (key === 'error' || key === 'failed' || key === 'fail') return '<span class="badge badge-red">失败</span>';
    return '<span class="badge badge-grey">' + U.esc(st || '—') + '</span>';
  }
  /* 信任级徽标：params.trust == "elevated" → 金色 ELEVATED，否则「常规」 */
  function trustBadge(w) {
    var trust = w && w.params && w.params.trust;
    return trust === 'elevated'
      ? '<span class="badge badge-gold" title="信任级 elevated：可执行高危命令（仍需审批）">ELEVATED</span>'
      : '<span class="badge badge-grey" title="信任级：常规">常规</span>';
  }

  /* ============================================================
     总览
     ============================================================ */
  var Overview = {
    data: null,

    showBanner: function (llmConfigured) {
      var host = document.getElementById('ov-banner');
      if (!host) return;
      if (llmConfigured) { host.innerHTML = ''; return; }
      host.innerHTML =
        '<div class="setup-banner">' +
        '<span class="brand-mark lg">' + U.icon('wand', 'lg') + '</span>' +
        '<div style="flex:1;min-width:0">' +
        '<h3>欢迎使用 DogBuddyCn · 快速开始</h3>' +
        '<p>还没有配置 LLM 接入。7 步向导帮你完成：选择服务商 → 填写地址与密钥 → 拉取模型 → 测试连接 → 保存。全程约 1 分钟。</p>' +
        '</div>' +
        '<button class="btn btn-primary" id="ov-open-wizard">' + U.icon('wand', 'sm') + '打开设置向导</button>' +
        '</div>';
      var btn = document.getElementById('ov-open-wizard');
      if (btn) btn.addEventListener('click', function () {
        global.DogBuddyViews.Settings.openWizard();
      });
    },

    renderStats: function (st, ov) {
      var host = document.getElementById('ov-stats');
      if (!host) return;
      var runs = (ov && ov.recent_runs) || [];
      var ok = 0, total = 0;
      runs.forEach(function (r) {
        var k = String(r.status || '').toLowerCase();
        if (k === 'running') return;
        total++;
        if (k === 'ok' || k === 'success' || k === 'succeeded') ok++;
      });
      var rate = total ? Math.round(ok / total * 100) : null;
      var today = 0;
      var day = U.fmtDate(Date.now());
      runs.forEach(function (r) { if (U.fmtDate(r.started_at) === day) today++; });

      var cards = [
        { label: '工位数', value: (st && st.windows) || 0, icon: 'monitor', cls: '', hint: '执行窗口总数' },
        { label: '启用任务数', value: (st && st.jobs) || 0, icon: 'clock', cls: 'violet', hint: '调度中的任务' },
        { label: '今日运行次数', value: today, icon: 'activity', cls: 'green', hint: '按最近运行记录统计' },
        {
          label: '24h 成功率', value: rate === null ? '—' : rate, unit: rate === null ? '' : '%',
          icon: 'zap', cls: 'amber', hint: ok + ' / ' + total + ' 次成功'
        }
      ];
      host.innerHTML = cards.map(function (c) {
        return '<div class="stat-card">' +
          '<div class="stat-top"><span class="stat-label">' + c.label + '</span>' +
          '<span class="stat-icon ' + c.cls + '">' + U.icon(c.icon, 'sm') + '</span></div>' +
          '<div class="stat-value num">' + U.esc(String(c.value)) + (c.unit ? '<em>' + c.unit + '</em>' : '') + '</div>' +
          '<div class="stat-hint">' + U.esc(c.hint) + '</div>' +
          '</div>';
      }).join('');
    },

    renderWindows: function (windows) {
      var host = document.getElementById('ov-win-grid');
      if (!host) return;
      if (!windows || !windows.length) {
        host.innerHTML = U.empty({
          art: 'box', title: '还没有工位',
          desc: '工位是执行任务的智能窗口。创建第一个工位后即可派活。',
          action: '新建工位', actionId: 'new-window'
        });
        return;
      }
      host.innerHTML = windows.map(function (w) {
        var key = String(w.status || '').toLowerCase();
        return '<div class="win-card' + (key === 'running' || key === 'busy' ? ' running' : '') + '" data-win-id="' + U.esc(w.id) + '">' +
          '<div class="win-card-head">' + statusDot(w.status) +
          '<span class="win-card-name">' + U.esc(w.name) + '</span>' +
          '<span class="badge badge-grey">' + U.esc(statusLabel(w.status)) + '</span></div>' +
          '<div class="win-card-badges">' +
          '<span class="badge badge-blue">' + U.esc(w.role || 'worker') + '</span>' +
          '<span class="badge badge-violet">' + U.esc(w.mode || 'BUILD') + '</span>' +
          trustBadge(w) +
          (w.model ? '<span class="badge badge-grey mono">' + U.esc(w.model) + '</span>' : '') +
          '</div>' +
          '<div class="win-card-task"><b>当前</b>' + U.esc(w.current_task || '空闲待命') + '</div>' +
          '<div class="win-card-output">' + U.esc(w.last_output || '暂无产出') + '</div>' +
          '</div>';
      }).join('');
      U.$$('.win-card[data-win-id]', host).forEach(function (card) {
        card.addEventListener('click', function () {
          global.App.go('windows', { win: card.getAttribute('data-win-id') });
        });
      });
    },

    renderJobs: function (jobs) {
      var host = document.getElementById('ov-jobs-body');
      if (!host) return;
      if (!jobs || !jobs.length) {
        host.innerHTML = '<tr><td colspan="4">' + U.empty({
          art: 'doc', title: '暂无任务', desc: '创建定时任务，让工位自动干活。', action: '新建任务', actionId: 'new-job'
        }) + '</td></tr>';
        return;
      }
      host.innerHTML = jobs.slice(0, 8).map(function (j) {
        return '<tr>' +
          '<td><b>' + U.esc(j.name) + '</b></td>' +
          '<td class="muted">' + U.esc(j.window_name || '—') + '</td>' +
          '<td>' + (j.enabled ? '<span class="badge badge-green">启用</span>' : '<span class="badge badge-grey">停用</span>') +
          (j.fail_count > 0 ? ' <span class="fail-dot">' + j.fail_count + '</span>' : '') + '</td>' +
          '<td class="countdown num">' + U.esc(U.countdown(j.next_run_at)) + '</td>' +
          '</tr>';
      }).join('');
    },

    renderRuns: function (runs) {
      var host = document.getElementById('ov-runs');
      if (!host) return;
      if (!runs || !runs.length) {
        host.innerHTML = U.empty({ art: 'chart', title: '还没有运行记录', desc: '任务执行后会在这里形成活动流。' });
        return;
      }
      host.innerHTML = runs.map(function (r) {
        var k = String(r.status || '').toLowerCase();
        var cls = (k === 'ok' || k === 'success') ? 'ok' : (k === 'running' ? 'run' : 'err');
        var ic = cls === 'ok' ? 'check' : (cls === 'run' ? 'play' : 'alert');
        return '<div class="tl-item">' +
          '<span class="tl-icon ' + cls + '">' + U.icon(ic) + '</span>' +
          '<div class="tl-title">' + U.esc(r.job_name || r.job_id || '任务') +
          ' <span class="muted">· ' + U.esc(r.window_name || '') + '</span></div>' +
          '<div class="tl-sum">' + U.esc(r.summary || '（无摘要）') + '</div>' +
          '<div class="tl-time">' + U.esc(U.relTime(r.started_at)) + ' · ' + U.esc(U.fmtTime(r.started_at)) + '</div>' +
          '</div>';
      }).join('');
    },

    load: function () {
      var host = document.getElementById('ov-stats');
      if (host) host.innerHTML = U.skeleton('cards');
      return Promise.all([A.status(), A.overview()]).then(function (res) {
        var st = res[0], ov = res[1] || {};
        Overview.data = ov;
        Overview.showBanner(st && st.llm_configured === false);
        Overview.renderStats(st, ov);
        Overview.renderWindows(ov.windows);
        Overview.renderJobs(ov.jobs);
        Overview.renderRuns(ov.recent_runs);
      }).catch(function (e) {
        U.toast('error', '总览加载失败：' + e.message);
        if (host) host.innerHTML = '';
      });
    }
  };

  /* ============================================================
     工位：列表 + 详情 + 会话流
     ============================================================ */
  var Windows = {
    list: [],
    selectedId: null,
    detail: null,
    messages: [],
    keyword: '',

    load: function () {
      return A.windows.list().then(function (rows) {
        Windows.list = rows || [];
        Windows.renderList();
        if (!Windows.selectedId && Windows.list.length) {
          Windows.select(Windows.list[0].id);
        } else if (Windows.selectedId) {
          Windows.select(Windows.selectedId);
        } else {
          Windows.renderEmptyDetail();
        }
      }).catch(function (e) {
        U.toast('error', '工位列表加载失败：' + e.message);
      });
    },

    renderList: function () {
      var host = document.getElementById('win-list');
      if (!host) return;
      var kw = Windows.keyword.trim().toLowerCase();
      var rows = Windows.list.filter(function (w) {
        return !kw || String(w.name || '').toLowerCase().indexOf(kw) >= 0 ||
          String(w.role || '').toLowerCase().indexOf(kw) >= 0;
      });
      if (!rows.length) {
        host.innerHTML = U.empty({
          art: 'box', title: kw ? '没有匹配的工位' : '还没有工位',
          desc: kw ? '换个关键词试试。' : '点击右上角「新建工位」创建第一个执行窗口。',
          action: kw ? '' : '新建工位', actionId: 'new-window'
        });
        return;
      }
      host.innerHTML = rows.map(function (w) {
        var key = String(w.status || '').toLowerCase();
        return '<div class="win-item' + (w.id === Windows.selectedId ? ' active' : '') + '" data-win-id="' + U.esc(w.id) + '">' +
          statusDot(w.status) +
          '<div class="win-item-info">' +
          '<div class="win-item-name">' + U.esc(w.name) + '</div>' +
          '<div class="win-item-sub">' + U.esc(w.role || '') + ' · ' + U.esc(w.model || w.resolved_model || '未设模型') +
          (w.model ? '' : '（继承）') + '</div>' +
          '</div>' +
          (key === 'running' || key === 'busy' ? '<span class="badge badge-blue">运行</span>' : '') +
          trustBadge(w) +
          '</div>';
      }).join('');
      U.$$('.win-item', host).forEach(function (item) {
        item.addEventListener('click', function () {
          Windows.select(item.getAttribute('data-win-id'));
        });
      });
    },

    renderEmptyDetail: function () {
      var host = document.getElementById('win-detail');
      if (host) host.innerHTML = U.empty({
        art: 'chat', title: '选择一个工位', desc: '左侧选择工位查看会话流，或新建一个工位开始工作。'
      });
    },

    select: function (id) {
      Windows.selectedId = id;
      Windows.renderList();
      var host = document.getElementById('win-detail');
      if (!host) return;
      host.innerHTML = U.skeleton();
      Promise.all([
        A.windows.get(id),
        A.windows.messages(id, 100)
      ]).then(function (res) {
        if (Windows.selectedId !== id) return;
        Windows.detail = res[0];
        Windows.messages = (res[1] && res[1].messages) || [];
        Windows.renderDetail();
      }).catch(function (e) {
        if (Windows.selectedId !== id) return;
        host.innerHTML = '<div class="empty"><div class="empty-title">加载失败</div><div class="empty-desc">' +
          U.esc(e.message) + '</div></div>';
      });
    },

    /* ---------- 会话气泡渲染 ---------- */
    renderContent: function (text) {
      var s = String(text || '');
      var out = [], parts = s.split(/```/);
      parts.forEach(function (chunk, i) {
        if (i % 2 === 1) {
          var body = chunk.replace(/^[a-zA-Z0-9_+-]*\n/, '');
          out.push('<pre class="mono">' + U.esc(body) + '</pre>');
        } else {
          out.push(U.esc(chunk).replace(/\n/g, '<br>'));
        }
      });
      return out.join('');
    },

    /**
     * 工具消息展示名：优先 DTO.tool_name（meta 带出）→ content JSON 里的
     * tool/name/function/action → 'tool' 兜底。
     * @param {Object} m 消息 DTO（role=tool，含 tool_name / meta）
     * @returns {string} 工具名
     */
    toolName: function (m) {
      var n = String((m && m.tool_name) || ((m && m.meta) || {}).tool_name || '');
      if (n) return n;
      try {
        var obj = JSON.parse(m && m.content);
        if (obj && typeof obj === 'object') {
          n = obj.tool || obj.name || obj.function || obj.action || '';
        }
      } catch (e) { /* 非 JSON：走兜底 */ }
      return String(n || 'tool');
    },

    renderToolCard: function (content, toolName) {
      var name = String(toolName || ''), args = '', result = String(content || '');
      try {
        var obj = JSON.parse(content);
        if (obj && typeof obj === 'object') {
          if (!name) name = obj.tool || obj.name || obj.function || obj.action || '';
          var a = obj.args || obj.arguments || obj.params || obj.input || null;
          if (a !== null && a !== undefined) {
            args = typeof a === 'string' ? a : JSON.stringify(a);
          }
          var r = obj.result !== undefined ? obj.result :
            (obj.output !== undefined ? obj.output :
              (obj.content !== undefined ? obj.content : null));
          if (r !== null && r !== undefined) {
            result = typeof r === 'string' ? r : JSON.stringify(r, null, 2);
          }
          if (args === '' && result === String(content || '')) {
            result = JSON.stringify(obj, null, 2);
          }
        }
      } catch (e) { /* 非 JSON：原样展示 */ }
      return '<div class="tool-card">' +
        '<div class="tool-head">' + U.icon('chevron-r', 'ic-chevron') +
        '<span class="badge badge-blue tool-badge mono">' + U.esc(name || 'tool') + '</span>' +
        '<span class="tool-args">' + U.esc(args || '（无参数）') + '</span>' +
        '</div>' +
        '<div class="tool-body">' + U.esc(result) + '</div>' +
        '</div>';
    },

    /* ---------- 消息渲染：tool 分组（同轮多工具折叠）+ tool_name 徽标 ---------- */

    /**
     * 单条非 tool 消息 HTML（气泡 + 头像 + 时间/tokens 元信息）。
     * @param {Object} m 消息 DTO
     * @param {string} role 角色（小写）
     * @returns {string} 消息 HTML
     */
    renderMsg: function (m, role) {
      var avatar = role === 'user' ? '我' : (role === 'system' ? 'S' : 'AI');
      // v0.10.38：群组协作消息标出来——群频道与成员会话是同一批消息的两份投影，
      // 带 group_id 的这条在群详情页也看得见（其他成员也看得到）。
      var grp = m.group_id
        ? '<span class="badge badge-amber" title="群组协作消息：群频道里其他成员也看得到">群组</span>'
        : '';
      return '<div class="msg ' + U.esc(role) + '">' +
        '<div class="msg-avatar">' + avatar + '</div>' +
        '<div style="min-width:0">' +
        '<div class="msg-bubble">' + Windows.renderContent(m.content) + '</div>' +
        '<div class="msg-meta"><span>' + U.esc(U.fmtClock(m.created_at)) + '</span>' +
        (m.tokens ? '<span class="num">' + U.esc(m.tokens) + ' tok</span>' : '') +
        grp +
        '</div></div></div>';
    },

    /**
     * 连续 role=tool 消息组的 HTML：单条直接渲染；多条折叠成
     * 「调用了 N 个工具」（可展开），每张卡带 tool_name 徽标。
     * @param {Array<Object>} group 连续的 tool 消息（按时间升序）
     * @returns {string} 消息 HTML
     */
    renderToolGroup: function (group) {
      var cards = group.map(function (m) {
        return Windows.renderToolCard(m.content, Windows.toolName(m));
      }).join('');
      var time = U.fmtClock(group[0].created_at);
      // v0.10.38：整组工具调用发生在群组协作里 → 同样标「群组」
      var grp = group[0] && group[0].group_id
        ? '<span class="badge badge-amber" title="群组协作消息：群频道里其他成员也看得到">群组</span>'
        : '';
      var bubble;
      if (group.length === 1) {
        bubble = '<div class="msg-bubble">工具调用' + cards + '</div>';
      } else {
        var badges = group.map(function (m) {
          return '<span class="badge badge-blue tool-badge mono">' +
            U.esc(Windows.toolName(m)) + '</span>';
        }).join('');
        bubble = '<div class="msg-bubble"><div class="tool-group">' +
          '<div class="tool-group-head">' + U.icon('chevron-r', 'ic-chevron') +
          '<span>调用了 ' + group.length + ' 个工具</span>' + badges + '</div>' +
          '<div class="tool-group-body">' + cards + '</div>' +
          '</div></div>';
      }
      return '<div class="msg tool">' +
        '<div class="msg-avatar">T</div>' +
        '<div style="min-width:0">' + bubble +
        '<div class="msg-meta"><span>' + U.esc(time) + '</span>' + grp + '</div></div></div>';
    },

    renderMessages: function () {
      var msgs = Windows.messages.slice().sort(function (a, b) {
        return (U.parseTime(a.created_at) || 0) - (U.parseTime(b.created_at) || 0);
      });
      if (!msgs.length) {
        return U.empty({
          art: 'chat', title: '会话为空',
          desc: '在下方输入框给这位员工派第一个活，Enter 发送。'
        });
      }
      var out = [];
      var lastDay = '';
      var i = 0;
      while (i < msgs.length) {
        var m = msgs[i];
        var d = U.parseTime(m.created_at);
        var day = d ? U.fmtDate(d) : '未知日期';
        if (day !== lastDay) {
          lastDay = day;
          out.push('<div class="chat-day"><span>' + U.esc(day) + '</span></div>');
        }
        var role = String(m.role || 'assistant').toLowerCase();
        if (role === 'tool') {
          var group = [];
          while (i < msgs.length &&
                 String(msgs[i].role || 'assistant').toLowerCase() === 'tool') {
            group.push(msgs[i]);
            i++;
          }
          out.push(Windows.renderToolGroup(group));
        } else {
          out.push(Windows.renderMsg(m, role));
          i++;
        }
      }
      return out.join('');
    },

    renderTodos: function (todos) {
      if (!todos || !todos.length) {
        return '<div class="todo-panel"><h4>待办清单</h4><p class="muted small">暂无待办，由会话自动维护。</p></div>';
      }
      return '<div class="todo-panel"><h4>待办清单 · ' +
        todos.filter(function (t) { return t.done; }).length + '/' + todos.length + '</h4>' +
        todos.map(function (t) {
          return '<div class="todo-item' + (t.done ? ' done' : '') + '" data-todo-id="' + U.esc(t.id) + '">' +
            '<span class="todo-box">' + U.icon('check', 'sm') + '</span>' +
            '<span class="todo-text">' + U.esc(t.content) + '</span></div>';
        }).join('') + '</div>';
    },

    renderDetail: function () {
      var w = Windows.detail;
      var host = document.getElementById('win-detail');
      if (!host || !w) return;
      var key = String(w.status || '').toLowerCase();
      var isPaused = key === 'paused' || key === 'pause';
      host.innerHTML =
        '<div class="win-detail-head">' +
        '<div><div class="win-detail-title">' + statusDot(w.status) + U.esc(w.name) +
        '<span class="badge ' + (isPaused ? 'badge-violet' : 'badge-green') +
        '" id="win-status-badge">' +
        U.esc(statusLabel(w.status)) + '</span>' + trustBadge(w) + '</div>' +
        '<div class="card-sub" style="margin-top:4px">' + U.esc(w.workspace_path || '') + '</div></div>' +
        '<div class="win-detail-actions">' +
        /* v0.10.39：工作区入口（owner："挨个翻文件夹找成果太麻烦"） */
        '<button class="btn btn-primary btn-sm" data-act="workspace" title="在文件管理器里打开该工位的工作区">' + U.icon('folder', 'sm') + '工作区</button>' +
        '<button class="btn btn-soft btn-sm" data-act="rename">' + U.icon('edit', 'sm') + '改名</button>' +
        '<button class="btn btn-soft btn-sm" data-act="model">' + U.icon('cpu', 'sm') + '模型 / 模式</button>' +
        '<button class="btn btn-soft btn-sm" data-act="params">' + U.icon('slider', 'sm') + '参数</button>' +
        (isPaused
          ? '<button class="btn btn-primary btn-sm" data-act="resume">' + U.icon('play', 'sm') + '恢复</button>'
          : '<button class="btn btn-soft btn-sm" data-act="pause">' + U.icon('pause', 'sm') + '暂停</button>') +
        '<button class="btn btn-ghost btn-sm" data-act="compress" title="压缩上下文">压缩</button>' +
        '<button class="btn btn-ghost btn-sm" data-act="inject" title="注入系统信息">注入</button>' +
        '<button class="btn btn-ghost btn-sm" data-act="reset" title="重置上下文">重置</button>' +
        '<button class="btn btn-danger btn-sm" data-act="delete">' + U.icon('trash', 'sm') + '</button>' +
        '</div></div>' +

        '<div class="win-detail-meta">' +
        '<span class="meta-pill">' + U.icon('user', 'sm') + U.esc(w.role || 'worker') + '</span>' +
        '<span class="meta-pill">' + U.icon('layers', 'sm') + U.esc(w.mode || 'BUILD') + '</span>' +
        '<span class="meta-pill">' + U.icon('cpu', 'sm') +
        (w.model
          ? '<span class="mono">' + U.esc(w.model) + '</span><span class="hint-inline">指定</span>'
          : '<span class="mono">' + U.esc(w.resolved_model || '未设置') + '</span><span class="hint-inline">继承</span>') +
        '</span></span>' +
        (w.llm_profile ? '<span class="meta-pill">' + U.icon('layers', 'sm') + '配置套 <span class="mono">' + U.esc(w.llm_profile) + '</span></span>' : '') +
        (w.tokens ? '<span class="meta-pill">' + U.icon('activity', 'sm') + '<span class="num">' + U.esc(w.tokens) + '</span> tokens</span>' : '') +
        (w.current_task ? '<span class="meta-pill">' + U.icon('play', 'sm') + U.esc(w.current_task) + '</span>' : '') +
        '</div>' +

        '<div class="ctx-usage" id="win-usage" data-win-id="' + U.esc(w.id) + '"></div>' +

        Windows.renderTodos(w.todos) +

        '<div class="chat" id="win-chat">' + Windows.renderMessages() + '</div>' +

        '<div class="composer">' +
        '<textarea id="win-composer" rows="1" placeholder="手动派活…（Enter 发送，Shift+Enter 换行）"></textarea>' +
        '<button class="btn btn-primary" id="win-send">' + U.icon('send', 'sm') + '派活</button>' +
        '</div>';

      /* 恢复流式半截气泡（若有），再滚动到底部 */
      Windows.renderStream();
      var chat = document.getElementById('win-chat');
      if (chat) chat.scrollTop = chat.scrollHeight;

      /* 工具卡折叠 / 多工具组折叠 */
      U.$$('.tool-head, .tool-group-head', host).forEach(function (h) {
        h.addEventListener('click', function () {
          h.parentNode.classList.toggle('open');
        });
      });
      /* 待办勾选动效（展示态） */
      U.$$('.todo-box', host).forEach(function (b) {
        b.addEventListener('click', function () {
          b.parentNode.classList.toggle('done');
          U.toast('info', '待办由会话自动维护，此处仅预览状态');
        });
      });
      /* 操作按钮 */
      U.$$('.win-detail-actions [data-act]', host).forEach(function (btn) {
        btn.addEventListener('click', function () {
          Windows.action(btn.getAttribute('data-act'));
        });
      });
      /* 派活 */
      var composer = document.getElementById('win-composer');
      var send = document.getElementById('win-send');
      if (send) send.addEventListener('click', Windows.dispatch);
      if (composer) {
        composer.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            Windows.dispatch();
          }
        });
      }
      /* 上下文用量条（30s 定时刷新由 Params 模块维护） */
      var paramsMod = (global.DogBuddyViews || {}).Params;
      if (paramsMod) paramsMod.mountUsage(Windows.selectedId);
    },

    /* ---------- 打字机流式输出（SSE window.stream，多窗口隔离） ---------- */

    /** @type {Object.<string, {text: string}>} window_id → 流式缓冲（各窗口互不串屏） */
    streams: {},
    /** @type {Object.<string, {status: string, code: string}>} 最近一次运行终态（按窗口） */
    lastRun: {},
    /** @type {Object.<string, {status: string, code: string}>} 最近一次运行终态（按窗口） */
    lastRun: {},
    /** @type {number|null} 完成检测轮询定时器 */
    streamTimer: null,
    /** @type {string} 正在观察流式完成的窗口 id */
    streamWin: '',
    /** @type {number} 轮询截止时间戳（超时兜底） */
    streamDeadline: 0,
    /** @type {Object.<string, number>} dispatch 前已存在的消息 id 集合（判定新 assistant 消息） */
    streamKnown: {},

    /**
     * SSE `window.message`：一条新消息已落库（工具调用 / 系统提示 / 助手正文）。
     *
     * v0.10.36（owner 实测："员工调用工具的时候，并没显示，得刷新一下才行"）：
     * 后端以前只推 `window.stream`（助手**文本增量**），`tool` 与 `system` 消息
     * 从来不推 —— 工具卡片、`[限速暂停]`/`[越权拦截]` 这类提示都只能刷新后从
     * `/messages` 重拉。现在按消息推送，前端直接插进列表。
     * @param {Object} payload {window_id, message}
     * @returns {void}
     */
    onMessage: function (payload) {
      payload = payload || {};
      var wid = String(payload.window_id || '');
      var msg = payload.message;
      if (!wid || !msg) return;
      /* 只有当前打开的窗口才动列表；其它窗口留给切换时的整表刷新（避免串屏） */
      if (wid !== String(Windows.selectedId)) return;
      var id = msg.id;
      if (id !== undefined && id !== null) {
        var dup = Windows.messages.some(function (m) { return String(m.id) === String(id); });
        if (dup) return;
      }
      Windows.messages.push(msg);
      /* 助手正文若已用流式气泡显示，落库后要收掉半截气泡 */
      if (String(msg.role) === 'assistant') {
        var st = Windows.streams[wid];
        if (st && st.text && String(msg.content || '').indexOf(st.text) === 0) {
          delete Windows.streams[wid];
        }
      }
      var chat = document.getElementById('win-chat');
      var keep = chat ? (chat.scrollHeight - chat.scrollTop - chat.clientHeight) < 80 : true;
      if (chat) {
        chat.innerHTML = Windows.renderMessages();
        Windows.renderStream();
        if (keep) chat.scrollTop = chat.scrollHeight;
      } else {
        Windows.renderChatOnly();
      }
    },

    /**
     * SSE window.stream：把 delta 拼进对应窗口的缓冲并刷新「正在输入…」气泡。
     * SSE 不可用时直接忽略（退回原轮询模式，不留半截气泡）。
     * @param {Object} payload {window_id, delta}
     * @returns {void}
     */
    onStream: function (payload) {
      payload = payload || {};
      var wid = String(payload.window_id || '');
      var delta = payload.delta;
      if (!wid || delta === undefined || delta === null) return;
      if (!global.SSE || !global.SSE.state || !global.SSE.state.connected) return;
      var st = Windows.streams[wid];
      if (!st) {
        st = { text: '' };
        Windows.streams[wid] = st;
      }
      st.text += String(delta);
      if (wid === String(Windows.selectedId)) Windows.renderStream();
    },

    /**
     * SSE `window.run_finished`：一次运行结束（成功/失败/限速暂停）。
     *
     * 为什么需要它：以前前端只能靠"轮询有没有新的 assistant 消息"判断结束；
     * LLM 掉线时落库的是 system 消息 → "正在输入…"一直挂到超时，而且**看不到错误码**
     * （owner 实测："显示的还是正在输入…但确实掉线了，也没有错误代码"）。
     * @param {Object} payload {window_id, status, error_code, error}
     * @returns {void}
     */
    onRunFinished: function (payload) {
      payload = payload || {};
      var wid = String(payload.window_id || '');
      if (!wid) return;
      var status = String(payload.status || 'ok');
      Windows.lastRun[wid] = { status: status, code: String(payload.error_code || '') };
      /* 立刻收起流式气泡（不等 180s 轮询超时） */
      if (Windows.streamWin === wid) Windows.finishStream(wid);
      else {
        delete Windows.streams[wid];
        if (wid === String(Windows.selectedId)) Windows.renderStream();
      }
      if (status !== 'ok') {
        var code = String(payload.error_code || '');
        var text = String(payload.error || '').trim();
        U.toast('error', '工位「' + (payload.window_name || wid) + '」运行失败' +
          (code ? ' [' + code + ']' : '') + (text ? '：' + text.slice(0, 120) : ''));
      }
      if (wid === String(Windows.selectedId)) {
        A.windows.messages(wid, 100).then(function (res) {
          if (Windows.streamWin === wid) return;
          Windows.messages = (res && res.messages) || Windows.messages;
          if (wid === String(Windows.selectedId)) Windows.renderChatOnly();
        }).catch(function () { /* 忽略 */ });
        Windows.select(wid);
      }
    },

    /**
     * SSE `window.run_finished`：一次运行结束（成功/失败/限速暂停）。
     *
     * 为什么需要它：以前前端只能靠"轮询有没有新的 assistant 消息"判断结束；
     * LLM 掉线时落库的是 system 消息 → "正在输入…"一直挂到超时，而且**看不到错误码**
     * （owner 实测："显示的还是正在输入…但确实掉线了，也没有错误代码"）。
     * @param {Object} payload {window_id, status, error_code, error}
     * @returns {void}
     */
    onRunFinished: function (payload) {
      payload = payload || {};
      var wid = String(payload.window_id || '');
      if (!wid) return;
      var status = String(payload.status || 'ok');
      Windows.lastRun[wid] = { status: status, code: String(payload.error_code || '') };
      /* 立刻收起流式气泡（不等 180s 轮询超时） */
      if (Windows.streamWin === wid) Windows.finishStream(wid);
      else {
        delete Windows.streams[wid];
        if (wid === String(Windows.selectedId)) Windows.renderStream();
      }
      if (status !== 'ok') {
        var code = String(payload.error_code || '');
        var text = String(payload.error || '').trim();
        U.toast('error', '工位「' + (payload.window_name || wid) + '」运行失败' +
          (code ? ' [' + code + ']' : '') + (text ? '：' + text.slice(0, 120) : ''));
      }
      if (wid === String(Windows.selectedId)) {
        A.windows.messages(wid, 100).then(function (res) {
          if (Windows.streamWin === wid) return;
          Windows.messages = (res && res.messages) || Windows.messages;
          if (wid === String(Windows.selectedId)) Windows.renderChatOnly();
        }).catch(function () { /* 忽略 */ });
        Windows.select(wid);
      }
    },

    /**
     * 渲染 / 更新 / 移除当前窗口的「正在输入…」流式气泡（追加在对话流末尾）。
     * @returns {void}
     */
    renderStream: function () {
      var chat = document.getElementById('win-chat');
      if (!chat) return;
      var st = Windows.streams[String(Windows.selectedId)];
      var node = document.getElementById('win-stream-bubble');
      if (!st || !st.text) {
        if (node && node.parentNode) node.parentNode.removeChild(node);
        return;
      }
      /* v0.10.15：**别整块重建 DOM**。以前每个 delta 都 outerHTML 换一次气泡，
         浏览器要重排+重绘整块，视口跟着抖 —— owner 的原话是"输出窗口会闪动"。
         现在只更新一个文本节点（配 CSS white-space: pre-wrap 保留换行）。 */
      if (!node) {
        chat.insertAdjacentHTML('beforeend',
          '<div class="msg assistant stream-msg" id="win-stream-bubble">' +
          '<div class="msg-avatar">AI</div>' +
          '<div style="min-width:0">' +
          '<div class="msg-bubble"><span class="stream-tag">正在输入…</span>' +
          '<span class="stream-text"></span><span class="stream-caret"></span></div>' +
          '</div></div>');
        node = document.getElementById('win-stream-bubble');
      }
      var body = node.querySelector('.stream-text');
      if (body && body.textContent !== st.text) body.textContent = st.text;
      Windows.stickToBottom(chat);
    },

    /**
     * 只有用户本来就贴在底部时才自动滚动（否则等于抢用户的滚动条，
     * 也是一种"闪"）。历史上滚 → 不动。
     * @param {HTMLElement} chat 消息容器
     * @returns {void}
     */
    stickToBottom: function (chat) {
      if (!chat) return;
      var gap = chat.scrollHeight - chat.scrollTop - chat.clientHeight;
      if (gap > 80) return;               // 用户在看上面的内容，别打扰
      chat.scrollTop = chat.scrollHeight;
    },

    /**
     * 只重渲染对话流区域（不动输入框 / 详情头部），末尾恢复流气泡。
     * @returns {void}
     */
    renderChatOnly: function () {
      var chat = document.getElementById('win-chat');
      if (!chat) return;
      chat.innerHTML = Windows.renderMessages();
      Windows.renderStream();
      chat.scrollTop = chat.scrollHeight;
    },

    /**
     * 清理某个窗口（或全部）的流式缓冲。
     * @param {string} [wid] 窗口 id；缺省清全部
     * @returns {void}
     */
    clearStream: function (wid) {
      if (wid) delete Windows.streams[String(wid)];
      else Windows.streams = {};
      Windows.renderStream();
    },

    /**
     * dispatch 后开始观察流式完成：每 2s 轮询 messages，出现「dispatch 前
     * 没有的 assistant 消息」即视为完整消息落库，用它替换半截气泡；
     * 3 分钟兜底停。SSE 不可用时这就是原轮询模式本身。
     * @param {string} id 窗口 id
     * @returns {void}
     */
    watchStream: function (id) {
      id = String(id);
      Windows.streamWin = id;
      Windows.streamDeadline = Date.now() + 180000;
      Windows.streamKnown = {};
      Windows.messages.forEach(function (m) {
        Windows.streamKnown[String(m.id)] = 1;
      });
      if (Windows.streamTimer) clearInterval(Windows.streamTimer);
      Windows.streamTimer = setInterval(function () {
        Windows.pollMessages(id);
      }, 2000);
      setTimeout(function () { Windows.pollMessages(id); }, 600);
    },

    /**
     * 轮询一次 messages：判定完整消息是否落库（见 watchStream）。
     * @param {string} id 窗口 id
     * @returns {void}
     */
    pollMessages: function (id) {
      id = String(id);
      if (Windows.streamWin !== id) return;
      if (Date.now() > Windows.streamDeadline) {
        Windows.finishStream(id, true);
        return;
      }
      A.windows.messages(id, 100).then(function (res) {
        if (Windows.streamWin !== id) return;
        var msgs = (res && res.messages) || [];
        /* v0.10.15：LLM 掉线时 loop 落库的是 **system** 消息（`[LLM error/xxx]`），
           原来只认 assistant → "正在输入…"会一直挂到 180s 超时。
           注意：瞬态重试也写 system（`[LLM 瞬态失败 … 继续重试]`）——那不是终态，
           不能收尾（否则重试期间气泡就没了，看起来像"跑完了"）。 */
        var fresh = msgs.some(function (m) {
          if (Windows.streamKnown[String(m.id)]) return false;
          var role = String(m.role || '').toLowerCase();
          if (role === 'assistant') return true;
          if (role === 'system') {
            var c = String(m.content || '');
            return c.indexOf('[LLM error/') >= 0 || c.indexOf('[限速暂停]') >= 0;
          }
          return false;
        });
        if (fresh) {
          Windows.messages = msgs;
          Windows.finishStream(id);
          if (String(Windows.selectedId) === id) Windows.renderChatOnly();
        } else if (!Windows.streams[id] && String(Windows.selectedId) === id) {
          /* 无流式气泡（SSE 不可用 / 流未开始）：保持原轮询渲染节奏 */
          Windows.messages = msgs;
          Windows.renderChatOnly();
        }
      }).catch(function () { /* 忽略 */ });
    },

    /**
     * 结束流式观察并清掉半截气泡。
     * @param {string} id 窗口 id
     * @param {boolean} [timeout] 是否因超时结束（会提示）
     * @returns {void}
     */
    finishStream: function (id, timeout) {
      if (Windows.streamTimer) {
        clearInterval(Windows.streamTimer);
        Windows.streamTimer = null;
      }
      if (id) delete Windows.streams[String(id)];
      if (Windows.streamWin === String(id)) Windows.streamWin = '';
      Windows.streamDeadline = 0;
      Windows.renderStream();
      if (timeout) U.toast('warn', '流式输出等待较久，已切回轮询刷新');
    },

    /**
     * SSE window.status：就地刷新总览工位卡 / 工位列表项 / 详情头的状态；
     * 状态回空闲且有半截气泡时立即拉 messages 判定完成。
     * @param {Object} payload {window_id, status, current_task?}
     * @returns {void}
     */
    onWindowStatus: function (payload) {
      payload = payload || {};
      var wid = String(payload.window_id || '');
      var status = String(payload.status || '');
      if (!wid || !status) return;
      var key = status.toLowerCase();
      var label = statusLabel(status);
      var paused = key.indexOf('pause') >= 0;
      var dotCls = 'dot ' + (STATUS_DOT[key] || 'dot-idle');

      /* 总览工位卡片 */
      U.$$('#ov-win-grid .win-card').forEach(function (card) {
        if (card.getAttribute('data-win-id') !== wid) return;
        var dot = U.$('.dot', card);
        if (dot) dot.className = dotCls;
        var badge = U.$('.win-card-head .badge', card);
        if (badge) badge.textContent = label;
        card.classList.toggle('running',
          key === 'running' || key === 'busy' || key === 'working');
      });
      /* 工位列表项 */
      U.$$('#win-list .win-item').forEach(function (item) {
        if (item.getAttribute('data-win-id') !== wid) return;
        var dot = U.$('.dot', item);
        if (dot) dot.className = dotCls;
        var badge = U.$('.badge', item);
        if (badge) badge.textContent = label;
      });
      /* 选中窗口的详情头 */
      if (wid === String(Windows.selectedId)) {
        var sb = document.getElementById('win-status-badge');
        if (sb) {
          sb.className = 'badge ' + (paused ? 'badge-violet' : 'badge-green');
          sb.textContent = label;
        }
        if ((key === 'idle' || key === 'stopped') && Windows.streams[wid]) {
          Windows.pollMessages(wid);
        }
      }
    },

    /**
     * SSE 断开（sse.down）：清掉半截气泡，退回原轮询模式。
     * @returns {void}
     */
    onSseDown: function () {
      Windows.streams = {};
      Windows.renderStream();
    },

    dispatch: function () {
      var input = /** @type {HTMLInputElement} */ (document.getElementById('win-composer'));
      if (!input) return;
      var text = input.value.trim();
      if (!text) return;
      var id = Windows.selectedId;
      input.value = '';
      A.windows.dispatch(id, text).then(function (r) {
        U.toast('success', (r && r.queued) ? '已加入执行队列' : '已派发');
        /* 流式气泡实时增长，轮询 messages 等完整消息落库后替换（SSE 不可用即原轮询） */
        Windows.watchStream(id);
      }).catch(function (e) {
        input.value = text;
        U.toast('error', '派活失败：' + e.message);
      });
    },

    action: function (act) {
      var w = Windows.detail;
      if (!w) return;
      var id = w.id;
      /* v0.10.39：跳到该工位的工作区（找成果不用挨个翻目录） */
      if (act === 'workspace') {
        var ws = w.effective_workspace || w.workspace_path || '';
        if (!ws) { U.toast('warn', '该工位没有工作区路径'); return; }
        if (global.App && global.App.go) global.App.go('files', { workspace: ws });
        return;
      }
      if (act === 'pause' || act === 'resume') {
        (act === 'pause' ? A.windows.pause(id) : A.windows.resume(id)).then(function () {
          U.toast('success', act === 'pause' ? '已暂停' : '已恢复');
          Windows.select(id);
        }).catch(function (e) { U.toast('error', e.message); });
      } else if (act === 'compress') {
        A.windows.compress(id).then(function () {
          U.toast('success', '上下文已压缩');
          Windows.select(id);
        }).catch(function (e) { U.toast('error', e.message); });
      } else if (act === 'reset') {
        UI.confirm({
          title: '重置上下文', danger: true, okLabel: '重置',
          text: '将清空该工位的会话上下文，确定继续？'
        }).then(function (ok) {
          if (!ok) return;
          A.windows.reset(id).then(function () {
            U.toast('success', '上下文已重置');
            Windows.select(id);
          }).catch(function (e) { U.toast('error', e.message); });
        });
      } else if (act === 'inject') {
        var m = U.modal({
          title: '注入系统信息',
          body: '<div class="field"><span class="field-label">注入内容</span>' +
            '<textarea class="textarea" id="inject-text" placeholder="将作为 system 信息注入该工位上下文…"></textarea></div>',
          buttons: [
            { label: '取消', cls: 'btn-ghost' },
            {
              label: '注入', cls: 'btn-primary',
              onClick: function (body) {
                var text = body.querySelector('#inject-text').value.trim();
                if (!text) { U.toast('warn', '请输入注入内容'); return false; }
                A.windows.inject(id, text).then(function () {
                  U.toast('success', '已注入');
                  Windows.select(id);
                }).catch(function (e) { U.toast('error', e.message); });
              }
            }
          ]
        });
        return m;
      } else if (act === 'rename') {
        U.modal({
          title: '重命名工位',
          body: '<div class="field"><span class="field-label">新名称</span>' +
            '<input class="input" id="rename-input" value="' + U.esc(w.name) + '"></div>',
          buttons: [
            { label: '取消', cls: 'btn-ghost' },
            {
              label: '保存', cls: 'btn-primary',
              onClick: function (body) {
                var name = body.querySelector('#rename-input').value.trim();
                if (!name) { U.toast('warn', '名称不能为空'); return false; }
                A.windows.update(id, { name: name }).then(function () {
                  U.toast('success', '已重命名');
                  Windows.load();
                }).catch(function (e) { U.toast('error', e.message); });
              }
            }
          ]
        });
      } else if (act === 'model') {
        Windows.openModelModal(w);
      } else if (act === 'params') {
        // 审查报告 P1-16：按钮在（data-act="params"）、渲染函数在
        // （views-params.js 的 Params.openPanel），但这里既没有分支也没有 else，
        // 点下去静默无反应 —— 典型的"有零件、没接线"。
        var V = global.DogBuddyViews || {};
        if (V.Params && typeof V.Params.openPanel === 'function') {
          V.Params.openPanel(w.id);
        } else {
          U.toast('warn', '参数面板未加载（views-params 未接线）');
        }
      } else if (act === 'chain') {
        Windows.openChainModal(w);
      } else if (act === 'delete') {
        UI.confirm({
          title: '删除工位', danger: true, okLabel: '删除',
          text: '将删除工位「' + w.name + '」及其会话，此操作不可恢复。确定继续？'
        }).then(function (ok) {
          if (!ok) return;
          A.windows.remove(id).then(function () {
            U.toast('success', '工位已删除');
            Windows.selectedId = null;
            Windows.load();
          }).catch(function (e) { U.toast('error', e.message); });
        });
      } else {
        // 未知 action 必须出声。P1-16 的根因就是"既没有分支也没有 else"，
        // 于是按钮点了静默无反应，谁也不知道是漏接线还是执行失败。
        U.toast('warn', '未知操作：' + act);
      }
    },

    openModelModal: function (w) {
      U.modal({
        title: '修改模型 / 模式',
        body:
          '<div class="field"><span class="field-label">模型（从模型目录里选）</span>' +
          '<select class="select mono" id="model-select"><option value="">（加载模型目录中…）</option></select>' +
          '<input class="input mono" id="model-input" value="' + U.esc(w.model || '') + '" placeholder="留空 = 继承管理员模型' +
          (w.resolved_model && !w.model ? '（当前 ' + U.esc(w.resolved_model) + '）' : '') + '" style="margin-top:6px">' +
          '<div class="hint">下拉里是**你已经配好的模型**（各带自己的服务商与密钥）；' +
          '留空即跟随管理员模型。</div></div>' +
          '<div class="set-row" style="border:0;padding-top:2px">' +
          '<button class="btn btn-soft btn-sm" id="model-fetch" type="button">从服务商拉取更多</button>' +
          '<span class="hint" id="model-src"></span></div>' +
          '<div class="field"><span class="field-label">模式</span>' +
          '<select class="select" id="mode-input">' +
          ['BUILD', 'PLAN', 'CHAT', 'REVIEW'].map(function (m) {
            return '<option' + (String(w.mode).toUpperCase() === m ? ' selected' : '') + '>' + m + '</option>';
          }).join('') + '</select></div>' +
          '<div class="field"><span class="field-label">LLM 配置套</span>' +
          '<select class="select" id="model-profile"><option value="">默认</option></select>' +
          '<div class="hint">空 = 使用默认配置套；更改后新消息将按该套发起。</div></div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: '保存', cls: 'btn-primary',
            onClick: function (body) {
              var model = body.querySelector('#model-input').value.trim();
              var mode = body.querySelector('#mode-input').value;
              A.windows.update(w.id, {
                model: model, mode: mode,
                llm_profile: body.querySelector('#model-profile').value
              }).then(function () {
                U.toast('success', '已更新模型 / 模式');
                Windows.select(w.id);
              }).catch(function (e) { U.toast('error', e.message); });
            }
          }
        ],
        body_after: null
      });
      /* LLM 配置套下拉（选项来自 GET /api/settings/profiles 的 names） */
      var ProfilesMod = (global.DogBuddyViews || {}).Profiles;
      if (ProfilesMod) {
        ProfilesMod.fillSelect(document.getElementById('model-profile'), w.llm_profile || '', '默认');
      }
      /* 拉取模型（用已保存的配置，可按所选配置套拉取） */
      /* v0.10.15：打开就能从**模型目录**里选。
         以前必须先点"拉取模型"，而拉取问的是服务商的**全量**列表（99 个），
         和"我自己配好的那几个"不是一回事（owner："工位点击修改模型，
         应该从模型列表里直接选配置好的模型"）。 */
      var catSel = document.getElementById('model-select');
      var catInput = document.getElementById('model-input');
      var catSrc = document.getElementById('model-src');
      if (catSel && catInput) {
        A.models.list().then(function (data) {
          var models = (data && data.models) || [];
          var cur = (data && data.current) || {};
          var opts = ['<option value="">（跟随管理员模型' +
            (cur.model ? '：' + U.esc(cur.model) : '') + '）</option>'];
          models.filter(function (m) { return m.enabled !== false; })
            .forEach(function (m) {
              var label = (m.display_name || m.model) +
                (m.provider && m.provider !== 'openai_compat' ? ' · ' + m.provider : '') +
                (m.model === cur.model ? ' · 管理员当前' : '');
              opts.push('<option value="' + U.esc(m.model) + '"' +
                (m.model === (w.model || '') ? ' selected' : '') + '>' + U.esc(label) + '</option>');
            });
          catSel.innerHTML = opts.join('');
          if (catSrc) {
            catSrc.textContent = models.length
              ? ('目录里 ' + models.length + ' 个模型')
              : '模型目录是空的 —— 去「模型管理」页拉取或新增';
          }
          catSel.addEventListener('change', function () {
            catInput.value = catSel.value || '';
          });
        }).catch(function () {
          catSel.innerHTML = '<option value="">（目录加载失败，可直接在下面手输）</option>';
        });
      }

      var fetchBtn = document.getElementById('model-fetch');
      if (fetchBtn) {
        fetchBtn.addEventListener('click', function () {
          fetchBtn.disabled = true;
          fetchBtn.textContent = '拉取中…';
          var profileSel = document.getElementById('model-profile');
          A.settings.models('', '', '', profileSel ? profileSel.value : '').then(function (r) {
            /* 全量进下拉（datalist 会按输入框已有文字过滤，所以不再用它） */
            U.fillModelSelect('model-select', r.models || [], 'model-input');
            if (r.error) U.toast('warn', '拉取模型失败：' + r.error);
            else U.toast('success', '已拉取 ' + ((r.models || []).length) + ' 个模型');
          }).catch(function (e) {
            U.toast('error', '拉取模型失败：' + e.message);
          }).then(function () {
            fetchBtn.disabled = false;
            fetchBtn.textContent = '拉取模型';
          });
        });
      }
    },

    openCreateModal: function () {
      U.modal({
        title: '新建工位',
        body:
          '<div class="field"><span class="field-label">名称 *</span>' +
          '<input class="input" id="nw-name" placeholder="如：前端开发-1"></div>' +
          '<div class="set-two">' +
          '<div class="field"><span class="field-label">角色</span>' +
          '<select class="select" id="nw-role">' +
          '<option value="worker_programmer">worker_programmer（程序员）</option>' +
          '<option value="supervisor">supervisor（主管 · 负责一个/多个项目）</option>' +
          '</select></div>' +
          '<div class="field"><span class="field-label">模式</span>' +
          '<select class="select" id="nw-mode"><option>BUILD</option><option>PLAN</option><option>CHAT</option><option>REVIEW</option></select>' +
          '</div></div>' +
          '<div class="field"><span class="field-label">工作区路径（可选）</span>' +
          '<input class="input" id="nw-ws" placeholder="留空则使用默认工作区"></div>' +
          '<div class="set-two">' +
          '<div class="field"><span class="field-label">模型（可选）</span>' +
          '<input class="input" id="nw-model" placeholder="留空 = 继承管理员模型' +
          (global.App && global.App.llmModel ? '（当前 ' + U.esc(global.App.llmModel) + '）' : '') + '">' +
          '<div class="hint">员工默认与管理员的模型保持一致；只有需要单独指定时才填。</div></div>' +
          '<div class="field"><span class="field-label">LLM 配置套</span>' +
          '<select class="select" id="nw-profile"><option value="">默认</option></select>' +
          '<div class="hint">空 = 跟随全局默认配置套</div></div>' +
          '</div>' +
          '<div class="set-two">' +
          '<div class="field"><span class="field-label">压缩阈值（0.3 ~ 0.95）</span>' +
          '<input class="input num" id="win-threshold" type="number" min="0.3" max="0.95" step="0.05"' +
          ' placeholder="留空 = 继承全局（默认 0.8）">' +
          '<div class="hint">上下文用到这个比例就自动压缩。按员工的活来定：' +
          '长任务/贵模型可以调高（0.85）省压缩，短任务可以调低（0.6）省上下文开销。</div></div>' +
          '<div class="field"><span class="field-label">上下文上限（tokens，可选）</span>' +
          '<input class="input num" id="win-maxctx" type="number" min="2000" step="1000"' +
          ' placeholder="留空 = 用模型的默认上下文">' +
          '<div class="hint">默认跟着模型走（模型页里填的上下文窗口）；只有这个工位要特殊对待时才填。</div></div>' +
          '</div>',
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: '创建', cls: 'btn-primary',
            onClick: function (body) {
              var name = body.querySelector('#nw-name').value.trim();
              if (!name) {
                var f = body.querySelector('#nw-name');
                f.style.borderColor = 'rgba(248,113,113,0.6)';
                f.focus();
                U.toast('warn', '请填写工位名称');
                return false;
              }
              var payload = {
                name: name,
                role: body.querySelector('#nw-role').value,
                mode: body.querySelector('#nw-mode').value,
                llm_profile: body.querySelector('#nw-profile').value
              };
              var ws = body.querySelector('#nw-ws').value.trim();
              var model = body.querySelector('#nw-model').value.trim();
              if (ws) payload.workspace_path = ws;
              if (model) payload.model = model;
              /* v0.10.12：管理员建员工时明确指定压缩阈值（可选，越界后端会 400） */
              var thrRaw = body.querySelector('#win-threshold').value.trim();
              var ctxRaw = body.querySelector('#win-maxctx').value.trim();
              var params = {};
              if (thrRaw !== '') {
                var thr = Number(thrRaw);
                if (!isFinite(thr) || thr < 0.3 || thr > 0.95) {
                  U.toast('warn', '压缩阈值要在 0.3 ~ 0.95 之间');
                  return false;
                }
                params.compact_threshold = thr;
              }
              if (ctxRaw !== '') params.max_context_tokens = Number(ctxRaw);
              if (Object.keys(params).length) payload.params = params;
              A.windows.create(payload).then(function () {
                U.toast('success', '工位已创建');
                Windows.load();
              }).catch(function (e) { U.toast('error', '创建失败：' + e.message); });
            }
          }
        ]
      });
      /* LLM 配置套下拉（选项来自 GET /api/settings/profiles 的 names） */
      var ProfilesMod = (global.DogBuddyViews || {}).Profiles;
      if (ProfilesMod) {
        ProfilesMod.fillSelect(document.getElementById('nw-profile'), '', '默认');
      }
    }
  };

  global.DogBuddyViews.Overview = Overview;
  global.DogBuddyViews.Windows = Windows;
  global.DogBuddyViews._statusDot = statusDot;
  global.DogBuddyViews._statusLabel = statusLabel;
  global.DogBuddyViews._runBadge = runBadge;

  /* SSE 钩子（松耦合：事件名 → 视图方法；sse.js 先于本文件加载，缺席则自动跳过） */
  if (global.SSE) {
    global.SSE.on('window.status', function (p) { Windows.onWindowStatus(p); });
    global.SSE.on('window.stream', function (p) { Windows.onStream(p); });
    /* v0.10.36：新消息（含工具调用/系统提示）实时插进会话流，不必刷新 */
    global.SSE.on('window.message', function (p) { Windows.onMessage(p); });
    /* v0.10.15：一次运行结束的终态（有错误码）—— 收到就收起"正在输入…" */
    global.SSE.on('window.run_finished', function (p) { Windows.onRunFinished(p); });
    global.SSE.on('sse.down', function () { Windows.onSseDown(); });
  }
})(window);
