/* ============================================================
   DogBuddyCn 控制台 · app.js
   应用外壳：登录 / 路由 / 侧边栏 / 顶栏 / 状态轮询 / 快捷键 / 空状态动作
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  var V = global.DogBuddyViews;

  var TITLES = {
    overview: '总览', chat: '对话', decisions: '待决', windows: '工位', jobs: '任务',
    groups: '群组', capabilities: '能力', projects: '项目', models: '模型', ledger: '账本', costs: '成本', files: '文件管理器',
    logs: '日志审计', settings: '设置'
  };

  var App = {
    view: 'overview',
    statusTimer: null,
    tickTimer: null,
    booted: false,

    /* ---------------- 登录层 ---------------- */
    loginMode: 'token',

    showLogin: function (msg) {
      var mask = document.getElementById('login-mask');
      var shell = document.getElementById('app-shell');
      if (mask) mask.classList.remove('hidden');
      if (shell) shell.classList.add('hidden');
      if (App.statusTimer) { clearInterval(App.statusTimer); App.statusTimer = null; }
      if (msg) {
        var err = document.getElementById('login-error');
        if (err) { err.textContent = msg; err.classList.remove('hidden'); }
      }
    },

    hideLogin: function () {
      var mask = document.getElementById('login-mask');
      var shell = document.getElementById('app-shell');
      if (mask) mask.classList.add('hidden');
      if (shell) shell.classList.remove('hidden');
    },

    logout: function () {
      A.clearToken();
      App.showLogin('已退出登录');
    },

    bindLogin: function () {
      var tabs = document.getElementById('login-tabs');
      if (tabs) {
        U.$$('.seg-btn', tabs).forEach(function (b) {
          b.addEventListener('click', function () {
            U.$$('.seg-btn', tabs).forEach(function (x) { x.classList.remove('active'); });
            b.classList.add('active');
            App.loginMode = b.getAttribute('data-mode');
            var label = document.getElementById('login-label');
            var input = document.getElementById('login-input');
            if (label) {
              label.textContent = App.loginMode === 'token'
                ? '访问令牌 (X-Auth-Token)' : '控制台密码';
            }
            if (input) {
              input.placeholder = App.loginMode === 'token' ? '粘贴 token' : '输入密码';
              input.value = '';
            }
          });
        });
      }
      var form = document.getElementById('login-form');
      if (form) {
        form.addEventListener('submit', function (e) {
          e.preventDefault();
          var input = document.getElementById('login-input');
          var btn = document.getElementById('btn-login');
          var errBox = document.getElementById('login-error');
          var value = (input && input.value || '').trim();
          if (!value) {
            if (errBox) { errBox.textContent = '请输入' + (App.loginMode === 'token' ? '访问令牌' : '密码'); errBox.classList.remove('hidden'); }
            return;
          }
          if (errBox) errBox.classList.add('hidden');
          if (btn) { btn.disabled = true; btn.textContent = '验证中…'; }
          var payload = App.loginMode === 'token' ? { token: value } : { password: value };
          A.login(payload).then(function (r) {
            if (r && r.token) A.setToken(r.token);
            else A.setToken(value); /* 兼容只返回 ok 的部署：直接用输入值作 token */
            if (btn) { btn.disabled = false; btn.textContent = '进入控制台'; }
            App.hideLogin();
            App.boot();
            U.toast('success', '欢迎回来');
          }).catch(function (e) {
            if (btn) { btn.disabled = false; btn.textContent = '进入控制台'; }
            if (errBox) {
              errBox.textContent = e.status === 401 ? '凭据错误，请重试' : (e.message || '登录失败');
              errBox.classList.remove('hidden');
            }
          });
        });
      }
    },

    /* ---------------- 路由 ---------------- */
    go: function (view, params) {
      if (!TITLES[view]) view = 'overview';
      App.view = view;
      U.$$('.view').forEach(function (v) {
        v.classList.toggle('active', v.id === 'view-' + view);
      });
      U.$$('#nav-main .nav-item').forEach(function (n) {
        n.classList.toggle('active', n.getAttribute('data-view') === view);
      });
      var title = document.getElementById('page-title');
      if (title) title.textContent = TITLES[view];
      var side = document.getElementById('sidebar');
      if (side) side.classList.remove('open');
      App.renderView(params);
    },

    renderView: function (params) {
      switch (App.view) {
        case 'overview':
          V.Overview.load();
          break;
        case 'chat':
          if (V.Chat) V.Chat.load();
          break;
        case 'windows':
          if (params && params.win) V.Windows.selectedId = params.win;
          V.Windows.load();
          break;
        case 'jobs':
          V.Jobs.load();
          break;
        case 'groups':
          if (V.Groups) V.Groups.load();
          break;
        case 'capabilities':
          if (V.Capabilities) V.Capabilities.load();
          break;
        case 'decisions':
          if (V.Decisions) V.Decisions.load();
          break;
        case 'ledger':
          if (V.Ledger) V.Ledger.load();
          break;
        case 'costs':
          if (V.Costs) V.Costs.load();
          break;
        case 'projects':

          if (V.Projects) V.Projects.load();

          break;

        case 'models':
          if (V.Models) V.Models.load();
          break;
        case 'files':
          /* v0.10.39：App.go('files', {path, workspace}) —— 从工位/群组/项目页
             跳转时直接落到那个工作区（不用再手动翻目录） */
          global.FileMgr.load(params || {});
          break;
        case 'logs':
          V.Logs.load();
          break;
        case 'settings':
          V.Settings.load();
          break;
        default:
          break;
      }
    },

    reloadView: function () {
      App.renderView();
    },

    /* ---------------- 系统状态 ---------------- */
    llmModel: '',
    refreshStatus: function () {
      return A.status().then(function (st) {
        App.llmModel = st.llm_model || '';
        var dot = document.getElementById('sys-dot');
        var title = document.getElementById('sys-running');
        var rate = document.getElementById('sys-rate');
        var wins = document.getElementById('sys-wins');
        var jobs = document.getElementById('sys-jobs');
        if (dot) {
          dot.className = 'dot ' + (st.running ? 'dot-run pulse' : 'dot-idle');
        }
        if (title) title.textContent = st.running ? '运行中' : '已停止';
        if (rate) {
          var waiting = st.rate_limit && st.rate_limit.waiting;
          rate.classList.toggle('hidden', !waiting);
          if (waiting) {
            rate.textContent = '限速等待' + (st.rate_limit.attempt ? ' · 第 ' + st.rate_limit.attempt + ' 次' : '');
          }
        }
        /* 顶栏限速徽标：SSE 实时置位，这里兜底清除（限速结束没有专门事件） */
        var topRate = document.getElementById('top-rate');
        if (topRate && !(st.rate_limit && st.rate_limit.waiting)) {
          topRate.classList.add('hidden');
        }
        if (wins) wins.textContent = st.windows || 0;
        if (jobs) jobs.textContent = st.jobs || 0;
        return st;
      }).catch(function () { return null; });
    },

    /* ---------------- 快捷键与全局交互 ---------------- */
    bindGlobal: function () {
      /* 导航 */
      U.$$('#nav-main .nav-item').forEach(function (n) {
        n.addEventListener('click', function () {
          App.go(n.getAttribute('data-view'));
        });
      });
      /* 顶栏 */
      var refresh = document.getElementById('btn-refresh');
      if (refresh) {
        refresh.addEventListener('click', function () {
          App.reloadView();
          App.refreshStatus();
          U.toast('info', '已刷新');
        });
      }
      var logout = document.getElementById('btn-logout');
      if (logout) {
        logout.addEventListener('click', function () {
          UI.confirm({ title: '退出登录', okLabel: '退出', text: '确定退出当前会话？' })
            .then(function (ok) { if (ok) App.logout(); });
        });
      }
      var toggle = document.getElementById('btn-sidebar-toggle');
      if (toggle) {
        toggle.addEventListener('click', function () {
          var side = document.getElementById('sidebar');
          if (side) side.classList.toggle('open');
        });
      }
      /* 全局搜索：跳转到工位页并过滤 */
      var search = document.getElementById('global-search');
      if (search) {
        search.addEventListener('keydown', function (e) {
          if (e.key !== 'Enter') return;
          var kw = search.value.trim();
          if (!kw) return;
          V.Windows.keyword = kw;
          var winSearch = document.getElementById('win-search');
          if (winSearch) winSearch.value = kw;
          App.go('windows');
          U.toast('info', '在工位中搜索：' + kw);
        });
      }
      /* 视图内跳转按钮 */
      document.addEventListener('click', function (e) {
        var goto = e.target.closest && e.target.closest('[data-goto]');
        if (goto) {
          App.go(goto.getAttribute('data-goto'));
          return;
        }
        var act = e.target.closest && e.target.closest('[data-empty-action]');
        if (act) {
          var id = act.getAttribute('data-empty-action');
          if (id === 'new-window') V.Windows.openCreateModal();
          else if (id === 'new-job') V.Jobs.openForm(null);
          else if (id === 'new-group' && V.Groups) V.Groups.openCreate();
          else if (id === 'fm-upload') {
            var input = document.getElementById('fm-upload-input');
            if (input) input.click();
          }
        }
      });
      /* 工位搜索 */
      var winSearch = document.getElementById('win-search');
      if (winSearch) {
        winSearch.addEventListener('input', U.debounce(function () {
          V.Windows.keyword = winSearch.value;
          V.Windows.renderList();
        }, 150));
      }
      /* 账本天数切换（7 / 30 / 90） */
      var ledDays = document.getElementById('led-days');
      if (ledDays) {
        U.$$('.seg-btn', ledDays).forEach(function (b) {
          b.addEventListener('click', function () {
            if (V.Ledger) V.Ledger.switchDays(b.getAttribute('data-days'));
          });
        });
      }

      /* SSE：限速退避徽标（顶栏实时显示「限速等待中 第N次退避」） */
      if (global.SSE) {
        global.SSE.on('rate_limit.status', function (p) {
          var topRate = document.getElementById('top-rate');
          if (!topRate) return;
          var attempt = Number(p && p.attempt) || 0;
          topRate.textContent = '限速等待中 ' + (attempt ? '第' + attempt + '次退避' : '退避中');
          topRate.classList.remove('hidden');
        });
        /* P0（v0.10.37）：运行台账实时事件 —— 顶栏「N 个任务在跑」+ 抽屉
           （owner："你在哪跑呢，我咋没看见"：群组/API 触发的运行以前完全不可见） */
        if (global.Runs) {
          ['run.started', 'run.updated', 'run.finished'].forEach(function (t) {
            global.SSE.on(t, function (p) { global.Runs.onEvent(t, p); });
          });
          global.Runs.bind();
        }
      }

      /* 新建按钮 */
      var nw = document.getElementById('btn-new-window');
      if (nw) nw.addEventListener('click', function () { V.Windows.openCreateModal(); });
      var nj = document.getElementById('btn-new-job');
      if (nj) nj.addEventListener('click', function () { V.Jobs.openForm(null); });
      var gn = document.getElementById('grp-new-btn');
      if (gn) gn.addEventListener('click', function () {
        if (V.Groups) V.Groups.openCreate();
      });

      /* 任务筛选 */
      var jf = document.getElementById('job-filter-window');
      if (jf) {
        jf.addEventListener('change', function () {
          V.Jobs.filterWindow = jf.value;
          V.Jobs.load();
        });
      }

      /* 日志 Tab / 过滤 */
      var logTabs = document.getElementById('log-tabs');
      if (logTabs) {
        U.$$('.tab', logTabs).forEach(function (t) {
          t.addEventListener('click', function () {
            V.Logs.switchTab(t.getAttribute('data-tab'));
          });
        });
      }
      var logFilter = document.getElementById('log-filter');
      if (logFilter) {
        logFilter.addEventListener('input', U.debounce(function () { V.Logs.render(); }, 150));
      }
      ['log-window-filter', 'log-status-filter'].forEach(function (id) {
        var n = document.getElementById(id);
        if (n) n.addEventListener('change', function () { V.Logs.render(); });
      });
      var logRefresh = document.getElementById('log-refresh');
      if (logRefresh) {
        logRefresh.addEventListener('click', function () {
          V.Logs.load();
          U.toast('info', '日志已刷新');
        });
      }
      var decRefresh = document.getElementById('dec-refresh');
      if (decRefresh) {
        decRefresh.addEventListener('click', function () {
          if (V.Decisions) V.Decisions.load();
        });
      }

      /* 键盘快捷键 */
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
          if (U.closeTopModal()) return;
          var wiz = document.getElementById('wizard-root');
          if (wiz && wiz.innerHTML) { V.Settings.closeWizard(); }
          return;
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
          e.preventDefault();
          var s = document.getElementById('global-search');
          if (s) s.focus();
          return;
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
          /* 文件编辑器 Ctrl+S 由 filemanager 处理；这里仅兜底阻止默认保存 */
          var editor = document.getElementById('fmp-editor');
          if (editor) e.preventDefault();
        }
      });

      /* 倒计时刷新（任务页） */
      App.tickTimer = setInterval(function () {
        if (App.view !== 'jobs') return;
        U.$$('[data-countdown]').forEach(function (n) {
          n.textContent = U.countdown(n.getAttribute('data-countdown'));
        });
      }, 1000);
    },

    /* ---------------- 启动 ---------------- */
    boot: function () {
      /* SSE 实时事件接上（断线自动重连；退订/分发见 sse.js 与各视图钩子） */
      if (global.SSE) global.SSE.start();
      /* 待决红点初值（后续由 SSE decision.* 实时增减） */
      if (V.Decisions) {
        A.decisions.list('pending', 1).then(function (r) {
          V.Decisions.updateBadge((r && r.pending_count) || 0);
        }).catch(function () { /* 忽略 */ });
      }
      App.refreshStatus().then(function (st) {
        if (st && st.llm_configured === false) {
          /* 首次设置：总览横幅 + 自动打开向导 */
          V.Overview.showBanner(false);
          if (!App.booted) V.Settings.openWizard();
        }
      }).catch(function () { /* 忽略 */ });
      App.statusTimer = setInterval(function () {
        App.refreshStatus();
      }, 15000);
      App.go(App.view || 'overview');
      App.booted = true;
    },

    init: function () {
      App.bindLogin();
      App.bindGlobal();
      global.FileMgr.init();
      if (A.getToken()) {
        App.hideLogin();
        App.boot();
      } else {
        App.showLogin();
      }
    }
  };

  global.App = App;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { App.init(); });
  } else {
    App.init();
  }
})(window);
