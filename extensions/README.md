# Hermes Hub — 个人中枢扩展

把 Hermes WebUI 变成个人工作中枢：需求、服务、资源、灵感都在这里管，
并且 **Hermes agent 能直接读写同一批数据**。

这是一个 WebUI 扩展包（见 `docs/EXTENSIONS.md`），**不改动项目任何核心文件**。
上游更新后直接 `git pull` 不会冲突；不想要了，把 `.env` 里那两行注释掉即可。

## 启用

仓库根目录的 `.env` 已经配好：

```bash
HERMES_WEBUI_EXTENSION_DIR=extensions
HERMES_WEBUI_EXTENSION_MANIFEST=extensions.json
```

路径是相对仓库根解析的（`start.ps1` / `start.sh` 启动前都会切到仓库根），
换机器克隆后无需改动。

> **注意**：`.env` 被 `.gitignore` 排除，不会随仓库推送。
> 在新机器上克隆后需要手工建这个文件，把上面两行填进去，Hub 才会出现。

正常启动 WebUI 后，左侧导航栏 Chat 下方会多出一个 **个人中枢** 图标。

首次进入会让你指定一个 **Hub 数据目录**（例如 `D:/hermes-hub`）。目录不存在会自动创建。
确认后 Hub 会：

1. 把该目录注册成名为 `Hermes Hub` 的工作区
2. 建一条绑定该目录的会话，专门用于读写数据
3. 在目录里铺好初始文件

## 模块

| 模块 | 用途 |
| --- | --- |
| **主页** | 问候与今日聚焦、四项统计、快速捕获、跨模块最近动态 |
| **产品设计** | 五阶段看板：想法 → 需求 → 设计中 → 评审 → 已交付 |
| **项目运维** | 服务清单（环境/状态/负责人）+ 常用命令速查 |
| **资源库** | 链接、文档、提示词的统一收藏，带标签筛选与全文搜索 |
| **收件箱** | 先记下来，之后一键转成设计条目或资源 |

每类条目都有一个 **交给 Agent** 按钮：把该条记录连同上下文填进聊天输入框并切到
Chat —— 不用再手工复述背景。

## 数据

全部平铺在 Hub 目录下，纯 JSON，可以直接编辑、可以 git 版本管理、可以备份：

```
hub-profile.json     个人档案与今日聚焦
hub-design.json      产品设计条目
hub-ops.json         服务与命令
hub-resources.json   资源收藏
hub-inbox.json       收件箱
HUB.md               给 agent 看的结构说明
```

`HUB.md` 描述了每个文件的字段与取值约定，agent 读到它就知道怎么改这些数据。
界面对文件被外部改动是容错的：缺字段会自动补齐，整个文件解析失败会回落成空视图
而不是白屏。

## 结构

```
extensions/
  extensions.json      清单（被后端加载器读取）
  hub/
    hub.css            样式，全部走核心设计令牌，跟随主题/皮肤
    hub-store.js       数据层：走 /api/file/* 读写工作区文件
    hub.js             面板注册与四个工作台视图
```

集成方式（三个挂载点，都是加法，不替换核心行为）：

- 往 `MAIN_VIEW_PANELS` 注册 `'hub'`，核心据此切 `showing-hub` 类
- 在 `.rail` 与 `.sidebar-nav` 插入导航按钮
- 包一层 `switchPanel`，进入 hub 时触发渲染
  （核心自身对 `switchPanel` / `switchSettingsSection` 也用同样的覆盖模式）

`hub.css` 里有一条必需的规则：`main.main.showing-hub > #mainChat{display:none!important}`。
核心判断"显示聊天"用的是一长串 `:not(.showing-*)`，那串里不会有 `showing-hub`，
不显式压掉的话进 Hub 时聊天会叠着一起渲染。

## 加新模块

1. 在 `hub.js` 的 `MODULES` 数组里加一项（id、名称、图标、副标题）
2. 写一个 `renderXxx()` 返回 HTML 字符串，在 `render()` 的 `switch` 里挂上
3. 需要新数据文件就在 `hub-store.js` 的 `FILES` 和 `DEFAULTS` 里各加一条，
   顺便更新 `HUB_README` 让 agent 知道新结构

所有交互走事件委托：给元素加 `data-hub-action="xxx"`，在 `onClick` 的 `switch` 里处理；
表单加 `data-hub-form="xxx"`，在 `onSubmit` 里处理。渲染是整块重画，不用手动同步 DOM。

写入用户内容一律经 `esc()`，链接一律经 `safeUrl()`（只放行 http/https）。
