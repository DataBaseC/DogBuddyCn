/* ============================================================
   DogBuddyCn 控制台 · views-chat.js
   对话：控制台直接与管理员 Agent 对话（建工位 / 派任务 / 查进度）
   —— v1.3 里管理员只接 QQ，控制台没有任何对话入口，这个视图补上
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var SENDER_KEY = 'dogbuddy_chat_sender';
  /* v9（v2 口径）：只有一个管理员与用户对接；用户可以开多个对话，
     每个对话是一份独立上下文。`sender` 字段保留为"对话 id"（后端同名）。 */
  var state = { sender: 'default', messages: [], admins: [], conversations: [],
                llmOk: true, sending: false, streamText: '' };

  /** 打字机缓冲：本次发送正在流式输出的文本（按对话隔离，切换对话就丢弃） */
  var stream = { conv: '', text: '', active: false };

  function sender() {
    try { return global.localStorage.getItem(SENDER_KEY) || 'default'; } catch (e) { return 'default'; }
  }
  function setSender(s) {
    state.sender = s;
    try { global.localStorage.setItem(SENDER_KEY, s); } catch (e) { /* 忽略 */ }
  }
  function currentConversation() {
    return state.conversations.filter(function (c) { return c.id === state.sender; })[0] || null;
  }

  /* ---------------- 上下文用量（管理员会话） ----------------
     管理员会话挂在内存里的合成窗口 `admin:<对话 id>`，**不在 windows 表**里，
     所以必须走 /api/admin/chat/usage；早期版本没有这个端点，界面上也就一直什么都看不到。 */
  function usageHost() { return U.$('#chat-usage'); }

  function refreshUsage(convId) {
    var host = usageHost();
    var id = convId || state.sender;
    if (host) host.setAttribute('data-conv-id', id);
    return A.adminChatUsage(id).then(function (u) {
      u = u || {};
      /* 管理员模型：管理员没有工位，它的模型就是运行时全局模型，
         在「模型管理」页点「设为当前」即可更换——这里直接显示给人看 */
      var badge = U.$('#chat-model');
      if (badge) {
        badge.textContent = '模型：' + (u.model || '(未设置)');
        badge.setAttribute('title', '管理员当前模型：' + (u.model || '(未设置)') +
          (u.base_url ? ' · ' + u.base_url : '') + '（在模型管理页点「设为当前」更换）');
      }
      if (!host) return;
      /* 异步回来时若已切换对话，丢弃这次结果 */
      if (host.getAttribute('data-conv-id') !== id) return;
      host.innerHTML = U.usageHTML(u, { label: '管理员上下文' });
    }).catch(function () {
      var badge2 = U.$('#chat-model');
      if (badge2) badge2.textContent = '模型：—';
      if (!host) return;
      if (host.getAttribute('data-conv-id') !== id) return;
      host.innerHTML = '<div class="ctx-usage-top"><span class="ctx-usage-label">' +
        U.icon('activity', 'sm') + '管理员上下文</span>' +
        '<span class="hint">用量暂时取不到</span></div>';
    });
  }

  function onNotice(payload) {
    payload = payload || {};
    var text = String(payload.text || '');
    if (!text) return;
    if (String(payload.conversation || '') !== String(state.sender)) {
      /* 汇报落在另一个对话里：提示一下，别让人以为没发生 */
      U.toast('info', '管理员有新汇报（在另一个对话里）');
      return;
    }
    state.messages.push({
      id: payload.message_id || ('notice-' + Date.now()),
      role: 'assistant', content: text,
      created_at: payload.created_at || new Date().toISOString(),
      meta: { notice: true }
    });
    render();
    refreshUsage(state.sender);
  }

  /* ---------------- 渲染 ---------------- */
  function renderSenders() {
    var sel = U.$('#chat-sender');
    if (!sel) return;
    var list = state.conversations.slice();
    if (!list.length) list = [{ id: state.sender, title: '默认对话' }];
    sel.innerHTML = list.map(function (c) {
      var label = c.title || c.id;
      var n = c.message_count || 0;
      return '<option value="' + U.esc(c.id) + '"' + (c.id === state.sender ? ' selected' : '') + '>' +
        U.esc(label) + (n ? '（' + n + '）' : '') + '</option>';
    }).join('');
  }

  function newConversation() {
    A.chatCreateConversation('').then(function (r) {
      var conv = (r && r.conversation) || {};
      state.conversations = (r && r.conversations) || state.conversations;
      if (conv.id) setSender(conv.id);
      U.toast('success', '已新建对话：' + (conv.title || conv.id || ''));
      return load();
    }).catch(function (e) { U.toast('error', '新建失败：' + (e && e.message ? e.message : e)); });
  }

  function renameConversation() {
    var conv = currentConversation();
    U.modal({
      title: '重命名对话',
      body: '<div class="field"><span class="field-label">对话名称</span>' +
        '<input class="input" id="conv-title" value="' +
        U.esc((conv && conv.title) || state.sender) + '"></div>',
      buttons: [
        { label: '取消', cls: 'btn-ghost' },
        { label: '保存', cls: 'btn-primary', onClick: function (body) {
          var v = (body.querySelector('#conv-title').value || '').trim();
          if (!v) { U.toast('warn', '名称不能为空'); return false; }
          A.chatRenameConversation(state.sender, v).then(function (r) {
            state.conversations = (r && r.conversations) || state.conversations;
            renderSenders();
            U.toast('success', '已重命名');
          }).catch(function (e) { U.toast('error', e.message); });
        } }
      ]
    });
  }

  function deleteConversation() {
    var conv = currentConversation();
    if (state.conversations.length <= 1) { U.toast('warn', '至少要保留一个对话'); return; }
    U.confirm({
      title: '删除对话',
      text: '将删除「' + ((conv && conv.title) || state.sender) + '」及其全部消息。确定？',
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      A.chatDeleteConversation(state.sender).then(function (r) {
        state.conversations = (r && r.conversations) || [];
        if (state.conversations.length) setSender(state.conversations[0].id);
        U.toast('success', '已删除');
        return load();
      }).catch(function (e) { U.toast('error', e.message); });
    });
  }

  function bubble(m) {
    var role = m.role === 'user' ? 'user' : (m.role === 'assistant' ? 'bot' : 'tool');
    var who = role === 'user' ? '我' : (role === 'assistant' ? '管理员' : m.role);
    var time = U.relTime(m.created_at);
    var content = U.esc(m.content || '');
    /* 工具消息常是 JSON，用等宽块呈现更易读 */
    if (role === 'tool') {
      content = '<pre class="chat-pre">' + content + '</pre>';
    } else {
      content = content.replace(/\n/g, '<br>');
    }
    return '<div class="chat-msg chat-' + role + '">' +
      '<div class="chat-meta"><span class="chat-who">' + U.esc(who) + '</span>' +
      '<span class="chat-time">' + U.esc(time) + '</span></div>' +
      '<div class="chat-body">' + content + '</div></div>';
  }

  function render() {
    var log = U.$('#chat-log');
    if (!log) return;
    if (!state.messages.length && !stream.active) {
      log.innerHTML = U.empty({
        art: 'chat',
        title: state.llmOk ? '还没有对话' : 'LLM 尚未配置',
        desc: state.llmOk
          ? '试着说：每天早上 8 点推进 XX 项目的待办，完成后简要汇报。'
          : '请先到「设置」填写 base_url / model / api_key。'
      });
      return;
    }
    log.innerHTML = state.messages.map(bubble).join('');
    renderStream();
    log.scrollTop = log.scrollHeight;
  }

  /* ---------------- 打字机（SSE admin.stream） ----------------
     管理员以前没有 on_delta（只有工位有 window.stream），所以控制台上永远是
     "发送中… → 整段蹦出来"。现在按对话收 admin.stream，边收边渲染。 */
  function renderStream() {
    var log = U.$('#chat-log');
    if (!log) return;
    var node = U.$('#chat-stream-bubble');
    if (!stream.active || !stream.text) {
      if (node && node.parentNode) node.parentNode.removeChild(node);
      return;
    }
    /* v0.10.15：增量更新（只改文本节点），不再每个 delta 重建 DOM —— 消除闪动 */
    if (!node) {
      log.insertAdjacentHTML('beforeend',
        '<div class="chat-msg chat-bot" id="chat-stream-bubble">' +
        '<div class="chat-meta"><span class="chat-who">管理员</span>' +
        '<span class="chat-time">正在输入…</span></div>' +
        '<div class="chat-body"><span class="stream-text"></span>' +
        '<span class="stream-caret"></span></div></div>');
      node = U.$('#chat-stream-bubble');
    }
    var body = node ? node.querySelector('.stream-text') : null;
    if (body && body.textContent !== stream.text) body.textContent = stream.text;
    var gap = log.scrollHeight - log.scrollTop - log.clientHeight;
    if (gap <= 80) log.scrollTop = log.scrollHeight;   // 用户在看上文就别抢滚动条
  }

  function onStream(payload) {
    payload = payload || {};
    if (String(payload.conversation || '') !== String(state.sender)) return;  // 别的对话，不串屏
    if (payload.delta === undefined || payload.delta === null) return;
    if (!global.SSE || !global.SSE.state || !global.SSE.state.connected) return;
    stream.active = true;
    stream.conv = state.sender;
    stream.text += String(payload.delta);
    renderStream();
  }

  function setBusy(on) {
    state.sending = !!on;
    var btn = U.$('#chat-send');
    var ta = U.$('#chat-text');
    if (btn) {
      btn.disabled = state.sending;
      btn.innerHTML = state.sending
        ? U.icon('refresh', 'spin') + '思考中…'
        : U.icon('send') + '发送';
    }
    if (ta) ta.disabled = state.sending;
  }

  /* ---------------- 数据 ---------------- */
  function load() {
    setSender(sender());
    return A.chatConversations().then(function (info) {
      state.conversations = (info && info.conversations) || [];
      state.admins = state.conversations.map(function (c) { return c.id; });
      state.llmOk = !info || info.llm_configured !== false;
      if (!state.conversations.length) {
        return A.chatCreateConversation('').then(function (r) {
          state.conversations = (r && r.conversations) || [];
          if (state.conversations.length) setSender(state.conversations[0].id);
        });
      }
      if (!currentConversation()) setSender(state.conversations[0].id);
      renderSenders();
      return null;
    }).then(function () {
      return A.adminChatMessages(state.sender, 200);
    }).then(function (res) {
      state.messages = (res && res.messages) || [];
      render();
      refreshUsage(state.sender);
    }).catch(function (e) {
      U.toast('error', '加载对话失败：' + (e && e.message ? e.message : e));
    });
  }

  function send() {
    if (state.sending) return;
    var ta = U.$('#chat-text');
    var text = (ta && ta.value || '').trim();
    if (!text) return;

    /* 乐观追加用户气泡，失败时回滚 */
    var optimistic = { id: 'pending', role: 'user', content: text, created_at: new Date().toISOString() };
    state.messages.push(optimistic);
    stream.active = true;                 // 立刻出现"正在输入…"气泡，等第一个 delta
    stream.conv = state.sender;
    stream.text = '';
    render();
    ta.value = '';
    setBusy(true);

    A.adminChat(text, state.sender).then(function (res) {
      stream.active = false; stream.text = '';   // 整段回复到手 → 收起流式气泡，渲染正式消息
      state.messages = (res && res.messages) || state.messages;
      setBusy(false);
      render();
      refreshUsage(state.sender);
    }).catch(function (e) {
      stream.active = false; stream.text = '';
      state.messages = state.messages.filter(function (m) { return m !== optimistic; });
      var msg = (e && e.message) ? e.message : String(e);
      setBusy(false);
      render();
      U.toast(e && e.status === 409 ? 'warn' : 'error',
        e && e.status === 409 ? msg : ('发送失败：' + msg));
    });
  }

  function reset() {
    U.confirm({
      title: '清空会话',
      text: '将删除「' + (state.sender === 'console' ? '控制台会话' : state.sender) +
        '」的对话历史（工位、任务、记忆不受影响）。确定继续？',
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      A.adminChatReset(state.sender).then(function (r) {
        U.toast('success', '已清空 ' + ((r && r.dropped) || 0) + ' 条消息');
        return load();
      }).catch(function (e) {
        U.toast('error', '清空失败：' + (e && e.message ? e.message : e));
      });
    });
  }

  /* ---------------- 事件绑定（幂等） ---------------- */
  var bound = false;
  function bind() {
    if (bound) return;
    bound = true;

    /* SSE：管理员打字机（admin.stream）+ 员工汇报写入对话流（admin.notice） */
    if (global.SSE && global.SSE.on) {
      global.SSE.on('admin.stream', onStream);
      global.SSE.on('admin.notice', onNotice);
    }

    var form = U.$('#chat-send');
    if (form) form.addEventListener('click', send);

    var ta = U.$('#chat-text');
    if (ta) {
      ta.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' && !ev.shiftKey) {
          ev.preventDefault();
          send();
        }
      });
    }

    var sel = U.$('#chat-sender');
    if (sel) {
      sel.addEventListener('change', function () {
        setSender(sel.value);
        state.messages = [];
        render();
        refreshUsage(sel.value);
        A.adminChatMessages(state.sender, 200).then(function (res) {
          state.messages = (res && res.messages) || [];
          render();
        });
      });
    }

    var rf = U.$('#chat-refresh');
    if (rf) rf.addEventListener('click', load);

    var mg = U.$('#chat-model-go');
    if (mg) {
      mg.addEventListener('click', function () {
        if (global.App && global.App.go) global.App.go('models');
        else U.toast('info', '请点左侧「模型管理」');
      });
    }

    var rs = U.$('#chat-reset');
    if (rs) rs.addEventListener('click', reset);

    /* v9：对话管理 */
    var nw = U.$('#chat-new');
    if (nw) nw.addEventListener('click', newConversation);
    var rn = U.$('#chat-rename');
    if (rn) rn.addEventListener('click', renameConversation);
    var del = U.$('#chat-delete');
    if (del) del.addEventListener('click', deleteConversation);
  }

  global.DogBuddyViews.Chat = {
    load: function () { bind(); return load(); },
    send: send,
    state: state
  };
})(window);
