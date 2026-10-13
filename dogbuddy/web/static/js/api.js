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
    adminChatAdmins: function () { return request('GET', '/api/admin/chat/admins'); },
    adminChatMessages: function (sender, limit) {
      return request('GET', '/api/admin/chat/messages' + qs({ sender: sender, limit: limit || 100 }));
    },
    adminChat: function (text, sender) {
      return request('POST', '/api/admin/chat', { text: text, sender: sender || 'console' });
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
    }
  };

  global.API = api;
})(window);
