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
    left: '<polyline points="15 18 9 12 15 6"/>',
    right: '<polyline points="9 18 15 12 9 6"/>'
  };

  /* ── 模块定义 ─────────────────────────────────────────────────────── */

  var MODULES = [
    { id: 'home', label: '主页', icon: ICON.home, sub: '今日聚焦、快速捕获与全局概览' },
    { id: 'design', label: '产品设计', icon: ICON.design, sub: '需求与设计稿从想法走到交付' },
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

  /* ── 视图状态 ─────────────────────────────────────────────────────── */

  var ready = null;   // HubStore.init() 的 promise，见 mount()

  var view = {
    module: 'home',
    data: null,
    form: null,        // { kind, id } — kind 为 design/service/command/resource
    query: '',
    tag: '',
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

  function reload() {
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
    var name = p.name ? '，' + esc(p.name) : '';

    var stats = [
      { v: activeDesign.length, l: '进行中的设计条目' },
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
      '<div class="hub-section-actions"><button class="hub-btn" data-hub-action="reload">刷新</button></div></div>' +
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

  /* ── 项目运维 ─────────────────────────────────────────────────────── */

  function renderOps() {
    var ops = view.data.ops;
    var html = '';

    html += '<div class="hub-section"><div class="hub-section-head">' +
      '<span class="hub-section-title">服务清单</span>' +
      '<div class="hub-section-actions">' +
      '<button class="hub-btn primary" data-hub-action="new-service">' + svg(ICON.plus, 13) + '新增服务</button>' +
      '</div></div>';
    if (view.form && view.form.kind === 'service') html += serviceForm();
    html += (ops.services.length
      ? '<div class="hub-services">' + ops.services.map(serviceCard).join('') + '</div>'
      : '<div class="hub-empty">还没登记服务。把你日常要盯的机器、站点、定时任务放进来。</div>') +
      '</div>';

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

    return html;
  }

  function serviceCard(s) {
    var url = safeUrl(s.url);
    return '<div class="hub-item"><div class="hub-item-main">' +
      '<div class="hub-item-title"><span class="hub-dot ' + esc(s.status || 'ok') + '"></span>' +
      (url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(s.name) + '</a>' : esc(s.name)) +
      '</div>' +
      '<div class="hub-item-sub">' + esc(labelOf(ENVS, s.env, '')) +
      ' · ' + esc(labelOf(STATUSES, s.status, '正常')) +
      (s.owner ? ' · 负责人 ' + esc(s.owner) : '') +
      (s.notes ? '<br>' + esc(s.notes) : '') + '</div></div>' +
      '<div class="hub-item-actions">' +
      iconBtn('service-agent', s.id, ICON.send, '让 Agent 检查这个服务') +
      iconBtn('edit-service', s.id, ICON.edit, '编辑') +
      iconBtn('del-service', s.id, ICON.trash, '删除', 'danger') +
      '</div></div>';
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

  /* ── 事件 ─────────────────────────────────────────────────────────── */

  function onInput(e) {
    if (e.target && e.target.id === 'hubSearch') {
      view.query = e.target.value;
      refreshResourceList();
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

    if (kind === 'service' || kind === 'command') {
      var listKey = kind === 'service' ? 'services' : 'commands';
      var list = d.ops[listKey];
      var target = editing ? findById(list, editing) : null;
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
      view.form = null; view.query = ''; view.tag = '';
      render(); renderSidebar();
      return;
    }

    var btn = e.target.closest('[data-hub-action]');
    if (!btn) return;
    var action = btn.getAttribute('data-hub-action');
    var id = btn.getAttribute('data-hub-id');
    var value = btn.getAttribute('data-hub-value');
    var d = view.data;

    switch (action) {
      case 'setup': doSetup(); return;
      case 'pick-ws': { var inp = $id('hubSetupPath'); if (inp) inp.value = value || ''; return; }
      case 'reload': reload(); return;
      case 'cancel-form': view.form = null; render(); return;

      case 'new-design': view.form = { kind: 'design', id: '' }; render(); return;
      case 'edit-design': view.form = { kind: 'design', id: id }; render(); return;
      case 'new-service': view.form = { kind: 'service', id: '' }; render(); return;
      case 'edit-service': view.form = { kind: 'service', id: id }; render(); return;
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

      case 'del-service':
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
        askAgent('帮我检查这个服务的状态并给出处理建议：\n\n' +
          '名称：' + sv.name + '\n环境：' + labelOf(ENVS, sv.env) +
          '\n当前状态：' + labelOf(STATUSES, sv.status) +
          (sv.url ? '\n地址：' + sv.url : '') +
          (sv.notes ? '\n备注：' + sv.notes : ''));
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
