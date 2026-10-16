/* ============================================================
   DogBuddyCn 控制台 · views-settings.js
   设置（LLM 接入 / LLM 配置套·views-profiles.js / 限速重试 / 面板对接 / 网关与回报 / 通知通道 / 白名单 / 安全）
   首次设置向导（7 步：provider → base_url → api_key → 拉取模型 → 选模型 → 测试连接 → 保存）
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var PROVIDERS = [
    { value: 'openai_compat', label: 'OpenAI 兼容 (openai_compat)' },
    { value: 'ollama', label: 'Ollama 本地 (ollama)' }
  ];
  var PRESETS = {
    openai_compat: [
      { label: 'OpenAI', url: 'https://api.openai.com/v1' },
      { label: 'DeepSeek', url: 'https://api.deepseek.com/v1' },
      { label: '自定义', url: '' }
    ],
    ollama: [{ label: 'Ollama 默认', url: 'http://127.0.0.1:11434' }]
  };
  var STRATEGIES = ['brief', 'full', 'none'];
  var WL_ROLES = [
    { value: 'owner', label: 'owner（所有者）' },
    { value: 'admin', label: 'admin（管理员）' },
    { value: 'viewer', label: 'viewer（只读）' }
  ];

  function masked(key) { return !!key && /^\*{4,}/.test(String(key)); }

  /* ============================================================
     设置卡片
     ============================================================ */
  var Settings = {
    data: null,
    modelList: [],

    load: function () {
      var host = document.getElementById('settings-cards');
      if (host) host.innerHTML = U.skeleton('cards') + U.skeleton('cards');
      var Approval = (global.DogBuddyViews || {}).Approval;
      return Promise.all([
        A.settings.get(),
        A.whitelist.list(),
        Approval ? Approval.load() : Promise.resolve()
      ]).then(function (res) {
        Settings.data = res[0] || {};
        Settings.whitelist = res[1] || [];
        Settings.render();
      }).catch(function (e) {
        U.toast('error', '设置加载失败：' + e.message);
      });
    },

    render: function () {
      var host = document.getElementById('settings-cards');
      if (!host) return;
      var d = Settings.data || {};
      var llm = d.llm || {}, panel = d.panel || {}, gw = d.gateway || {}, rep = d.report || {};
      var Profiles = (global.DogBuddyViews || {}).Profiles;
      var Approval = (global.DogBuddyViews || {}).Approval;
      host.innerHTML =
        Settings.cardLLM(llm) +
        (Profiles ? Profiles.cardHTML() : '') +
        Settings.cardRetry(llm.retry || {}) +
        Settings.cardPanel(panel) +
        Settings.cardGateway(gw, rep) +
        Settings.cardNotify(d.notify || {}) +
        Settings.cardWhitelist() +
        (Approval ? Approval.cardHTML() : '') +
        Settings.cardSecurity();
      Settings.bind();
      if (Profiles) Profiles.bind();
      if (Approval) Approval.bind();
    },

    /* ---------- 卡片模板 ---------- */
    cardLLM: function (llm) {
      return '<div class="card set-card" data-group="llm">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('cpu', 'sm') + '</span>' +
        '<div><h3>LLM 兜底配置</h3><p>服务商 · 地址 · 兜底密钥 · 参数 —— <b>管理员用的模型、以及每个模型的上下文，都在「模型管理」页</b></p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="field"><span class="field-label">服务商 Provider</span>' +
        '<select class="select" id="set-provider">' +
        PROVIDERS.map(function (p) {
          return '<option value="' + p.value + '"' + (llm.provider === p.value ? ' selected' : '') + '>' + p.label + '</option>';
        }).join('') + '</select></div>' +
        '<div class="field"><span class="field-label">Base URL</span>' +
        '<input class="input mono" id="set-base-url" value="' + U.esc(llm.base_url || '') + '" placeholder="https://api.openai.com/v1"></div>' +
        '<div class="field"><span class="field-label">全局 API Key（已停用）</span>' +
        '<input class="input mono" id="set-api-key" type="password" value="" disabled ' +
        'placeholder="' + U.esc(llm.api_key || '（未设置）') + '">' +
        '<div class="hint">v0.10.25：**这个全局密钥框已停用** —— 密钥统一在「模型管理」页的' +
        '**模型条目**上配置（点「新增模型」或那一行的编辑，填 API Key），' +
        '点「设为管理员模型」时会连同密钥一起切换。这里只读显示当前生效的密钥掩码。' +
        '</div></div>' +
        '<div class="set-row" style="border:0;padding-top:2px;gap:10px">' +
        '<span class="hint">管理员当前模型：<b class="mono">' + U.esc(llm.model || '(未设置)') + '</b> · ' +
        '换模型请到「模型管理」页点「设为管理员模型」（每个模型可自带密钥与上下文）</span>' +
        '<button class="btn btn-soft btn-sm" id="set-goto-models" type="button">打开模型管理</button></div>' +
        '<div class="set-row" style="border:0;padding-top:2px">' +
        '<button class="btn btn-soft btn-sm" id="set-test-llm" type="button">' + U.icon('zap', 'sm') + '测试兜底配置</button>' +
        '<span class="hint" id="set-test-result">只测「地址 + 密钥」能否连上并列出模型，不做对话</span></div>' +
        '<button class="adv-toggle" id="set-adv-toggle" type="button">高级参数' + U.icon('chevron-d', 'sm') + '</button>' +
        '<div class="hidden" id="set-adv">' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">temperature</span>' +
        '<input class="input num" id="set-temperature" type="number" step="0.1" value="' + U.esc(llm.temperature !== undefined ? llm.temperature : 0.7) + '"></div>' +
        '<div class="field"><span class="field-label">max_context_tokens</span>' +
        '<input class="input num" id="set-max-ctx" type="number" value="' + U.esc(llm.max_context_tokens || 128000) + '"></div>' +
        '</div><div class="set-two">' +
        '<div class="field"><span class="field-label">compact_threshold</span>' +
        '<input class="input num" id="set-compact" type="number" step="0.05" value="' + U.esc(llm.compact_threshold !== undefined ? llm.compact_threshold : 0.7) + '"></div>' +
        '<div class="field"><span class="field-label">request_timeout（秒）</span>' +
        '<input class="input num" id="set-timeout" type="number" value="' + U.esc(llm.request_timeout || 120) + '"></div>' +
        '</div></div></div>' +
        '<div class="set-card-foot"><span class="hint">api_key 掩码时不回传，留空即保持已保存值。</span>' +
        '<div class="spacer"></div><button class="btn btn-primary btn-sm" data-save="llm">保存该组</button></div></div>';
    },

    cardRetry: function (r) {
      return '<div class="card set-card" data-group="retry">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('refresh', 'sm') + '</span>' +
        '<div><h3>限速重试</h3><p>遇到限速 / 瞬时错误时的退避策略</p></div></div>' +
        '<div class="set-card-body">' +
        '<p class="hint" style="margin-bottom:12px">触发限速后按「基础间隔 × 倍率」递增等待，直至超过最大间隔或达到最大重试次数。' +
        '例：基础 10s、指数退避 → 10s → 20s → 40s → 80s …</p>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">base_interval（秒）</span>' +
        '<input class="input num" id="set-rbase" type="number" value="' + U.esc(r.base_interval !== undefined ? r.base_interval : 10) + '"></div>' +
        '<div class="field"><span class="field-label">max_interval（秒）</span>' +
        '<input class="input num" id="set-rmax" type="number" value="' + U.esc(r.max_interval !== undefined ? r.max_interval : 300) + '"></div>' +
        '</div><div class="set-two">' +
        '<div class="field"><span class="field-label">max_retries</span>' +
        '<input class="input num" id="set-rretries" type="number" value="' + U.esc(r.max_retries !== undefined ? r.max_retries : 5) + '"></div>' +
        '<div class="field"><span class="field-label">backoff</span>' +
        '<select class="select" id="set-rbackoff">' +
        ['exponential', 'fixed', 'linear'].map(function (b) {
          return '<option' + (r.backoff === b ? ' selected' : '') + '>' + b + '</option>';
        }).join('') + '</select></div></div>' +
        '<div class="backoff-preview" id="set-backoff-preview"></div></div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-primary btn-sm" data-save="retry">保存该组</button></div></div>';
    },

    cardPanel: function (p) {
      return '<div class="card set-card" data-group="panel">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('layers', 'sm') + '</span>' +
        '<div><h3>面板对接</h3><p>外部管理面板联动</p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="set-row"><span class="field-label">启用面板对接</span>' +
        '<label class="switch"><input type="checkbox" id="set-panel-enabled"' + (p.enabled ? ' checked' : '') + '><i></i></label></div>' +
        '<div class="field"><span class="field-label">面板 Base URL</span>' +
        '<input class="input mono" id="set-panel-url" value="' + U.esc(p.base_url || '') + '" placeholder="http://127.0.0.1:8080"></div>' +
        '<div class="field"><span class="field-label">Agent Token' +
        (p.agent_token ? '（已保存：' + U.esc(p.agent_token) + '，留空保持不变）' : '') + '</span>' +
        '<input class="input mono" id="set-panel-token" type="password" value="" placeholder="' +
        U.esc(p.agent_token ? '留空保持不变' : '从面板「Agent 接入」复制') + '"></div></div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-primary btn-sm" data-save="panel">保存该组</button></div></div>';
    },

    cardGateway: function (gw, rep) {
      return '<div class="card set-card" data-group="gateway">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('zap', 'sm') + '</span>' +
        '<div><h3>网关与回报</h3><p>消息网关 · 推送目标 · 默认回报策略</p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="set-row"><span class="field-label">启用消息网关</span>' +
        '<label class="switch"><input type="checkbox" id="set-gw-enabled"' + (gw.enabled ? ' checked' : '') + '><i></i></label></div>' +
        '<div class="field"><span class="field-label">push_target（JSON）</span>' +
        '<textarea class="textarea mono" id="set-gw-target" style="min-height:72px" placeholder=\'{"type":"group","id":"123"}\'>' +
        U.esc(gw.push_target ? JSON.stringify(gw.push_target) : '') + '</textarea>' +
        '<div class="hint">推送目标对象，按后端约定序列化为 JSON。</div></div>' +
        '<div class="field"><span class="field-label">默认回报策略</span>' +
        '<select class="select" id="set-report-strategy">' +
        STRATEGIES.map(function (s) {
          return '<option' + (rep.default_strategy === s ? ' selected' : '') + '>' + s + '</option>';
        }).join('') + '</select></div></div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-primary btn-sm" data-save="gateway">保存该组</button></div></div>';
    },

    /* 通知通道（只读展示 notify.channels；通道名供任务「回报通道」使用） */
    cardNotify: function (notify) {
      var channels = (notify && notify.channels) || [];
      var body;
      if (!channels.length) {
        body = '<div class="pf-empty">' + U.empty({
          art: 'chat', title: '当前仅控制台输出', desc: '尚未配置外部通知通道，任务回报只输出到控制台。'
        }) + '</div>';
      } else {
        body = '<div class="notify-list">' + channels.map(function (c) {
          return '<div class="notify-row' + (c.enabled ? '' : ' notify-off') + '">' +
            '<div class="notify-main">' +
            '<div class="notify-name">' +
            '<span class="badge badge-blue mono">' + U.esc(c.name || '—') + '</span>' +
            '<span class="badge badge-grey mono">' + U.esc(c.type || '—') + '</span>' +
            (c.enabled ? '<span class="badge badge-green">启用</span>' : '<span class="badge badge-amber">已停用</span>') +
            '</div>' +
            '<div class="notify-meta">' +
            (c.url ? '<span class="mono">' + U.esc(c.url) + '</span>' : '') +
            (c.target_type
              ? '<span class="muted">target ' + U.esc(c.target_type) +
              (c.target_id ? ':' + U.esc(c.target_id) : '') + '</span>'
              : '') +
            '</div></div></div>';
        }).join('') + '</div>';
      }
      return '<div class="card set-card" data-group="notify">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('send', 'sm') + '</span>' +
        '<div><h3>通知通道</h3><p>任务回报 · 消息推送的目标通道</p></div></div>' +
        '<div class="set-card-body">' + body +
        '<div class="hint" style="margin-top:10px">任务的「回报通道」请填此处的' +
        '<b>通道名</b>（如 console / webhook-ops）；留空 = 全部通道。</div></div></div>';
    },

    cardWhitelist: function () {
      var rows = (Settings.whitelist || []).map(function (w) {
        var role = w.role || 'admin';
        return '<tr><td class="mono">' + U.esc(w.qq_id) + '</td><td>' + U.esc(w.name || '—') + '</td>' +
          '<td><select class="select wl-role" data-wl-role="' + U.esc(w.qq_id) + '" data-wl-name="' + U.esc(w.name || '') + '">' +
          WL_ROLES.map(function (r) {
            return '<option value="' + r.value + '"' + (role === r.value ? ' selected' : '') + '>' + r.label + '</option>';
          }).join('') + '</select></td>' +
          '<td><div class="row-actions"><button class="icon-btn danger" data-wl-del="' + U.esc(w.qq_id) + '">' +
          U.icon('trash', 'sm') + '</button></div></td></tr>';
      }).join('');
      return '<div class="card set-card" data-group="whitelist">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('shield', 'sm') + '</span>' +
        '<div><h3>白名单</h3><p>允许通过网关交互的 QQ 号</p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="table-wrap"><table class="table">' +
        '<thead><tr><th>QQ ID</th><th>备注名</th><th>角色</th><th class="th-actions">操作</th></tr></thead>' +
        '<tbody id="wl-body">' + (rows || '<tr><td colspan="4" class="muted small" style="text-align:center;padding:18px">暂无白名单成员</td></tr>') + '</tbody>' +
        '</table></div>' +
        '<div style="display:flex;gap:8px;margin-top:12px">' +
        '<input class="input mono" id="wl-qq" placeholder="QQ 号" style="width:150px">' +
        '<input class="input" id="wl-name" placeholder="备注名（可选）">' +
        '<button class="btn btn-primary btn-sm" id="wl-add">' + U.icon('plus', 'sm') + '添加</button></div></div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-ghost btn-sm" data-save="whitelist">刷新列表</button></div></div>';
    },

    cardSecurity: function () {
      return '<div class="card set-card" data-group="security">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('lock', 'sm') + '</span>' +
        '<div><h3>安全</h3><p>控制台访问与本地会话</p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="set-row"><span class="field-label">鉴权方式</span><span class="badge badge-green">X-Auth-Token</span></div>' +
        '<div class="set-row"><span class="field-label">本地登录态</span><span class="hint mono">localStorage.dogbuddy_token</span></div>' +
        '<p class="hint" style="margin-top:10px">访问令牌 / 密码保存在服务端配置。若怀疑泄露，请在服务端更新后重新登录；' +
        '离开公用设备前建议点击下方按钮立即锁定控制台。</p></div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-danger btn-sm" id="set-lock">' + U.icon('lock', 'sm') + '锁定控制台</button></div></div>';
    },

    /* ---------- 交互绑定 ---------- */
    bind: function () {
      /* 高级折叠 */
      var advBtn = document.getElementById('set-adv-toggle');
      if (advBtn) {
        advBtn.addEventListener('click', function () {
          advBtn.classList.toggle('open');
          var adv = document.getElementById('set-adv');
          if (adv) adv.classList.toggle('hidden');
        });
      }
      /* 模型本身在「模型管理」页维护：这里只留"去那边"的入口 + 兜底配置连通性测试 */
      var gotoModels = document.getElementById('set-goto-models');
      if (gotoModels) {
        gotoModels.addEventListener('click', function () {
          if (global.App && global.App.go) global.App.go('models');
        });
      }
      var testBtn = document.getElementById('set-test-llm');
      if (testBtn) {
        testBtn.addEventListener('click', function () {
          Settings.runTest(testBtn, document.getElementById('set-test-result'));
        });
      }
    },

    renderBackoff: function () {
      var host = document.getElementById('set-backoff-preview');
      if (!host) return;
      var base = Number((document.getElementById('set-rbase') || {}).value) || 10;
      var max = Number((document.getElementById('set-rmax') || {}).value) || 300;
      var mode = ((document.getElementById('set-rbackoff') || {}).value) || 'exponential';
      var vals = [], v = base;
      for (var i = 0; i < 6; i++) {
        vals.push(Math.min(Math.round(v), max));
        v = mode === 'fixed' ? base : (mode === 'linear' ? base * (i + 2) : v * 2);
      }
      var peak = Math.max.apply(null, vals) || 1;
      host.innerHTML = vals.map(function (n) {
        var h = Math.max(8, Math.round(n / peak * 64));
        return '<div class="backoff-bar"><i style="height:' + h + 'px"></i><span>' + n + 's</span></div>';
      }).join('') + '<div class="backoff-bar" style="flex:0"><i style="height:0"></i><span>→ max ' + max + 's</span></div>';
    },

    runTest: function (btn, out) {
      var payload = {};
      var p = document.getElementById('set-provider');
      var b = document.getElementById('set-base-url');
      var k = document.getElementById('set-api-key');
      if (p) payload.provider = p.value;
      if (b && b.value.trim()) payload.base_url = b.value.trim();
      if (k && k.value.trim()) payload.api_key = k.value.trim();
      /* 不传模型：这里测的是"兜底配置能不能连上 + 能列出多少模型"；
         按模型测连接在模型管理页（那条可以自带密钥） */
      if (btn) { btn.disabled = true; btn.textContent = '测试中…'; }
      if (out) out.textContent = '正在测试连接…';
      A.settings.testLLM(payload).then(function (r) {
        if (r.ok) {
          U.toast('success', '连接成功 · ' + (r.latency_ms || 0) + 'ms');
          if (out) {
            out.innerHTML = '<span style="color:var(--ok)">连接成功</span> · 延迟 ' +
              U.esc(r.latency_ms || 0) + 'ms · 模型 ' + U.esc(r.model_count || 0) + ' 个' +
              (r.chat_sample ? '<br>样例回复：' + U.esc(String(r.chat_sample).slice(0, 120)) : '');
          }
        } else {
          U.toast('error', '连接失败：' + (r.message || '未知错误'));
          if (out) {
            out.innerHTML = '<span style="color:var(--err)">失败</span> · ' +
              U.esc(r.message || '') + (r.chat_error ? ' · ' + U.esc(r.chat_error) : '');
          }
        }
      }).catch(function (e) {
        U.toast('error', '测试失败：' + e.message);
        if (out) out.textContent = '测试失败：' + e.message;
      }).then(function () {
        if (btn) { btn.disabled = false; btn.textContent = '测试连接'; }
      });
    },

    saveGroup: function (group) {
      var body = {};
      try {
        if (group === 'llm') {
          /* v0.10.25：全局密钥已停用 —— 这里不再读取/提交它（密钥归「模型管理」页的条目）。
             旧代码会读 #set-api-key，现在那个框是 disabled 的，读也读不到。 */
          var payload = {
            provider: document.getElementById('set-provider').value,
            base_url: document.getElementById('set-base-url').value.trim(),
            temperature: Number(document.getElementById('set-temperature').value),
            max_context_tokens: Number(document.getElementById('set-max-ctx').value),
            compact_threshold: Number(document.getElementById('set-compact').value),
            request_timeout: Number(document.getElementById('set-timeout').value)
          };
          body.llm = payload;
        } else if (group === 'retry') {
          body.llm = {
            retry: {
              base_interval: Number(document.getElementById('set-rbase').value),
              max_interval: Number(document.getElementById('set-rmax').value),
              max_retries: Number(document.getElementById('set-rretries').value),
              backoff: document.getElementById('set-rbackoff').value
            }
          };
        } else if (group === 'panel') {
          body.panel = {
            enabled: document.getElementById('set-panel-enabled').checked,
            base_url: document.getElementById('set-panel-url').value.trim()
          };
          /* 密钥：留空 = 不改（服务端也拦一道，绝不把掩码存成真 token） */
          var ptoken = document.getElementById('set-panel-token').value.trim();
          if (ptoken && !masked(ptoken)) body.panel.agent_token = ptoken;
        } else if (group === 'gateway') {
          var target = document.getElementById('set-gw-target').value.trim();
          var push = {};
          if (target) {
            try { push = JSON.parse(target); }
            catch (e) { U.toast('warn', 'push_target 不是合法 JSON'); return; }
          }
          body.gateway = {
            enabled: document.getElementById('set-gw-enabled').checked,
            push_target: push
          };
          body.report = { default_strategy: document.getElementById('set-report-strategy').value };
        } else if (group === 'whitelist') {
          Settings.load();
          return;
        } else {
          return;
        }
      } catch (e) {
        U.toast('error', '表单读取失败：' + e.message);
        return;
      }
      A.settings.update(body).then(function () {
        U.toast('success', '已保存');
        A.status().then(function (st) {
          if (global.App && App.refreshStatus) App.refreshStatus();
          Settings.data = Settings.data || {};
          if (st && st.llm_configured !== undefined && global.DogBuddyViews.Overview) {
            global.DogBuddyViews.Overview.showBanner(st.llm_configured === false);
          }
        }).catch(function () { /* 忽略 */ });
      }).catch(function (e) {
        U.toast('error', '保存失败：' + e.message);
      });
    },

    /* ============================================================
       首次设置向导（全屏层）
       ============================================================ */
    wizardState: null,

    openWizard: function () {
      var state = {
        step: 0,
        provider: 'openai_compat',
        base_url: 'https://api.openai.com/v1',
        api_key: '',
        model: '',
        models: [],
        modelError: '',
        tested: null,
        saved: false
      };
      Settings.wizardState = state;
      var root = document.getElementById('wizard-root');
      root.innerHTML = '';
      var mask = U.el('<div class="wizard-mask"></div>');
      var box = U.el(
        '<div class="wizard">' +
        '<div class="wizard-head">' +
        '<span class="brand-mark">' + U.icon('wand') + '</span>' +
        '<div style="flex:1"><h2>快速开始 · LLM 接入向导</h2><p>7 步完成模型配置，随时可在「设置」中修改</p></div>' +
        '<button class="icon-btn" id="wz-close">' + U.icon('x', 'sm') + '</button>' +
        '</div>' +
        '<div class="wizard-main">' +
        '<div class="wizard-rail" id="wz-rail"></div>' +
        '<div class="wizard-body" id="wz-body"></div>' +
        '</div>' +
        '<div class="wizard-foot">' +
        '<button class="btn btn-ghost" id="wz-prev">上一步</button>' +
        '<div class="spacer"></div>' +
        '<span class="hint" id="wz-status"></span>' +
        '<button class="btn btn-primary" id="wz-next">下一步</button>' +
        '</div></div>'
      );
      mask.appendChild(box);
      root.appendChild(mask);
      mask.addEventListener('mousedown', function (e) {
        if (e.target === mask) Settings.closeWizard();
      });
      document.getElementById('wz-close').addEventListener('click', Settings.closeWizard);
      document.getElementById('wz-prev').addEventListener('click', function () {
        Settings.wizardGo(state.step - 1);
      });
      document.getElementById('wz-next').addEventListener('click', Settings.wizardNext);
      Settings.renderWizard();
    },

    closeWizard: function () {
      var root = document.getElementById('wizard-root');
      if (root) root.innerHTML = '';
    },

    WIZ_STEPS: [
      { title: '选择服务商', desc: 'OpenAI 兼容接口覆盖绝大多数云服务商；Ollama 用于本地模型。' },
      { title: '填写 Base URL', desc: '服务商的接口地址。常用服务有一键预设。' },
      { title: '填写 API Key', desc: '仅保存在服务端配置，界面以掩码显示。' },
      { title: '拉取模型', desc: '从服务商获取可用模型列表，免手输。' },
      { title: '选择模型', desc: '从列表中选一个默认模型，也可手动输入。' },
      { title: '测试连接', desc: '真实调用一次：返回延迟、模型数量与聊天样例。' },
      { title: '保存配置', desc: '写入配置后向导横幅自动消失。' }
    ],

    renderWizard: function () {
      var state = Settings.wizardState;
      if (!state) return;
      /* 左侧步骤条 */
      var rail = document.getElementById('wz-rail');
      if (rail) {
        rail.innerHTML = Settings.WIZ_STEPS.map(function (s, i) {
          return '<div class="wstep' + (i === state.step ? ' active' : '') +
            (i < state.step ? ' done' : '') + '" data-step="' + i + '">' +
            '<span class="wstep-num">' + (i < state.step ? '✓' : (i + 1)) + '</span>' +
            '<span>' + U.esc(s.title) + '</span></div>';
        }).join('');
        U.$$('.wstep', rail).forEach(function (n) {
          n.addEventListener('click', function () {
            Settings.wizardGo(Number(n.getAttribute('data-step')));
          });
        });
      }
      /* 主体 */
      var body = document.getElementById('wz-body');
      if (!body) return;
      var s = Settings.WIZ_STEPS[state.step];
      var inner = '<h3>' + (state.step + 1) + '. ' + U.esc(s.title) + '</h3><p class="desc">' + U.esc(s.desc) + '</p>';
      if (state.step === 0) {
        inner += '<div class="chips" id="wz-provider-chips">' + PROVIDERS.map(function (p) {
          return '<button type="button" class="chip' + (state.provider === p.value ? ' active' : '') +
            '" data-p="' + p.value + '">' + U.esc(p.label) + '</button>';
        }).join('') + '</div>';
      } else if (state.step === 1) {
        inner += '<div class="chips" id="wz-preset-chips" style="margin-bottom:12px">' +
          (PRESETS[state.provider] || []).map(function (p) {
            return '<button type="button" class="chip' + (state.base_url === p.url && p.url ? ' active' : '') +
              '" data-url="' + U.esc(p.url) + '">' + U.esc(p.label) + '</button>';
          }).join('') + '</div>' +
          '<div class="field"><span class="field-label">Base URL</span>' +
          '<input class="input mono" id="wz-base-url" value="' + U.esc(state.base_url) + '" placeholder="https://…"></div>';
      } else if (state.step === 2) {
        inner += '<div class="field"><span class="field-label">API Key</span>' +
          '<input class="input mono" id="wz-api-key" type="password" value="' + U.esc(state.api_key) + '" placeholder="sk-…（Ollama 可留空）"></div>' +
          '<p class="hint">密钥通过 X-Auth-Token 鉴权的后端保存，不会出现在前端日志。</p>';
      } else if (state.step === 3) {
        inner += '<button class="btn btn-primary" id="wz-fetch">' + U.icon('refresh', 'sm') + '拉取模型</button>' +
          '<div id="wz-fetch-result" style="margin-top:14px">' +
          (state.modelError
            ? '<div class="form-error">' + U.esc(state.modelError) + '</div>'
            : (state.models.length
              ? '<div class="form-ok">已拉取 ' + state.models.length + ' 个模型：' +
                U.esc(state.models.slice(0, 6).join('、')) + (state.models.length > 6 ? ' …' : '') + '</div>'
              : '<p class="hint">点击上方按钮，从服务商获取模型列表。</p>')) +
          '</div>';
      } else if (state.step === 4) {
        inner += '<div class="field"><span class="field-label">默认模型</span>' +
          '<input class="input mono" id="wz-model" list="wz-model-options" value="' + U.esc(state.model) + '" placeholder="选择或输入模型名">' +
          '<select class="select mono" id="wz-model-select" style="margin-top:6px"><option value="">（点上面「拉取模型」后在这里选）</option></select></div>' +
          (state.models.length ? '<p class="hint">来自上一步拉取的模型列表，共 ' + state.models.length + ' 个。</p>'
            : '<p class="hint">还没有拉取模型列表，可直接手输模型名。</p>');
      } else if (state.step === 5) {
        inner += '<button class="btn btn-primary" id="wz-test">' + U.icon('zap', 'sm') + '测试连接</button>' +
          '<div id="wz-test-result" style="margin-top:14px">' + Settings.wizardTestHTML(state.tested) + '</div>';
      } else {
        inner += '<div class="test-result"><div class="tr-grid">' +
          '<div class="tr-item"><b>' + U.esc(state.provider) + '</b><span>Provider</span></div>' +
          '<div class="tr-item"><b style="font-size:13px">' + U.esc(state.base_url || '（空）') + '</b><span>Base URL</span></div>' +
          '<div class="tr-item"><b style="font-size:13px">' + U.esc(state.model || '（未选）') + '</b><span>模型</span></div>' +
          '<div class="tr-item"><b>' + (state.tested && state.tested.ok ? state.tested.latency_ms + 'ms' : '—') + '</b><span>测试延迟</span></div>' +
          '</div><p class="hint">点击右下角「保存配置」写入服务端，完成后回到总览。</p></div>';
      }
      body.innerHTML = inner;

      /* 主体事件 */
      U.$$('#wz-provider-chips .chip').forEach(function (c) {
        c.addEventListener('click', function () {
          state.provider = c.getAttribute('data-p');
          state.base_url = (PRESETS[state.provider][0] || {}).url || '';
          Settings.renderWizard();
        });
      });
      U.$$('#wz-preset-chips .chip').forEach(function (c) {
        c.addEventListener('click', function () {
          var url = c.getAttribute('data-url');
          if (url) {
            state.base_url = url;
            var input = document.getElementById('wz-base-url');
            if (input) input.value = url;
            U.$$('#wz-preset-chips .chip').forEach(function (x) { x.classList.remove('active'); });
            c.classList.add('active');
          } else {
            var inp = document.getElementById('wz-base-url');
            if (inp) { inp.value = ''; inp.focus(); }
          }
        });
      });
      var bInput = document.getElementById('wz-base-url');
      if (bInput) bInput.addEventListener('input', function () { state.base_url = bInput.value.trim(); });
      var kInput = document.getElementById('wz-api-key');
      if (kInput) kInput.addEventListener('input', function () { state.api_key = kInput.value; });
      var mInput = document.getElementById('wz-model');
      if (mInput) mInput.addEventListener('input', function () { state.model = mInput.value.trim(); });

      var fetchBtn = document.getElementById('wz-fetch');
      if (fetchBtn) {
        fetchBtn.addEventListener('click', function () {
          fetchBtn.disabled = true;
          fetchBtn.textContent = '拉取中…';
          var st = document.getElementById('wz-status');
          if (st) st.textContent = '正在拉取模型列表…';
          A.settings.models(state.provider, state.base_url, state.api_key).then(function (r) {
            state.models = r.models || [];
            state.modelError = r.error || '';
            if (r.error) U.toast('warn', '拉取失败：' + r.error);
            else U.toast('success', '已拉取 ' + state.models.length + ' 个模型');
            /* v0.10.11：**不再自动填第一个** —— 那会让 datalist 只剩一项。
               现在全量进下拉，用户自己选。 */
            U.fillModelSelect('wz-model-select', state.models, 'wz-model');
          }).catch(function (e) {
            state.modelError = e.message;
            U.toast('error', '拉取失败：' + e.message);
          }).then(function () {
            if (st) st.textContent = '';
            Settings.renderWizard();
          });
        });
      }
      var testBtn = document.getElementById('wz-test');
      if (testBtn) {
        testBtn.addEventListener('click', function () {
          testBtn.disabled = true;
          testBtn.textContent = '测试中…';
          A.settings.testLLM({
            provider: state.provider,
            base_url: state.base_url,
            api_key: state.api_key || undefined,
            model: state.model || undefined
          }).then(function (r) {
            state.tested = r;
            if (r.ok) U.toast('success', '连接成功 · ' + (r.latency_ms || 0) + 'ms');
            else U.toast('error', '连接失败：' + (r.message || ''));
            Settings.renderWizard();
          }).catch(function (e) {
            state.tested = { ok: false, message: e.message };
            U.toast('error', '测试失败：' + e.message);
            Settings.renderWizard();
          });
        });
      }

      /* 底部按钮 */
      var prev = document.getElementById('wz-prev');
      var next = document.getElementById('wz-next');
      if (prev) prev.style.visibility = state.step === 0 ? 'hidden' : 'visible';
      if (next) next.textContent = state.step === Settings.WIZ_STEPS.length - 1 ? '保存配置' : '下一步';
    },

    wizardTestHTML: function (t) {
      if (!t) return '<p class="hint">点击上方按钮测试连通性，将返回延迟、模型数与聊天样例。</p>';
      if (!t.ok) {
        return '<div class="form-error">连接失败：' + U.esc(t.message || '未知错误') +
          (t.chat_error ? '<br>聊天错误：' + U.esc(t.chat_error) : '') + '</div>';
      }
      return '<div class="test-result">' +
        '<div class="tr-grid">' +
        '<div class="tr-item"><b>' + U.esc(t.latency_ms || 0) + ' ms</b><span>延迟</span></div>' +
        '<div class="tr-item"><b>' + U.esc(t.model_count || 0) + '</b><span>模型数</span></div>' +
        '</div>' +
        (t.message ? '<div class="hint" style="margin-bottom:8px">' + U.esc(t.message) + '</div>' : '') +
        (t.chat_sample ? '<div class="tr-chat">' + U.esc(String(t.chat_sample)) + '</div>'
          : (t.chat_error ? '<div class="form-error">聊天样例失败：' + U.esc(t.chat_error) + '</div>' : '')) +
        '</div>';
    },

    wizardGo: function (i) {
      var state = Settings.wizardState;
      if (!state) return;
      state.step = Math.max(0, Math.min(Settings.WIZ_STEPS.length - 1, i));
      Settings.renderWizard();
    },

    wizardNext: function () {
      var state = Settings.wizardState;
      if (!state) return;
      if (state.step === 1 && !state.base_url) {
        U.toast('warn', '请填写 Base URL');
        return;
      }
      if (state.step === Settings.WIZ_STEPS.length - 1) {
        /* 保存：PUT /api/settings（api_key 为空不回传） */
        var payload = {
          provider: state.provider,
          base_url: state.base_url,
          model: state.model
        };
        if (state.api_key) payload.api_key = state.api_key;
        var btn = document.getElementById('wz-next');
        if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
        A.settings.update({ llm: payload }).then(function () {
          U.toast('success', 'LLM 配置已保存');
          state.saved = true;
          Settings.closeWizard();
          /* v0.10.17：**模型只有一个来源 —— 模型目录**。向导里填的模型立刻收进目录，
             免得出现"配置里有个模型、模型页里查不到"的两套说法。 */
          A.models.adoptCurrent().then(function (r) {
            var name = (r && r.model && r.model.model) || payload.model;
            if (name) U.toast('info', '已把 ' + name + ' 纳入模型目录（可在模型页切换/备注）');
          }).catch(function () { /* 收编失败不影响已保存的配置 */ });
          if (global.App) {
            App.refreshStatus().then(function () {
              if (App.reloadView) App.reloadView();
            }).catch(function () { /* 忽略 */ });
          }
        }).catch(function (e) {
          U.toast('error', '保存失败：' + e.message);
          if (btn) { btn.disabled = false; btn.textContent = '保存配置'; }
        });
        return;
      }
      Settings.wizardGo(state.step + 1);
    }
  };

  global.DogBuddyViews.Settings = Settings;
})(window);
