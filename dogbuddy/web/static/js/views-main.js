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

    renderToolCard: function (content) {
      var name = 'tool', args = '', result = String(content || '');
      try {
        var obj = JSON.parse(content);
        if (obj && typeof obj === 'object') {
          name = obj.tool || obj.name || obj.function || obj.action || name;
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
        '<span class="tool-name">' + U.esc(name) + '</span>' +
        '<span class="tool-args">' + U.esc(args || '（无参数）') + '</span>' +
        '</div>' +
        '<div class="tool-body">' + U.esc(result) + '</div>' +
        '</div>';
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
      var lastDay = '';
      return msgs.map(function (m) {
        var d = U.parseTime(m.created_at);
        var day = d ? U.fmtDate(d) : '未知日期';
        var head = '';
        if (day !== lastDay) {
          lastDay = day;
          head = '<div class="chat-day"><span>' + U.esc(day) + '</span></div>';
        }
        var role = String(m.role || 'assistant').toLowerCase();
        var avatar = role === 'user' ? '我' : (role === 'tool' ? 'T' : (role === 'system' ? 'S' : 'AI'));
        var bubble;
        if (role === 'tool') {
          bubble = '<div class="msg-bubble">工具调用' + Windows.renderToolCard(m.content) + '</div>';
        } else {
          bubble = '<div class="msg-bubble">' + Windows.renderContent(m.content) + '</div>';
        }
        return head +
          '<div class="msg ' + U.esc(role) + '">' +
          '<div class="msg-avatar">' + avatar + '</div>' +
          '<div style="min-width:0">' + bubble +
          '<div class="msg-meta"><span>' + U.esc(U.fmtClock(m.created_at)) + '</span>' +
          (m.tokens ? '<span class="num">' + U.esc(m.tokens) + ' tok</span>' : '') +
          '</div></div></div>';
      }).join('');
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
        '<span class="badge ' + (isPaused ? 'badge-violet' : 'badge-green') + '">' +
        U.esc(statusLabel(w.status)) + '</span></div>' +
        '<div class="card-sub" style="margin-top:4px">' + U.esc(w.workspace_path || '') + '</div></div>' +
        '<div class="win-detail-actions">' +
        '<button class="btn btn-soft btn-sm" data-act="rename">' + U.icon('edit', 'sm') + '改名</button>' +
        '<button class="btn btn-soft btn-sm" data-act="model">' + U.icon('cpu', 'sm') + '模型 / 模式</button>' +
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

        Windows.renderTodos(w.todos) +

        '<div class="chat" id="win-chat">' + Windows.renderMessages() + '</div>' +

        '<div class="composer">' +
        '<textarea id="win-composer" rows="1" placeholder="手动派活…（Enter 发送，Shift+Enter 换行）"></textarea>' +
        '<button class="btn btn-primary" id="win-send">' + U.icon('send', 'sm') + '派活</button>' +
        '</div>';

      /* 滚动到底部 */
      var chat = document.getElementById('win-chat');
      if (chat) chat.scrollTop = chat.scrollHeight;

      /* 工具卡折叠 */
      U.$$('.tool-head', host).forEach(function (h) {
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
    },

    dispatch: function () {
      var input = document.getElementById('win-composer');
      if (!input) return;
      var text = input.value.trim();
      if (!text) return;
      var id = Windows.selectedId;
      input.value = '';
      A.windows.dispatch(id, text).then(function (r) {
        U.toast('success', (r && r.queued) ? '已加入执行队列' : '已派发');
        setTimeout(function () {
          if (Windows.selectedId === id) {
            A.windows.messages(id, 100).then(function (res) {
              Windows.messages = (res && res.messages) || [];
              Windows.renderDetail();
            }).catch(function () { /* 忽略 */ });
          }
        }, 600);
      }).catch(function (e) {
        input.value = text;
        U.toast('error', '派活失败：' + e.message);
      });
    },

    action: function (act) {
      var w = Windows.detail;
      if (!w) return;
      var id = w.id;
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
      }
    },

    openModelModal: function (w) {
      U.modal({
        title: '修改模型 / 模式',
        body:
          '<div class="field"><span class="field-label">模型（可输入，可从已保存配置拉取）</span>' +
          '<div style="display:flex;gap:8px">' +
          '<input class="input" id="model-input" list="model-options" value="' + U.esc(w.model || '') + '" placeholder="留空 = 继承管理员模型' +
          (w.resolved_model && !w.model ? '（当前 ' + U.esc(w.resolved_model) + '）' : '') + '">' +
          '<button class="btn btn-soft btn-sm" id="model-fetch" type="button">拉取模型</button></div>' +
          '<div class="hint">留空即跟随管理员使用的模型；填了则此工位单独使用该模型。</div>' +
          '<datalist id="model-options"></datalist></div>' +
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
      var fetchBtn = document.getElementById('model-fetch');
      if (fetchBtn) {
        fetchBtn.addEventListener('click', function () {
          fetchBtn.disabled = true;
          fetchBtn.textContent = '拉取中…';
          var profileSel = document.getElementById('model-profile');
          A.settings.models('', '', '', profileSel ? profileSel.value : '').then(function (r) {
            var dl = document.getElementById('model-options');
            if (dl) {
              dl.innerHTML = (r.models || []).map(function (m) {
                return '<option value="' + U.esc(m) + '"></option>';
              }).join('');
            }
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
          '<option value="admin">admin（管理员）</option>' +
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
})(window);
