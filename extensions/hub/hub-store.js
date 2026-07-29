/* Hermes Hub — 数据层
 *
 * 个人数据以扁平 JSON 文件落在一个专用的 "Hub 工作区" 目录里，走 WebUI 已有的
 * /api/file/* 接口。这样做的关键收益：Hermes agent 读写的就是同一批文件，
 * 你在界面上记的东西 agent 直接能看见 —— 这是"数字分身"能成立的前提。
 *
 * 文件全部平铺在 Hub 根目录（不建子目录），因此不需要 mkdir 权限:
 *   HUB.md              给 agent 看的目录说明
 *   hub-profile.json    个人档案 / 今日聚焦
 *   hub-design.json     产品设计工作台
 *   hub-ops.json        项目运维工作台
 *   hub-resources.json  个人资源库
 *   hub-inbox.json      快速捕获收件箱
 *
 * 文件读写绑定在一个 session 上（WebUI 的 file API 以 session 的 workspace 为根）。
 * Hub 首次配置时会注册工作区并创建一个专属会话，之后 session id 记在 localStorage。
 */
(function () {
  'use strict';
  if (window.HubStore) return;

  var LS_ROOT = 'hermes-hub.root';
  var LS_SID = 'hermes-hub.session';

  var FILES = {
    profile: 'hub-profile.json',
    design: 'hub-design.json',
    ops: 'hub-ops.json',
    resources: 'hub-resources.json',
    inbox: 'hub-inbox.json'
  };

  var DEFAULTS = {
    profile: function () {
      return { name: '', role: '', focus: '', focusDate: '', updatedAt: '' };
    },
    design: function () { return { items: [] }; },
    ops: function () { return { services: [], commands: [] }; },
    resources: function () { return { items: [] }; },
    inbox: function () { return { items: [] }; }
  };

  var HUB_README = [
    '# Hermes Hub',
    '',
    '这是本人的个人中枢数据目录，由 Hermes WebUI 的 Hub 扩展读写。',
    '**你（agent）可以直接读取和修改这些文件**，界面会在下次打开时读到最新内容。',
    '',
    '| 文件 | 内容 | 结构 |',
    '| --- | --- | --- |',
    '| `hub-profile.json` | 个人档案与今日聚焦 | `{name, role, focus, focusDate}` |',
    '| `hub-design.json` | 产品设计工作台 | `{items:[{id,title,stage,priority,tags,link,notes}]}` |',
    '| `hub-ops.json` | 项目运维工作台 | `{services:[{id,name,env,url,status,owner,notes}], commands:[{id,label,command,notes}]}` |',
    '| `hub-resources.json` | 个人资源库 | `{items:[{id,title,url,category,tags,note}]}` |',
    '| `hub-inbox.json` | 快速捕获收件箱 | `{items:[{id,text,done,createdAt}]}` |',
    '',
    '约定：',
    '',
    '- `design.stage` 取值：`idea` | `spec` | `design` | `review` | `done`',
    '- `design.priority` 取值：`high` | `normal` | `low`',
    '- `ops.services[].env` 取值：`prod` | `staging` | `dev`',
    '- `ops.services[].status` 取值：`ok` | `watch` | `down`',
    '- 时间字段是 ISO 8601 字符串',
    '- 每个条目的 `id` 必须唯一；新增时生成即可',
    '',
    '改动这些文件时请保持上述结构，否则界面会回退成空白视图。',
    ''
  ].join('\n');

  var ctx = { sessionId: '', root: '', ready: false, reason: 'uninitialized' };
  var cache = Object.create(null);

  function lsGet(k) { try { return localStorage.getItem(k) || ''; } catch (_) { return ''; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (_) { } }
  function lsDel(k) { try { localStorage.removeItem(k); } catch (_) { } }

  function api(path, opts) {
    if (typeof window.api !== 'function') return Promise.reject(new Error('WebUI api() 尚未就绪'));
    return window.api(path, opts || {});
  }

  /* 用一次目录列举验证 session 仍然有效且其 workspace 可读。 */
  function probe(sid) {
    return api('/api/list?session_id=' + encodeURIComponent(sid) + '&path=.', {
      retries: 0, timeoutToast: false
    }).then(function () { return true; }, function () { return false; });
  }

  /* ── 读写原语 ───────────────────────────────────────────────────────── */

  function readText(relPath) {
    return api('/api/file?session_id=' + encodeURIComponent(ctx.sessionId) +
      '&path=' + encodeURIComponent(relPath), { retries: 0, timeoutToast: false })
      .then(function (data) { return data && typeof data.content === 'string' ? data.content : ''; });
  }

  /* 先 save（文件已存在的常态路径），失败再 create。避免每次写都多一次探测请求。 */
  function writeText(relPath, content) {
    var body = JSON.stringify({ session_id: ctx.sessionId, path: relPath, content: content });
    return api('/api/file/save', { method: 'POST', body: body, retries: 0, timeoutToast: false })
      .catch(function () {
        return api('/api/file/create', { method: 'POST', body: body, retries: 0, timeoutToast: false });
      });
  }

  /* ── 配置与引导 ─────────────────────────────────────────────────────── */

  function markReady(sid, root) {
    ctx.sessionId = sid;
    ctx.root = root || '';
    ctx.ready = true;
    ctx.reason = '';
    lsSet(LS_SID, sid);
    if (root) lsSet(LS_ROOT, root);
  }

  function markUnready(reason) {
    ctx.ready = false;
    ctx.reason = reason || 'unconfigured';
  }

  /* 恢复上次配置；session 失效时降级为"未配置"而不是静默写到别的目录去。 */
  function init() {
    var sid = lsGet(LS_SID);
    var root = lsGet(LS_ROOT);
    if (!sid) { markUnready('unconfigured'); return Promise.resolve(ctx); }
    return probe(sid).then(function (ok) {
      if (ok) markReady(sid, root);
      else markUnready('session_lost');
      return ctx;
    });
  }

  /* 列出已注册的工作区，供配置界面直接选取。 */
  function listWorkspaces() {
    return api('/api/workspaces', { retries: 0, timeoutToast: false })
      .then(function (data) {
        var list = (data && (data.workspaces || data.items)) || [];
        return Array.isArray(list) ? list : [];
      }, function () { return []; });
  }

  /* 把某个目录设为 Hub 根：注册工作区 → 建专属会话 → 铺初始文件。 */
  function setup(rootPath) {
    var root = String(rootPath || '').trim();
    if (!root) return Promise.reject(new Error('请填写 Hub 数据目录的绝对路径'));

    return api('/api/workspaces/add', {
      method: 'POST',
      // create:true —— 目录不存在时由后端建出来，省掉"先去开个终端 mkdir"这一步。
      body: JSON.stringify({ path: root, name: 'Hermes Hub', create: true }),
      retries: 0, timeoutToast: false
    }).catch(function (err) {
      // 目录已经注册过是正常情况，继续往下走；真正的坏路径会在建会话时报出来。
      var msg = String((err && err.message) || '');
      if (/exist|已存在|already/i.test(msg)) return null;
      throw err;
    }).then(function () {
      return api('/api/session/new', {
        method: 'POST',
        body: JSON.stringify({ workspace: root, worktree: false }),
        retries: 0, timeoutToast: false, timeoutMs: 60000
      });
    }).then(function (data) {
      var sid = data && data.session && data.session.session_id;
      if (!sid) throw new Error('未能创建 Hub 会话');
      markReady(sid, root);
      cache = Object.create(null);
      return scaffold();
    }).then(function () { return ctx; });
  }

  /* 初始文件：缺什么补什么，已有内容一律不覆盖。 */
  function scaffold() {
    var jobs = [readText('HUB.md').catch(function () { return null; }).then(function (existing) {
      if (existing === null || existing === '') return writeText('HUB.md', HUB_README);
      return null;
    })];
    Object.keys(FILES).forEach(function (key) {
      jobs.push(read(key).then(function (value) { return write(key, value); }));
    });
    return Promise.all(jobs);
  }

  function reset() {
    lsDel(LS_SID);
    lsDel(LS_ROOT);
    cache = Object.create(null);
    ctx.sessionId = '';
    ctx.root = '';
    markUnready('unconfigured');
  }

  /* ── 领域读写 ───────────────────────────────────────────────────────── */

  /* 读不到或解析失败一律回落到默认结构，保证界面永远有东西可渲染。 */
  function read(key) {
    if (!ctx.ready) return Promise.resolve(DEFAULTS[key]());
    if (cache[key]) return Promise.resolve(cache[key]);
    return readText(FILES[key]).then(function (text) {
      var value;
      try { value = text ? JSON.parse(text) : DEFAULTS[key](); }
      catch (_) { value = DEFAULTS[key](); }
      if (!value || typeof value !== 'object') value = DEFAULTS[key]();
      cache[key] = normalize(key, value);
      return cache[key];
    }, function () {
      cache[key] = DEFAULTS[key]();
      return cache[key];
    });
  }

  /* 补齐缺失的数组字段——agent 手改文件时很容易漏掉某个键。 */
  function normalize(key, value) {
    var def = DEFAULTS[key]();
    Object.keys(def).forEach(function (field) {
      if (Array.isArray(def[field]) && !Array.isArray(value[field])) value[field] = [];
      else if (typeof def[field] === 'string' && typeof value[field] !== 'string') value[field] = def[field];
    });
    return value;
  }

  function write(key, value) {
    cache[key] = value;
    if (!ctx.ready) return Promise.reject(new Error('Hub 数据目录尚未配置'));
    return writeText(FILES[key], JSON.stringify(value, null, 2));
  }

  function readAll() {
    var keys = Object.keys(FILES);
    return Promise.all(keys.map(read)).then(function (values) {
      var out = {};
      keys.forEach(function (k, i) { out[k] = values[i]; });
      return out;
    });
  }

  function invalidate() { cache = Object.create(null); }

  function newId() {
    return 'h' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  window.HubStore = {
    FILES: FILES,
    context: function () { return ctx; },
    init: init,
    setup: setup,
    reset: reset,
    listWorkspaces: listWorkspaces,
    read: read,
    write: write,
    readAll: readAll,
    invalidate: invalidate,
    newId: newId
  };
})();
