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
  var state = { sender: 'console', messages: [], admins: [], llmOk: true, sending: false };

  function sender() {
    try { return global.localStorage.getItem(SENDER_KEY) || 'console'; } catch (e) { return 'console'; }
  }
  function setSender(s) {
    state.sender = s;
    try { global.localStorage.setItem(SENDER_KEY, s); } catch (e) { /* 忽略 */ }
  }

  /* ---------------- 渲染 ---------------- */
  function renderSenders() {
    var sel = U.$('#chat-sender');
    if (!sel) return;
    var list = state.admins.slice();
    if (list.indexOf(state.sender) < 0) list.push(state.sender);
    list = list.filter(Boolean).sort();
    sel.innerHTML = list.map(function (n) {
      return '<option value="' + U.esc(n) + '"' + (n === state.sender ? ' selected' : '') + '>' +
        U.esc(n === 'console' ? '控制台会话' : n) + '</option>';
    }).join('');
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
    if (!state.messages.length) {
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
    log.scrollTop = log.scrollHeight;
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
    return A.adminChatAdmins().then(function (info) {
      state.admins = (info && info.admins) || [];
      state.llmOk = !info || info.llm_configured !== false;
      renderSenders();
      return A.adminChatMessages(state.sender, 200);
    }).then(function (res) {
      state.messages = (res && res.messages) || [];
      render();
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
    render();
    ta.value = '';
    setBusy(true);

    A.adminChat(text, state.sender).then(function (res) {
      state.messages = (res && res.messages) || state.messages;
      setBusy(false);
      render();
    }).catch(function (e) {
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
        A.adminChatMessages(state.sender, 200).then(function (res) {
          state.messages = (res && res.messages) || [];
          render();
        });
      });
    }

    var rf = U.$('#chat-refresh');
    if (rf) rf.addEventListener('click', load);

    var rs = U.$('#chat-reset');
    if (rs) rs.addEventListener('click', reset);
  }

  global.DogBuddyViews.Chat = {
    load: function () { bind(); return load(); },
    send: send,
    state: state
  };
})(window);
