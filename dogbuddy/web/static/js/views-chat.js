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
               llmOk: true, sending: false, streamText: '',
               /* v0.10.51：本轮实时工具回执（SSE admin.tool）。收尾时清空，
                  之后由持久化的 tool 消息（折叠卡片）接管。 */
               liveTools: [] };

  /** 打字机缓冲：本次发送正在流式输出的文本（按对话隔离，切换对话就丢弃） */
  var stream = { conv: '', text: '', active: false, startedAt: 0 };
  /** 等待计时器：首帧还没到时每秒刷新"等待模型响应…Ns"，让人看得见页面是活的 */
  var tick = null;

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

  /* v0.10.47（owner："管理员会发出莫名的空白气泡"）：工具调用轮的 assistant 消息
     content 是空的/只有空白、只有 tool_calls，以前照样渲染成一个空壳气泡。
     工位视图 0.10.43 已经这么过滤了，对话页当时漏了 —— 这里补齐：
     非 user 消息只要 content trim 后为空就不渲染（工具本身也没有正文可看）。 */
  function visibleMessages() {
    return state.messages.filter(function (m) {
      if (!m) return false;
      if (String(m.role || '') === 'user') return true;
      return String(m.content || '').trim() !== '';
    });
  }

  /* ---------------- 工具调用卡片（v0.10.51） ----------------
     owner 两条反馈："管理员调用工具不是实时显示" + "没有折叠选项"。
     做法：① 服务端每次工具返回就发一帧 admin.tool（本轮实时追加）；
     ② 持久化的连续 tool 消息折成一张 <details>（默认收起，点开看参数与输出）。
     用原生 <details>/<summary>——不需要额外 JS 状态，键盘也能操作。 */
  function toolDetailsHtml(items, opts) {
    opts = opts || {};
    var open = opts.open ? ' open' : '';
    var failed = 0;
    var rows = items.map(function (t) {
      if (t.ok === false || t.pending) failed += 1;
      var flag = t.pending ? '待确认' : (t.ok === false ? '失败' : '完成');
      var cls = t.pending ? 'warn' : (t.ok === false ? 'err' : 'ok');
      var out = String(t.output || '');
      return '<div class="chat-tool-one">' +
        '<div class="chat-tool-head">' +
        '<span class="chat-tool-name mono">' + U.esc(t.name || 'tool') + '</span>' +
        '<span class="chat-tool-flag chat-tool-' + cls + '">' + flag + '</span>' +
        (out ? '<span class="hint">' + out.length + ' 字</span>' : '') +
        '</div>' +
        (t.args ? '<pre class="chat-pre chat-tool-args">' + U.esc(t.args) + '</pre>' : '') +
        (out ? '<pre class="chat-pre">' + U.esc(out) + '</pre>' : '') +
        '</div>';
    }).join('');
    var label = opts.live
      ? ('正在调用工具 ×' + items.length + (failed ? '（' + failed + ' 个异常）' : ''))
      : ('工具调用 ×' + items.length + (failed ? '（' + failed + ' 个失败/待确认）' : ''));
    return '<details class="chat-tools" data-count="' + items.length + '"' + open + '>' +
      '<summary>' + U.icon('zap', 'sm') + '<span>' + label + '</span>' +
      '<span class="chat-tools-hint">点开看参数与输出</span></summary>' +
      '<div class="chat-tools-body">' + rows + '</div></details>';
  }

  function renderLiveTools() {
    var log = U.$('#chat-log');
    if (!log) return;
    var host = U.$('#chat-tool-live');
    if (!state.liveTools.length) {
      if (host && host.parentNode) host.parentNode.removeChild(host);
      return;
    }
    if (!host) {
      log.insertAdjacentHTML('beforeend', '<div id="chat-tool-live"></div>');
      host = U.$('#chat-tool-live');
    }
    if (!host) return;
    host.innerHTML = toolDetailsHtml(state.liveTools, { live: true, open: true });
    var gap = log.scrollHeight - log.scrollTop - log.clientHeight;
    if (gap <= 80) log.scrollTop = log.scrollHeight;
  }

  function onTool(payload) {
    payload = payload || {};
    if (String(payload.conversation || '') !== String(state.sender)) return;  // 别的对话不串屏
    state.liveTools.push({
      name: payload.name, args: payload.args || '', ok: payload.ok,
      pending: !!payload.pending, output: payload.output || ''
    });
    renderLiveTools();
  }

  function render() {
    var log = U.$('#chat-log');
    if (!log) return;
    var msgs = visibleMessages();
    if (!msgs.length && !stream.active) {
      log.innerHTML = U.empty({
        art: 'chat',
        title: state.llmOk ? '还没有对话' : 'LLM 尚未配置',
        desc: state.llmOk
          ? '试着说：每天早上 8 点推进 XX 项目的待办，完成后简要汇报。'
          : '请先到「设置」填写 base_url / model / api_key。'
      });
      return;
    }
    /* v0.10.51：连续的 tool 消息合并成一张可折叠卡片，其余仍逐条气泡。
       工具一多（一轮 10+ 次）时，以前是十几段 JSON 铺满整个对话流。 */
    var parts = [];
    var group = [];
    function flushTools() {
      if (!group.length) return;
      parts.push(toolDetailsHtml(group.map(function (m) {
        var meta = m.meta || {};
        return { name: m.tool_name || meta.tool_name || '工具',
                 args: '',
                 ok: String(m.content || '').indexOf('[DENIED/ERROR]') !== 0,
                 output: String(m.content || '') };
      }), {}));
      group = [];
    }
    msgs.forEach(function (m) {
      if (String(m.role || '') === 'tool') { group.push(m); return; }
      flushTools();
      parts.push(bubble(m));
    });
    flushTools();
    log.innerHTML = parts.join('');
    renderStream();
    renderLiveTools();
    log.scrollTop = log.scrollHeight;
  }

  /* ---------------- 打字机（SSE admin.stream） ----------------
     管理员以前没有 on_delta（只有工位有 window.stream），所以控制台上永远是
     "发送中… → 整段蹦出来"。现在按对话收 admin.stream，边收边渲染。 */
  function stopTick() {
    if (tick) { global.clearInterval(tick); tick = null; }
  }

  function elapsedSec() {
    return stream.startedAt ? Math.max(0, Math.round((Date.now() - stream.startedAt) / 1000)) : 0;
  }

  /* 首帧之前的"活着"的证据：POST 常常要等模型 20–90s 才吐第一个字，
     以前页面就干瞪着"思考中…"，人以为卡死 → 手动刷新。这里每秒刷一次计时。 */
  function startTick() {
    stopTick();
    stream.startedAt = Date.now();
    tick = global.setInterval(function () {
      if (!stream.active) { stopTick(); return; }
      var node = U.$('#chat-stream-bubble');
      if (!node) return;
      var t = node.querySelector('.chat-time');
      if (t) t.textContent = '正在输入… ' + elapsedSec() + 's';
      var ph = node.querySelector('.stream-wait');
      if (ph) ph.textContent = '等待模型响应… ' + elapsedSec() + 's';
    }, 1000);
  }

  /** 本轮收尾：关掉打字机、清计时器、把半截气泡与实时工具卡片摘掉（幂等）。 */
  function finishStreamTurn() {
    stream.active = false;
    stream.text = '';
    stopTick();
    var node = U.$('#chat-stream-bubble');
    if (node && node.parentNode) node.parentNode.removeChild(node);
    // 实时工具卡片交给持久化的 tool 消息（render 里折成同一张卡片）
    state.liveTools = [];
    var live = U.$('#chat-tool-live');
    if (live && live.parentNode) live.parentNode.removeChild(live);
  }

  function renderStream() {
    var log = U.$('#chat-log');
    if (!log) return;
    var node = U.$('#chat-stream-bubble');
    if (!stream.active) {
      if (node && node.parentNode) node.parentNode.removeChild(node);
      return;
    }
    /* v0.10.15：增量更新（只改文本节点），不再每个 delta 重建 DOM —— 消除闪动 */
    if (!node) {
      log.insertAdjacentHTML('beforeend',
        '<div class="chat-msg chat-bot" id="chat-stream-bubble">' +
        '<div class="chat-meta"><span class="chat-who">管理员</span>' +
        '<span class="chat-time">正在输入… 0s</span></div>' +
        '<div class="chat-body"><span class="stream-text"></span>' +
        '<span class="stream-caret"></span></div></div>');
      node = U.$('#chat-stream-bubble');
    }
    var body = node ? node.querySelector('.stream-text') : null;
    if (body && body.textContent !== stream.text) body.textContent = stream.text;
    /* v0.10.47：首帧没到就别留"空壳"——那正是 owner 说的空白气泡。
       放一行可读的等待提示 + 秒数，字一到就撤掉。 */
    var ph = node ? node.querySelector('.stream-wait') : null;
    var bodyBox = node ? node.querySelector('.chat-body') : null;
    if (!stream.text && !ph && bodyBox) {
      bodyBox.insertAdjacentHTML('beforeend',
        '<span class="stream-wait hint">等待模型响应… ' + elapsedSec() + 's</span>');
    } else if (stream.text && ph && ph.parentNode) {
      ph.parentNode.removeChild(ph);
    }
    var gap = log.scrollHeight - log.scrollTop - log.clientHeight;
    if (gap <= 80) log.scrollTop = log.scrollHeight;   // 用户在看上文就别抢滚动条
  }

  function onStream(payload) {
    payload = payload || {};
    if (String(payload.conversation || '') !== String(state.sender)) return;  // 别的对话，不串屏
    if (payload.delta === undefined || payload.delta === null) return;
    /* v0.10.47（owner："管理员会发出莫名的气泡"）：**只在本轮发送中**收增量。
       以前这里会自己把 stream.active 置 true —— 于是 POST 已经返回、正式消息都渲染完
       之后才到的尾巴帧（服务端 finally 里补发的最后一帧）又把气泡**重新建出来**，
       内容只有最后几个字、标题还写着"正在输入…"，且再也没人收走它。
       收尾态直接丢弃：整段正文已经在正式消息里了。 */
    if (!stream.active) return;
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
    state.liveTools = [];                 // v0.10.51：新的一轮，实时工具卡片清空
    startTick();                          // v0.10.47：首帧之前的秒数计时，证明页面没卡死
    render();
    ta.value = '';
    setBusy(true);

    A.adminChat(text, state.sender).then(function (res) {
      finishStreamTurn();                 // 整段回复到手 → 收起流式气泡，渲染正式消息
      state.messages = (res && res.messages) || state.messages;
      setBusy(false);
      render();
      refreshUsage(state.sender);
    }).catch(function (e) {
      finishStreamTurn();
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
      global.SSE.on('admin.tool', onTool);      // v0.10.51：工具调用实时回执
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
