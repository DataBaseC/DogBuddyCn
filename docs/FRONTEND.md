# DogBuddyCn 前端约定（FRONTEND.md）

> 控制台前端 = 原生 JS + 深色终端风 CSS，**零构建、零 npm 运行时依赖**。
> 本文档沉淀前端工程约定：模块写法、bundle 拼接、缓存指纹、以及本地怎么跑检查。
> 对应 ROADMAP：Batch 1 · T1.1（工位参数）/ T1.2（能力清单）/ T1.3（缓存）/ T1.4（工程化补强）。

## 1. 目录布局

| 位置 | 内容 |
| --- | --- |
| `dogbuddy/web/static/index.html` | 唯一页面（SPA），只加载 `js/index.bundle.js` |
| `dogbuddy/web/static/js/*.js` | 前端源码（浏览器 IIFE 模块） |
| `dogbuddy/web/static/js/*.bundle.js` | **自动生成**的拼接产物，勿手改 |
| `dogbuddy/web/static/css/*.css` | 样式（`base.css` 设计令牌/通用件，`views.css` 视图样式） |
| `tools/bundle-order.json` | bundle 拼接登记表（源文件顺序） |
| `tools/rebuild-bundles.py` | bundle 拼接脚本（构建入口） |
| `tools/fingerprint.py` | 资源缓存指纹 `?v=<hash>` |

注意：任务文档里的「public/」即本仓库的 `dogbuddy/web/static/`（FastAPI 把它挂载到
`/assets`，所以 HTML 里引用写 `/assets/js/xxx.js`）。构建脚本按
`public/` → `dogbuddy/web/static/` 的顺序自动探测静态根目录，两套布局都能跑。

## 2. 模块写法：IIFE + 全局命名空间

- 每个源文件是一个浏览器 IIFE，**非 ESM**，模块内 `'use strict'`：

  ```js
  /* global UI, API, DogBuddyViews */
  (function (global) {
    'use strict';
    var U = global.UI, A = global.API;
    global.DogBuddyViews = global.DogBuddyViews || {};
    // ...
    global.DogBuddyViews.Xxx = Xxx;
  })(window);
  ```

- 公共服务：`window.UI`（DOM/弹窗/Toast/格式化，见 `ui.js`）、`window.API`
  （fetch 封装，自动带 `X-Auth-Token`，401 回登录层，见 `api.js`）。
- 视图模块统一挂 `window.DogBuddyViews.<Name>`，由 `app.js` 的 `renderView` 分发；
  新增视图需要同步改三处：`index.html`（nav 按钮 + `<section class="view" id="view-xxx">`）、
  `app.js`（`TITLES` + `renderView` case）、新视图文件本身。
- 可变共享状态用 `/* global X */` 注释声明，避免 `no-undef` 误报；能走
  `global.X` 的尽量走 IIFE 参数取，别直接裸写全局名。
- 加载顺序敏感：`ui.js` → `api.js` → 视图文件 → `filemanager.js` → `app.js`
  （app.js 在最后负责启动），登记 `bundle-order.json` 时保持这个相对顺序。

## 3. bundle 拼接（tools/rebuild-bundles.py）

- 页面**只加载** `js/<page>.bundle.js`；bundle 由源文件按登记顺序拼接，
  每段前置横幅 `/* ==== js/<name>.js ==== */` 作为分界。
- **新增源文件必须登记到 `tools/bundle-order.json`**，格式：

  ```json
  { "js/index.bundle.js": ["js/ui.js", "js/api.js", "…", "js/app.js"] }
  ```

  没登记的文件不会进 bundle，页面加载不到（脚本会在 stderr 打警告提醒）。
- 用法：

  ```bash
  python3 tools/rebuild-bundles.py           # 生成 bundle + 刷新 ?v= 指纹（幂等可重跑）
  python3 tools/rebuild-bundles.py --check   # CI 校验：bundle/指纹是否最新，不写文件
  ```

## 4. 缓存指纹（tools/fingerprint.py，T1.3/T1.4）

- 背景：用户反馈「改了按钮不显示/老缓存」。浏览器缓存 js/css，URL 不变就用旧文件。
- 做法：对 `js/*.js`、`css/*.css`、`assets/**`（若有）算 sha256 前 10 位，
  把 HTML 里的 `src`/`href` 引用改写为 `?v=<hash>`（替换已有 `?v=` 或追加）。
  内容一变指纹一变，浏览器只能重新拉取。
- `rebuild-bundles.py` 跑完会自动调用它；也可单独跑
  `python3 tools/fingerprint.py [--check]`。幂等，反复跑无副作用；
  `--check` 只校验不写文件，过期退出码 1。

## 5. 类型检查与 Lint（T1.4）

```bash
# JS 语法校验（沙箱/CI 最低要求，逐文件跑）
node --check dogbuddy/web/static/js/views-params.js

# 类型检查（checkJs + JSDoc，宽松模式；不产出文件）
npx tsc -p tsconfig.json
# 沙箱无网时装不了 typescript：配置已备好，联网后 npx 一条命令即可跑。
# 首次跑会在老文件上报一些基线错误，按文件逐步补 JSDoc 消化，别为过关乱改逻辑。

# Lint（no-undef / no-unused-vars / eqeqeq）
npx eslint dogbuddy/web/static/js
```

- 新写的 JS 文件**必须**带 JSDoc（`@param` / `@returns` / `@typedef`）；
  老文件不强制回补。
- `tsconfig.json`：`checkJs: true`、`noEmit: true`、`strict: false`（先宽松起步）；
  生成物 `*.bundle.js` 被 exclude，只查源文件。
- `dogbuddy/web/static/js/globals.d.ts`：window 全局命名空间（UI/API/App/
  DogBuddyViews/FileMgr）的类型声明，纯检查用、不参与运行；老文件的
  `.value`/`.checked` 之类 HTMLElement 取属性的基线报错按文件逐步补
  JSDoc cast（`/** @type {HTMLInputElement} */ (el)`）消化。
- `.eslintrc.json`：`browser + es2022` 环境，`sourceType: script`（IIFE 非 ESM）。

## 6. 新增页面/模块 checklist

1. 新源文件写成 IIFE，挂到对应命名空间；
2. **登记 `tools/bundle-order.json`**（注意加载顺序）；
3. 新视图同步改 `index.html`（nav + section）与 `app.js`（TITLES + renderView）；
4. 样式追加到 `css/views.css`（沿用 `base.css` 的设计令牌 `--bg-*`/`--tx-*`/`--acc-*` 等）；
5. 跑 `python3 tools/rebuild-bundles.py` → `python3 tools/rebuild-bundles.py --check`
   → `node --check <改动的 js>`；
6. 确认 `index.html` 里 js/css 引用都带 `?v=` 指纹（构建脚本自动做，grep 复核即可）。

## 7. 中文界面文案与视觉

- 界面文案全中文，术语统一：工位（window）/ 任务（job）/ 上下文（context）/
  覆盖（override）/ 继承（inherited）。
- 深色终端风照旧：卡片 `.card`、徽标 `.badge-*`、chips `.chips/.chip`、
  表格 `.table`、按钮 `.btn-*`、空状态 `U.empty()`、骨架屏 `U.skeleton()`，
  新 UI 尽量复用这些现成件，别另起一套。
