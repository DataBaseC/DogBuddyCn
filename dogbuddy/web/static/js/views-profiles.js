/* ============================================================
   DogBuddyCn 控制台 · views-profiles.js
   LLM 配置套（profiles）管理：列表 / 新建 / 编辑 / 复制 / 删除 / 设为默认
   弹窗内测试连接（test_llm 带 profile）· 工位与任务下拉选项统一由此提供
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var PROFILE_PROVIDERS = [
    { value: 'openai_compat', label: 'OpenAI 兼容 (openai_compat)' },
    { value: 'ollama', label: 'Ollama 本地 (ollama)' }
  ];
  var RETRY_BACKOFFS = ['exponential', 'fixed', 'linear'];
  /* api_key 掩码值不回传（沿用现有约定） */
  function maskedKey(k) { return !!k && /^\*{4,}/.test(String(k)); }
  function profileLabel(name) { return name ? name : '默认'; }
  /* keys 兼容数组 / 逗号字符串两种形态 → 文本 */
  function keysText(keys) {
    if (!keys) return '';
    if (Object.prototype.toString.call(keys) === '[object Array]') return keys.join(', ');
    return String(keys);
  }

  var Profiles = {
    data: null,        /* {active, names, profiles} */
    namesCache: null,  /* 下拉选项用的 names 缓存 */

    /* ============================================================
       卡片骨架（由 views-settings.js 的 render 挂入）
       ============================================================ */
    cardHTML: function () {
      return '<div class="card set-card" data-group="profiles">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('layers', 'sm') + '</span>' +
        '<div><h3>LLM 配置套</h3><p>多套模型接入 · 工位与任务可分别指定</p></div>' +
        '<div class="spacer"></div>' +
        '<button class="btn btn-primary btn-sm" id="pf-new">' + U.icon('plus', 'sm') + '新建套</button></div>' +
        '<div class="set-card-body"><div class="pf-list" id="pf-list">' + U.skeleton('rows') + '</div></div>' +
        '<div class="set-card-foot"><span class="hint">当前激活套用于未指定配置套的工位与任务；「默认」套不可删除。</span>' +
        '</div></div>';
    },

    /* ============================================================
       数据加载与列表渲染（加载态 / 空状态 / 错误态）
       ============================================================ */
    load: function () {
      var host = document.getElementById('pf-list');
      if (host) host.innerHTML = U.skeleton('rows');
      return A.settings.profiles.list().then(function (res) {
        Profiles.data = res || {};
        Profiles.namesCache = Profiles.data.names || [];
        Profiles.renderList();
      }).catch(function (e) {
        U.toast('error', '配置套加载失败：' + e.message);
        if (host) {
          host.innerHTML = '<div class="pf-empty">' +
            '<div class="form-error">配置套加载失败：' + U.esc(e.message) + '</div>' +
            '<button class="btn btn-soft btn-sm" id="pf-retry" style="margin-top:10px">' +
            U.icon('refresh', 'sm') + '重试</button></div>';
          var retry = document.getElementById('pf-retry');
          if (retry) retry.addEventListener('click', Profiles.load);
        }
      });
    },

    renderList: function () {
      var host = document.getElementById('pf-list');
      if (!host) return;
      var d = Profiles.data || {};
      var profiles = d.profiles || {};
      var names = d.names && d.names.length ? d.names : Object.keys(profiles);
      /* ""（默认套）恒排最前 */
      names = names.slice().sort(function (a, b) {
        if (a === '') return -1;
        if (b === '') return 1;
        return String(a).localeCompare(String(b));
      });
      if (!names.length) {
        host.innerHTML = '<div class="pf-empty">' + U.empty({
          art: 'box', title: '暂无配置套', desc: '点击右上角「新建套」创建第一套 LLM 配置。'
        }) + '</div>';
        return;
      }
      host.innerHTML = names.map(function (name) {
        var p = profiles[name] || {};
        var isActive = name === (d.active || '');
        return '<div class="pf-row' + (isActive ? ' active' : '') + '">' +
          '<div class="pf-main">' +
          '<div class="pf-name">' +
          '<span class="badge ' + (name ? 'badge-blue' : 'badge-grey') + ' mono">' + U.esc(profileLabel(name)) + '</span>' +
          (isActive ? '<span class="badge badge-green">当前激活</span>' : '') +
          '</div>' +
          '<div class="pf-meta">' +
          '<span class="badge badge-grey mono">' + U.esc(p.provider || '—') + '</span>' +
          '<span class="mono" title="' + U.esc(p.base_url || '') + '">' + U.esc(p.base_url || '未设置 base_url') + '</span>' +
          '<span class="muted">·</span>' +
          '<span class="mono">' + U.esc(p.model || '未设置模型') + '</span>' +
          '</div></div>' +
          '<div class="pf-actions">' +
          (isActive
            ? '<span class="badge badge-green tiny">默认</span>'
            : '<button class="btn btn-soft btn-sm" data-pf-act="activate" data-pf-name="' + U.esc(name) + '">设为默认</button>') +
          '<button class="btn btn-ghost btn-sm" data-pf-act="edit" data-pf-name="' + U.esc(name) + '">编辑</button>' +
          '<button class="btn btn-ghost btn-sm" data-pf-act="copy" data-pf-name="' + U.esc(name) + '">复制</button>' +
          (name
            ? '<button class="icon-btn danger" data-pf-act="delete" data-pf-name="' + U.esc(name) + '" title="删除">' +
            U.icon('trash', 'sm') + '</button>'
            : '') +
          '</div></div>';
      }).join('');

      U.$$('[data-pf-act]', host).forEach(function (btn) {
        btn.addEventListener('click', function () {
          Profiles.action(btn.getAttribute('data-pf-act'), btn.getAttribute('data-pf-name'));
        });
      });
    },

    /* ============================================================
       列表操作：设为默认 / 编辑 / 复制 / 删除（二次确认）
       ============================================================ */
    action: function (act, name) {
      if (act === 'activate') {
        A.settings.profiles.update(name, { set_active: true }).then(function () {
          U.toast('success', '已设为默认：' + profileLabel(name) + '（新工位将使用该配置套）');
          Profiles.load();
        }).catch(function (e) { U.toast('error', '设置失败：' + e.message); });
      } else if (act === 'edit') {
        Profiles.openForm('edit', name);
      } else if (act === 'copy') {
        Profiles.openForm('copy', name);
      } else if (act === 'delete') {
        if (!name) { U.toast('warn', '「默认」套不可删除'); return; }
        UI.confirm({
          title: '删除配置套', danger: true, okLabel: '删除',
          text: '将删除配置套「' + name + '」，引用它的工位与任务会回落到默认配置。确定继续？'
        }).then(function (ok) {
          if (!ok) return;
          A.settings.profiles.remove(name).then(function (r) {
            U.toast('success', '已删除：' + name + (r && r.active !== undefined ? '（当前激活：' + profileLabel(r.active) + '）' : ''));
            Profiles.load();
          }).catch(function (e) { U.toast('error', '删除失败：' + e.message); });
        });
      }
    },

    /* ============================================================
       新建 / 编辑 / 复制 弹窗
       ============================================================ */
    openForm: function (mode, name) {
      var profiles = (Profiles.data && Profiles.data.profiles) || {};
      var src = (mode === 'create' ? {} : (profiles[name] || {}));
      var title = mode === 'edit' ? '编辑配置套 · ' + profileLabel(name)
        : (mode === 'copy' ? '复制配置套 · ' + profileLabel(name) : '新建配置套');
      var m = U.modal({
        wide: true,
        title: title,
        body: Profiles.formHTML(mode, name, src),
        buttons: [
          { label: '取消', cls: 'btn-ghost' },
          {
            label: mode === 'edit' ? '保存修改' : '创建配置套', cls: 'btn-primary',
            onClick: function (body) {
              return Profiles.submitForm(mode, name, body, m);
            }
          }
        ]
      });
      Profiles.bindForm(mode, name, src, m);
      return m;
    },

    formHTML: function (mode, name, src) {
      src = src || {};
      var retry = src.retry || {};
      var isNew = mode !== 'edit';
      var nameVal = mode === 'copy' ? (name ? name + '-copy' : '') : '';
      var h = '';
      h += '<p class="hint" style="margin-bottom:12px">「默认」套即全局 LLM 接入配置；工位 / 任务未指定配置套时，使用当前激活套。</p>';
      if (isNew) {
        h += '<div class="set-two">' +
          '<div class="field"><span class="field-label">配置套名称 *</span>' +
          '<input class="input mono" id="pf-name" value="' + U.esc(nameVal) + '" placeholder="如 deepseek / ollama-local"></div>' +
          '<div class="field"><span class="field-label">初值</span>' +
          '<div class="set-row" style="border:0;padding:4px 0">' +
          '<label class="switch"><input type="checkbox" id="pf-copy-default"><i></i></label>' +
          '<span class="hint">从当前默认套复制初值</span></div></div></div>';
      } else {
        h += '<div class="set-row"><span class="field-label">配置套名称</span>' +
          '<span class="badge badge-blue mono">' + U.esc(profileLabel(name)) + '</span></div>';
      }
      h += '<div class="field"><span class="field-label">服务商 Provider</span>' +
        '<select class="select" id="pf-provider">' +
        PROFILE_PROVIDERS.map(function (p) {
          return '<option value="' + p.value + '"' + (src.provider === p.value ? ' selected' : '') + '>' + p.label + '</option>';
        }).join('') + '</select></div>';
      h += '<div class="field"><span class="field-label">Base URL</span>' +
        '<input class="input mono" id="pf-base-url" value="' + U.esc(src.base_url || '') + '" placeholder="https://api.openai.com/v1"></div>';
      h += '<div class="field"><span class="field-label">API Key' +
        (src.api_key ? '（已保存：' + U.esc(src.api_key) + '，留空保持不变）' : '') + '</span>' +
        '<input class="input mono" id="pf-api-key" type="password" placeholder="sk-…"></div>';
      h += '<div class="field"><span class="field-label">模型</span>' +
        '<div style="display:flex;gap:8px">' +
        '<input class="input mono" id="pf-model" list="pf-model-options" value="' + U.esc(src.model || '') + '" placeholder="gpt-4o-mini">' +
        '<button class="btn btn-soft btn-sm" id="pf-fetch-models" type="button">拉取模型</button></div>' +
        '<select class="select mono" id="pf-model-select" style="margin-top:6px"><option value="">（点「拉取模型」后在这里选）</option></select>' +
        '<datalist id="pf-model-options"></datalist></div>';
      h += '<div class="field"><span class="field-label">keys（多密钥，逗号分隔，可选）</span>' +
        '<input class="input mono" id="pf-keys" value="' + U.esc(keysText(src.keys)) + '" placeholder="留空不改动"></div>';
      h += '<div class="set-row" style="border:0;padding-top:2px">' +
        '<button class="btn btn-soft btn-sm" id="pf-test" type="button">' + U.icon('zap', 'sm') + '测试连接</button>' +
        '<span class="hint" id="pf-test-result">' +
        (mode === 'edit' ? '将按已保存的配置测试连通性并做一次聊天往返' : '将按当前表单填写的配置测试连通性') + '</span></div>';
      h += '<button class="adv-toggle" id="pf-adv-toggle" type="button">高级参数' + U.icon('chevron-d', 'sm') + '</button>';
      h += '<div class="hidden" id="pf-adv">' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">temperature</span>' +
        '<input class="input num" id="pf-temperature" type="number" step="0.1" value="' +
        U.esc(src.temperature !== undefined ? src.temperature : 0.7) + '"></div>' +
        '<div class="field"><span class="field-label">max_context_tokens</span>' +
        '<input class="input num" id="pf-max-ctx" type="number" value="' +
        U.esc(src.max_context_tokens || 128000) + '"></div>' +
        '</div><div class="set-two">' +
        '<div class="field"><span class="field-label">compact_threshold</span>' +
        '<input class="input num" id="pf-compact" type="number" step="0.05" value="' +
        U.esc(src.compact_threshold !== undefined ? src.compact_threshold : 0.7) + '"></div>' +
        '<div class="field"><span class="field-label">request_timeout（秒）</span>' +
        '<input class="input num" id="pf-timeout" type="number" value="' +
        U.esc(src.request_timeout || 120) + '"></div>' +
        '</div>' +
        '<div class="set-two">' +
        '<div class="field"><span class="field-label">retry.base_interval（秒）</span>' +
        '<input class="input num" id="pf-retry-base" type="number" value="' +
        U.esc(retry.base_interval !== undefined ? retry.base_interval : 10) + '"></div>' +
        '<div class="field"><span class="field-label">retry.max_interval（秒）</span>' +
        '<input class="input num" id="pf-retry-max" type="number" value="' +
        U.esc(retry.max_interval !== undefined ? retry.max_interval : 300) + '"></div>' +
        '</div><div class="set-two">' +
        '<div class="field"><span class="field-label">retry.max_retries</span>' +
        '<input class="input num" id="pf-retry-retries" type="number" value="' +
        U.esc(retry.max_retries !== undefined ? retry.max_retries : 5) + '"></div>' +
        '<div class="field"><span class="field-label">retry.backoff</span>' +
        '<select class="select" id="pf-retry-backoff">' +
        RETRY_BACKOFFS.map(function (b) {
          return '<option' + (retry.backoff === b ? ' selected' : '') + '>' + b + '</option>';
        }).join('') + '</select></div>' +
        '</div></div>';
      if (isNew) {
        h += '<div class="set-row" style="margin-top:8px"><span class="field-label">创建后设为默认（新工位使用该套）</span>' +
          '<label class="switch"><input type="checkbox" id="pf-set-active"><i></i></label></div>';
      }
      h += '<div class="hint" style="margin-top:10px">api_key 掩码时不回传，留空即保持已保存值。</div>';
      return h;
    },

    bindForm: function (mode, name, src, m) {
      var root = m.body;
      /* 高级折叠 */
      var advBtn = root.querySelector('#pf-adv-toggle');
      if (advBtn) {
        advBtn.addEventListener('click', function () {
          advBtn.classList.toggle('open');
          var adv = root.querySelector('#pf-adv');
          if (adv) adv.classList.toggle('hidden');
        });
      }
      /* 新建：从默认套复制初值 */
      var copyBox = root.querySelector('#pf-copy-default');
      if (copyBox) {
        copyBox.addEventListener('change', function () {
          var profiles = (Profiles.data && Profiles.data.profiles) || {};
          Profiles.fillForm(root, copyBox.checked ? (profiles[''] || {}) : {});
        });
      }
      /* 拉取模型（新建用表单值；编辑按该套拉取） */
      var fetchBtn = root.querySelector('#pf-fetch-models');
      if (fetchBtn) {
        fetchBtn.addEventListener('click', function () {
          fetchBtn.disabled = true;
          fetchBtn.textContent = '拉取中…';
          var profile = mode === 'edit' ? name : '';
          var req = profile
            ? A.settings.models('', '', '', profile)
            : A.settings.models(
              root.querySelector('#pf-provider').value,
              root.querySelector('#pf-base-url').value.trim(),
              root.querySelector('#pf-api-key').value.trim()
            );
          req.then(function (r) {
            var dl = root.querySelector('#pf-model-options');
            if (dl) {
              dl.innerHTML = (r.models || []).map(function (mm) {
                return '<option value="' + U.esc(mm) + '"></option>';
              }).join('');
            }
            /* 全量进下拉（datalist 会按输入框已有文字过滤） */
            U.fillModelSelect('pf-model-select', r.models || [], 'pf-model');
            if (r.error) U.toast('warn', '拉取失败：' + r.error);
            else U.toast('success', '已拉取 ' + ((r.models || []).length) + ' 个模型');
          }).catch(function (e) {
            U.toast('error', '拉取失败：' + e.message);
          }).then(function () {
            fetchBtn.disabled = false;
            fetchBtn.textContent = '拉取模型';
          });
        });
      }
      /* 弹窗内测试连接（test_llm 带 profile 参数） */
      var testBtn = root.querySelector('#pf-test');
      if (testBtn) {
        testBtn.addEventListener('click', function () {
          var out = root.querySelector('#pf-test-result');
          var body = {};
          if (mode === 'edit') {
            body.profile = name || '';
          } else {
            body.provider = root.querySelector('#pf-provider').value;
            var bu = root.querySelector('#pf-base-url').value.trim();
            var key = root.querySelector('#pf-api-key').value.trim();
            var model = root.querySelector('#pf-model').value.trim();
            if (bu) body.base_url = bu;
            if (key && !maskedKey(key)) body.api_key = key;
            if (model) body.model = model;
          }
          testBtn.disabled = true;
          testBtn.textContent = '测试中…';
          if (out) out.textContent = '正在测试连接…';
          A.settings.testLLM(body).then(function (r) {
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
            testBtn.disabled = false;
            testBtn.textContent = '测试连接';
          });
        });
      }
    },

    /* 用一份 llm 值填充 / 清空表单（复制初值用） */
    fillForm: function (root, v) {
      v = v || {};
      var retry = v.retry || {};
      function setVal(id, val) {
        var n = root.querySelector('#' + id);
        if (n) n.value = val === undefined || val === null ? '' : val;
      }
      setVal('pf-provider', v.provider || 'openai_compat');
      setVal('pf-base-url', v.base_url || '');
      setVal('pf-api-key', '');
      setVal('pf-model', v.model || '');
      setVal('pf-keys', keysText(v.keys));
      setVal('pf-temperature', v.temperature !== undefined ? v.temperature : 0.7);
      setVal('pf-max-ctx', v.max_context_tokens || 128000);
      setVal('pf-compact', v.compact_threshold !== undefined ? v.compact_threshold : 0.7);
      setVal('pf-timeout', v.request_timeout || 120);
      setVal('pf-retry-base', retry.base_interval !== undefined ? retry.base_interval : 10);
      setVal('pf-retry-max', retry.max_interval !== undefined ? retry.max_interval : 300);
      setVal('pf-retry-retries', retry.max_retries !== undefined ? retry.max_retries : 5);
      setVal('pf-retry-backoff', retry.backoff || 'exponential');
    },

    /* 读取表单 → {llm:{...}}；非法数字返回 null 并 toast */
    readLlm: function (root) {
      function num(id, label) {
        var raw = root.querySelector('#' + id).value.trim();
        if (raw === '') return undefined;
        var n = Number(raw);
        if (isNaN(n)) throw new Error(label + ' 不是有效数字');
        return n;
      }
      var llm;
      try {
        llm = {
          provider: root.querySelector('#pf-provider').value,
          base_url: root.querySelector('#pf-base-url').value.trim(),
          model: root.querySelector('#pf-model').value.trim(),
          temperature: num('pf-temperature', 'temperature'),
          max_context_tokens: num('pf-max-ctx', 'max_context_tokens'),
          compact_threshold: num('pf-compact', 'compact_threshold'),
          request_timeout: num('pf-timeout', 'request_timeout'),
          retry: {
            base_interval: num('pf-retry-base', 'retry.base_interval'),
            max_interval: num('pf-retry-max', 'retry.max_interval'),
            max_retries: num('pf-retry-retries', 'retry.max_retries'),
            backoff: root.querySelector('#pf-retry-backoff').value
          }
        };
        /* api_key 掩码时不回传：留空不传 */
        var key = root.querySelector('#pf-api-key').value.trim();
        if (key && !maskedKey(key)) llm.api_key = key;
        var keysRaw = root.querySelector('#pf-keys').value.trim();
        if (keysRaw) {
          llm.keys = keysRaw.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
        }
      } catch (e) {
        U.toast('error', '表单读取失败：' + e.message);
        return null;
      }
      return llm;
    },

    submitForm: function (mode, name, root, m) {
      var isNew = mode !== 'edit';
      var newName = name;
      if (isNew) {
        newName = (root.querySelector('#pf-name').value || '').trim();
        if (!newName) {
          U.toast('warn', '请填写配置套名称');
          return false;
        }
      }
      var llm = Profiles.readLlm(root);
      if (!llm) return false;
      var payload = { llm: llm };
      if (isNew) {
        var sa = root.querySelector('#pf-set-active');
        payload.set_active = !!(sa && sa.checked);
      }
      var req = isNew
        ? A.settings.profiles.create(newName, payload)
        : A.settings.profiles.update(name, payload);
      req.then(function () {
        U.toast('success', isNew ? '配置套已创建：' + newName : '配置套已更新：' + profileLabel(name));
        Profiles.load();
      }).catch(function (e) {
        U.toast('error', (isNew ? '创建失败：' : '保存失败：') + e.message);
      });
    },

    /* ============================================================
       下拉选项支持：工位 / 任务弹窗统一取 names（空字符串 → 空标签）
       ============================================================ */
    namesList: function () {
      if (Profiles.namesCache) return Promise.resolve(Profiles.namesCache);
      return A.settings.profiles.list().then(function (res) {
        Profiles.namesCache = (res && res.names) || [];
        return Profiles.namesCache;
      });
    },

    /* fillSelect(selectEl, selected, emptyLabel)：空值项文案由调用方决定
       工位='默认' / 任务='继承工位' */
    fillSelect: function (sel, selected, emptyLabel) {
      if (!sel) return Promise.resolve();
      var empty = emptyLabel || '默认';
      sel.innerHTML = '<option value="">' + U.esc(empty) + '</option>';
      return Profiles.namesList().then(function (names) {
        var opts = names.filter(function (n) { return !!n; }).map(function (n) {
          return '<option value="' + U.esc(n) + '"' + (selected === n ? ' selected' : '') + '>' +
            U.esc(n) + '</option>';
        }).join('');
        sel.innerHTML = '<option value="">' + U.esc(empty) + '</option>' + opts;
        sel.value = selected || '';
      }).catch(function (e) {
        U.toast('warn', '配置套列表加载失败：' + e.message);
      });
    },

    /* 卡片绑定（由 views-settings.js 的 bind 后调用） */
    bind: function () {
      var btn = document.getElementById('pf-new');
      if (btn) btn.addEventListener('click', function () { Profiles.openForm('create'); });
      Profiles.load();
    }
  };

  global.DogBuddyViews.Profiles = Profiles;
})(window);
