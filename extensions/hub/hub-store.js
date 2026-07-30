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
 *   hub-ops.json        人工维护的服务、命令和自动服务备注
 *   hub-ops-auto.json   只读监控生成的服务器与服务快照
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
  var OPS_AUTO_FILE = 'hub-ops-auto.json';

  var DEFAULTS = {
    profile: function () {
      return { name: '', role: '', focus: '', focusDate: '', updatedAt: '' };
    },
    design: function () { return { items: [] }; },
    ops: function () {
      return {
        generatedAt: '',
        source: { kind: 'manual', name: 'Hermes Hub' },
        machines: [],
        services: [],
        commands: []
      };
    },
    resources: function () { return { items: [] }; },
    inbox: function () { return { items: [] }; }
  };

  var HUB_README = [
    '# Hermes Hub',
    '',
    '这是本人的个人中枢数据目录，由 Hermes WebUI 的 Hub 扩展读写。',
    '**你（agent）可以直接读取和修改人工数据文件**；`hub-ops-auto.json` 由只读监控独占写入，agent 和界面只读。',
    '',
    '| 文件 | 内容 | 结构 |',
    '| --- | --- | --- |',
    '| `hub-profile.json` | 个人档案与今日聚焦 | `{name, role, focus, focusDate}` |',
    '| `hub-design.json` | 产品设计工作台 | `{items:[{id,title,stage,priority,tags,link,notes}]}` |',
    '| `hub-ops.json` | 运维人工数据 | `{services:[手工服务或{id,managed:true,notes}], commands:[{id,label,command,notes}]}` |',
    '| `hub-ops-auto.json` | 只读监控自动快照 | `{generatedAt, source, machines:[{id,name,ownership,role,host,region,os,resources,status,checks}], services:[{id,machineId,name,status,detail,updatedAt,managed}]}` |',
    '| `hub-resources.json` | 个人资源库 | `{items:[{id,title,url,category,tags,note}]}` |',
    '| `hub-inbox.json` | 快速捕获收件箱 | `{items:[{id,text,done,createdAt}]}` |',
    '',
    '约定：',
    '',
    '- `design.stage` 取值：`idea` | `spec` | `design` | `review` | `done`',
    '- `design.priority` 取值：`high` | `normal` | `low`',
    '- `ops.services[].env` 取值：`prod` | `staging` | `dev`',
    '- `ops.services[].status` 取值：`ok` | `watch` | `down`',
    '- `ops.machines[].ownership` 取值：`personal` | `company`',
    '- `hub-ops-auto.json` 仅由只读监控原子更新，界面永不写入',
    '- `hub-ops.json` 仅存手工服务、命令和自动服务 notes；界面读取时按 id 合并',
    '- 时间字段是 ISO 8601 字符串',
    '- 每个条目的 `id` 必须唯一；新增时生成即可',
    '',
    '改动这些文件时请保持上述结构，否则界面会回退成空白视图。',
    ''
  ].join('\n');

  var ctx = { sessionId: '', root: '', ready: false, reason: 'uninitialized' };
  var cache = Object.create(null);
  var opsManualWriteBlocked = false;
  var opsManualMissing = false;
  var opsManualBase = { services: [], commands: [] };

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

  function secureInit() {
    return api('/api/hub/init', {
      method: 'POST',
      body: JSON.stringify({ session_id: ctx.sessionId }),
      retries: 0, timeoutToast: false
    });
  }

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
      if (!ok) { markUnready('session_lost'); return ctx; }
      markReady(sid, root);
      return secureInit().then(function () { return ctx; }, function () {
        markUnready('permission_init_failed');
        return ctx;
      });
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
      return secureInit().then(scaffold);
    }).then(function () { return ctx; }, function (err) {
      markUnready('permission_init_failed');
      throw err;
    });
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

  function parseObject(text, fallback) {
    var value;
    try { value = text ? JSON.parse(text) : fallback; }
    catch (_) { value = fallback; }
    return value && typeof value === 'object' && !Array.isArray(value) ? value : fallback;
  }

  function parseManualOps(text) {
    if (!text || !String(text).trim()) {
      // A known 404 is handled by read(); an existing empty file is corrupted/truncated.
      opsManualWriteBlocked = true;
      opsManualMissing = false;
      return DEFAULTS.ops();
    }
    try {
      var value = JSON.parse(text);
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('root is not object');
      opsManualWriteBlocked = false;
      opsManualMissing = false;
      opsManualBase = JSON.parse(JSON.stringify(manualOpsFromMerged(normalize('ops', value))));
      return value;
    } catch (_) {
      // Keep automatic monitoring visible, but never overwrite a corrupted manual file.
      opsManualWriteBlocked = true;
      return DEFAULTS.ops();
    }
  }

  function mergeOps(manual, automatic) {
    manual = normalize('ops', manual || {});
    automatic = normalize('ops', automatic || {});
    var notes = Object.create(null);
    var manualServices = [];
    manual.services.forEach(function (service) {
      if (service && service.managed === true && service.id) notes[service.id] = service.notes || '';
      else if (service) manualServices.push(service);
    });
    var automaticServices = automatic.services.map(function (service) {
      var merged = Object.assign({}, service);
      if (Object.prototype.hasOwnProperty.call(notes, service.id)) merged.notes = notes[service.id];
      return merged;
    });
    return {
      generatedAt: automatic.generatedAt || '',
      staleAfterMinutes: automatic.staleAfterMinutes,
      source: automatic.source || { kind: 'manual', name: 'Hermes Hub' },
      machines: automatic.machines || [],
      services: automaticServices.concat(manualServices),
      commands: (manual.commands || []).map(function (command) { return Object.assign({}, command); })
    };
  }

  /* 运维自动快照与人工数据分文件读取，避免监控和界面整文件互相覆盖。 */
  function read(key) {
    if (!ctx.ready) return Promise.resolve(DEFAULTS[key]());
    if (cache[key]) return Promise.resolve(cache[key]);
    if (key === 'ops') {
      return Promise.all([
        readText(FILES.ops).then(parseManualOps, function () {
          opsManualWriteBlocked = false;
          opsManualMissing = true;
          opsManualBase = { services: [], commands: [] };
          return DEFAULTS.ops();
        }),
        readText(OPS_AUTO_FILE).then(function (text) { return parseObject(text, DEFAULTS.ops()); }, function () { return DEFAULTS.ops(); })
      ]).then(function (values) {
        cache.ops = mergeOps(values[0], values[1]);
        return cache.ops;
      });
    }
    return readText(FILES[key]).then(function (text) {
      cache[key] = normalize(key, parseObject(text, DEFAULTS[key]()));
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
      else if (def[field] && typeof def[field] === 'object' && !Array.isArray(def[field]) &&
        (!value[field] || typeof value[field] !== 'object' || Array.isArray(value[field]))) value[field] = def[field];
    });
    return value;
  }

  function manualOpsFromMerged(value) {
    var services = (value.services || []).map(function (service) {
      if (service && service.managed === true) {
        return service.notes ? { id: service.id, managed: true, notes: service.notes } : null;
      }
      return service;
    }).filter(Boolean);
    return { services: services, commands: value.commands || [] };
  }

  function sameJson(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

  function mergeRows(baseRows, localRows, remoteRows, label) {
    var base = Object.create(null), local = Object.create(null), remote = Object.create(null), ids = Object.create(null);
    function index(rows, target) {
      (rows || []).forEach(function (row) {
        if (!row || !row.id) throw new Error(label + '存在缺少 id 的条目，已拒绝保存');
        target[row.id] = row; ids[row.id] = true;
      });
    }
    index(baseRows, base); index(localRows, local); index(remoteRows, remote);
    return Object.keys(ids).sort().map(function (id) {
      var localChanged = !sameJson(local[id], base[id]);
      var remoteChanged = !sameJson(remote[id], base[id]);
      if (localChanged && remoteChanged && !sameJson(local[id], remote[id])) {
        throw new Error(label + '「' + id + '」已被其他页面或 Agent 修改，请刷新后重试');
      }
      return localChanged ? local[id] : remote[id];
    }).filter(Boolean);
  }

  function mergeManualOps(base, local, remote) {
    return {
      services: mergeRows(base.services, local.services, remote.services, '服务'),
      commands: mergeRows(base.commands, local.commands, remote.commands, '命令')
    };
  }

  function applyManualToMerged(value, manual) {
    var notes = Object.create(null), manualServices = [];
    (manual.services || []).forEach(function (service) {
      if (service.managed === true) notes[service.id] = service.notes || '';
      else manualServices.push(service);
    });
    var automaticServices = (value.services || []).filter(function (service) { return service.managed === true; }).map(function (service) {
      var next = Object.assign({}, service);
      if (Object.prototype.hasOwnProperty.call(notes, service.id)) next.notes = notes[service.id];
      else delete next.notes;
      return next;
    });
    value.services = automaticServices.concat(manualServices);
    value.commands = manual.commands || [];
    return value;
  }

  function write(key, value) {
    if (!ctx.ready) return Promise.reject(new Error('Hub 数据目录尚未配置'));
    if (key !== 'ops') {
      return writeText(FILES[key], JSON.stringify(value, null, 2)).then(function () { cache[key] = value; });
    }
    if (opsManualWriteBlocked) {
      return Promise.reject(new Error('hub-ops.json 无法解析，已禁止覆盖；请先修复或恢复该文件'));
    }
    var local = manualOpsFromMerged(value);
    function mergeAndPersist(text) {
      var remote;
      try {
        remote = text ? JSON.parse(text) : { services: [], commands: [] };
        if (!remote || typeof remote !== 'object' || Array.isArray(remote)) throw new Error('root');
        remote = manualOpsFromMerged(normalize('ops', remote));
      } catch (_) {
        opsManualWriteBlocked = true;
        throw new Error('hub-ops.json 无法解析，已禁止覆盖；请先修复或恢复该文件');
      }
      var persisted = mergeManualOps(opsManualBase, local, remote);
      return writeText(FILES.ops, JSON.stringify(persisted, null, 2)).then(function () {
        opsManualMissing = false;
        opsManualBase = JSON.parse(JSON.stringify(persisted));
        cache.ops = applyManualToMerged(value, persisted);
      });
    }
    return readText(FILES.ops).then(mergeAndPersist, function () {
      if (opsManualMissing) return mergeAndPersist('');
      throw new Error('保存前无法重新读取 hub-ops.json，已拒绝覆盖；请稍后重试');
    });
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
