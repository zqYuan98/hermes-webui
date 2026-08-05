/* Hermes Hub — 个人中枢面板
 *
 * 在核心 UI 旁边挂一个 "Hub" 主视图，不改动任何核心文件：
 *   - 往 MAIN_VIEW_PANELS 里注册 'hub'（核心用它来切 showing-* 类）
 *   - 在 rail / 侧栏导航插入入口按钮
 *   - 包一层 switchPanel，进入 hub 时触发渲染（核心的懒加载分支不认识 hub）
 *
 * 所有数据经 HubStore 落成工作区里的 JSON 文件，agent 可直接读写。
 */
(function () {
  'use strict';
  if (window.__hermesHubMounted) return;
  window.__hermesHubMounted = true;

  var PANEL = 'hub';

  /* ── 图标 ─────────────────────────────────────────────────────────── */

  function svg(paths, size) {
    return '<svg width="' + (size || 18) + '" height="' + (size || 18) + '" viewBox="0 0 24 24" ' +
      'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ' +
      'stroke-linejoin="round" aria-hidden="true">' + paths + '</svg>';
  }

  var ICON = {
    hub: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="3.5" r="2"/><circle cx="20" cy="16.5" r="2"/>' +
      '<circle cx="4" cy="16.5" r="2"/><path d="M12 9V5.5"/><path d="m14.6 13.6 3.7 2.1"/><path d="m9.4 13.6-3.7 2.1"/>',
    home: '<path d="m3 10 9-7 9 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    design: '<path d="M12 19a7 7 0 1 0 0-14 7 7 0 0 0 0 14z"/><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/><path d="M12 2v3"/><path d="M12 19v3"/><path d="M2 12h3"/><path d="M19 12h3"/>',
    meetings: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4"/><path d="M8 3v4"/><path d="M3 10h18"/><path d="M8 14h3"/><path d="M8 17h6"/>',
    ops: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 7.5h.01"/><path d="M7 17.5h.01"/>',
    resources: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    trash: '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    send: '<path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/>',
    copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    check: '<polyline points="20 6 9 17 4 12"/>',
    undo: '<path d="M3 7v6h6"/><path d="M3.5 13a9 9 0 1 0 2.3-5.7L3 10"/>',
    close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    left: '<polyline points="15 18 9 12 15 6"/>',
    right: '<polyline points="9 18 15 12 9 6"/>'
  };

  /* ── 模块定义 ─────────────────────────────────────────────────────── */

  var MODULES = [
    { id: 'home', label: '主页', icon: ICON.home, sub: '今日聚焦、快速捕获与全局概览' },
    { id: 'design', label: '产品设计', icon: ICON.design, sub: '需求与设计稿从想法走到交付' },
    { id: 'meetings', label: '会议', icon: ICON.meetings, sub: '纪要、决策与行动项闭环' },
    { id: 'ops', label: '项目运维', icon: ICON.ops, sub: '服务清单、状态与常用命令速查' },
    { id: 'resources', label: '资源库', icon: ICON.resources, sub: '链接、文档与提示词的统一收藏' },
    { id: 'inbox', label: '收件箱', icon: ICON.inbox, sub: '先记下来，之后再归类' }
  ];

  var STAGES = [
    { id: 'idea', label: '想法' },
    { id: 'spec', label: '需求' },
    { id: 'design', label: '设计中' },
    { id: 'review', label: '评审' },
    { id: 'done', label: '已交付' }
  ];

  var PRIORITIES = [
    { id: 'high', label: '高' },
    { id: 'normal', label: '中' },
    { id: 'low', label: '低' }
  ];

  var MEETING_TYPES = [
    { id: 'sync', label: '同步会' },
    { id: 'planning', label: '规划会' },
    { id: 'review', label: '评审会' },
    { id: 'decision', label: '决策会' },
    { id: 'retrospective', label: '复盘会' },
    { id: 'other', label: '其他' }
  ];

  var MEETING_STATUSES = [
    { id: 'planned', label: '待召开' },
    { id: 'in_progress', label: '进行中' },
    { id: 'completed', label: '已完成' },
    { id: 'cancelled', label: '已取消' }
  ];

  var ACTION_STATUSES = [
    { id: 'open', label: '待处理' },
    { id: 'in_progress', label: '进行中' },
    { id: 'blocked', label: '受阻' },
    { id: 'done', label: '已完成' }
  ];

  var ENVS = [
    { id: 'prod', label: '生产' },
    { id: 'staging', label: '预发' },
    { id: 'dev', label: '开发' }
  ];

  var STATUSES = [
    { id: 'ok', label: '正常' },
    { id: 'watch', label: '观察' },
    { id: 'down', label: '故障' }
  ];

  var OWNERS = [
    { id: 'all', label: '全部' },
    { id: 'personal', label: '个人' },
    { id: 'company', label: '公司' }
  ];

  var OWNER_LABELS = { personal: '个人', company: '公司' };

  var STATUS_LABELS = {
    ok: '正常',
    watch: '观察',
    down: '故障',
    stale: '过期'
  };

  var STATUS_WEIGHT = { ok: 0, watch: 1, stale: 1, down: 2 };

  var OPS_VIEWS = [
    { id: 'servers', label: '服务器' },
    { id: 'services', label: '服务' },
    { id: 'exceptions', label: '异常' }
  ];

  var OPS_STATUS_FILTERS = [
    { id: 'all', label: '全部状态' },
    { id: 'ok', label: '正常' },
    { id: 'watch', label: '观察' },
    { id: 'stale', label: '过期' },
    { id: 'down', label: '故障' }
  ];

  /* ── 视图状态 ─────────────────────────────────────────────────────── */

  var ready = null;   // HubStore.init() 的 promise，见 mount()
  var autoRefreshTimer = null;
  var AUTO_REFRESH_MS = 60000;
  var serviceDrawerRestoreFocus = null;

  var view = {
    module: 'home',
    data: null,
    form: null,        // { kind, id } — kind 为 design/service/command/resource/meeting
    meetingDraft: null,
    meetingActionDrafts: null,
    query: '',
    tag: '',
    opsOwner: 'personal',
    opsView: 'servers',
    opsStatus: 'all',
    opsKind: 'all',
    opsQuery: '',
    opsSelectedService: '',
    opsMachine: '',
    setupError: ''
  };

  /* ── 小工具 ───────────────────────────────────────────────────────── */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* 只放行 http(s)，挡掉 javascript: 之类的注入向量。 */
  function safeUrl(u) {
    var s = String(u || '').trim();
    return /^https?:\/\//i.test(s) ? s : '';
  }

  function toast(msg, type) {
    if (typeof window.showToast === 'function') window.showToast(msg, null, type);
  }

  /* navigator.clipboard 只在安全上下文里存在。通过局域网 http 访问 WebUI 时
   * 它是 undefined，直接调用会抛 TypeError，所以保留 execCommand 兜底。 */
  function copyText(text) {
    var done = function () { toast('命令已复制', 'success'); };
    var fail = function () { toast('复制失败，请手动选取', 'error'); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, fail);
      return;
    }
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:-1000px;opacity:0';
    document.body.appendChild(ta);
    try {
      ta.select();
      document.execCommand('copy') ? done() : fail();
    } catch (_) { fail(); } finally { document.body.removeChild(ta); }
  }

  function $id(id) { return document.getElementById(id); }

  function labelOf(list, id, fallback) {
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i].label;
    return fallback || id || '';
  }

  function nowIso() { return new Date().toISOString(); }

  function fmtDate(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    var today = new Date();
    var sameDay = d.toDateString() === today.toDateString();
    if (sameDay) return '今天 ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    return (d.getMonth() + 1) + '月' + d.getDate() + '日';
  }

  function parseTags(s) {
    return String(s || '').split(/[,，\s]+/).map(function (t) { return t.trim(); })
      .filter(function (t) { return t; }).slice(0, 12);
  }

  function parseList(s, limit) {
    return String(s || '').split(/[\n,，]+/).map(function (item) { return item.trim(); })
      .filter(function (item) { return item; }).slice(0, limit || 100);
  }

  function dateTimeInputValue(value) {
    if (!value) return '';
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value).slice(0, 16);
    var local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function isoFromInput(value) {
    if (!value) return '';
    var date = new Date(value);
    return isNaN(date.getTime()) ? value : date.toISOString();
  }

  function localDateKey(date) {
    date = date || new Date();
    return date.getFullYear() + '-' + String(date.getMonth() + 1).padStart(2, '0') + '-' +
      String(date.getDate()).padStart(2, '0');
  }

  function optionsHtml(list, selected) {
    return list.map(function (o) {
      return '<option value="' + esc(o.id) + '"' + (o.id === selected ? ' selected' : '') + '>' + esc(o.label) + '</option>';
    }).join('');
  }

  function iconBtn(action, id, icon, title, cls) {
    return '<button class="hub-icon-btn ' + (cls || '') + '" data-hub-action="' + action + '"' +
      (id ? ' data-hub-id="' + esc(id) + '"' : '') + ' title="' + esc(title) + '" ' +
      'aria-label="' + esc(title) + '">' + svg(icon, 14) + '</button>';
  }

  /* ── 挂载 ─────────────────────────────────────────────────────────── */

  function mount() {
    if (typeof MAIN_VIEW_PANELS !== 'undefined' && MAIN_VIEW_PANELS.indexOf(PANEL) === -1) {
      MAIN_VIEW_PANELS.push(PANEL);
    }
    mountRailButton();
    mountSidebarNavButton();
    mountSidebarPanel();
    mountMainView();
    wrapSwitchPanel();
    // 探测上次绑定的会话要一次网络往返。open() 必须等它落定，否则用户在
    // 探测完成前点进 Hub 会看到"未配置"的引导页，而其实早就配好了。
    ready = HubStore.init();
    startAutoRefresh();
    ready.then(function () {
      // 冷启动时核心可能已经把 hub 恢复成当前面板（用户上次停在这里）。
      if (document.querySelector('main.main').classList.contains('showing-' + PANEL)) open();
    });
  }

  function railButtonHtml(cls) {
    return '<button class="' + cls + ' nav-tab has-tooltip has-tooltip--bottom" data-panel="' + PANEL + '" ' +
      'data-label="Hub" data-tooltip="个人中枢" aria-label="个人中枢">' + svg(ICON.hub, 20) + '</button>';
  }

  function attachNavButton(container, cls) {
    if (!container || container.querySelector('[data-panel="' + PANEL + '"]')) return;
    var tmp = document.createElement('div');
    tmp.innerHTML = railButtonHtml(cls);
    var btn = tmp.firstChild;
    btn.addEventListener('click', function () { window.switchPanel(PANEL, { fromRailClick: true }); });
    // 排在 Chat 之后 —— 这是每天第一个要看的东西，不该沉到导航底部。
    var chat = container.querySelector('[data-panel="chat"]');
    if (chat && chat.nextSibling) container.insertBefore(btn, chat.nextSibling);
    else container.appendChild(btn);
  }

  function mountRailButton() { attachNavButton(document.querySelector('.rail'), 'rail-btn'); }
  function mountSidebarNavButton() { attachNavButton(document.querySelector('.sidebar-nav'), ''); }

  function mountSidebarPanel() {
    if ($id('panelHub')) return;
    var sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;
    var el = document.createElement('div');
    el.className = 'panel-view';
    el.id = 'panelHub';
    el.innerHTML =
      '<div class="panel-head"><span>个人中枢</span></div>' +
      '<div class="hub-nav" id="hubNav"></div>' +
      '<div class="hub-sidebar-foot" id="hubSidebarFoot"></div>';
    sidebar.appendChild(el);
  }

  function mountMainView() {
    if ($id('mainHub')) return;
    var main = document.querySelector('main.main');
    if (!main) return;
    var el = document.createElement('div');
    el.className = 'main-view';
    el.id = 'mainHub';
    el.innerHTML = '<div class="hub-scroll" id="hubScroll"></div>';
    main.appendChild(el);
    el.addEventListener('click', onClick);
    el.addEventListener('submit', onSubmit);
    el.addEventListener('input', onInput);
    el.addEventListener('change', onChange);
    document.addEventListener('keydown', onKeydown);
  }

  /* 核心的 switchPanel 只认识它自己那张懒加载表，hub 的渲染要在这里补上。 */
  function wrapSwitchPanel() {
    var original = window.switchPanel;
    if (typeof original !== 'function' || original.__hubWrapped) return;
    var wrapped = async function (name, opts) {
      var result = await original.apply(this, arguments);
      if (name === PANEL && result !== false) open();
      return result;
    };
    wrapped.__hubWrapped = true;
    window.switchPanel = wrapped;
  }

  /* ── 载入与渲染调度 ───────────────────────────────────────────────── */

  function open() {
    renderSidebar();
    (ready || Promise.resolve()).then(function () {
      if (!HubStore.context().ready) { render(); return; }
      return HubStore.readAll().then(function (data) {
        view.data = data;
        render();
        renderSidebar();
      });
    });
  }

  function reloadIfVisible() {
    // A background tab must not keep polling the file API: the visibilitychange
    // and focus handlers below re-run this the moment the tab comes back.
    if (document.hidden) return;
    var main = document.querySelector('main.main');
    if (!main || !main.classList.contains('showing-' + PANEL)) return;
    if (view.form) return; // Never discard unsaved form input during polling.
    HubStore.invalidate();
    open();
  }

  function startAutoRefresh() {
    if (autoRefreshTimer) return;
    autoRefreshTimer = setInterval(reloadIfVisible, AUTO_REFRESH_MS);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) reloadIfVisible();
    });
    window.addEventListener('focus', reloadIfVisible);
  }

  function reload() {
    if (view.form) {
      toast('请先保存或取消当前编辑，再重新读取数据', 'error');
      return;
    }
    HubStore.invalidate();
    open();
  }

  function render() {
    var host = $id('hubScroll');
    if (!host) return;
    var ctx = HubStore.context();
    if (!ctx.ready) {
      host.innerHTML = renderSetup(ctx);
      fillSetupWorkspaces();   // 异步补上已注册工作区，不阻塞首屏
      return;
    }
    if (!view.data) { host.innerHTML = '<div class="hub-empty">正在读取 Hub 数据…</div>'; return; }
    switch (view.module) {
      case 'design': host.innerHTML = renderDesign(); break;
      case 'meetings': host.innerHTML = renderMeetings(); break;
      case 'ops': host.innerHTML = renderOps(); break;
      case 'resources': host.innerHTML = renderResources(); break;
      case 'inbox': host.innerHTML = renderInbox(); break;
      default: host.innerHTML = renderHome();
    }
  }

  function renderSidebar() {
    var nav = $id('hubNav');
    var foot = $id('hubSidebarFoot');
    if (!nav) return;
    var d = view.data;
    var counts = {
      home: '',
      design: d ? d.design.items.length : '',
      meetings: d ? d.meetings.items.length : '',
      ops: d ? (d.ops.services.length + d.ops.commands.length) : '',
      resources: d ? d.resources.items.length : '',
      inbox: d ? d.inbox.items.filter(function (i) { return !i.done; }).length : ''
    };
    nav.innerHTML = MODULES.map(function (m) {
      return '<button class="hub-nav-item' + (view.module === m.id ? ' active' : '') + '" ' +
        'data-hub-module="' + m.id + '">' + svg(m.icon, 16) + '<span>' + esc(m.label) + '</span>' +
        (counts[m.id] !== '' ? '<span class="hub-nav-count">' + counts[m.id] + '</span>' : '') + '</button>';
    }).join('');

    var ctx = HubStore.context();
    if (foot) {
      foot.innerHTML = ctx.ready
        ? '数据目录<code>' + esc(ctx.root || '(已绑定)') + '</code>'
        : '尚未配置数据目录';
    }
    // 侧栏按钮在面板重建时会失去监听，统一用委托重新绑定。
    if (!nav.__hubBound) {
      nav.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-hub-module]');
        if (!btn) return;
        view.module = btn.getAttribute('data-hub-module');
        view.form = null;
        view.query = '';
        view.tag = '';
        view.opsSelectedService = '';
        render();
        renderSidebar();
      });
      nav.__hubBound = true;
    }
  }

  /* ── 配置引导 ─────────────────────────────────────────────────────── */

  function renderSetup(ctx) {
    var lost = ctx.reason === 'session_lost';
    return '<div class="hub-setup">' +
      '<h2>把这里变成你的数字分身</h2>' +
      (lost
        ? '<p>之前绑定的 Hub 会话已经失效（可能被删除或换了 profile）。重新指定数据目录即可，<strong>目录里的文件不会丢</strong>。</p>'
        : '<p>Hub 会把你的需求、服务清单、资源收藏以 JSON 文件的形式存在一个你指定的目录里。' +
          '选这种方式而不是浏览器本地存储，是因为 <strong>Hermes agent 能直接读写同一批文件</strong>—— ' +
          '你在界面上记的东西，对话里它立刻就能用。</p>') +
      '<div class="hub-card">' +
      '<div class="hub-field"><span class="hub-field-label">Hub 数据目录（绝对路径）</span>' +
      '<input class="hub-input" id="hubSetupPath" placeholder="例如 D:\\hermes-hub 或 /home/you/hermes-hub" ' +
      'value="' + esc(ctx.root || '') + '"></div>' +
      '<div class="hub-ws-list" id="hubSetupWorkspaces"></div>' +
      '<div class="hub-form-actions" style="margin-top:12px">' +
      '<button class="hub-btn primary" data-hub-action="setup">' + svg(ICON.check, 13) + '完成配置</button></div>' +
      (view.setupError ? '<div class="hub-error">' + esc(view.setupError) + '</div>' : '') +
      '</div>' +
      '<div class="hub-setup-hint">目录需要已经存在。配置会注册一个名为 Hermes Hub 的工作区，' +
      '并建一条绑定该目录的会话专门用于读写数据 —— 你也可以直接在那条会话里让 agent 整理你的资料。</div>' +
      '</div>';
  }

  function fillSetupWorkspaces() {
    var box = $id('hubSetupWorkspaces');
    if (!box) return;
    HubStore.listWorkspaces().then(function (list) {
      if (!list.length || !$id('hubSetupWorkspaces')) return;
      $id('hubSetupWorkspaces').innerHTML = '<span class="hub-field-label" style="width:100%">或从已有工作区里选一个：</span>' +
        list.slice(0, 8).map(function (w) {
          var p = w && (w.path || w.dir || w);
          return '<button class="hub-tag" data-hub-action="pick-ws" data-hub-value="' + esc(p) + '">' + esc(p) + '</button>';
        }).join('');
    });
  }

  function doSetup() {
    var input = $id('hubSetupPath');
    var path = input ? input.value.trim() : '';
    view.setupError = '';
    if (!path) { view.setupError = '请填写目录路径'; render(); return; }
    toast('正在配置 Hub…');
    HubStore.setup(path).then(function () {
      toast('Hub 已就绪', 'success');
      reload();
    }, function (err) {
      view.setupError = (err && err.message) || '配置失败，请确认目录存在且可写';
      render();
    });
  }

  /* ── 主页 ─────────────────────────────────────────────────────────── */

  function greeting() {
    var h = new Date().getHours();
    if (h < 5) return '夜深了';
    if (h < 11) return '早上好';
    if (h < 14) return '中午好';
    if (h < 18) return '下午好';
    return '晚上好';
  }

  function renderHome() {
    var d = view.data;
    var p = d.profile;
    var openInbox = d.inbox.items.filter(function (i) { return !i.done; });
    var activeDesign = d.design.items.filter(function (i) { return i.stage !== 'done'; });
    var badServices = d.ops.services.filter(function (s) { return s.status && s.status !== 'ok'; });
    var upcomingMeetings = d.meetings.items.filter(function (meeting) {
      return meeting.status !== 'completed' && meeting.status !== 'cancelled';
    });
    var openActions = [];
    d.meetings.items.forEach(function (meeting) {
      (meeting.actionItems || []).forEach(function (action) {
        if (action.status !== 'done') openActions.push(action);
      });
    });
    var today = localDateKey();
    var overdueActions = openActions.filter(function (action) { return action.due && action.due < today; });
    var name = p.name ? '，' + esc(p.name) : '';

    var stats = [
      { v: activeDesign.length, l: '进行中的设计条目' },
      { v: upcomingMeetings.length, l: '近期会议' },
      { v: openActions.length, l: '待办行动项' + (overdueActions.length ? '（' + overdueActions.length + ' 项逾期）' : '') },
      { v: d.ops.services.length, l: '在管服务' + (badServices.length ? '（' + badServices.length + ' 项异常）' : '') },
      { v: d.resources.items.length, l: '资源收藏' },
      { v: openInbox.length, l: '待归类' }
    ];

    var recent = collectRecent(6);

    return '<div class="hub-hero">' +
      '<div class="hub-greeting">' + greeting() + name + '</div>' +
      '<div class="hub-datemeta">' + new Date().toLocaleDateString('zh-CN',
        { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' }) + '</div>' +
      '</div>' +

      '<div class="hub-focus">' +
      '<span class="hub-focus-label">今日聚焦</span>' +
      '<span class="hub-focus-text' + (p.focus ? '' : ' placeholder') + '">' +
      esc(p.focus || '还没定 —— 点右边写一句今天真正要推进的事') + '</span>' +
      iconBtn('edit-focus', '', ICON.edit, '编辑今日聚焦') +
      '</div>' +

      '<div class="hub-stats">' + stats.map(function (s) {
        return '<div class="hub-stat"><div class="hub-stat-value">' + s.v + '</div>' +
          '<div class="hub-stat-label">' + esc(s.l) + '</div></div>';
      }).join('') + '</div>' +

      '<div class="hub-section">' +
      '<div class="hub-section-head"><span class="hub-section-title">快速捕获</span></div>' +
      '<form class="hub-capture" data-hub-form="capture">' +
      '<input class="hub-input" name="text" placeholder="想到什么先丢进来，回车存入收件箱…" autocomplete="off">' +
      '<button class="hub-btn primary" type="submit">' + svg(ICON.plus, 13) + '记下</button>' +
      '</form></div>' +

      '<div class="hub-section">' +
      '<div class="hub-section-head"><span class="hub-section-title">工作台</span></div>' +
      '<div class="hub-tiles">' + MODULES.filter(function (m) { return m.id !== 'home'; }).map(function (m) {
        return '<button class="hub-tile" data-hub-module="' + m.id + '">' +
          '<span class="hub-tile-icon">' + svg(m.icon, 20) + '</span>' +
          '<span class="hub-tile-title">' + esc(m.label) + '</span>' +
          '<span class="hub-tile-sub">' + esc(m.sub) + '</span></button>';
      }).join('') + '</div></div>' +

      '<div class="hub-section">' +
      '<div class="hub-section-head"><span class="hub-section-title">最近动态</span>' +
      '<div class="hub-section-actions"><button class="hub-btn" data-hub-action="reload">刷新显示</button></div></div>' +
      (recent.length
        ? '<div class="hub-list">' + recent.map(function (r) {
          return '<div class="hub-item"><div class="hub-item-main">' +
            '<div class="hub-item-title">' + esc(r.title) + '</div>' +
            '<div class="hub-item-sub">' + esc(r.where) + ' · ' + esc(fmtDate(r.at)) + '</div>' +
            '</div></div>';
        }).join('') + '</div>'
        : '<div class="hub-empty">还没有记录。从上面的快速捕获开始，或者进任意工作台新建一条。</div>') +
      '</div>';
  }

  /* 把各模块最近更新的条目合成一条时间线，作为主页的"我最近在忙什么"。 */
  function collectRecent(limit) {
    var d = view.data;
    var all = [];
    d.design.items.forEach(function (i) {
      all.push({ title: i.title, where: '产品设计 · ' + labelOf(STAGES, i.stage), at: i.updatedAt || i.createdAt });
    });
    d.meetings.items.forEach(function (meeting) {
      all.push({
        title: meeting.title,
        where: '会议 · ' + labelOf(MEETING_STATUSES, meeting.status, '待召开'),
        at: meeting.updatedAt || meeting.startAt || meeting.createdAt
      });
      (meeting.actionItems || []).forEach(function (action) {
        all.push({
          title: action.title || action.deliverable,
          where: '会议行动项 · ' + (action.owner || '未指定负责人'),
          at: action.updatedAt || action.due
        });
      });
    });
    d.ops.services.forEach(function (s) {
      all.push({ title: s.name, where: '项目运维 · ' + labelOf(STATUSES, s.status), at: s.updatedAt });
    });
    d.resources.items.forEach(function (r) {
      all.push({ title: r.title, where: '资源库' + (r.category ? ' · ' + r.category : ''), at: r.updatedAt || r.createdAt });
    });
    d.inbox.items.forEach(function (i) {
      all.push({ title: i.text, where: '收件箱', at: i.createdAt });
    });
    return all.filter(function (x) { return x.title && x.at; })
      .sort(function (a, b) { return String(b.at).localeCompare(String(a.at)); })
      .slice(0, limit);
  }

  /* ── 产品设计 ─────────────────────────────────────────────────────── */

  function renderDesign() {
    var items = view.data.design.items;
    var html = '<div class="hub-section-head">' +
      '<span class="hub-section-title">产品设计工作台</span>' +
      '<div class="hub-section-actions">' +
      '<button class="hub-btn primary" data-hub-action="new-design">' + svg(ICON.plus, 13) + '新建条目</button>' +
      '</div></div>';

    if (view.form && view.form.kind === 'design') html += designForm();

    html += '<div class="hub-board">' + STAGES.map(function (st) {
      var col = items.filter(function (i) { return (i.stage || 'idea') === st.id; });
      return '<div class="hub-col">' +
        '<div class="hub-col-head"><span>' + esc(st.label) + '</span>' +
        '<span class="hub-col-count">' + col.length + '</span></div>' +
        '<div class="hub-col-body">' +
        (col.length ? col.map(designCard).join('') : '<div class="hub-col-empty">空</div>') +
        '</div></div>';
    }).join('') + '</div>';
    return html;
  }

  function designCard(i) {
    var stageIdx = STAGES.map(function (s) { return s.id; }).indexOf(i.stage || 'idea');
    var link = safeUrl(i.link);
    return '<div class="hub-cardlet">' +
      '<div class="hub-cardlet-title">' +
      (link ? '<a href="' + esc(link) + '" target="_blank" rel="noopener noreferrer">' + esc(i.title) + '</a>' : esc(i.title)) +
      '</div>' +
      '<div class="hub-cardlet-meta">' +
      '<span class="hub-pri-' + esc(i.priority || 'normal') + '">优先级 ' + esc(labelOf(PRIORITIES, i.priority, '中')) + '</span>' +
      (i.updatedAt ? ' · ' + esc(fmtDate(i.updatedAt)) : '') +
      '</div>' +
      (i.notes ? '<div class="hub-cardlet-meta">' + esc(i.notes.slice(0, 90)) + (i.notes.length > 90 ? '…' : '') + '</div>' : '') +
      ((i.tags && i.tags.length) ? '<div class="hub-tags">' + i.tags.map(function (t) {
        return '<span class="hub-tag">' + esc(t) + '</span>';
      }).join('') + '</div>' : '') +
      '<div class="hub-cardlet-actions">' +
      (stageIdx > 0 ? iconBtn('design-back', i.id, ICON.left, '退回上一阶段') : '') +
      (stageIdx < STAGES.length - 1 ? iconBtn('design-fwd', i.id, ICON.right, '推进到下一阶段') : '') +
      iconBtn('design-agent', i.id, ICON.send, '交给 Agent 处理') +
      iconBtn('edit-design', i.id, ICON.edit, '编辑') +
      iconBtn('del-design', i.id, ICON.trash, '删除', 'danger') +
      '</div></div>';
  }

  function designForm() {
    var it = findById(view.data.design.items, view.form.id) || {};
    return '<form class="hub-card hub-section" data-hub-form="design">' +
      '<div class="hub-form-grid">' +
      field('title', '标题', it.title, 'text', true) +
      selectField('stage', '阶段', STAGES, it.stage || 'idea') +
      selectField('priority', '优先级', PRIORITIES, it.priority || 'normal') +
      field('link', '相关链接', it.link, 'text') +
      field('tags', '标签（逗号分隔）', (it.tags || []).join(', '), 'text') +
      '</div>' +
      '<div class="hub-field" style="margin-bottom:12px"><span class="hub-field-label">备注</span>' +
      '<textarea class="hub-textarea" name="notes">' + esc(it.notes || '') + '</textarea></div>' +
      formActions() + '</form>';
  }

  /* ── 会议纪要 ─────────────────────────────────────────────────────── */

  function renderMeetings() {
    var meetings = view.data.meetings.items.slice().sort(function (a, b) {
      return String(b.startAt || b.createdAt || '').localeCompare(String(a.startAt || a.createdAt || ''));
    });
    var html = '<div class="hub-section-head">' +
      '<span class="hub-section-title">会议纪要</span>' +
      '<div class="hub-section-actions"><button class="hub-btn primary" data-hub-action="new-meeting">' +
      svg(ICON.plus, 13) + '新建会议</button></div></div>';
    if (view.form && view.form.kind === 'meeting') html += meetingForm();
    html += meetings.length
      ? '<div class="hub-meeting-list">' + meetings.map(meetingCard).join('') + '</div>'
      : '<div class="hub-empty">还没有会议记录。新建会议后，在同一处维护纪要、决策和行动项。</div>';
    return html;
  }

  function meetingCard(meeting) {
    var actions = meeting.actionItems || [];
    var openCount = actions.filter(function (action) { return action.status !== 'done'; }).length;
    var links = (meeting.projectLinks || []).map(function (link) {
      var url = safeUrl(link);
      return url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(link) + '</a>' : esc(link);
    });
    return '<article class="hub-card hub-meeting" data-hub-meeting-id="' + esc(meeting.id) + '">' +
      '<div class="hub-meeting-head"><div><div class="hub-item-title">' + esc(meeting.title) + '</div>' +
      '<div class="hub-item-sub">' + esc(labelOf(MEETING_TYPES, meeting.type, '会议')) + ' · ' +
      esc(labelOf(MEETING_STATUSES, meeting.status, '待召开')) +
      (meeting.startAt ? ' · ' + esc(fmtDate(meeting.startAt)) : '') +
      (meeting.participants && meeting.participants.length ? ' · ' + meeting.participants.length + ' 人' : '') +
      (openCount ? ' · ' + openCount + ' 个待办' : '') + '</div></div>' +
      '<div class="hub-item-actions">' + iconBtn('edit-meeting', meeting.id, ICON.edit, '编辑会议') +
      iconBtn('del-meeting', meeting.id, ICON.trash, '删除会议', 'danger') + '</div></div>' +
      (meeting.summary ? '<div class="hub-meeting-summary">' + esc(meeting.summary) + '</div>' : '') +
      meetingListBlock('决策', meeting.decisions) +
      meetingActionsBlock(actions) +
      meetingListBlock('风险', meeting.risks) +
      meetingListBlock('待确认问题', meeting.openQuestions) +
      (links.length ? '<div class="hub-meeting-meta"><span>项目链接</span>' + links.join('<br>') + '</div>' : '') +
      (meeting.transcriptFile ? '<div class="hub-meeting-meta"><span>逐字稿</span><code>' + esc(meeting.transcriptFile) + '</code></div>' : '') +
      (meeting.minutesFile ? '<div class="hub-meeting-meta"><span>纪要文件</span><code>' + esc(meeting.minutesFile) + '</code></div>' : '') +
      (meeting.nextReviewAt ? '<div class="hub-meeting-meta"><span>下次回顾</span>' + esc(fmtDate(meeting.nextReviewAt)) + '</div>' : '') +
      '</article>';
  }

  function meetingListBlock(label, items) {
    items = (items || []).filter(Boolean);
    if (!items.length) return '';
    return '<div class="hub-meeting-block"><div class="hub-meeting-block-title">' + esc(label) + '</div><ul>' +
      items.map(function (item) { return '<li>' + esc(item) + '</li>'; }).join('') + '</ul></div>';
  }

  function meetingActionsBlock(actions) {
    if (!actions.length) return '';
    return '<div class="hub-meeting-block"><div class="hub-meeting-block-title">行动项</div><div class="hub-action-list">' +
      actions.map(function (action) {
        return '<div class="hub-action-item ' + esc(action.status || 'open') + '"><div><strong>' +
          esc(action.title || action.deliverable || '未命名行动项') + '</strong><span>' +
          esc(action.owner || '未指定负责人') + (action.due ? ' · ' + esc(action.due) : '') + ' · ' +
          esc(labelOf(ACTION_STATUSES, action.status, '待处理')) + '</span></div>' +
          (action.deliverable ? '<div><span>交付物</span>' + esc(action.deliverable) + '</div>' : '') +
          (action.acceptance ? '<div><span>验收</span>' + esc(action.acceptance) + '</div>' : '') +
          (action.dependencies ? '<div><span>依赖</span>' + esc(action.dependencies) + '</div>' : '') + '</div>';
      }).join('') + '</div></div>';
  }

  function blankMeetingAction() {
    return { id: '', title: '', owner: '', due: '', deliverable: '', acceptance: '', dependencies: '', status: 'open' };
  }

  function openMeetingForm(id) {
    var meeting = findById(view.data.meetings.items, id);
    view.form = { kind: 'meeting', id: id || '' };
    view.meetingDraft = Object.assign({}, meeting || {});
    view.meetingActionDrafts = meeting && Array.isArray(meeting.actionItems)
      ? meeting.actionItems.map(function (action) { return Object.assign(blankMeetingAction(), action); })
      : [blankMeetingAction()];
    render();
  }

  function syncMeetingDraft() {
    var form = document.querySelector('[data-hub-form="meeting"]');
    if (!form) return;
    var data = new FormData(form);
    var get = function (name) { return String(data.get(name) || '').trim(); };
    view.meetingDraft = Object.assign({}, view.meetingDraft || {}, {
      title: get('title'), type: get('type') || 'sync', status: get('status') || 'planned',
      startAt: get('startAt'), endAt: get('endAt'), nextReviewAt: get('nextReviewAt'),
      participants: parseList(get('participants')), projectLinks: parseList(get('projectLinks')),
      transcriptFile: get('transcriptFile'), minutesFile: get('minutesFile'), summary: get('summary'),
      decisions: parseList(get('decisions')), risks: parseList(get('risks')),
      openQuestions: parseList(get('openQuestions'))
    });
  }

  function syncMeetingActionDrafts() {
    var rows = document.querySelectorAll('[data-hub-meeting-action]');
    view.meetingActionDrafts = Array.prototype.map.call(rows, function (row, index) {
      var value = function (name) {
        var input = row.querySelector('[name="' + name + '"]');
        return input ? String(input.value || '').trim() : '';
      };
      return Object.assign({}, (view.meetingActionDrafts || [])[index] || {}, {
        id: value('action_id'), title: value('action_title'), owner: value('action_owner'),
        due: value('action_due'), deliverable: value('action_deliverable'),
        acceptance: value('action_acceptance'), dependencies: value('action_dependencies'),
        status: value('action_status') || 'open'
      });
    });
  }

  function syncMeetingFormDrafts() {
    syncMeetingDraft();
    syncMeetingActionDrafts();
  }

  function meetingActionEditor(action, index) {
    return '<div class="hub-action-editor" data-hub-meeting-action="' + index + '">' +
      '<input type="hidden" name="action_id" value="' + esc(action.id || '') + '">' +
      '<div class="hub-action-editor-head"><span>行动项 ' + (index + 1) + '</span>' +
      '<button class="hub-icon-btn danger" type="button" data-hub-action="remove-meeting-action" data-hub-index="' + index + '" aria-label="删除行动项">' + svg(ICON.trash, 14) + '</button></div>' +
      '<div class="hub-form-grid">' +
      field('action_title', '事项', action.title, 'text') +
      field('action_owner', '负责人', action.owner, 'text') +
      field('action_due', '截止日期', action.due, 'date') +
      selectField('action_status', '状态', ACTION_STATUSES, action.status || 'open') +
      field('action_deliverable', '交付物', action.deliverable, 'text') +
      field('action_acceptance', '验收标准', action.acceptance, 'text') +
      field('action_dependencies', '依赖', action.dependencies, 'text') +
      '</div></div>';
  }

  function meetingForm() {
    var meeting = view.meetingDraft || findById(view.data.meetings.items, view.form.id) || {};
    var actions = view.meetingActionDrafts || (meeting.actionItems || []).map(function (action) {
      return Object.assign(blankMeetingAction(), action);
    });
    return '<form class="hub-card hub-section hub-meeting-form" data-hub-form="meeting">' +
      '<div class="hub-form-grid">' +
      field('title', '会议名称', meeting.title, 'text', true) +
      selectField('type', '会议类型', MEETING_TYPES, meeting.type || 'sync') +
      selectField('status', '状态', MEETING_STATUSES, meeting.status || 'planned') +
      field('startAt', '开始时间', dateTimeInputValue(meeting.startAt), 'datetime-local') +
      field('endAt', '结束时间', dateTimeInputValue(meeting.endAt), 'datetime-local') +
      field('nextReviewAt', '下次回顾时间', dateTimeInputValue(meeting.nextReviewAt), 'datetime-local') +
      field('participants', '参与人（逗号或换行分隔）', (meeting.participants || []).join(', '), 'text') +
      field('projectLinks', '项目链接（逗号或换行分隔）', (meeting.projectLinks || []).join(', '), 'text') +
      field('transcriptFile', '逐字稿文件引用', meeting.transcriptFile, 'text') +
      field('minutesFile', '纪要文件引用', meeting.minutesFile, 'text') + '</div>' +
      '<label class="hub-field"><span class="hub-field-label">摘要</span><textarea class="hub-textarea" name="summary">' + esc(meeting.summary || '') + '</textarea></label>' +
      '<div class="hub-form-grid hub-meeting-notes-grid">' +
      textAreaField('decisions', '决策（每行一项）', (meeting.decisions || []).join('\n')) +
      textAreaField('risks', '风险（每行一项）', (meeting.risks || []).join('\n')) +
      textAreaField('openQuestions', '待确认问题（每行一项）', (meeting.openQuestions || []).join('\n')) + '</div>' +
      '<div class="hub-action-editor-section"><div class="hub-section-head"><span class="hub-section-title">行动项</span>' +
      '<div class="hub-section-actions"><button class="hub-btn" type="button" data-hub-action="add-meeting-action">' + svg(ICON.plus, 13) + '添加行动项</button></div></div>' +
      (actions.length ? actions.map(meetingActionEditor).join('') : '<div class="hub-empty">暂无行动项，可按需添加。</div>') + '</div>' +
      formActions() + '</form>';
  }

  /* ── 项目运维 ─────────────────────────────────────────────────────── */

  function statusToHub(status, stale) {
    var s = String(status || '').toLowerCase();
    if (s === 'critical' || s === 'unknown' || s === 'down' || s === 'failed') return 'down';
    if (stale || s === 'stale') return 'stale';
    if (s === 'ok' || s === 'healthy' || s === 'running') return 'ok';
    if (s === 'warning' || s === 'watch' || s === 'degraded') return 'watch';
    return 'down';
  }

  function worstStatus(items, fallback) {
    var worst = statusToHub(fallback || 'ok');
    items.forEach(function (item) {
      var st = statusToHub(item && item.status, item && item.stale);
      if ((STATUS_WEIGHT[st] || 0) > (STATUS_WEIGHT[worst] || 0)) worst = st;
    });
    return worst;
  }

  function machineChecks(machine) {
    var checks = machine && machine.checks;
    return Array.isArray(checks) ? checks : [];
  }

  function serviceChecks(service) {
    var checks = service && service.checks;
    return Array.isArray(checks) ? checks : [];
  }

  function machineUpdatedAt(machine) {
    return machine && (machine.updatedAt || machine.lastCollectedAt || machine.lastSeenAt || '');
  }

  function serviceUpdatedAt(service) {
    return service && (service.updatedAt || service.lastCollectedAt || service.lastSeenAt || '');
  }

  function parseIsoMs(iso) {
    var d = new Date(iso || '');
    return isNaN(d.getTime()) ? 0 : d.getTime();
  }

  function isStale(item, ops) {
    ops = ops || {};
    if (!item) return true;
    if (item.stale === true) return true;
    var at = machineUpdatedAt(item) || serviceUpdatedAt(item) || ops.generatedAt || '';
    var seen = parseIsoMs(at);
    if (!seen) return true;
    var itemAge = Number(item.staleAfterMinutes);
    var opsAge = Number(ops.staleAfterMinutes);
    var maxAge = Number.isFinite(itemAge) && itemAge > 0 ? itemAge :
      (Number.isFinite(opsAge) && opsAge > 0 ? opsAge : 180);
    return (Date.now() - seen) > maxAge * 60 * 1000;
  }

  function ownerLabel(owner) {
    return OWNER_LABELS[owner] || owner || '未分类';
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || status || '未知';
  }

  function statusBadge(status, stale) {
    var st = statusToHub(status, stale);
    return '<span class="hub-status ' + esc(st) + '">' +
      '<span class="hub-dot ' + esc(st) + '"></span>' + esc(statusLabel(st)) + '</span>';
  }

  function machineStatus(machine, ops) {
    var checks = machineChecks(machine);
    return statusToHub(worstStatus(checks, machine && machine.status), isStale(machine, ops));
  }

  function serviceStatus(service, ops) {
    return statusToHub(service && service.status, service && service.managed ? isStale(service, ops) : false);
  }

  function machineMap(ops) {
    var map = {};
    (ops.machines || []).forEach(function (m) { if (m && m.id) map[m.id] = m; });
    return map;
  }

  function serviceOwner(service, machinesById) {
    return service.ownership || (machinesById[service.machineId] && machinesById[service.machineId].ownership) || 'personal';
  }

  function serviceKind(service) {
    return String(service.kind || (isManagedService(service) ? 'automatic' : 'manual') || '').trim();
  }

  function serviceKindLabel(kind) {
    if (kind === 'manual') return '手工';
    if (kind === 'automatic') return '自动';
    return kind || '未分类';
  }

  function listenText(service) {
    if (Array.isArray(service.listen)) return service.listen.filter(Boolean).join(' ');
    return String(service.listen || '');
  }

  function valueText(value) {
    if (value == null) return '';
    if (Array.isArray(value)) return value.map(valueText).join(' ');
    if (typeof value === 'object') {
      try { return JSON.stringify(value); } catch (_) { return ''; }
    }
    return String(value);
  }

  function machineSearchText(machine) {
    var os = machine && machine.os;
    var resources = machine && (machine.resources || machine.specs);
    return [
      machine && machine.id,
      machine && machine.name,
      machine && machine.hostname,
      machine && machine.host,
      machine && machine.ip,
      machine && machine.ipAddress,
      machine && machine.publicIp,
      machine && machine.privateIp,
      machine && machine.region,
      machine && machine.role,
      valueText(os),
      valueText(resources)
    ].join(' ').toLowerCase();
  }

  function serviceSearchText(service, machinesById) {
    var machine = machinesById[service.machineId] || {};
    return [
      service.name,
      service.machineId,
      service.hostname,
      service.host,
      service.ip,
      service.ipAddress,
      machine.id,
      machine.name,
      machine.hostname,
      machine.host,
      machine.ip,
      machine.ipAddress,
      machine.publicIp,
      machine.privateIp,
      listenText(service),
      service.kind,
      service.startup,
      service.control,
      service.detail,
      service.notes,
      service.url,
      service.owner
    ].join(' ').toLowerCase();
  }

  function queryMatches(text) {
    var q = String(view.opsQuery || '').trim().toLowerCase();
    return !q || String(text || '').indexOf(q) !== -1;
  }

  function statusMatches(status) {
    return view.opsStatus === 'all' || status === view.opsStatus;
  }

  function kindMatches(kind) {
    return view.opsKind === 'all' || kind === view.opsKind;
  }

  function opsKindOptions(ops) {
    var seen = {};
    (ops.services || []).forEach(function (service) {
      var kind = serviceKind(service);
      if (kind) seen[kind] = true;
    });
    return Object.keys(seen).sort().map(function (kind) {
      return { id: kind, label: serviceKindLabel(kind) };
    });
  }

  function servicesForMachine(ops, machineId) {
    return (ops.services || []).filter(function (service) { return service.machineId === machineId; });
  }

  function visibleServices(ops, opts) {
    opts = opts || {};
    var machinesById = machineMap(ops);
    return (ops.services || []).filter(function (service) {
      var status = serviceStatus(service, ops);
      if (view.opsOwner !== 'all' && serviceOwner(service, machinesById) !== view.opsOwner) return false;
      if (!statusMatches(status)) return false;
      if (!kindMatches(serviceKind(service))) return false;
      if (view.opsMachine && service.machineId !== view.opsMachine) return false;
      if (opts.exceptionsOnly && status === 'ok') return false;
      if (!queryMatches(serviceSearchText(service, machinesById))) return false;
      return true;
    }).sort(function (a, b) {
      return (STATUS_WEIGHT[serviceStatus(b, ops)] || 0) - (STATUS_WEIGHT[serviceStatus(a, ops)] || 0) ||
        String(serviceUpdatedAt(b)).localeCompare(String(serviceUpdatedAt(a))) ||
        String(a.name || '').localeCompare(String(b.name || ''));
    });
  }

  function visibleMachines(ops, opts) {
    opts = opts || {};
    var machines = Array.isArray(ops.machines) ? ops.machines : [];
    var machinesById = machineMap(ops);
    return machines.filter(function (machine) {
      var status = machineStatus(machine, ops);
      var services = servicesForMachine(ops, machine.id);
      if (view.opsOwner !== 'all' && (machine.ownership || 'personal') !== view.opsOwner) return false;
      if (!statusMatches(status)) return false;
      if (view.opsMachine && machine.id !== view.opsMachine) return false;
      if (view.opsKind !== 'all' && !services.some(function (service) { return serviceKind(service) === view.opsKind; })) return false;
      if (opts.exceptionsOnly && status === 'ok') return false;
      if (!queryMatches(machineSearchText(machine) + ' ' + services.map(function (service) {
        return serviceSearchText(service, machinesById);
      }).join(' '))) return false;
      return true;
    }).sort(function (a, b) {
      return (STATUS_WEIGHT[machineStatus(b, ops)] || 0) - (STATUS_WEIGHT[machineStatus(a, ops)] || 0) ||
        String(machineUpdatedAt(b)).localeCompare(String(machineUpdatedAt(a))) ||
        String(a.name || a.id || '').localeCompare(String(b.name || b.id || ''));
    });
  }

  function opsFiltered(ops, opts) {
    return {
      machines: visibleMachines(ops, opts),
      services: visibleServices(ops, opts)
    };
  }

  function opsViewCount(id, ops) {
    if (id === 'servers') return visibleMachines(ops).length;
    if (id === 'services') return visibleServices(ops).length;
    var bad = opsFiltered(ops, { exceptionsOnly: true });
    return bad.machines.length + bad.services.length;
  }

  function snapshotIsStale(ops) {
    return isStale({ updatedAt: ops.generatedAt, staleAfterMinutes: ops.staleAfterMinutes }, ops);
  }

  function specLine(machine) {
    var r = machine.resources || machine.specs || {};
    var cpu = r.cpu || machine.cpu || '';
    if (cpu && typeof cpu === 'object') cpu = cpu.logicalCpus || cpu.logical_cpus || cpu.model || '';
    var memory = r.memory || r.memoryGiB || r.memory_gib || machine.memoryGiB || machine.memory_gib || machine.memory_gib;
    var gpu = r.gpu || machine.gpu || '';
    if (gpu && typeof gpu === 'object') {
      gpu = [gpu.count ? gpu.count + 'x' : '', gpu.model || gpu.vendor || 'GPU'].filter(Boolean).join(' ');
    }
    var disk = r.disk || r.diskGiB || r.rootDiskGiB || machine.rootDiskGiB || machine.root_disk_gib || '';
    return [
      cpu ? 'CPU ' + cpu : '',
      memory ? '内存 ' + memory + (String(memory).match(/[a-z]/i) ? '' : ' GiB') : '',
      gpu ? 'GPU ' + gpu : '',
      disk ? '磁盘 ' + disk + (String(disk).match(/[a-z]/i) ? '' : ' GiB') : ''
    ].filter(Boolean).join(' · ');
  }

  function osLine(machine) {
    var os = machine.os || {};
    if (typeof os === 'string') return os;
    return [os.family, os.version].filter(Boolean).join(' ');
  }

  function machineLocation(machine) {
    return [machine.host, machine.region].filter(Boolean).join(' · ');
  }

  function metricValueFromDetail(detail) {
    var m = String(detail || '').match(/(-?\d+(?:\.\d+)?)\s*(?:%|°C|C)/);
    return m ? Number(m[1]) : null;
  }

  function metricKind(check) {
    var key = String(check.key || '').toLowerCase();
    var label = String(check.label || '').toLowerCase();
    if (key.indexOf('disk') !== -1 || label.indexOf('磁盘') !== -1) return '磁盘';
    if (key.indexOf('memory') !== -1 || label.indexOf('内存') !== -1) return '内存';
    if (key.indexOf('swap') !== -1 || label.indexOf('swap') !== -1) return 'Swap';
    if (key.indexOf('gpu_temp') !== -1 || label.indexOf('温度') !== -1) return 'GPU 温度';
    return '';
  }

  function renderResourceBars(machine) {
    var checks = machineChecks(machine).filter(function (check) { return metricKind(check) && metricValueFromDetail(check.detail) !== null; });
    if (!checks.length) return '';
    return '<div class="hub-resource-bars">' + checks.slice(0, 5).map(function (check) {
      var value = metricValueFromDetail(check.detail);
      var pct = Math.max(0, Math.min(100, value));
      var st = statusToHub(check.status);
      return '<div class="hub-resource-bar">' +
        '<div class="hub-resource-bar-head"><span>' + esc(metricKind(check)) + '</span><span>' + esc(check.detail) + '</span></div>' +
        '<div class="hub-resource-track"><span class="' + esc(st) + '" style="width:' + pct + '%"></span></div>' +
        '</div>';
    }).join('') + '</div>';
  }

  function renderOps() {
    var ops = view.data.ops;
    var html = renderOpsOverview(ops) + renderOpsFilters(ops);
    html += '<div id="hubOpsDynamic">' + renderOpsDynamic(ops) + '</div>';
    html += '<div class="hub-section"><div class="hub-section-head">' +
      '<span class="hub-section-title">常用命令</span>' +
      '<div class="hub-section-actions">' +
      '<button class="hub-btn primary" data-hub-action="new-command">' + svg(ICON.plus, 13) + '新增命令</button>' +
      '</div></div>';
    if (view.form && view.form.kind === 'command') html += commandForm();
    html += (ops.commands.length
      ? '<div class="hub-list">' + ops.commands.map(commandRow).join('') + '</div>'
      : '<div class="hub-empty">把那些每次都要翻历史记录的命令存这儿，一键复制或直接交给 Agent 执行。</div>') +
      '</div>';
    html += renderServiceDrawer(ops);
    return html;
  }

  function renderOpsOverview(ops) {
    var machines = ops.machines || [];
    var services = ops.services || [];
    var counts = { ok: 0, watch: 0, stale: 0, down: 0 };
    machines.forEach(function (m) { counts[machineStatus(m, ops)] += 1; });
    var watch = counts.watch + counts.stale;
    return '<div class="hub-ops-overview-label">全局快照</div><div class="hub-ops-metrics">' +
      '<div class="hub-stat"><div class="hub-stat-value">' + machines.length + '</div><div class="hub-stat-label">主机数</div></div>' +
      '<div class="hub-stat"><div class="hub-stat-value">' + counts.ok + '</div><div class="hub-stat-label">正常</div></div>' +
      '<div class="hub-stat"><div class="hub-stat-value">' + watch + '</div><div class="hub-stat-label">观察</div></div>' +
      '<div class="hub-stat"><div class="hub-stat-value">' + counts.down + '</div><div class="hub-stat-label">故障</div></div>' +
      '<div class="hub-stat"><div class="hub-stat-value">' + services.length + '</div><div class="hub-stat-label">服务数</div></div>' +
      '<div class="hub-stat wide"><div class="hub-stat-value small">' + esc(fmtDate(ops.generatedAt) || '未知') + '</div><div class="hub-stat-label">生成时间 / 最近同步' +
      (snapshotIsStale(ops) ? ' · 数据过期' : ' · 数据有效') + '</div></div>' +
      '</div>';
  }

  function renderOpsFilters(ops) {
    var kinds = [{ id: 'all', label: '全部类型' }].concat(opsKindOptions(ops));
    return '<div class="hub-ops-snapshot">' +
      '<button class="hub-btn" data-hub-action="reload">重新读取数据</button>' +
      '<span>仅重新读取本地监控快照，不会连接或操作远端服务器</span>' +
      '<span>生成时间：' + esc(fmtDate(ops.generatedAt) || '未知') + '</span>' +
      '<span class="' + (snapshotIsStale(ops) ? 'stale' : 'ok') + '">' + (snapshotIsStale(ops) ? '数据过期' : '数据有效') + '</span>' +
      '</div>' +
      '<div class="hub-ops-filterbar">' +
      '<div class="hub-ops-filter" role="group" aria-label="归属筛选">' + OWNERS.map(function (owner) {
        return '<button class="hub-segment' + (view.opsOwner === owner.id ? ' active' : '') + '" data-hub-owner-filter="' + owner.id + '">' +
          esc(owner.label) + '</button>';
      }).join('') + '</div>' +
      '<div class="hub-ops-filter" role="group" aria-label="状态筛选">' + OPS_STATUS_FILTERS.map(function (status) {
        return '<button class="hub-segment' + (view.opsStatus === status.id ? ' active' : '') + '" data-hub-status-filter="' + status.id + '">' +
          esc(status.label) + '</button>';
      }).join('') + '</div>' +
      '<label class="hub-field compact"><span class="hub-field-label">类型</span>' +
      '<select class="hub-select" data-hub-kind-filter>' + optionsHtml(kinds, view.opsKind) + '</select></label>' +
      '<label class="hub-field search"><span class="hub-field-label">搜索</span>' +
      '<input class="hub-input" data-hub-ops-query value="' + esc(view.opsQuery) + '" placeholder="搜索服务、主机、IP、端口或 unit" autocomplete="off"></label>' +
      (view.opsMachine ? '<button class="hub-btn" data-hub-action="clear-machine-filter">清除机器筛选</button>' : '') +
      '</div>';
  }

  function renderOpsDynamic(ops) {
    var html = '<div class="hub-ops-view-tabs" role="tablist" aria-label="运维视图">' + OPS_VIEWS.map(function (tab) {
      return '<button class="hub-ops-view-tab' + (view.opsView === tab.id ? ' active' : '') + '" data-hub-ops-view="' + tab.id + '" role="tab" ' +
        'aria-selected="' + (view.opsView === tab.id ? 'true' : 'false') + '">' +
        '<span>' + esc(tab.id === 'exceptions' ? '异常实体' : tab.label) + '</span><span class="hub-view-count">' + opsViewCount(tab.id, ops) + '</span></button>';
    }).join('') + '</div>';
    if (view.opsView === 'services') return html + renderServicesView(ops, false);
    if (view.opsView === 'exceptions') return html + renderExceptionsView(ops);
    return html + renderServersView(ops, false);
  }

  function refreshOpsView() {
    var box = $id('hubOpsDynamic');
    if (!box || !view.data || !view.data.ops) { render(); return; }
    box.innerHTML = renderOpsDynamic(view.data.ops);
  }

  function renderServersView(ops, exceptionsOnly) {
    var machines = visibleMachines(ops, { exceptionsOnly: !!exceptionsOnly });
    return '<div class="hub-section"><div class="hub-section-head">' +
      '<span class="hub-section-title">' + (exceptionsOnly ? '异常服务器' : '服务器') + '</span></div>' +
      (machines.length
        ? '<div class="hub-machine-grid">' + machines.map(function (m) { return renderMachineCard(m, ops); }).join('') + '</div>'
        : '<div class="hub-empty">当前筛选下没有服务器。</div>') +
      '</div>';
  }

  function renderServicesView(ops, exceptionsOnly) {
    var services = visibleServices(ops, { exceptionsOnly: !!exceptionsOnly });
    var title = exceptionsOnly ? '异常服务' : '服务';
    return '<div class="hub-section"><div class="hub-section-head">' +
      '<span class="hub-section-title">' + esc(title) + '</span>' +
      '<div class="hub-section-actions">' +
      '<button class="hub-btn primary" data-hub-action="new-service">' + svg(ICON.plus, 13) + '新增手工服务</button>' +
      '</div></div>' +
      (view.form && view.form.kind === 'service' ? serviceForm() : '') +
      (services.length ? renderServiceTable(services, ops) :
        '<div class="hub-empty">' + (exceptionsOnly ? '当前筛选下没有异常服务。' : '还没登记服务。把你日常要盯的机器、站点、定时任务放进来。') + '</div>') +
      '</div>';
  }

  function renderExceptionsView(ops) {
    return renderOpsLifecycle(ops) + renderServersView(ops, true) + renderServicesView(ops, true);
  }

  function lifecycleEventId(event, index) {
    return String(event && (event.id || event.eventId) || [
      event && event.entityType,
      event && event.entityId,
      event && event.type,
      event && event.statusChangedAt,
      event && event.incidentOpenedAt,
      event && event.lifecycleSource
    ].filter(Boolean).join(':') || ('event-' + index));
  }

  function lifecycleEventTime(event) {
    return String(event && (
      event.occurredAt || event.createdAt || event.updatedAt || event.time || event.timestamp ||
      event.statusChangedAt || event.incidentOpenedAt
    ) || '');
  }

  function lifecycleActionLabel(type) {
    return ({ opened: '首次异常', status_changed: '状态变化', recovered: '已恢复' })[String(type || '').toLowerCase()] || '状态事件';
  }

  function acknowledgementFor(eventId, ops) {
    return (ops.acknowledgements || []).filter(function (ack) { return ack && ack.eventId === eventId; })[0] || null;
  }

  function lifecycleEntityLabel(type, id, ops) {
    if (type === 'machine') {
      var machine = findById(ops.machines || [], id);
      return machine ? (machine.name || machine.id) : id;
    }
    if (type === 'service') {
      var service = findById(ops.services || [], id);
      return service ? (service.name || service.id) : id;
    }
    return id || '未知实体';
  }

  function renderOpsLifecycle(ops) {
    var events = (ops.events || []).slice().sort(function (a, b) {
      return String(lifecycleEventTime(b)).localeCompare(String(lifecycleEventTime(a)));
    });
    return '<div class="hub-section hub-lifecycle" aria-labelledby="hubOpsLifecycleTitle">' +
      '<div class="hub-section-head"><span class="hub-section-title" id="hubOpsLifecycleTitle">事件时间线</span>' +
      '<div class="hub-section-actions"><button class="hub-btn primary" data-hub-action="new-maintenance">' +
      svg(ICON.plus, 13) + '维护窗口</button></div></div>' +
      (view.form && view.form.kind === 'maintenance' ? maintenanceForm() : '') +
      (events.length ? '<div class="hub-lifecycle-list" role="list">' + events.map(function (event, index) {
        return renderLifecycleEvent(event, ops, index);
      }).join('') + '</div>' : '<div class="hub-empty">当前没有自动事件。维护窗口只作为上下文显示，不会隐藏真实故障。</div>') +
      '</div>';
  }

  function renderLifecycleEvent(event, ops, index) {
    var id = lifecycleEventId(event, index);
    var type = String(event.entityType || event.targetType || '').toLowerCase();
    var entityId = String(event.entityId || event.targetId || '');
    var ack = acknowledgementFor(id, ops);
    var title = event.title || event.summary || event.message || lifecycleActionLabel(event.type);
    var detail = event.detail || event.description || event.reason || '';
    var status = statusToHub(event.status || event.toStatus || event.severity || event.level, false);
    var source = event.lifecycleSource || (event.source && (event.source.name || event.source.kind)) || '';
    return '<div class="hub-lifecycle-event" role="listitem" data-hub-event-id="' + esc(id) + '">' +
      '<div class="hub-lifecycle-main">' +
      '<div class="hub-lifecycle-head">' + statusBadge(status, false) +
      '<span class="hub-lifecycle-title">' + esc(title) + '</span></div>' +
      '<div class="hub-lifecycle-meta">' +
      '<span>事件时间：' + esc(lifecycleEventTime(event) || '未知') + '</span>' +
      (entityId ? '<span>' + esc(type || 'entity') + '：' + esc(lifecycleEntityLabel(type, entityId, ops)) + '</span>' : '') +
      (source ? '<span>来源：' + esc(source) + '</span>' : '') +
      '</div>' +
      (detail ? '<div class="hub-lifecycle-detail">' + esc(detail) + '</div>' : '') +
      (ack ? '<div class="hub-lifecycle-ack">已确认：' + esc(ack.note || '无备注') +
        (ack.createdAt ? ' · ' + esc(ack.createdAt) : '') + '</div>' : '') +
      '</div>' +
      '<div class="hub-item-actions">' +
      (ack ? '<span class="hub-managed-lock">已确认</span>' :
        '<button class="hub-btn compact" data-hub-action="ack-event" data-hub-id="' + esc(id) + '">确认</button>') +
      '</div></div>';
  }

  function maintenanceTimes(row) {
    return {
      start: row && (row.start || row.startsAt || ''),
      end: row && (row.end || row.endsAt || '')
    };
  }

  function isActiveMaintenance(row, now) {
    var times = maintenanceTimes(row);
    var start = parseIsoMs(times.start);
    var end = parseIsoMs(times.end);
    var t = now || Date.now();
    if (start && t < start) return false;
    if (end && t > end) return false;
    return !!(row && row.entityType && row.entityId);
  }

  function activeMaintenanceFor(entityType, entityId, ops) {
    return (ops.maintenance || []).filter(function (row) {
      return row && row.entityType === entityType && row.entityId === entityId && isActiveMaintenance(row);
    });
  }

  function maintenanceDetails(entityType, entityId, ops) {
    var rows = activeMaintenanceFor(entityType, entityId, ops);
    if (!rows.length) return '';
    return '<div class="hub-maintenance-details">' + rows.map(function (row) {
      var times = maintenanceTimes(row);
      return '<span class="hub-maintenance">' + esc(row.reason || '维护中') + '</span>' +
        '<span>维护中：' + esc(times.start || '未定') + ' → ' + esc(times.end || '未定') + '</span>';
    }).join('') + '</div>';
  }

  function renderMachineCard(machine, ops) {
    var stale = isStale(machine, ops);
    var status = machineStatus(machine, ops);
    var checks = machineChecks(machine);
    var services = servicesForMachine(ops, machine.id);
    var badChecks = checks.filter(function (check) { return statusToHub(check.status, check.stale) !== 'ok'; });
    var okChecks = checks.length - badChecks.length;
    return '<div class="hub-machine-card" data-machine-id="' + esc(machine.id) + '">' +
      '<div class="hub-machine-head">' +
      '<div class="hub-machine-title">' + esc(machine.name || machine.id) + '</div>' +
      statusBadge(status, stale) +
      '</div>' +
      '<div class="hub-machine-meta">' +
      '<span>' + esc(ownerLabel(machine.ownership)) + '</span>' +
      (machine.role ? '<span>' + esc(machine.role) + '</span>' : '') +
      (machineLocation(machine) ? '<span>' + esc(machineLocation(machine)) + '</span>' : '') +
      '</div>' +
      '<div class="hub-machine-sync">最近采集：' + esc(fmtDate(machineUpdatedAt(machine)) || '未知') + (stale ? ' · 数据过期' : '') + '</div>' +
      maintenanceDetails('machine', machine.id, ops) +
      renderResourceBars(machine) +
      '<div class="hub-machine-summary">' +
      '<span>' + services.length + ' 个服务</span>' +
      (badChecks.length ? '<span class="danger">' + esc(badChecks.slice(0, 2).map(function (check) {
        return (check.label || check.key || '检查') + (check.detail ? '：' + check.detail : '');
      }).join('；')) + (badChecks.length > 2 ? ' 等' : '') + '</span>' :
        '<span>异常原因：无</span>') +
      '</div>' +
      (okChecks > 0 ? '<details class="hub-checks-fold"><summary>正常检查 ' + okChecks + ' 项</summary>' +
        checks.filter(function (check) { return statusToHub(check.status, check.stale) === 'ok'; }).slice(0, 8).map(function (check) {
          return '<div class="hub-check-row">' + statusBadge(check.status, false) +
            '<span>' + esc(check.label || check.key) + '</span><span>' + esc(check.detail || '') + '</span></div>';
        }).join('') + '</details>' : '') +
      '<div class="hub-machine-actions"><button class="hub-btn" data-hub-action="machine-services" data-hub-id="' + esc(machine.id) + '">查看服务</button></div>' +
      '</div>';
  }

  function isManagedService(service) {
    return !!(service && service.managed === true);
  }

  function serviceCard(s, ops) {
    var url = safeUrl(s.url);
    var managed = isManagedService(s);
    var status = serviceStatus(s, ops || view.data.ops);
    var stale = managed && isStale(s, ops || view.data.ops);
    var listen = Array.isArray(s.listen) ? s.listen.filter(Boolean).slice(0, 12) : [];
    return '<div class="hub-item"><div class="hub-item-main">' +
      '<div class="hub-item-title">' + statusBadge(status, stale) +
      (url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(s.name) + '</a>' : esc(s.name)) +
      '</div>' +
      '<div class="hub-item-sub">' +
      (managed ? '自动登记' : esc(labelOf(ENVS, s.env, ''))) +
      (s.machineId ? ' · machineId ' + esc(s.machineId) : '') +
      (s.kind ? ' · 类型 ' + esc(s.kind) : '') +
      (s.owner ? ' · 负责人 ' + esc(s.owner) : '') +
      (s.updatedAt ? ' · 最近采集 ' + esc(fmtDate(s.updatedAt)) : '') +
      (stale ? ' · 数据过期' : '') +
      (s.startup ? '<br>启动：' + esc(s.startup) : '') +
      (listen.length ? '<br>监听：' + esc(listen.join(' · ')) : '') +
      (s.control ? '<br>管理：<code>' + esc(s.control) + '</code>' : '') +
      (s.detail ? '<br>详情：' + esc(s.detail) : '') +
      (s.notes ? '<br>' + esc(s.notes) : '') + '</div>' +
      maintenanceDetails('service', s.id, ops || view.data.ops) + '</div>' +
      '<div class="hub-item-actions">' +
      iconBtn('service-agent', s.id, ICON.send, '让 Agent 检查这个服务') +
      (managed ? iconBtn('edit-service', s.id, ICON.edit, '编辑自动服务备注') +
        '<span class="hub-managed-lock" title="自动登记服务不可删除或覆盖核心字段">自动</span>' :
        iconBtn('edit-service', s.id, ICON.edit, '编辑') + iconBtn('del-service', s.id, ICON.trash, '删除', 'danger')) +
      '</div></div>';
  }

  function serviceHostLine(service, machinesById) {
    var machine = machinesById[service.machineId] || {};
    return [
      machine.name || machine.id || service.machineId,
      machine.host || machine.hostname,
      machine.ip || machine.ipAddress || machine.publicIp || machine.privateIp
    ].filter(Boolean).join(' · ');
  }

  function renderServiceActions(service) {
    var managed = isManagedService(service);
    return '<button class="hub-btn compact" data-hub-action="open-service" data-hub-id="' + esc(service.id) + '">查看</button>' +
      iconBtn('service-agent', service.id, ICON.send, '让 Agent 检查这个服务') +
      (managed ? iconBtn('edit-service', service.id, ICON.edit, '编辑自动服务备注') +
        '<span class="hub-managed-lock" title="自动登记服务不可删除或覆盖核心字段">自动</span>' :
        iconBtn('edit-service', service.id, ICON.edit, '编辑') + iconBtn('del-service', service.id, ICON.trash, '删除', 'danger'));
  }

  function renderServiceTable(services, ops) {
    var machinesById = machineMap(ops);
    return '<div class="hub-service-table-wrap">' +
      '<table class="hub-service-table">' +
      '<thead><tr><th>状态</th><th>服务</th><th>主机</th><th>启动方式</th><th>监听</th><th>最近采集</th><th>操作</th></tr></thead>' +
      '<tbody>' + services.map(function (service) { return renderServiceRow(service, ops, machinesById); }).join('') + '</tbody>' +
      '</table></div>';
  }

  function renderServiceRow(service, ops, machinesById) {
    var managed = isManagedService(service);
    var status = serviceStatus(service, ops);
    var stale = managed && isStale(service, ops);
    var url = safeUrl(service.url);
    var listen = listenText(service);
    return '<tr class="hub-service-row" data-hub-service-row="' + esc(service.id) + '" tabindex="0">' +
      '<td data-label="状态">' + statusBadge(status, stale) + '</td>' +
      '<td data-label="服务"><div class="hub-service-name">' +
      (url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(service.name) + '</a>' : esc(service.name || service.id)) +
      '</div><div class="hub-service-meta">' + esc(managed ? '自动登记' : labelOf(ENVS, service.env, '手工')) +
      ' · ' + esc(serviceKindLabel(serviceKind(service))) + (service.owner ? ' · ' + esc(service.owner) : '') + '</div>' +
      maintenanceDetails('service', service.id, ops) + '</td>' +
      '<td data-label="主机">' + esc(serviceHostLine(service, machinesById) || service.machineId || '未绑定') + '</td>' +
      '<td data-label="启动方式">' + esc(service.startup || '未记录') + '</td>' +
      '<td data-label="监听">' + esc(listen || '未记录') + '</td>' +
      '<td data-label="最近采集">' + esc(fmtDate(serviceUpdatedAt(service)) || '未知') + (stale ? '<div class="hub-service-meta">数据过期</div>' : '') + '</td>' +
      '<td data-label="操作"><div class="hub-item-actions">' + renderServiceActions(service) + '</div></td>' +
      '</tr>';
  }

  function isReadOnlyCommand(command) {
    var c = String(command || '').trim();
    var lower = c.toLowerCase();
    if (!c || /[;&|`<>]/.test(c)) return false;
    return lower.indexOf('systemctl status ') === 0 ||
      lower.indexOf('docker ps') === 0 ||
      lower.indexOf('pm2 status ') === 0 ||
      lower.indexOf('ss ') === 0 ||
      lower.indexOf('netstat ') === 0 ||
      lower.indexOf('lsof ') === 0;
  }

  function readOnlyCommandsForService(service) {
    var commands = [];
    var add = function (label, command) {
      if (isReadOnlyCommand(command)) commands.push({ label: label, command: command });
    };
    var unit = String(service.unit || service.systemdUnit || '').trim();
    if (!unit && /\.service\b/.test(String(service.startup || ''))) {
      var match = String(service.startup).match(/([\w@.:-]+\.service)\b/);
      unit = match ? match[1] : '';
    }
    if (unit && /^[\w@.:-]+\.service$/.test(unit)) {
      add('systemd 状态', 'systemctl status ' + unit + ' --no-pager');
    }
    var container = String(service.container || service.containerName || '').trim();
    if (container && /^[\w.-]+$/.test(container)) {
      add('容器状态', 'docker ps --filter name=' + container);
    }
    var processName = String(service.process || service.processName || '').trim();
    if (processName && /^[\w.-]+$/.test(processName)) {
      add('PM2 状态', 'pm2 status ' + processName);
    }
    add('已登记管理命令', service.control);
    return commands;
  }

  function renderServiceFields(service) {
    var rows = Object.keys(service || {}).sort().map(function (key) {
      var value = service[key];
      var text = valueText(value);
      var safe = key === 'url' ? safeUrl(value) : '';
      return '<div class="hub-drawer-field"><span>' + esc(key) + '</span><span>' +
        (safe ? '<a href="' + esc(safe) + '" target="_blank" rel="noopener noreferrer">' + esc(text) + '</a>' : esc(text || '')) +
        '</span></div>';
    });
    return rows.length ? rows.join('') : '<div class="hub-empty">没有字段。</div>';
  }

  function renderServiceDrawer(ops) {
    var service = findById(ops.services || [], view.opsSelectedService);
    if (!service) return '';
    var machinesById = machineMap(ops);
    var commands = readOnlyCommandsForService(service);
    var status = serviceStatus(service, ops);
    var stale = isManagedService(service) && isStale(service, ops);
    return '<div class="hub-service-drawer-backdrop" data-hub-action="close-service-drawer"></div>' +
      '<aside class="hub-service-drawer" role="dialog" aria-modal="true" aria-labelledby="hubServiceDrawerTitle">' +
      '<div class="hub-drawer-head">' +
      '<div><div class="hub-section-title">服务详情</div><h2 id="hubServiceDrawerTitle">' + esc(service.name || service.id) + '</h2></div>' +
      '<button class="hub-icon-btn" data-hub-action="close-service-drawer" title="关闭" aria-label="关闭">' + svg(ICON.close, 16) + '</button>' +
      '</div>' +
      '<div class="hub-drawer-status">' + statusBadge(status, stale) + '<span>' + esc(serviceHostLine(service, machinesById) || '未绑定主机') + '</span></div>' +
      maintenanceDetails('service', service.id, ops) +
      '<div class="hub-drawer-block"><div class="hub-drawer-block-title">人工备注</div>' +
      '<div class="hub-item-sub">' + esc(service.notes || '未记录') + '</div>' +
      '<div class="hub-drawer-actions">' + iconBtn('edit-service', service.id, ICON.edit, isManagedService(service) ? '编辑自动服务备注' : '编辑') + '</div></div>' +
      '<div class="hub-drawer-block"><div class="hub-drawer-block-title">安全只读管理命令</div>' +
      (commands.length ? commands.map(function (cmd) {
        return '<div class="hub-drawer-command"><div><span>' + esc(cmd.label) + '</span><code>' + esc(cmd.command) + '</code></div>' +
          '<button class="hub-btn compact" data-hub-action="copy-service-command" data-hub-copy="' + esc(cmd.command) + '">' + svg(ICON.copy, 13) + '复制</button></div>';
      }).join('') : '<div class="hub-item-sub">没有可确认的只读管理命令。</div>') +
      '</div>' +
      '<div class="hub-drawer-block"><div class="hub-drawer-block-title">完整字段</div>' + renderServiceFields(service) + '</div>' +
      '</aside>';
  }

  function commandRow(c) {
    return '<div class="hub-cmd"><div class="hub-cmd-main">' +
      '<div class="hub-cmd-label">' + esc(c.label) + '</div>' +
      '<div class="hub-cmd-code">' + esc(c.command) + '</div>' +
      (c.notes ? '<div class="hub-item-sub">' + esc(c.notes) + '</div>' : '') +
      '</div><div class="hub-item-actions">' +
      iconBtn('copy-command', c.id, ICON.copy, '复制命令') +
      iconBtn('command-agent', c.id, ICON.send, '交给 Agent 执行') +
      iconBtn('edit-command', c.id, ICON.edit, '编辑') +
      iconBtn('del-command', c.id, ICON.trash, '删除', 'danger') +
      '</div></div>';
  }

  function serviceForm() {
    var it = findById(view.data.ops.services, view.form.id) || {};
    if (isManagedService(it)) {
      return '<form class="hub-card hub-section" data-hub-form="service">' +
        '<div class="hub-item-sub">自动登记服务只允许编辑人工备注，不覆盖 machineId、status、detail、updatedAt 等核心自动字段。</div>' +
        '<div class="hub-field" style="margin:12px 0"><span class="hub-field-label">备注</span>' +
        '<textarea class="hub-textarea" name="notes">' + esc(it.notes || '') + '</textarea></div>' +
        formActions() + '</form>';
    }
    return '<form class="hub-card hub-section" data-hub-form="service">' +
      '<div class="hub-form-grid">' +
      field('name', '服务名', it.name, 'text', true) +
      selectField('env', '环境', ENVS, it.env || 'prod') +
      selectField('status', '状态', STATUSES, it.status || 'ok') +
      field('url', '地址', it.url, 'text') +
      field('owner', '负责人', it.owner, 'text') +
      '</div>' +
      '<div class="hub-field" style="margin-bottom:12px"><span class="hub-field-label">备注</span>' +
      '<textarea class="hub-textarea" name="notes">' + esc(it.notes || '') + '</textarea></div>' +
      formActions() + '</form>';
  }

  function commandForm() {
    var it = findById(view.data.ops.commands, view.form.id) || {};
    return '<form class="hub-card hub-section" data-hub-form="command">' +
      '<div class="hub-form-grid">' +
      field('label', '名称', it.label, 'text', true) +
      field('notes', '备注', it.notes, 'text') +
      '</div>' +
      '<div class="hub-field" style="margin-bottom:12px"><span class="hub-field-label">命令</span>' +
      '<textarea class="hub-textarea" name="command" required>' + esc(it.command || '') + '</textarea></div>' +
      formActions() + '</form>';
  }

  function maintenanceForm() {
    var entityTypes = [{ id: 'service', label: '服务' }, { id: 'machine', label: '服务器' }];
    return '<form class="hub-card hub-section" data-hub-form="maintenance">' +
      '<div class="hub-form-grid">' +
      selectField('entityType', '实体类型', entityTypes, 'service') +
      field('entityId', '实体 ID', '', 'text', true) +
      field('start', '开始时间', '', 'text', true) +
      field('end', '结束时间', '', 'text', true) +
      '</div>' +
      '<div class="hub-field" style="margin-bottom:12px"><span class="hub-field-label">原因</span>' +
      '<textarea class="hub-textarea" name="reason" required></textarea></div>' +
      '<div class="hub-item-sub" style="margin-bottom:12px">维护窗口只作为界面上下文保存，不会隐藏真实故障，也不会触发任何远端操作。</div>' +
      formActions() + '</form>';
  }

  /* ── 资源库 ───────────────────────────────────────────────────────── */

  function renderResources() {
    var items = view.data.resources.items;
    var tags = {};
    items.forEach(function (i) { (i.tags || []).forEach(function (t) { tags[t] = (tags[t] || 0) + 1; }); });
    var tagList = Object.keys(tags).sort(function (a, b) { return tags[b] - tags[a]; }).slice(0, 20);

    var q = view.query.toLowerCase();
    var shown = items.filter(function (i) {
      if (view.tag && (i.tags || []).indexOf(view.tag) === -1) return false;
      if (!q) return true;
      return [i.title, i.url, i.category, i.note, (i.tags || []).join(' ')]
        .join(' ').toLowerCase().indexOf(q) !== -1;
    });

    var html = '<div class="hub-section-head">' +
      '<span class="hub-section-title">个人资源库</span>' +
      '<div class="hub-section-actions">' +
      '<button class="hub-btn primary" data-hub-action="new-resource">' + svg(ICON.plus, 13) + '收藏资源</button>' +
      '</div></div>';

    if (view.form && view.form.kind === 'resource') html += resourceForm();

    html += '<div class="hub-toolbar">' +
      '<input class="hub-input" id="hubSearch" placeholder="搜索标题、链接、备注、标签…" value="' + esc(view.query) + '">' +
      (tagList.length ? '<div class="hub-tags">' + tagList.map(function (t) {
        return '<button class="hub-tag' + (view.tag === t ? ' active' : '') + '" data-hub-action="filter-tag" ' +
          'data-hub-value="' + esc(t) + '">' + esc(t) + ' ' + tags[t] + '</button>';
      }).join('') + '</div>' : '') +
      '</div>';

    html += '<div id="hubResourceList">' + resourceListHtml(shown, items.length) + '</div>';
    return html;
  }

  function resourceListHtml(shown, total) {
    return shown.length
      ? '<div class="hub-list">' + shown.map(resourceRow).join('') + '</div>'
      : '<div class="hub-empty">' + (total ? '没有匹配的资源。' :
        '还没有收藏。链接、文档、常用提示词、账号所在位置 —— 都可以放这里统一检索。') + '</div>';
  }

  /* 只重画结果列表。整块重画会把用户正在填的新建表单一起冲掉，
   * 也会让输入框失焦、丢光标位置。 */
  function refreshResourceList() {
    var box = $id('hubResourceList');
    if (!box) { render(); return; }
    var items = view.data.resources.items;
    var q = view.query.toLowerCase();
    var shown = items.filter(function (i) {
      if (view.tag && (i.tags || []).indexOf(view.tag) === -1) return false;
      if (!q) return true;
      return [i.title, i.url, i.category, i.note, (i.tags || []).join(' ')]
        .join(' ').toLowerCase().indexOf(q) !== -1;
    });
    box.innerHTML = resourceListHtml(shown, items.length);
  }

  function resourceRow(r) {
    var url = safeUrl(r.url);
    return '<div class="hub-item"><div class="hub-item-main">' +
      '<div class="hub-item-title">' +
      (url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(r.title) + '</a>' : esc(r.title)) +
      '</div>' +
      '<div class="hub-item-sub">' + (r.category ? esc(r.category) + ' · ' : '') +
      esc(url || r.url || '') + (r.note ? '<br>' + esc(r.note) : '') + '</div>' +
      ((r.tags && r.tags.length) ? '<div class="hub-tags">' + r.tags.map(function (t) {
        return '<button class="hub-tag" data-hub-action="filter-tag" data-hub-value="' + esc(t) + '">' + esc(t) + '</button>';
      }).join('') + '</div>' : '') +
      '</div><div class="hub-item-actions">' +
      iconBtn('resource-agent', r.id, ICON.send, '交给 Agent') +
      iconBtn('edit-resource', r.id, ICON.edit, '编辑') +
      iconBtn('del-resource', r.id, ICON.trash, '删除', 'danger') +
      '</div></div>';
  }

  function resourceForm() {
    var it = findById(view.data.resources.items, view.form.id) || {};
    return '<form class="hub-card hub-section" data-hub-form="resource">' +
      '<div class="hub-form-grid">' +
      field('title', '标题', it.title, 'text', true) +
      field('url', '链接或位置', it.url, 'text') +
      field('category', '分类', it.category, 'text') +
      field('tags', '标签（逗号分隔）', (it.tags || []).join(', '), 'text') +
      '</div>' +
      '<div class="hub-field" style="margin-bottom:12px"><span class="hub-field-label">备注</span>' +
      '<textarea class="hub-textarea" name="note">' + esc(it.note || '') + '</textarea></div>' +
      formActions() + '</form>';
  }

  /* ── 收件箱 ───────────────────────────────────────────────────────── */

  function renderInbox() {
    var items = view.data.inbox.items.slice().sort(function (a, b) {
      if (!!a.done !== !!b.done) return a.done ? 1 : -1;
      return String(b.createdAt).localeCompare(String(a.createdAt));
    });
    return '<div class="hub-section-head"><span class="hub-section-title">收件箱</span></div>' +
      '<form class="hub-capture hub-section" data-hub-form="capture">' +
      '<input class="hub-input" name="text" placeholder="记一条…" autocomplete="off">' +
      '<button class="hub-btn primary" type="submit">' + svg(ICON.plus, 13) + '记下</button></form>' +
      (items.length
        ? '<div class="hub-list">' + items.map(function (i) {
          return '<div class="hub-item' + (i.done ? ' done' : '') + '"><div class="hub-item-main">' +
            '<div class="hub-item-title">' + esc(i.text) + '</div>' +
            '<div class="hub-item-sub">' + esc(fmtDate(i.createdAt)) + '</div>' +
            '</div><div class="hub-item-actions">' +
            iconBtn('inbox-to-design', i.id, ICON.design, '转为设计条目') +
            iconBtn('inbox-to-resource', i.id, ICON.resources, '转为资源') +
            iconBtn('inbox-agent', i.id, ICON.send, '交给 Agent') +
            iconBtn('inbox-toggle', i.id, i.done ? ICON.undo : ICON.check, i.done ? '标记未完成' : '标记完成') +
            iconBtn('del-inbox', i.id, ICON.trash, '删除', 'danger') +
            '</div></div>';
        }).join('') + '</div>'
        : '<div class="hub-empty">收件箱是空的。想法先丢进来，不用当场决定它属于哪儿。</div>');
  }

  /* ── 表单构件 ─────────────────────────────────────────────────────── */

  function field(name, label, value, type, required) {
    return '<label class="hub-field"><span class="hub-field-label">' + esc(label) + '</span>' +
      '<input class="hub-input" name="' + esc(name) + '" type="' + (type || 'text') + '" ' +
      (required ? 'required ' : '') + 'value="' + esc(value || '') + '"></label>';
  }

  function selectField(name, label, list, selected) {
    return '<label class="hub-field"><span class="hub-field-label">' + esc(label) + '</span>' +
      '<select class="hub-select" name="' + esc(name) + '">' + optionsHtml(list, selected) + '</select></label>';
  }

  function textAreaField(name, label, value) {
    return '<label class="hub-field"><span class="hub-field-label">' + esc(label) + '</span>' +
      '<textarea class="hub-textarea" name="' + esc(name) + '">' + esc(value || '') + '</textarea></label>';
  }

  function formActions() {
    return '<div class="hub-form-actions">' +
      '<button class="hub-btn" type="button" data-hub-action="cancel-form">取消</button>' +
      '<button class="hub-btn primary" type="submit">保存</button></div>';
  }

  function findById(list, id) {
    if (!id) return null;
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }

  /* ── 持久化 ───────────────────────────────────────────────────────── */

  function save(key, afterMsg) {
    return HubStore.write(key, view.data[key]).then(function () {
      if (afterMsg) toast(afterMsg, 'success');
      render();
      renderSidebar();
    }, function (err) {
      toast('保存失败：' + ((err && err.message) || '未知错误'), 'error');
    });
  }

  function saveMeetingsCandidate(candidate, afterMsg, closeFormOnSuccess) {
    return HubStore.write('meetings', candidate).then(function () {
      return HubStore.read('meetings');
    }).then(function (persisted) {
      view.data.meetings = persisted;
      if (closeFormOnSuccess) {
        view.form = null;
        view.meetingDraft = null;
        view.meetingActionDrafts = null;
      }
      if (afterMsg) toast(afterMsg, 'success');
      render();
      renderSidebar();
    }, function (err) {
      HubStore.invalidate();
      return HubStore.read('meetings').then(function (remote) {
        view.data.meetings = remote;
        toast('保存失败：' + ((err && err.message) || '未知错误') + '；已重新读取磁盘数据', 'error');
        render();
        renderSidebar();
      }, function () {
        toast('保存失败：' + ((err && err.message) || '未知错误') + '；重新读取磁盘数据也失败', 'error');
      });
    });
  }

  /* ── 与 Agent 联动 ────────────────────────────────────────────────── */

  /* Hub 的价值一半在这里：把一条记录直接变成给 agent 的指令，不用手打上下文。 */
  function askAgent(text) {
    var box = $id('msg');
    if (!box) { toast('找不到输入框', 'error'); return; }
    box.value = text;
    box.dispatchEvent(new Event('input', { bubbles: true }));
    window.switchPanel('chat', { fromRailClick: false });
    setTimeout(function () { box.focus(); }, 60);
  }

  function designPrompt(i) {
    return '这是我产品设计工作台里的一条记录，帮我推进它：\n\n' +
      '标题：' + i.title + '\n' +
      '阶段：' + labelOf(STAGES, i.stage) + '\n' +
      '优先级：' + labelOf(PRIORITIES, i.priority, '中') +
      (i.link ? '\n相关链接：' + i.link : '') +
      ((i.tags && i.tags.length) ? '\n标签：' + i.tags.join(', ') : '') +
      (i.notes ? '\n\n备注：\n' + i.notes : '');
  }

  function servicePrompt(s) {
    return '帮我检查这个个人中枢服务的状态并给出处理建议：\n\n' +
      '名称：' + s.name +
      (s.machineId ? '\nmachineId：' + s.machineId : '') +
      (s.status ? '\n当前状态：' + statusLabel(serviceStatus(s, view.data.ops)) : '') +
      (s.updatedAt ? '\n最近采集：' + s.updatedAt : '') +
      (s.detail ? '\n详情：' + s.detail : '') +
      (s.url ? '\n地址：' + s.url : '') +
      (s.notes ? '\n备注：' + s.notes : '');
  }

  /* ── 事件 ─────────────────────────────────────────────────────────── */

  function focusServiceDrawerClose() {
    setTimeout(function () {
      var btn = document.querySelector('.hub-service-drawer [data-hub-action="close-service-drawer"]');
      if (btn && typeof btn.focus === 'function') btn.focus();
    }, 0);
  }

  function openServiceDrawer(id) {
    var active = document.activeElement;
    serviceDrawerRestoreFocus = {
      node: active,
      serviceId: id || '',
      action: active && active.getAttribute ? active.getAttribute('data-hub-action') : ''
    };
    view.opsSelectedService = id || '';
    render();
    focusServiceDrawerClose();
  }

  function restoreServiceFocus(restore) {
    if (restore && restore.node && typeof restore.node.focus === 'function' && document.contains(restore.node)) {
      restore.node.focus();
      return;
    }
    var selector = restore && restore.serviceId;
    var candidates = selector ? document.querySelectorAll('[data-hub-id]') : [];
    for (var i = 0; i < candidates.length; i += 1) {
      if (candidates[i].getAttribute('data-hub-id') === selector &&
          (!restore.action || candidates[i].getAttribute('data-hub-action') === restore.action)) {
        candidates[i].focus();
        return;
      }
    }
    var rows = selector ? document.querySelectorAll('[data-hub-service-row]') : [];
    for (var j = 0; j < rows.length; j += 1) {
      if (rows[j].getAttribute('data-hub-service-row') === selector && typeof rows[j].focus === 'function') {
        rows[j].focus();
        return;
      }
    }
  }

  function closeServiceDrawer() {
    var restore = serviceDrawerRestoreFocus;
    serviceDrawerRestoreFocus = null;
    view.opsSelectedService = '';
    render();
    setTimeout(function () { restoreServiceFocus(restore); }, 0);
  }

  function onKeydown(e) {
    if (e.key === 'Escape' && view.opsSelectedService) {
      e.preventDefault();
      closeServiceDrawer();
      return;
    }
    if ((e.key === 'Enter' || e.key === ' ') && e.target && e.target.closest) {
      var row = e.target.closest('[data-hub-service-row]');
      if (row && !e.target.closest('a, button, input, select, textarea')) {
        e.preventDefault();
        openServiceDrawer(row.getAttribute('data-hub-service-row'));
      }
    }
  }

  function guardOpsForm() {
    if (!view.form) return false;
    toast('请先保存或取消当前编辑', 'error');
    return true;
  }

  function onInput(e) {
    if (e.target && e.target.id === 'hubSearch') {
      view.query = e.target.value;
      refreshResourceList();
    }
    if (e.target && e.target.matches('[data-hub-ops-query]')) {
      if (view.form) return;
      view.opsQuery = e.target.value;
      refreshOpsView();
    }
  }

  function onChange(e) {
    if (e.target && e.target.matches('[data-hub-kind-filter]')) {
      if (view.form) { toast('请先保存或取消当前编辑', 'error'); return; }
      view.opsKind = e.target.value || 'all';
      render();
    }
  }

  function onSubmit(e) {
    var form = e.target.closest('[data-hub-form]');
    if (!form) return;
    e.preventDefault();
    var kind = form.getAttribute('data-hub-form');
    var f = new FormData(form);
    var get = function (k) { return String(f.get(k) || '').trim(); };
    var d = view.data;
    var editing = view.form && view.form.id;

    if (kind === 'capture') {
      var text = get('text');
      if (!text) return;
      d.inbox.items.unshift({ id: HubStore.newId(), text: text, done: false, createdAt: nowIso() });
      form.reset();
      save('inbox', '已记入收件箱');
      return;
    }

    if (kind === 'design') {
      var item = editing ? findById(d.design.items, editing) : null;
      var payload = {
        title: get('title'), stage: get('stage') || 'idea', priority: get('priority') || 'normal',
        link: get('link'), tags: parseTags(get('tags')), notes: get('notes'), updatedAt: nowIso()
      };
      if (!payload.title) return;
      if (item) Object.assign(item, payload);
      else d.design.items.unshift(Object.assign({ id: HubStore.newId(), createdAt: nowIso() }, payload));
      view.form = null;
      save('design', item ? '已更新' : '已新建');
      return;
    }

    if (kind === 'meeting') {
      var currentMeeting = editing ? findById(d.meetings.items, editing) : null;
      var meetingStart = isoFromInput(get('startAt'));
      var meetingEnd = isoFromInput(get('endAt'));
      if (meetingStart && meetingEnd && new Date(meetingEnd).getTime() < new Date(meetingStart).getTime()) {
        toast('会议结束时间不能早于开始时间', 'error');
        return;
      }
      var actionIds = f.getAll('action_id');
      var actionTitles = f.getAll('action_title');
      var actionOwners = f.getAll('action_owner');
      var actionDues = f.getAll('action_due');
      var actionDeliverables = f.getAll('action_deliverable');
      var actionAcceptance = f.getAll('action_acceptance');
      var actionDependencies = f.getAll('action_dependencies');
      var actionStatuses = f.getAll('action_status');
      var actionItems = actionTitles.map(function (title, index) {
        return Object.assign({}, (view.meetingActionDrafts || [])[index] || {}, {
          id: String(actionIds[index] || HubStore.newId()),
          title: String(title || '').trim(),
          owner: String(actionOwners[index] || '').trim(),
          due: String(actionDues[index] || '').trim(),
          deliverable: String(actionDeliverables[index] || '').trim(),
          acceptance: String(actionAcceptance[index] || '').trim(),
          dependencies: String(actionDependencies[index] || '').trim(),
          status: String(actionStatuses[index] || 'open'),
          updatedAt: nowIso()
        });
      }).filter(function (action) {
        return action.title || action.owner || action.due || action.deliverable || action.acceptance || action.dependencies;
      });
      var meetingPayload = {
        title: get('title'), type: get('type') || 'sync', status: get('status') || 'planned',
        startAt: meetingStart, endAt: meetingEnd,
        participants: parseList(get('participants')), projectLinks: parseList(get('projectLinks')),
        summary: get('summary'), decisions: parseList(get('decisions')),
        actionItems: actionItems, risks: parseList(get('risks')),
        openQuestions: parseList(get('openQuestions')), transcriptFile: get('transcriptFile'),
        minutesFile: get('minutesFile'), nextReviewAt: isoFromInput(get('nextReviewAt')), updatedAt: nowIso()
      };
      if (!meetingPayload.title) return;
      var meetingCandidate = JSON.parse(JSON.stringify(d.meetings));
      var candidateCurrent = editing ? findById(meetingCandidate.items, editing) : null;
      if (candidateCurrent) Object.assign(candidateCurrent, meetingPayload);
      else meetingCandidate.items.unshift(Object.assign({ id: HubStore.newId(), createdAt: nowIso() }, meetingPayload));
      view.meetingDraft = Object.assign({}, currentMeeting || {}, meetingPayload);
      view.meetingActionDrafts = actionItems;
      saveMeetingsCandidate(meetingCandidate, currentMeeting ? '会议已更新' : '会议已新建', true);
      return;
    }

    if (kind === 'service' || kind === 'command') {
      var listKey = kind === 'service' ? 'services' : 'commands';
      var list = d.ops[listKey];
      var target = editing ? findById(list, editing) : null;
      if (kind === 'service' && isManagedService(target)) {
        target.notes = get('notes');
        view.form = null;
        save('ops', '已更新备注');
        return;
      }
      var body = kind === 'service'
        ? {
          name: get('name'), env: get('env') || 'prod', status: get('status') || 'ok',
          url: get('url'), owner: get('owner'), notes: get('notes'), updatedAt: nowIso()
        }
        : { label: get('label'), command: get('command'), notes: get('notes'), updatedAt: nowIso() };
      if (!(body.name || body.label) || (kind === 'command' && !body.command)) return;
      if (target) Object.assign(target, body);
      else list.unshift(Object.assign({ id: HubStore.newId(), createdAt: nowIso() }, body));
      view.form = null;
      save('ops', target ? '已更新' : '已新建');
      return;
    }

    if (kind === 'maintenance') {
      var start = get('start');
      var end = get('end');
      var startMs = parseIsoMs(start);
      var endMs = parseIsoMs(end);
      if (startMs && endMs && endMs < startMs) {
        toast('维护结束时间不能早于开始时间', 'error');
        return;
      }
      var maintenance = {
        id: HubStore.newId(),
        entityType: get('entityType') || 'service',
        entityId: get('entityId'),
        start: start,
        end: end,
        reason: get('reason'),
        createdAt: nowIso()
      };
      if (!maintenance.entityId || !maintenance.reason) return;
      d.ops.maintenance.unshift(maintenance);
      view.form = null;
      save('ops', '已记录维护窗口');
      return;
    }

    if (kind === 'resource') {
      var res = editing ? findById(d.resources.items, editing) : null;
      var rp = {
        title: get('title'), url: get('url'), category: get('category'),
        tags: parseTags(get('tags')), note: get('note'), updatedAt: nowIso()
      };
      if (!rp.title) return;
      if (res) Object.assign(res, rp);
      else d.resources.items.unshift(Object.assign({ id: HubStore.newId(), createdAt: nowIso() }, rp));
      view.form = null;
      save('resources', res ? '已更新' : '已收藏');
    }
  }

  function onClick(e) {
    var mod = e.target.closest('[data-hub-module]');
    if (mod) {
      view.module = mod.getAttribute('data-hub-module');
      view.form = null; view.query = ''; view.tag = ''; view.opsSelectedService = '';
      render(); renderSidebar();
      return;
    }

    var ownerFilter = e.target.closest('[data-hub-owner-filter]');
    if (ownerFilter) {
      if (guardOpsForm()) return;
      view.opsOwner = ownerFilter.getAttribute('data-hub-owner-filter') || 'personal';
      render(); renderSidebar();
      return;
    }
    var viewFilter = e.target.closest('[data-hub-ops-view]');
    if (viewFilter) {
      if (guardOpsForm()) return;
      view.opsView = viewFilter.getAttribute('data-hub-ops-view') || 'servers';
      view.opsSelectedService = '';
      render();
      return;
    }
    var statusFilter = e.target.closest('[data-hub-status-filter]');
    if (statusFilter) {
      if (guardOpsForm()) return;
      view.opsStatus = statusFilter.getAttribute('data-hub-status-filter') || 'all';
      render();
      return;
    }
    var btn = e.target.closest('[data-hub-action]');
    if (!btn) {
      if (e.target.closest('a')) return;
      var row = e.target.closest('[data-hub-service-row]');
      if (row) openServiceDrawer(row.getAttribute('data-hub-service-row'));
      return;
    }
    var action = btn.getAttribute('data-hub-action');
    var id = btn.getAttribute('data-hub-id');
    var value = btn.getAttribute('data-hub-value');
    var d = view.data;

    switch (action) {
      case 'setup': doSetup(); return;
      case 'pick-ws': { var inp = $id('hubSetupPath'); if (inp) inp.value = value || ''; return; }
      case 'reload': reload(); return;
      case 'cancel-form': view.form = null; view.meetingDraft = null; view.meetingActionDrafts = null; render(); return;
      case 'clear-machine-filter': if (guardOpsForm()) return; view.opsMachine = ''; render(); return;
      case 'machine-services': if (guardOpsForm()) return; view.opsMachine = id || ''; view.opsView = 'services'; view.opsSelectedService = ''; render(); return;
      case 'open-service': openServiceDrawer(id); return;
      case 'close-service-drawer': closeServiceDrawer(); return;
      case 'copy-service-command': copyText(btn.getAttribute('data-hub-copy') || ''); return;
      case 'new-maintenance': if (guardOpsForm()) return; view.form = { kind: 'maintenance', id: '' }; render(); return;
      case 'ack-event': {
        if (guardOpsForm()) return;
        if (acknowledgementFor(id, d.ops)) return;
        var note = window.prompt('确认这条事件，可选短备注（最多 80 字）：', '');
        if (note === null) return;
        d.ops.acknowledgements.unshift({
          id: HubStore.newId(),
          eventId: id,
          createdAt: nowIso(),
          note: String(note || '').trim().slice(0, 80)
        });
        save('ops', '已确认事件');
        return;
      }

      case 'new-design': view.form = { kind: 'design', id: '' }; render(); return;
      case 'edit-design': view.form = { kind: 'design', id: id }; render(); return;
      case 'new-meeting': openMeetingForm(''); return;
      case 'edit-meeting': openMeetingForm(id); return;
      case 'add-meeting-action':
        syncMeetingFormDrafts();
        view.meetingActionDrafts.push(blankMeetingAction());
        render();
        return;
      case 'remove-meeting-action':
        syncMeetingFormDrafts();
        view.meetingActionDrafts.splice(Number(btn.getAttribute('data-hub-index')), 1);
        render();
        return;
      case 'new-service': view.opsSelectedService = ''; view.form = { kind: 'service', id: '' }; render(); return;
      case 'edit-service': view.opsSelectedService = ''; view.opsView = 'services'; view.form = { kind: 'service', id: id }; render(); return;
      case 'new-command': view.form = { kind: 'command', id: '' }; render(); return;
      case 'edit-command': view.form = { kind: 'command', id: id }; render(); return;
      case 'new-resource': view.form = { kind: 'resource', id: '' }; render(); return;
      case 'edit-resource': view.form = { kind: 'resource', id: id }; render(); return;

      case 'edit-focus': {
        var cur = d.profile.focus || '';
        var next = window.prompt('今天真正要推进的一件事：', cur);
        if (next === null) return;
        d.profile.focus = next.trim();
        d.profile.focusDate = nowIso();
        d.profile.updatedAt = nowIso();
        save('profile', '已更新今日聚焦');
        return;
      }

      case 'design-fwd':
      case 'design-back': {
        var it = findById(d.design.items, id);
        if (!it) return;
        var ids = STAGES.map(function (s) { return s.id; });
        var at = ids.indexOf(it.stage || 'idea');
        var to = action === 'design-fwd' ? at + 1 : at - 1;
        if (to < 0 || to >= ids.length) return;
        it.stage = ids[to];
        it.updatedAt = nowIso();
        save('design');
        return;
      }

      case 'del-design':
        if (!confirm('删除这条设计条目？')) return;
        d.design.items = d.design.items.filter(function (x) { return x.id !== id; });
        save('design', '已删除');
        return;

      case 'del-meeting':
        if (!confirm('删除这条会议记录？')) return;
        var deleteCandidate = JSON.parse(JSON.stringify(d.meetings));
        deleteCandidate.items = deleteCandidate.items.filter(function (meeting) { return meeting.id !== id; });
        saveMeetingsCandidate(deleteCandidate, '会议已删除', false);
        return;

      case 'del-service':
        if (isManagedService(findById(d.ops.services, id))) { toast('自动登记服务不能在界面删除', 'error'); return; }
        if (!confirm('删除这个服务？')) return;
        d.ops.services = d.ops.services.filter(function (x) { return x.id !== id; });
        save('ops', '已删除');
        return;

      case 'del-command':
        if (!confirm('删除这条命令？')) return;
        d.ops.commands = d.ops.commands.filter(function (x) { return x.id !== id; });
        save('ops', '已删除');
        return;

      case 'del-resource':
        if (!confirm('删除这条资源？')) return;
        d.resources.items = d.resources.items.filter(function (x) { return x.id !== id; });
        save('resources', '已删除');
        return;

      case 'del-inbox':
        d.inbox.items = d.inbox.items.filter(function (x) { return x.id !== id; });
        save('inbox', '已删除');
        return;

      case 'inbox-toggle': {
        var box = findById(d.inbox.items, id);
        if (!box) return;
        box.done = !box.done;
        save('inbox');
        return;
      }

      case 'inbox-to-design': {
        var src = findById(d.inbox.items, id);
        if (!src) return;
        d.design.items.unshift({
          id: HubStore.newId(), title: src.text, stage: 'idea', priority: 'normal',
          link: '', tags: [], notes: '', createdAt: nowIso(), updatedAt: nowIso()
        });
        d.inbox.items = d.inbox.items.filter(function (x) { return x.id !== id; });
        // 两个文件都要落盘；先写目标再写来源，中途失败时条目不会凭空消失。
        HubStore.write('design', d.design)
          .then(function () { return save('inbox', '已转入产品设计'); })
          .catch(function (err) { toast('转换失败：' + ((err && err.message) || ''), 'error'); });
        return;
      }

      case 'inbox-to-resource': {
        var src2 = findById(d.inbox.items, id);
        if (!src2) return;
        d.resources.items.unshift({
          id: HubStore.newId(), title: src2.text, url: '', category: '', tags: [],
          note: '', createdAt: nowIso(), updatedAt: nowIso()
        });
        d.inbox.items = d.inbox.items.filter(function (x) { return x.id !== id; });
        HubStore.write('resources', d.resources)
          .then(function () { return save('inbox', '已转入资源库'); })
          .catch(function (err) { toast('转换失败：' + ((err && err.message) || ''), 'error'); });
        return;
      }

      case 'filter-tag':
        view.tag = (view.tag === value) ? '' : value;
        view.module = 'resources';
        render(); renderSidebar();
        return;

      case 'copy-command': {
        var cmd = findById(d.ops.commands, id);
        if (cmd) copyText(cmd.command);
        return;
      }

      case 'design-agent': {
        var da = findById(d.design.items, id);
        if (da) askAgent(designPrompt(da));
        return;
      }

      case 'service-agent': {
        var sv = findById(d.ops.services, id);
        if (!sv) return;
        askAgent(servicePrompt(sv));
        return;
      }

      case 'command-agent': {
        var cm = findById(d.ops.commands, id);
        if (cm) askAgent('帮我执行这条命令，并解释输出：\n\n```\n' + cm.command + '\n```' +
          (cm.notes ? '\n\n背景：' + cm.notes : ''));
        return;
      }

      case 'resource-agent': {
        var rs = findById(d.resources.items, id);
        if (rs) askAgent('这是我资源库里的一条记录，帮我看看：\n\n' +
          '标题：' + rs.title + (rs.url ? '\n位置：' + rs.url : '') +
          (rs.category ? '\n分类：' + rs.category : '') + (rs.note ? '\n备注：' + rs.note : ''));
        return;
      }

      case 'inbox-agent': {
        var ib = findById(d.inbox.items, id);
        if (ib) askAgent(ib.text);
        return;
      }
    }
  }

  /* ── 启动 ─────────────────────────────────────────────────────────── */

  /* 扩展脚本是 defer，核心脚本已执行完，但要等 DOM 与全局函数都就绪。 */
  function boot(attempt) {
    if (typeof window.switchPanel === 'function' && typeof window.api === 'function'
      && document.querySelector('.rail') && document.querySelector('main.main')) {
      try { mount(); } catch (err) { console.error('[hermes-hub] 挂载失败', err); }
      return;
    }
    if ((attempt || 0) > 60) { console.warn('[hermes-hub] 核心 UI 未就绪，已放弃挂载'); return; }
    setTimeout(function () { boot((attempt || 0) + 1); }, 100);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { boot(0); });
  } else {
    boot(0);
  }
})();
