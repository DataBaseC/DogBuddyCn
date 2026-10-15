/* ============================================================
   DogBuddyCn 控制台 · api.js
   fetch 封装：自动携带 X-Auth-Token，401 自动回登录层
   全部接口与后端契约一一对应（纯 JSON）
   ============================================================ */
(function (global) {
  'use strict';

  var TOKEN_KEY = 'dogbuddy_token';

  function getToken() {
    try { return global.localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
  }
  function setToken(t) {
    try { global.localStorage.setItem(TOKEN_KEY, t); } catch (e) { /* 忽略 */ }
  }
  function clearToken() {
    try { global.localStorage.removeItem(TOKEN_KEY); } catch (e) { /* 忽略 */ }
  }

  function ApiError(message, status, payload) {
    this.name = 'ApiError';
    this.message = message || ('请求失败 (' + status + ')');
    this.status = status || 0;
    this.payload = payload;
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  function qs(params) {
    var parts = [];
    Object.keys(params || {}).forEach(function (k) {
      var v = params[k];
      if (v === undefined || v === null || v === '') return;
      parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    });
    return parts.length ? '?' + parts.join('&') : '';
  }

  /* 核心请求：method / url / body(JSON 或 FormData) / opts {blob} */
  function request(method, url, body, opts) {
    opts = opts || {};
    var headers = { 'Accept': 'application/json' };
    var token = getToken();
    if (token) headers['X-Auth-Token'] = token;

    var init = { method: method, headers: headers, cache: 'no-store' };
    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      init.body = body; /* multipart：不手动设置 Content-Type */
    } else if (body !== undefined && body !== null) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }

    return global.fetch(url, init).then(function (res) {
      if (res.status === 401 && !opts.noAuthRedirect) {
        clearToken();
        if (global.App && typeof global.App.showLogin === 'function') {
          global.App.showLogin('登录已失效，请重新登录');
        }
        throw new ApiError('未授权（401）：请重新登录', 401);
      }
      if (opts.blob) {
        if (!res.ok) throw new ApiError('下载失败（' + res.status + '）', res.status);
        return res.blob();
      }
      if (res.status === 204) return null;
      var isJson = (res.headers.get('content-type') || '').indexOf('json') >= 0;
      var parsed = isJson ? res.json() : res.text();
      return parsed.catch(function () { return null; }).then(function (data) {
        if (!res.ok) {
          var msg = data && (data.detail || data.message || data.error || data.msg);
          if (typeof msg !== 'string' || !msg) msg = '请求失败（' + res.status + '）';
          throw new ApiError(msg, res.status, data);
        }
        return data;
      });
    }, function (err) {
      if (err instanceof ApiError) throw err;
      throw new ApiError('网络错误：' + (err && err.message ? err.message : err), 0);
    });
  }

  function enc(id) { return encodeURIComponent(id); }

  var api = {
    getToken: getToken,
    setToken: setToken,
    clearToken: clearToken,
    ApiError: ApiError,

    /* ---------------- 认证：POST /api/auth/login {"password"|"token"} → {"token"} ---------------- */
    login: function (payload) {
      return request('POST', '/api/auth/login', payload, { noAuthRedirect: true });
    },

    /* ---------------- 状态 / 总览 ---------------- */
    status: function () { return request('GET', '/api/status'); },            /* GET /api/status */
    overview: function () { return request('GET', '/api/overview'); },        /* GET /api/overview */

    /* ---------------- 控制台 ↔ 管理员对话 ---------------- */
    /* ---------------- 对话（v9：一个管理员，多个对话） ---------------- */
    chatConversations: function () { return request('GET', '/api/admin/chat/conversations'); },
    chatCreateConversation: function (title) {
      return request('POST', '/api/admin/chat/conversations', { title: title || '' });
    },
    chatRenameConversation: function (id, title) {
      return request('PATCH', '/api/admin/chat/conversations/' + enc(id), { title: title });
    },
    chatDeleteConversation: function (id) {
      return request('DELETE', '/api/admin/chat/conversations/' + enc(id));
    },    adminChatAdmins: function () { return request('GET', '/api/admin/chat/admins'); },
    adminChatMessages: function (sender, limit) {
      return request('GET', '/api/admin/chat/messages' + qs({ conversation: sender, limit: limit || 100 }));
    },
    adminChat: function (text, sender) {
      return request('POST', '/api/admin/chat', { text: text, conversation: sender || 'default' });
    },
    /* GET /api/admin/chat/usage?conversation= → {context_tokens, max_context_tokens, ratio, threshold, tokens, estimated}
       管理员会话不落 windows 表，所以**不能**走 /api/windows/<id>/usage（会 404）。 */
    adminChatUsage: function (sender) {
      return request('GET', '/api/admin/chat/usage' + qs({ conversation: sender || 'default' }));
    },
    adminChatReset: function (sender) {
      return request('POST', '/api/admin/chat/reset', { sender: sender || 'console' });
    },

    /* ---------------- 工位 ---------------- */
    windows: {
      list: function () { return request('GET', '/api/windows'); },
      create: function (body) { return request('POST', '/api/windows', body); },
      get: function (id) { return request('GET', '/api/windows/' + enc(id)); },
      update: function (id, body) { return request('PATCH', '/api/windows/' + enc(id), body); },
      /* GET /api/windows/{id}/usage → {context_tokens, max_context_tokens, ratio, threshold, tokens} */
      usage: function (id) { return request('GET', '/api/windows/' + enc(id) + '/usage'); },
      remove: function (id) { return request('DELETE', '/api/windows/' + enc(id)); },
      pause: function (id) { return request('POST', '/api/windows/' + enc(id) + '/pause'); },
      resume: function (id) { return request('POST', '/api/windows/' + enc(id) + '/resume'); },
      dispatch: function (id, prompt) {
        return request('POST', '/api/windows/' + enc(id) + '/dispatch', { prompt: prompt });
      },
      messages: function (id, limit) {
        return request('GET', '/api/windows/' + enc(id) + '/messages' + qs({ limit: limit || 100 }));
      },
      compress: function (id) { return request('POST', '/api/windows/' + enc(id) + '/context/compress'); },
      reset: function (id) { return request('POST', '/api/windows/' + enc(id) + '/context/reset'); },
      inject: function (id, text) {
        return request('POST', '/api/windows/' + enc(id) + '/context/inject', { text: text });
      }
    },

    /* ---------------- 任务 ---------------- */
    jobs: {
      list: function (windowId) { return request('GET', '/api/jobs' + qs({ window_id: windowId })); },
      create: function (body) { return request('POST', '/api/jobs', body); },
      update: function (id, body) { return request('PATCH', '/api/jobs/' + enc(id), body); },
      remove: function (id) { return request('DELETE', '/api/jobs/' + enc(id)); },
      pause: function (id) { return request('POST', '/api/jobs/' + enc(id) + '/pause'); },
      resume: function (id) { return request('POST', '/api/jobs/' + enc(id) + '/resume'); },
      runNow: function (id) { return request('POST', '/api/jobs/' + enc(id) + '/run_now'); },
      runs: function (id) { return request('GET', '/api/jobs/' + enc(id) + '/runs'); }
    },

    /* ---------------- 群组（共享工作频道） ---------------- */
    /* ---------------- 项目（ROADMAP T7.1/T7.6） ---------------- */
    projects: {
      /* GET /api/projects → {projects, summary, workspaces} */
      list: function () { return request('GET', '/api/projects'); },
      /* POST /api/projects {name,root_path,git_init,params} */
      create: function (body) { return request('POST', '/api/projects', body); },
      /* PATCH /api/projects/{id} */
      update: function (id, body) { return request('PATCH', '/api/projects/' + enc(id), body); },
      /* DELETE /api/projects/{id}（解挂工位/群组，保留目录） */
      remove: function (id) { return request('DELETE', '/api/projects/' + enc(id)); },
      /* POST /api/projects/mount {window_id,project_id,subdir} */
      mount: function (body) { return request('POST', '/api/projects/mount', body); },
      /* POST /api/projects/unmount/{window_id} */
      unmount: function (windowId) { return request('POST', '/api/projects/unmount/' + enc(windowId)); },
      /* GET /api/projects/{id}/params-chain?window_id= → 参数继承链与实际生效值 */
      chain: function (id, windowId) {
        return request('GET', '/api/projects/' + enc(id) + '/params-chain' + qs({ window_id: windowId }));
      },
      /* GET /api/projects/{id}/chain → 指挥链（管理员→主管→员工）与健康度。
         审查报告 P1-2：这个接口后端一直在（T9.5 的验收点），但**前端没有任何调用者**
         —— 全前端搜 chain 只命中上面的 params-chain。是个只能 curl 的孤儿接口。 */
      cmdChain: function (id) {
        return request('GET', '/api/projects/' + enc(id) + '/chain');
      }
    },

    /* ---------------- 项目迭代（ROADMAP T8.5/T8.6） ---------------- */
    iterations: {
      /* GET /api/iterations?project_id= → {iterations:[{...plan}], presets} */
      list: function (projectId) {
        return request('GET', '/api/iterations' + qs({ project_id: projectId }));
      },
      /* GET /api/iterations/presets → 三档预设（保守/标准/激进） */
      presets: function () { return request('GET', '/api/iterations/presets'); },
      /* POST /api/iterations {project_id,goal,mode,window_ids,speed_minutes,...} */
      create: function (body) { return request('POST', '/api/iterations', body); },
      /* GET /api/iterations/{id} → {iteration, plan, rounds} */
      detail: function (id) { return request('GET', '/api/iterations/' + enc(id)); },
      update: function (id, body) { return request('PATCH', '/api/iterations/' + enc(id), body); },
      stop: function (id, reason) {
        return request('POST', '/api/iterations/' + enc(id) + '/stop' + qs({ reason: reason }));
      },
      resume: function (id) { return request('POST', '/api/iterations/' + enc(id) + '/resume'); },
      /* 手动推一轮（不等下一个间隔） */
      runNow: function (id) { return request('POST', '/api/iterations/' + enc(id) + '/run_now'); },
      remove: function (id) { return request('DELETE', '/api/iterations/' + enc(id)); }
    },
    /* ---------------- 模型管理（目录 + 备注 + 切换） ---------------- */
    models: {
      /* GET /api/models?enabled_only= → {models:[...], current:{...}, summary:{...}} */
      list: function (enabledOnly) {
        return request('GET', '/api/models' + qs({ enabled_only: enabledOnly ? true : undefined }));
      },
      /* GET /api/models/options → {capabilities:[], speeds:[], costs:[]} */
      options: function () { return request('GET', '/api/models/options'); },
      /* POST /api/models {model,provider,base_url,display_name,note,capability,
         context_window,speed,cost} → {model, models:[...]} */
      create: function (body) { return request('POST', '/api/models', body); },
      /* PATCH /api/models/{id}（部分更新；备注走这里） */
      update: function (id, body) { return request('PATCH', '/api/models/' + enc(id), body); },
      /* DELETE /api/models/{id} */
      remove: function (id) { return request('DELETE', '/api/models/' + enc(id)); },
      /* POST /api/models/{id}/activate → 写运行时配置并落盘（换模型） */
      activate: function (id) { return request('POST', '/api/models/' + enc(id) + '/activate'); },
      /* POST /api/models/import {provider,base_url,api_key} → 拉取列表批量入库 */
      import: function (body) { return request('POST', '/api/models/import', body || {}); }
    },
    groups: {
      /* GET /api/groups → {groups:[{id,name,description,lead_window_id,created_at,
         updated_at,members:[{window_id,window_name,role,joined_at,exists}]}]} */
      list: function () { return request('GET', '/api/groups'); },
      /* POST /api/groups {name,description,lead_window_id} → 群组 DTO */
      create: function (body) { return request('POST', '/api/groups', body); },
      /* GET /api/groups/{ref}（ref = 组 id 或组名）→ 群组 DTO */
      get: function (ref) { return request('GET', '/api/groups/' + enc(ref)); },
      /* PATCH /api/groups/{ref} {name?,description?,lead_window_id?} */
      update: function (ref, body) { return request('PATCH', '/api/groups/' + enc(ref), body); },
      /* DELETE /api/groups/{ref} → {deleted:true} */
      remove: function (ref) { return request('DELETE', '/api/groups/' + enc(ref)); },
      /* POST /api/groups/{ref}/members {window,role:"member|lead"} → 群组 DTO */
      addMember: function (ref, window, role) {
        return request('POST', '/api/groups/' + enc(ref) + '/members',
          { window: window, role: role || 'member' });
      },
      /* DELETE /api/groups/{ref}/members/{window} → {removed:true} */
      removeMember: function (ref, window) {
        return request('DELETE', '/api/groups/' + enc(ref) + '/members/' + enc(window));
      },
      /* GET /api/groups/{ref}/channel?limit=100 →
         {messages:[{id,window_id,window_name,role,content,created_at}]} */
      channel: function (ref, limit) {
        return request('GET', '/api/groups/' + enc(ref) + '/channel' + qs({ limit: limit || 100 }));
      },
      /* POST /api/groups/{ref}/run {prompt} → {group_id,group_name,
         steps:[{window_id,window_name,is_lead,ok,summary}],summary}
         同步等待整组跑完、可能耗时：请求层不设短超时，UI 端置 loading 即可 */
      run: function (ref, prompt) {
        return request('POST', '/api/groups/' + enc(ref) + '/run', { prompt: prompt });
      }
    },

    /* ---------------- 运行时配置（kv）：approval.* 等 ---------------- */
    /* 与后端约定：GET /api/config → 配置键值对象（兼容 {config:{...}} 包装）；
       POST /api/config {key, value} 写单个键（对应后端 ConfigService.set(key,value)） */
    config: {
      get: function () { return request('GET', '/api/config'); },
      set: function (key, value) {
        return request('POST', '/api/config', { key: key, value: value });
      }
    },

    /* ---------------- 设置 ---------------- */
    settings: {
      get: function () { return request('GET', '/api/settings'); },
      update: function (body) { return request('PUT', '/api/settings', body); },
      /* GET /api/settings/models?provider=&base_url=&api_key=&profile= → {models, error}
         profile 可选（v1.3）：按配置套拉模型，不传按原逻辑 */
      models: function (provider, baseUrl, apiKey, profile) {
        return request('GET', '/api/settings/models' + qs({
          provider: provider, base_url: baseUrl, api_key: apiKey, profile: profile
        }));
      },
      /* POST /api/settings/test_llm {provider?,base_url?,api_key?,model?,profile?} */
      testLLM: function (body) {
        return request('POST', '/api/settings/test_llm', body || {});
      },

      /* ---------------- LLM 配置套（profiles，v1.3） ---------------- */
      profiles: {
        /* GET /api/settings/profiles → {active, names: ["", ...], profiles: {name: {...llm 结构}}} */
        list: function () { return request('GET', '/api/settings/profiles'); },
        /* POST /api/settings/profiles/{name} {llm:{...}, set_active?:bool} → 全量 settings（创建/覆盖） */
        create: function (name, body) {
          return request('POST', '/api/settings/profiles/' + enc(name), body || {});
        },
        /* PATCH /api/settings/profiles/{name} 同 POST（部分更新；{set_active:true} 即"设为默认"） */
        update: function (name, body) {
          return request('PATCH', '/api/settings/profiles/' + enc(name), body || {});
        },
        /* DELETE /api/settings/profiles/{name} → {deleted, active} */
        remove: function (name) {
          return request('DELETE', '/api/settings/profiles/' + enc(name));
        }
      }
    },

    /* ---------------- 白名单 ---------------- */
    whitelist: {
      list: function () { return request('GET', '/api/whitelist'); },
      /* POST /api/whitelist {qq_id,name,role}（role 可省略，默认 admin；同 qq_id 视为更新） */
      add: function (qqId, name, role) {
        return request('POST', '/api/whitelist', {
          qq_id: String(qqId), name: name || '', role: role || 'admin'
        });
      },
      /* DELETE body 保持 {qq_id} */
      remove: function (qqId) {
        return request('DELETE', '/api/whitelist', { qq_id: String(qqId) });
      }
    },

    /* ---------------- 文件管理器（path 为工作区相对路径；workspace 可选） ---------------- */
    files: {
      tree: function (path) {
        return request('GET', '/api/files/tree' + qs({ path: path || '' }));
      },
      list: function (path) {
        return request('GET', '/api/files/list' + qs({ path: path || '' }));
      },
      read: function (path) {
        return request('GET', '/api/files/read' + qs({ path: path }));
      },
      download: function (path) {
        return request('GET', '/api/files/download' + qs({ path: path }), null, { blob: true });
      },
      image: function (path) {
        return request('GET', '/api/files/image' + qs({ path: path }), null, { blob: true });
      },
      upload: function (path, file) {
        var fd = new FormData();
        fd.append('file', file);
        fd.append('path', path || '');
        return request('POST', '/api/files/upload', fd);
      },
      mkdir: function (path, name) {
        return request('POST', '/api/files/mkdir', { path: path || '', name: name });
      },
      rename: function (path, newName) {
        return request('POST', '/api/files/rename', { path: path, new_name: newName });
      },
      remove: function (path) {
        return request('POST', '/api/files/delete', { path: path });
      },
      write: function (path, content) {
        return request('POST', '/api/files/write', { path: path, content: content });
      }
    },

    /* ---------------- 日志 ---------------- */
    logs: {
      audit: function (limit) { return request('GET', '/api/logs/audit' + qs({ limit: limit || 200 })); },
      runs: function (limit, windowId) {
        return request('GET', '/api/logs/runs' + qs({ limit: limit || 100, window_id: windowId }));
      }
    },

    /* ---------------- 待决事项（股东模式） ---------------- */
    decisions: {
      /* GET /api/decisions?status=pending|answered|timeout|cancelled|<空=全部>&limit=100
         注意：status 空串 = 全部，必须显式传 ?status=（后端缺省值是 pending） */
      list: function (status, limit) {
        var s = status === undefined || status === null ? 'pending' : status;
        return request('GET', '/api/decisions?status=' + encodeURIComponent(s) +
          '&limit=' + (Number(limit) > 0 ? Number(limit) : 100));
      },
      /* POST /api/decisions/{id}/answer {"answer": "..."} */
      answer: function (id, answer) {
        return request('POST', '/api/decisions/' + enc(id) + '/answer', { answer: answer });
      },
      /* POST /api/decisions/{id}/cancel */
      cancel: function (id) {
        return request('POST', '/api/decisions/' + enc(id) + '/cancel');
      },
      /* POST /api/decisions/sweep → {"expired": [...]} 把过期待决翻成 timeout */
      sweep: function () {
        return request('POST', '/api/decisions/sweep');
      }
    },

    /* ---------------- 账本（token 用量 / 成本） ---------------- */
    ledger: {
      /* GET /api/ledger?days=30 → {since, by_window[], by_day[], total_tokens,
         total_messages, price_per_1m, total_cost} */
      get: function (days) {
        return request('GET', '/api/ledger' + qs({ days: days || 30 }));
      },
      /* GET /api/ledger/price → {"price_per_1m": 数字} */
      price: function () { return request('GET', '/api/ledger/price'); },
      /* POST /api/ledger/price {"price_per_1m": 数字}（0=只显示 tokens 不算钱） */
      setPrice: function (pricePer1m) {
        return request('POST', '/api/ledger/price', { price_per_1m: Number(pricePer1m) || 0 });
      }
    },

    /* ---------------- SSE 实时事件（GET /api/events?token=…） ---------------- */
    /**
     * 连接 SSE 实时事件流。EventSource 无法带请求头，token 走 query
     * （与 X-Auth-Token 同值，无鉴权部署留空）。断线（onerror）后指数退避
     * 重建连接；坏帧（非 JSON / 非对象）静默忽略；回调抛错不影响连接。
     * @param {(frame: Object) => void} onEvent 每帧 JSON 对象 {type, payload, created_at} 的回调
     * @returns {{close: () => void}} 连接句柄（close 后不再重连）
     */
    events: function (onEvent) {
      var closed = false;
      var es = null;
      var attempt = 0;
      var timer = null;

      function schedule() {
        if (closed || timer) return;
        attempt += 1;
        var delay = Math.min(30000, 1000 * Math.pow(2, attempt - 1));
        timer = setTimeout(function () { timer = null; connect(); }, delay);
      }

      function connect() {
        if (closed) return;
        var url = '/api/events' + qs({ token: getToken() });
        try {
          es = new EventSource(url);
        } catch (e) {
          schedule();
          return;
        }
        es.onopen = function () { attempt = 0; };
        es.onmessage = function (ev) {
          if (closed || !ev || typeof ev.data !== 'string') return;
          var frame = null;
          try { frame = JSON.parse(ev.data); } catch (e) { return; } /* 坏帧忽略 */
          if (!frame || typeof frame !== 'object') return;
          try { onEvent(frame); } catch (e) { /* 回调异常不断连 */ }
        };
        es.onerror = function () {
          if (closed) return;
          try { es.close(); } catch (e) { /* 忽略 */ }
          es = null;
          schedule();
        };
      }

      connect();
      return {
        close: function () {
          closed = true;
          if (timer) { clearTimeout(timer); timer = null; }
          if (es) {
            try { es.close(); } catch (e) { /* 忽略 */ }
            es = null;
          }
        }
      };
    }
  };

  global.API = api;
})(window);
