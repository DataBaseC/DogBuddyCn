/* ============================================================
   DogBuddyCn 控制台 · views-approval.js
   审批设置（高危命令审批，ROADMAP T4）：
   —— 放行清单（approval.allowlist：每条 = 一条允许的 shell 命令前缀/模式）
   —— 托管代审开关（approval.escalation_review：管理员托管模式，派独立 LLM 审查）
   以「设置卡片」形式挂进设置视图（同 views-profiles.js 的注入模式）。
   后端契约（后端并行开发中，字段以此为准）：
     GET  /api/config                → 配置键值（兼容 {config:{...}} 包装 / 平铺两种形状）
     POST /api/config {key, value}   → 写单键（对应 ConfigService.set(key,value)）
   读到的形状不确定时按「取不到 = 默认值」降级，绝不让审批卡拖垮设置页。
   ============================================================ */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var KEY_ALLOWLIST = 'approval.allowlist';
  var KEY_ESCALATION = 'approval.escalation_review';

  var Approval = {
    /** @type {Array<string>} 放行清单（允许执行、免审批的 shell 命令前缀/模式） */
    allowlist: [],
    /** @type {boolean} 托管代审开关（true = 管理员托管模式） */
    escalationReview: false,
    /** @type {boolean} 配置是否已从后端读到（读不到保持默认值） */
    loaded: false,

    /* ---------------- 配置读取（多形状兼容） ---------------- */

    /**
     * 从 GET /api/config 的原始响应里取一个配置键。
     * 兼容形状：{config:{key:value,...}} / {values:{...}} / 平铺 {key:value} /
     * {keys:[{key,value}]}；取不到返回 undefined。
     * @param {*} raw GET /api/config 的原始响应
     * @param {string} key 配置键（如 approval.allowlist）
     * @returns {*} 键对应值（取不到为 undefined）
     */
    pick: function (raw, key) {
      if (!raw || typeof raw !== 'object') return undefined;
      var bags = [raw, raw.config, raw.values, raw.settings];
      for (var i = 0; i < bags.length; i++) {
        var bag = bags[i];
        if (bag && typeof bag === 'object' && key in bag) return bag[key];
      }
      if (Array.isArray(raw.keys)) {
        for (var j = 0; j < raw.keys.length; j++) {
          var item = raw.keys[j];
          if (item && item.key === key) return item.value;
        }
      }
      return undefined;
    },

    /**
     * 把 allowlist 值规整成字符串数组（数组原样去空；字符串按行拆）。
     * @param {*} v 原始值
     * @returns {Array<string>} 字符串数组
     */
    normList: function (v) {
      var arr = Array.isArray(v) ? v : (typeof v === 'string' ? v.split('\n') : []);
      return arr.map(function (s) { return String(s).trim(); })
        .filter(function (s) { return !!s; });
    },

    /**
     * 拉取 approval.* 配置（失败静默用默认值，永不 reject）。
     * @returns {Promise<void>} 完成即 resolve
     */
    load: function () {
      return A.config.get().then(function (raw) {
        Approval.allowlist = Approval.normList(Approval.pick(raw, KEY_ALLOWLIST));
        var esc = Approval.pick(raw, KEY_ESCALATION);
        Approval.escalationReview = esc === true || esc === 'true' || esc === 1;
        Approval.loaded = true;
      }).catch(function () {
        /* 后端 /api/config 未就绪：保持默认值，卡片照常展示 */
        Approval.loaded = false;
      });
    },

    /* ---------------- 卡片模板（由 views-settings.js 注入） ---------------- */

    /**
     * 审批设置卡片 HTML：放行清单编辑 + 托管代审开关。
     * @returns {string} 卡片 HTML
     */
    cardHTML: function () {
      return '<div class="card set-card" data-group="approval">' +
        '<div class="set-card-head"><span class="set-card-icon">' + U.icon('shield', 'sm') + '</span>' +
        '<div><h3>审批设置</h3><p>高危命令审批 · 放行清单 · 托管代审</p></div></div>' +
        '<div class="set-card-body">' +
        '<div class="field"><span class="field-label">放行清单（approval.allowlist）</span>' +
        '<textarea class="textarea mono" id="appr-allowlist" rows="5" ' +
        'placeholder="每行一条允许的 shell 命令前缀 / 模式，例如：&#10;git status&#10;ls&#10;npm run build"></textarea>' +
        '<div class="hint">每条 = 一条允许的 shell 命令前缀/模式。命中放行清单的命令免审批直接执行；' +
        '清单之外的非常规命令会进入「待决」等你审批。</div></div>' +
        '<div class="set-row appr-esc-row">' +
        '<div><span class="field-label">托管代审（approval.escalation_review）</span>' +
        '<div class="hint">开启后非常规命令先由独立审查代理把关；不可逆命令永远需要你亲自批准。</div></div>' +
        '<label class="switch"><input type="checkbox" id="appr-esc"' +
        (Approval.escalationReview ? ' checked' : '') + '><i></i></label>' +
        '</div>' +
        (Approval.loaded ? '' :
          '<div class="hint appr-offline">⚠ 未读到后端 /api/config 当前值，展示的是默认值；保存后即按新值生效。</div>') +
        '</div>' +
        '<div class="set-card-foot"><div class="spacer"></div>' +
        '<button class="btn btn-primary btn-sm" id="appr-save">' + U.icon('save', 'sm') + '保存审批设置</button>' +
        '</div></div>';
    },

    /* ---------------- 交互 ---------------- */

    /**
     * 绑定保存按钮（每次卡片渲染后调用，幂等）。
     * @returns {void}
     */
    bind: function () {
      var ta = document.getElementById('appr-allowlist');
      if (ta) ta.value = Approval.allowlist.join('\n');
      var btn = document.getElementById('appr-save');
      if (btn) btn.addEventListener('click', function () { Approval.save(btn); });
    },

    /**
     * 保存两项配置：先写 allowlist，再写 escalation 开关（逐键 POST /api/config）。
     * @param {HTMLButtonElement} btn 保存按钮（置灰防重复提交）
     * @returns {void}
     */
    save: function (btn) {
      var ta = document.getElementById('appr-allowlist');
      var sw = document.getElementById('appr-esc');
      var list = Approval.normList(ta ? ta.value : '');
      var esc = !!(sw && sw.checked);
      if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
      A.config.set(KEY_ALLOWLIST, list).then(function () {
        return A.config.set(KEY_ESCALATION, esc);
      }).then(function () {
        Approval.allowlist = list;
        Approval.escalationReview = esc;
        Approval.loaded = true;
        U.toast('success', '审批设置已保存（放行 ' + list.length + ' 条 · 托管代审' +
          (esc ? '开' : '关') + '）');
      }).catch(function (e) {
        U.toast('error', '保存失败：' + (e && e.message ? e.message : e));
      }).then(function () {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = U.icon('save', 'sm') + '保存审批设置';
        }
      });
    }
  };

  global.DogBuddyViews.Approval = Approval;
})(window);
