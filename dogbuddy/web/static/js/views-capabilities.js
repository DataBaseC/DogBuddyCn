/* ============================================================
   DogBuddyCn 控制台 · views-capabilities.js
   能力清单视图（T1.2）：工具卡片（权限 / 风险徽标 + 可展开参数表）
   + 权限 / 角色 / 关键词筛选 + 角色对照区（工具名 chips）
   后端契约：GET /api/capabilities → {tools, roles, tool_count, permissions}
   ============================================================ */
/* global DogBuddyViews */
(function (global) {
  'use strict';
  var U = global.UI, A = global.API;
  global.DogBuddyViews = global.DogBuddyViews || {};

  var PERM_LABEL = { read: '读取', write: '写入', network: '网络', shell: '命令行' };
  var PERM_BADGE = { read: 'badge-green', write: 'badge-blue', network: 'badge-amber', shell: 'badge-red' };
  var RISK_LABEL = { low: '低风险', mid: '中风险', high: '高风险' };
  var RISK_BADGE = { low: 'badge-green', mid: 'badge-amber', high: 'badge-red' };

  /**
   * 权限徽标 HTML（read 绿 / write 蓝 / network 黄 / shell 红）。
   * @param {string} perm 权限名
   * @returns {string} 徽标 HTML
   */
  function permBadge(perm) {
    var cls = PERM_BADGE[perm] || 'badge-grey';
    var label = PERM_LABEL[perm] || perm || '未知';
    return '<span class="badge ' + cls + '" title="权限：' + U.esc(perm || '') + '">' +
      U.esc(label) + '</span>';
  }

  /**
   * 风险标签 HTML（low / mid / high → 低 / 中 / 高）。
   * @param {string} risk 风险等级
   * @returns {string} 标签 HTML
   */
  function riskBadge(risk) {
    var cls = RISK_BADGE[risk] || 'badge-grey';
    var label = RISK_LABEL[risk] || risk || '未知';
    return '<span class="badge ' + cls + '" title="风险等级">风险' + U.esc(label.replace('风险', '')) + '</span>';
  }

  /**
   * 单个工具的参数表 HTML（名称 / 类型 / 必填 / 说明 / 默认值）。
   * @param {Array<Object>} args 参数行 [{name,type,required,description,default,enum}]
   * @returns {string} 参数表 HTML
   */
  function argsTable(args) {
    if (!args || !args.length) {
      return '<p class="muted small">该工具没有参数。</p>';
    }
    var rows = args.map(function (a) {
      var desc = a.description || '';
      if (a.enum && a.enum.length) {
        desc += (desc ? ' · ' : '') + '可选值：' + a.enum.map(String).join(' / ');
      }
      var def = (a['default'] === undefined || a['default'] === null)
        ? '—' : (typeof a['default'] === 'string' ? a['default'] : JSON.stringify(a['default']));
      return '<tr>' +
        '<td class="mono">' + U.esc(a.name) + '</td>' +
        '<td class="mono">' + U.esc(a.type || 'any') + '</td>' +
        '<td>' + (a.required
          ? '<span class="badge badge-amber">必填</span>'
          : '<span class="badge badge-grey">可选</span>') + '</td>' +
        '<td>' + U.esc(desc || '—') + '</td>' +
        '<td class="mono">' + U.esc(def) + '</td>' +
        '</tr>';
    }).join('');
    return '<div class="table-wrap"><table class="table sub-table">' +
      '<thead><tr><th>名称</th><th>类型</th><th>必填</th><th>说明</th><th>默认值</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div>';
  }

  /**
   * 单张工具卡片 HTML（头部门槛点击可展开参数表）。
   * @param {Object} t 工具 DTO {name,title,description,permission,risk,args}
   * @returns {string} 卡片 HTML
   */
  function toolCardHTML(t) {
    return '<div class="cap-tool">' +
      '<div class="cap-tool-head">' + U.icon('chevron-r', 'ic-chevron') +
      '<div class="cap-tool-main">' +
      '<div class="cap-tool-name mono">' + U.esc(t.name) +
      (t.title ? '<span class="cap-tool-title">' + U.esc(t.title) + '</span>' : '') + '</div>' +
      '<div class="cap-tool-desc">' + U.esc(t.description || '暂无说明') + '</div>' +
      '</div>' +
      '<div class="cap-tool-badges">' + permBadge(t.permission) + riskBadge(t.risk) + '</div>' +
      '</div>' +
      '<div class="cap-tool-body">' + argsTable(t.args) + '</div>' +
      '</div>';
  }

  /**
   * 角色对照卡 HTML（显示名 / 模式 / 工具数 / 工具名 chips）。
   * @param {Object} r 角色 DTO {name,display_name,default_mode,is_admin,tool_names,tool_count}
   * @returns {string} 卡片 HTML
   */
  function roleCardHTML(r) {
    var chips = (r.tool_names || []).map(function (n) {
      return '<span class="chip mono" data-cap-tool="' + U.esc(n) + '" title="点击筛选该工具">' +
        U.esc(n) + '</span>';
    }).join('');
    return '<div class="card cap-role" data-cap-role="' + U.esc(r.name) + '">' +
      '<div class="card-head">' +
      '<div><h3 class="card-title">' + U.esc(r.display_name || r.name) +
      (r.is_admin
        ? '<span class="badge badge-violet" style="margin-left:8px">管理员</span>'
        : '<span class="badge badge-blue" style="margin-left:8px">员工</span>') + '</h3>' +
      '<p class="card-sub mono">' + U.esc(r.name) + ' · 模式 ' + U.esc(r.default_mode || '—') +
      ' · 工具 ' + (r.tool_count || 0) + ' 个</p></div>' +
      '<button class="btn btn-ghost btn-sm" data-cap-role-filter="' + U.esc(r.name) + '">只看该角色</button>' +
      '</div>' +
      '<div class="card-body"><div class="chips">' +
      (chips || '<span class="muted small">该角色暂无工具</span>') + '</div></div>' +
      '</div>';
  }

  var Capabilities = {
    /** @type {Object|null} GET /api/capabilities 的响应缓存 */
    data: null,
    /** @type {{perm: string, role: string, kw: string}} 当前筛选条件 */
    filters: { perm: '', role: '', kw: '' },
    /** @type {boolean} 筛选控件是否已绑定事件 */
    bound: false,

    /**
     * 视图入口：拉取能力清单并渲染（可反复调用，筛选状态保留）。
     * @returns {Promise<void>}
     */
    load: function () {
      Capabilities.ensureBound();
      var grid = document.getElementById('cap-grid');
      if (grid) grid.innerHTML = U.skeleton('rows');
      return A.capabilities().then(function (data) {
        Capabilities.data = data || { tools: [], roles: [], permissions: [] };
        Capabilities.fillRoleOptions();
        Capabilities.render();
      }).catch(function (e) {
        if (grid) {
          grid.innerHTML = '<div class="empty"><div class="empty-title">能力清单加载失败</div>' +
            '<div class="empty-desc">' + U.esc(e.message) + '</div></div>';
        }
        U.toast('error', '能力清单加载失败：' + e.message);
      });
    },

    /**
     * 绑定筛选控件事件（幂等，只绑一次）。
     * @returns {void}
     */
    ensureBound: function () {
      if (Capabilities.bound) return;
      Capabilities.bound = true;
      var perm = /** @type {HTMLSelectElement|null} */ (document.getElementById('cap-perm-filter'));
      var role = /** @type {HTMLSelectElement|null} */ (document.getElementById('cap-role-filter'));
      var kw = /** @type {HTMLInputElement|null} */ (document.getElementById('cap-search'));
      if (perm) {
        perm.addEventListener('change', function () {
          Capabilities.filters.perm = perm.value;
          Capabilities.render();
        });
      }
      if (role) {
        role.addEventListener('change', function () {
          Capabilities.filters.role = role.value;
          Capabilities.render();
        });
      }
      if (kw) {
        kw.addEventListener('input', U.debounce(function () {
          Capabilities.filters.kw = kw.value.trim().toLowerCase();
          Capabilities.render();
        }, 150));
      }
    },

    /**
     * 填充「按角色筛选」下拉框选项。
     * @returns {void}
     */
    fillRoleOptions: function () {
      var sel = /** @type {HTMLSelectElement|null} */ (document.getElementById('cap-role-filter'));
      if (!sel) return;
      var roles = (Capabilities.data && Capabilities.data.roles) || [];
      sel.innerHTML = '<option value="">全部角色</option>' + roles.map(function (r) {
        return '<option value="' + U.esc(r.name) + '">' +
          U.esc(r.display_name || r.name) + '（' + (r.tool_count || 0) + '）</option>';
      }).join('');
      sel.value = Capabilities.filters.role;
    },

    /**
     * 按筛选条件过滤工具列表。
     * @returns {Array<Object>} 过滤后的工具 DTO 数组
     */
    filteredTools: function () {
      var data = Capabilities.data || {};
      var tools = data.tools || [];
      var f = Capabilities.filters;
      var roleNames = null;
      if (f.role) {
        var role = (data.roles || []).filter(function (r) { return r.name === f.role; })[0];
        roleNames = role ? (role.tool_names || []) : [];
      }
      return tools.filter(function (t) {
        if (f.perm && t.permission !== f.perm) return false;
        if (roleNames && roleNames.indexOf(t.name) < 0) return false;
        if (f.kw) {
          var hay = (String(t.name || '') + ' ' + String(t.description || '') +
            ' ' + String(t.title || '')).toLowerCase();
          if (hay.indexOf(f.kw) < 0) return false;
        }
        return true;
      });
    },

    /**
     * 渲染工具区 + 角色对照区，并绑定卡片交互。
     * @returns {void}
     */
    render: function () {
      var grid = document.getElementById('cap-grid');
      var roleHost = document.getElementById('cap-roles');
      var tools = Capabilities.filteredTools();
      var data = Capabilities.data || {};

      if (grid) {
        if (!tools.length) {
          grid.innerHTML = U.empty({
            art: 'box', title: '没有匹配的工具',
            desc: '换个关键词，或把权限 / 角色筛选调回「全部」试试。'
          });
        } else {
          grid.innerHTML = tools.map(toolCardHTML).join('');
        }
        /* 参数表折叠 */
        U.$$('.cap-tool-head', grid).forEach(function (h) {
          h.addEventListener('click', function () {
            h.parentNode.classList.toggle('open');
          });
        });
      }

      if (roleHost) {
        roleHost.innerHTML = (data.roles || []).map(roleCardHTML).join('') ||
          '<p class="muted small">暂无角色。</p>';
        /* 角色卡：只看该角色 */
        U.$$('[data-cap-role-filter]', roleHost).forEach(function (btn) {
          btn.addEventListener('click', function (e) {
            e.stopPropagation();
            Capabilities.filters.role = btn.getAttribute('data-cap-role-filter');
            var sel = /** @type {HTMLSelectElement|null} */ (document.getElementById('cap-role-filter'));
            if (sel) sel.value = Capabilities.filters.role;
            Capabilities.render();
            U.toast('info', '只看角色工具：' + Capabilities.filters.role);
          });
        });
        /* 工具 chips：点击按工具名筛选 */
        U.$$('[data-cap-tool]', roleHost).forEach(function (chip) {
          chip.addEventListener('click', function () {
            var name = chip.getAttribute('data-cap-tool');
            Capabilities.filters.kw = String(name).toLowerCase();
            var kw = /** @type {HTMLInputElement|null} */ (document.getElementById('cap-search'));
            if (kw) kw.value = name;
            Capabilities.render();
          });
        });
      }
    }
  };

  /* 挂进共享命名空间（可变共享状态，文件头已用 global 指令声明） */
  DogBuddyViews.Capabilities = Capabilities;
})(window);
