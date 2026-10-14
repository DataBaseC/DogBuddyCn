/* ============================================================
   DogBuddyCn 控制台 · sse.js
   SSE 实时事件总线（松耦合层）：
     window.SSE 订阅表（事件名 → handlers，'*' 订阅全部帧）
   + 连接生命周期（start/stop，底层走 API.events 的断线重连）
   各视图在自己文件里 SSE.on('事件名', fn) 注册钩子，app.js 只负责 start。
   坏帧在 API.events 里已过滤；这里只做分发，handler 异常互不影响。
   ============================================================ */
(function (global) {
  'use strict';
  var A = global.API;

  /** @type {Object} 事件名 → 处理器数组的映射（'*' = 全部） */
  var handlers = {};
  /** @type {Object|null} 底层连接句柄 {close: function()} */
  var conn = null;

  /**
   * 订阅一类 SSE 事件。
   * @param {string} type 事件名（如 'window.status'）；'*' 订阅全部帧
   * @param {Function} fn 处理器 function(payload, frame)
   * @returns {Function} 退订函数
   */
  function on(type, fn) {
    if (typeof fn !== 'function') return function () {};
    var key = String(type || '*');
    handlers[key] = handlers[key] || [];
    handlers[key].push(fn);
    return function () { off(key, fn); };
  }

  /**
   * 退订一类 SSE 事件的某个处理器。
   * @param {string} type 事件名
   * @param {Function} fn 之前注册的处理器
   * @returns {void}
   */
  function off(type, fn) {
    var key = String(type || '*');
    var list = handlers[key];
    if (!list) return;
    var i = list.indexOf(fn);
    if (i >= 0) list.splice(i, 1);
    if (!list.length) delete handlers[key];
  }

  /**
   * 把一帧广播给订阅者（先精确事件名，再 '*'）。
   * 单个 handler 抛错不影响其他 handler。
   * @param {Object} frame 帧对象 {type, payload, created_at}
   * @returns {void}
   */
  function dispatch(frame) {
    if (!frame || typeof frame !== 'object') return;
    var type = String(frame.type || '');
    var payload = (frame.payload && typeof frame.payload === 'object') ? frame.payload : {};
    [type, '*'].forEach(function (key) {
      (handlers[key] || []).slice().forEach(function (fn) {
        try { fn(payload, frame); } catch (e) { /* handler 异常互不影响 */ }
      });
    });
    state.lastType = type;
    state.lastAt = frame.created_at || '';
    state.received += 1;
  }

  /** @type {{connected: boolean, received: number, lastType: string, lastAt: string}} 连接状态（供降级判断） */
  var state = { connected: false, received: 0, lastType: '', lastAt: '' };

  /**
   * 启动 SSE 连接（幂等：已连接则跳过）。连接/断开时广播
   * 'sse.open' / 'sse.down'（payload 为空对象），供视图做降级清理。
   * @returns {void}
   */
  function start() {
    if (conn || typeof A.events !== 'function') return;
    state.connected = true;
    conn = A.events(function (frame) {
      dispatch(frame);
    });
    dispatch({ type: 'sse.open', payload: {} });
  }

  /**
   * 停止 SSE 连接（登录失效 / 退出登录时调用），并广播 'sse.down'
   * 让视图把「正在输入…」这类半截态清理干净、退回轮询模式。
   * @returns {void}
   */
  function stop() {
    if (!conn) return;
    conn.close();
    conn = null;
    state.connected = false;
    dispatch({ type: 'sse.down', payload: {} });
  }

  global.SSE = {
    on: on,
    off: off,
    start: start,
    stop: stop,
    state: state
  };
})(window);
