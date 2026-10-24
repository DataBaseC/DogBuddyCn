/* ============================================================
   DogBuddyCn 控制台 · views-decisions.js
   待决事项（股东模式核心，T2.3）：员工向老板提问的收件箱
   —— 决定，而不是盯着看。待决卡片实时插入，已回答/超时进「历史」折叠区。
   后端契约：
     GET  /api/decisions?status=&limit=  → {decisions:[{id,window_id,question,options,
           answer,status,source,created_at,answered_at,expires_at}], pending_count}
     POST /api/decisions/{id}/answer {"answer"} / {id}/cancel / decisions/sweep
     SSE  decision.created / decision.answered / decision.timeout
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var STATUS_BADGE = {
    pending: '<span class="badge badge-red">待决</span>',
    answered: '<span class="badge badge-green">已回答</span>',
    timeout: '<span class="badge badge-amber">已超时</span>',
    cancelled: '<span class="badge badge-grey">已取消</span>'
  };

  var Decisions = {
    /** @type {Array<Object>} 待决列表（status=pending） */
    pending: [],
    /** @type {Array<Object>} 历史（answered / timeout / cancelled） */
    history: [],
    /** @type {Object.<string,string>} 工位 id → 名称 */
    names: {},
    loaded: false,

    /* ---------------- 工位名解析 ---------------- */

    /**
     * 工位显示名：能查到用名称，查不到原样显示 window_id。
     * @param {string} wid 工位 id
     * @returns {string} 工位显示名
     */
    winName: function (wid) {
      return Decisions.names[wid] || wid || '—';
    },

    /**
     * 拉取工位名映射（失败静默，回退显示 window_id）。
     * @returns {Promise<void>} 完成即 resolve
     */
    loadNames: function () {
      return A.windows.list().then(function (rows) {
        (rows || []).forEach(function (w) {
          if (w && w.id) Decisions.names[w.id] = w.name || w.id;
        });
      }).catch(function () { /* 名称映射失败不影响主流程 */ });
    },

    /* ---------------- 数据 ---------------- */

    /**
     * 加载待决 + 历史，并刷新红点。
     * @returns {Promise<void>} 渲染完成
     */
    load: function () {
      var host = document.getElementById('dec-list');
      if (host && !Decisions.loaded) host.innerHTML = U.skeleton();
      return Promise.all([
        A.decisions.list('pending', 100),
        A.decisions.list('', 100),
        Decisions.loadNames()
      ]).then(function (res) {
        var pendingRes = (res[0] && res[0].decisions) || [];
        var all = (res[1] && res[1].decisions) || [];
        Decisions.pending = pendingRes;
        Decisions.history = all.filter(function (d) {
          return String(d.status || '') !== 'pending';
        });
        Decisions.loaded = true;
        Decisions.updateBadge((res[0] && res[0].pending_count) || pendingRes.length);
        Decisions.render();
      }).catch(function (e) {
        if (host) {
          host.innerHTML = '<div class="empty"><div class="empty-title">加载失败</div>' +
            '<div class="empty-desc">' + U.esc(e && e.message ? e.message : e) + '</div></div>';
        }
      });
    },

    /* ---------------- 红点 ---------------- */

    /**
     * 更新侧边栏「待决」导航红点（0 隐藏）。
     * @param {number} n 待决数量
     * @returns {void}
     */
    updateBadge: function (n) {
      var dot = document.getElementById('nav-decisions-badge');
      if (!dot) return;
      var count = Number(n) || 0;
      dot.textContent = count > 99 ? '99+' : String(count);
      dot.classList.toggle('hidden', count <= 0);
    },

    /* ---------------- 渲染 ---------------- */

    /**
     * 单张待决卡片 HTML。按 source 分两种：
     * source=confirm →「⚠ 高危操作待审批」卡（命令等宽显示，批准执行/拒绝 二选一）；
     * 其余（ask_user 等）→ 普通提问卡（问题 / 选项按钮 / 回答框）。
     * @param {Object} d 待决 DTO
     * @returns {string} 卡片 HTML
     */
    /**
     * 是否"审批类"待决（要人点「批准/拒绝」，而不是写一段话）。
     *
     * v0.10.53 修 bug 时一起理的：以前只认 `source === 'confirm'`，而
     * ① `shell` 危险命令的等待是 `source: "ask_user"`（看库里的真实数据）
     * ② v0.10.50 的 `server_exec` 人工确认是 `source: "server_exec"`
     * 两者都落到"普通提问卡"，选项 chips 点一下只是**填进输入框**——用户以为
     * 点了批准，其实什么都没提交。现在按 option 内容识别，三种来源统一成审批卡。
     * @param {Object} d 待决 DTO
     * @returns {boolean}
     */
    isApproval: function (d) {
      var src = String((d && d.source) || '');
      if (src === 'confirm' || src === 'server_exec') return true;
      var opts = (d && Array.isArray(d.options)) ? d.options : [];
      return opts.length === 2 && opts.indexOf('批准') >= 0 && opts.indexOf('拒绝') >= 0;
    },

    card: function (d) {
      /* ⚠ v0.10.53 修的关键 bug：普通分支以前**漏了卡片根元素**
         （`return head + ... + '</div>'`——只写闭合、没写开头），于是
         `bind()` 里 `U.$$('.dec-card')` 一个都找不到 → 点批准/点回答/按回车
         全部无反应，还多出一个 </div> 把 #dec-list 提前闭合。
         现在两个分支都必须以 `<div class="dec-card" data-dec-id="…">` 开头。 */
      var head = '<div class="dec-head">' +
        (STATUS_BADGE[d.status] || STATUS_BADGE.pending) +
        '<span class="dec-from">来自 <b>' + U.esc(Decisions.winName(d.window_id)) + '</b></span>' +
        '<span class="dec-time">提出 ' + U.esc(U.relTime(d.created_at)) +
        (d.expires_at ? ' · 超时 ' + U.esc(U.countdown(d.expires_at)) : ' · 不超时') + '</span>' +
        '</div>';
      var open = '<div class="dec-card' + (Decisions.isApproval(d) ? ' dec-card-danger' : '') +
        '" data-dec-id="' + U.esc(d.id) + '">';

      if (Decisions.isApproval(d)) {
        /* 审批卡：命令等宽显示，批准/拒绝二选一（点了就提交，不用再点「回答」） */
        var isCmd = String(d.source || '') === 'confirm' ||
          String(d.source || '') === 'server_exec';
        return open +
          head.replace(STATUS_BADGE[d.status] || STATUS_BADGE.pending,
            '<span class="badge badge-red">⚠ 待审批</span>') +
          (isCmd ? '<div class="dec-cmd-label">待执行内容</div>' +
            '<pre class="dec-cmd">' + U.esc(d.question || '（空命令）') + '</pre>'
            : '<div class="dec-question">' + U.esc(d.question || '（空问题）') + '</div>') +
          '<div class="dec-answer-row">' +
          '<button class="btn btn-danger btn-sm dec-approve">' + U.icon('check', 'sm') + '批准</button>' +
          '<button class="btn btn-ghost btn-sm dec-reject">' + U.icon('x', 'sm') + '拒绝</button>' +
          '<span class="hint">批准 = 放行执行；拒绝 = 告诉员工不要做。</span>' +
          '</div></div>';
      }

      var options = Array.isArray(d.options) ? d.options : [];
      return open + head +
        '<div class="dec-question">' + U.esc(d.question || '（空问题）') + '</div>' +
        (options.length
          ? '<div class="chips dec-options">' + options.map(function (o) {
              return '<button class="chip dec-opt" data-opt="' + U.esc(o) + '">' + U.esc(o) + '</button>';
            }).join('') + '</div>'
          : '') +
        '<div class="dec-answer-row">' +
        '<input class="input dec-input" placeholder="输入回答…（可点上方选项填入）" autocomplete="off">' +
        '<button class="btn btn-primary btn-sm dec-answer">' + U.icon('check', 'sm') + '回答</button>' +
        '<button class="btn btn-ghost btn-sm dec-cancel" title="跳过该待决事项">跳过（取消）</button>' +
        '</div></div>';
    },

    /**
     * 单条历史条目 HTML（已回答/超时/取消，只读）。
     * @param {Object} d 决策 DTO
     * @returns {string} 条目 HTML
     */
    historyItem: function (d) {
      return '<div class="dec-hist-item">' +
        '<div class="dec-hist-head">' +
        (String(d.source || '') === 'confirm'
          ? '<span class="badge badge-red">⚠ 高危</span>' : '') +
        (STATUS_BADGE[d.status] || '') +
        '<span class="dec-from">来自 <b>' + U.esc(Decisions.winName(d.window_id)) + '</b></span>' +
        '<span class="dec-time">' + U.esc(U.relTime(d.answered_at || d.created_at)) + '</span>' +
        '</div>' +
        '<div class="dec-hist-q">' + U.esc(d.question || '') + '</div>' +
        (d.answer
          ? '<div class="dec-hist-a">回答：' + U.esc(d.answer) + '</div>'
          : '<div class="dec-hist-a muted">（无回答）</div>') +
        '</div>';
    },

    /**
     * 渲染整页：待决卡片列表 + 历史折叠区 + 空态。
     * @returns {void}
     */
    render: function () {
      var host = document.getElementById('dec-list');
      if (!host) return;
      var pend = Decisions.pending;
      var hist = Decisions.history;

      if (!pend.length) {
        host.innerHTML = U.empty({
          art: 'chat',
          title: '没有待决事项——员工们正在自主推进。',
          desc: '员工遇到需要你拍板的问题时，会实时出现在这里（含建议选项，可一键采纳）。'
        });
      } else {
        host.innerHTML = pend.map(Decisions.card).join('');
      }

      var histHost = document.getElementById('dec-history');
      if (histHost) {
        histHost.innerHTML = '<details class="dec-hist"' + (pend.length ? '' : ' open') + '>' +
          '<summary>' + U.icon('chevron-r', 'ic-chevron') + '历史（' + hist.length + '）</summary>' +
          (hist.length
            ? '<div class="dec-hist-list">' + hist.map(Decisions.historyItem).join('') + '</div>'
            : '<p class="muted small dec-hist-empty">暂无历史记录。</p>') +
          '</details>';
      }

      var meta = document.getElementById('dec-meta');
      if (meta) {
        meta.textContent = '待决 ' + pend.length + ' · 历史 ' + hist.length;
      }

      Decisions.bind();
    },

    /* ---------------- 交互（每次 render 后重绑，幂等） ---------------- */

    /**
     * 绑定卡片内交互：选项按钮填入 / 回答 / 取消。
     * @returns {void}
     */
    bind: function () {
      /* 选择器兜底：优先 `.dec-card`，找不到就退回 `[data-dec-id]`。
         v0.10.53 的 bug 正是"渲染时漏了 .dec-card，bind 一个都没绑上"，
         结果按钮全哑——有了兜底就不会因为一个 class 写错而整页失灵。 */
      var cards = U.$$('.dec-card');
      if (!cards.length) cards = U.$$('[data-dec-id]');
      cards.forEach(function (card) {
        var id = Number(card.getAttribute('data-dec-id'));
        if (!id) return;                       // 没有 id 的卡片不绑（也说明渲染有问题）
        card.setAttribute('data-bound', '1');
        var input = U.$('.dec-input', card);
        U.$$('.dec-opt', card).forEach(function (b) {
          b.addEventListener('click', function () {
            if (input) {
              input.value = b.getAttribute('data-opt') || '';
              input.focus();
            }
          });
        });
        var answerBtn = U.$('.dec-answer', card);
        if (answerBtn) {
          answerBtn.addEventListener('click', function () {
            Decisions.answer(id, (input && input.value || '').trim(), answerBtn);
          });
        }
        if (input) {
          input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
              e.preventDefault();
              Decisions.answer(id, input.value.trim(), answerBtn);
            }
          });
        }
        var cancelBtn = U.$('.dec-cancel', card);
        if (cancelBtn) {
          cancelBtn.addEventListener('click', function () {
            Decisions.cancel(id, cancelBtn);
          });
        }
        /* 高危审批卡（source=confirm）：批准执行 / 拒绝 → answer 文本固定 */
        var approveBtn = U.$('.dec-approve', card);
        if (approveBtn) {
          approveBtn.addEventListener('click', function () {
            Decisions.answer(id, '批准', approveBtn);
          });
        }
        var rejectBtn = U.$('.dec-reject', card);
        if (rejectBtn) {
          rejectBtn.addEventListener('click', function () {
            Decisions.answer(id, '拒绝', rejectBtn);
          });
        }
      });
    },

    /**
     * 回答一条待决（空回答拒绝；成功后刷新列表）。
     * @param {number} id 决策 id
     * @param {string} text 回答文本
     * @param {HTMLButtonElement|null} btn 触发按钮（置灰防重复提交）
     * @returns {void}
     */
    answer: function (id, text, btn) {
      if (!text) {
        U.toast('warn', '请先输入回答（或点建议选项填入）');
        return;
      }
      if (btn) btn.disabled = true;
      A.decisions.answer(id, text).then(function () {
        U.toast('success', '已回答，员工继续推进');
        return Decisions.load();
      }).catch(function (e) {
        if (btn) btn.disabled = false;
        U.toast('error', '回答失败：' + (e && e.message ? e.message : e));
      });
    },

    /**
     * 跳过（取消）一条待决。
     * @param {number} id 决策 id
     * @param {HTMLButtonElement|null} btn 触发按钮
     * @returns {void}
     */
    cancel: function (id, btn) {
      if (btn) btn.disabled = true;
      A.decisions.cancel(id).then(function () {
        U.toast('info', '已取消该待决事项');
        return Decisions.load();
      }).catch(function (e) {
        if (btn) btn.disabled = false;
        U.toast('error', '取消失败：' + (e && e.message ? e.message : e));
      });
    },

    /* ---------------- SSE 钩子 ---------------- */

    /**
     * decision.created：红点 +1、提示条、待决卡片实时插入（视图已打开时）。
     * payload 至少含 {id, window_id, question}；source=confirm 走红色高危提示。
     * 缺字段时回退整页刷新。
     * @param {Object} payload 事件负载
     * @returns {void}
     */
    onCreated: function (payload) {
      payload = payload || {};
      var dot = document.getElementById('nav-decisions-badge');
      var cur = parseInt(String((dot && dot.textContent) || '0'), 10);
      if (!isFinite(cur)) cur = 0;
      Decisions.updateBadge(cur + 1);
      if (String(payload.source || '') === 'confirm') {
        /* 高危命令待审批：红色高亮提示（红点在导航「待决」上） */
        U.toast('error', '⚠ 高危操作待审批（来自 ' + U.esc(Decisions.winName(payload.window_id)) +
          '）——去「待决」批准或拒绝', 8000);
      } else {
        U.toast('warn', '新待决：' + String(payload.question || '员工有问题要问你') +
          '（来自 ' + U.esc(Decisions.winName(payload.window_id)) + '）', 6000);
      }

      var host = document.getElementById('dec-list');
      var view = document.getElementById('view-decisions');
      if (!host || !view || !view.classList.contains('active')) return;

      if (payload.id && payload.question) {
        var d = {
          id: payload.id,
          window_id: payload.window_id || '',
          question: payload.question,
          options: Array.isArray(payload.options) ? payload.options : [],
          status: 'pending',
          source: payload.source || '',
          created_at: payload.created_at || new Date().toISOString(),
          expires_at: payload.expires_at || ''
        };
        if (Decisions.pending.some(function (x) { return x.id === d.id; })) return;
        Decisions.pending.unshift(d);
        if (!Decisions.pending.length) Decisions.render();
        else {
          var emptyNode = U.$('.empty', host);
          if (emptyNode) Decisions.render();
          else host.insertAdjacentHTML('afterbegin', Decisions.card(d));
          Decisions.bind();
        }
      } else {
        Decisions.load(); /* payload 不全：整页刷新兜底 */
      }
    },

    /**
     * decision.answered / decision.timeout：待决移入历史、红点同步。
     * @param {Object} payload 事件负载
     * @returns {void}
     */
    onClosed: function (payload) {
      payload = payload || {};
      var dot = document.getElementById('nav-decisions-badge');
      var cur = parseInt(String((dot && dot.textContent) || '0'), 10);
      if (!isFinite(cur)) cur = 0;
      Decisions.updateBadge(Math.max(0, cur - 1));
      var view = document.getElementById('view-decisions');
      if (view && view.classList.contains('active')) Decisions.load();
    }
  };

  /* SSE 松耦合注册：事件名 → 视图钩子（sse.js 先于本文件加载） */
  if (global.SSE) {
    global.SSE.on('decision.created', function (p) { Decisions.onCreated(p); });
    global.SSE.on('decision.answered', function (p) { Decisions.onClosed(p); });
    global.SSE.on('decision.timeout', function (p) { Decisions.onClosed(p); });
  }

  global.DogBuddyViews.Decisions = Decisions;
})(window);
