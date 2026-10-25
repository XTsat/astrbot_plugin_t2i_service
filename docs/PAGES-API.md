# AstrBot 插件 Pages（WebUI 页面）机制 + 插件 Web API 参考

基准源码：`/vol1/@appdata/deepseek.harness/home/AstrBot`，版本 **v4.29.1**（`pyproject.toml:3`）。
容器实测版本：`astrbot` 容器 **v4.29.3**（net=host，Dashboard `:6185`）、`t2i-envlab` 沙箱 **v4.29.2**；两份源码里本机制**逐行一致**（同样有 `PLUGIN_PAGE_ROOT_DIR_NAMES = ("views", "pages")`、`/api/plugin/page/entry`、`bridge-sdk.js`，同样**没有** `data/plugin_pages` 之类的路径）。
Plugin Pages 机制自 **v4.24.2** 引入（`changelogs/v4.24.2.md:10`）；本仓库声明 `astrbot_version: ">=4.16,<5"`，若要依赖 Pages 请把下界提到 `>=4.24.2`。
官方文档（与源码一致，可作交叉验证）：[`docs/zh/dev/star/guides/plugin-pages.md`](../AstrBot/docs/zh/dev/star/guides/plugin-pages.md)（英文版 `docs/en/dev/star/guides/plugin-pages.md`）。

> 说明：所有 `文件:行号` 均指 AstrBot 源码（不是本插件仓库）。带 ✅ 的结论在 §9 有隔离实例的实测记录。

---

## 0. 结论速查

| 问题 | 结论 |
| --- | --- |
| 页面目录 | `views/<name>/index.html`（**首选**）或 `pages/<name>/index.html`（别名，v4.24.2 起沿用）；两者都存在时 **`views/` 优先**（`astrbot/dashboard/services/plugin_page_service.py:29`） |
| index.html | **必须存在**，否则该子目录不被识别为页面（`plugin_page_service.py:496-497`）。页面名 = 目录名（页面名不能含 `/`、`\`、不能以 `.` 开头） |
| 前端 URL | `/#/plugin-view/<plugin_name>/<page_name>`（Dashboard 用 hash 路由，`dashboard/src/router/index.ts:9`；路由定义 `dashboard/src/router/MainRoutes.ts:100-102`）；旧路径 `/#/plugin-page/<plugin_name>/<page_name>` 会重定向（`MainRoutes.ts:106-108`） |
| 页面运行方式 | Dashboard 用 `<iframe sandbox="allow-scripts allow-forms allow-downloads">` 内嵌（`dashboard/src/views/PluginViewPage.vue:651`）—— **没有 `allow-same-origin`** |
| 因此页面怎么调后端 | 必须用注入的桥 `window.AstrBotPluginView`（兼容别名 `window.AstrBotPluginPage`，`astrbot/dashboard/plugin_page_bridge.js:207,288`）：`apiGet/apiPost/upload/download/subscribeSSE`。原生 `fetch` 到 Dashboard API 不带登录凭证，会 401/403 |
| 插件注册路由 | `context.register_web_api("/<plugin_name>/<endpoint>", handler, ["GET"], "desc")`，route **必须带插件名前缀**，匹配用的路径就是 `/<plugin_name>/<endpoint>`（`astrbot/dashboard/api/plugins.py:209-221,233-252`） |
| 浏览器实际 URL | `GET/POST /api/v1/plugins/extensions/<plugin_name>/<endpoint>`（`PluginViewPage.vue:122-124`），由桥在父窗口用带登录态的 axios 发出 |
| 鉴权 | Dashboard 登录态 + `require_plugin_scope`（`plugins.py:398-441`）；页面静态资源走一次性/长期 `asset_token`（JWT，路径内携带） |
| i18n | `.astrbot-plugin/i18n/<lang>.json`；AstrBot Dashboard 支持的语言码：`zh-CN`、`en-US`、`ja-JP`、`ru-RU`（`dashboard/src/i18n/locales/`），默认回退 `zh-CN`（`dashboard/src/i18n/composables.ts:27-33`）。键：`pages.<name>.title` / `pages.<name>.description`（列表用）、`views.<name>.title`（页面上下文优先），桥内部 `t()` 的回退顺序是 `当前语言 → zh-CN → en-US → 你给的 fallback`（`plugin_page_bridge.js:98-113`）。⚠️ **本仓库 `.astrbot-plugin/i18n/` 目前是空目录**，实测入口返回 `title:"t2i"`、`i18n_key:"pages.t2i"`（即回退成页面目录名）。 |
| metadata.yaml | **不需要**额外字段；页面发现是目录扫描。可选的 `views:`/`pages:` 列表只被解析进 `StarMetadata.views`（`astrbot/core/star/star_manager.py:556-563`），不参与发现 |
| 与命令共存 | 无冲突；同一 route+methods 重复注册是**替换**，不报错（`astrbot/core/star/context.py:782-786`） |

---

## 1. pages 目录结构、发现与路由

### 1.1 目录约定

```text
astrbot_plugin_t2i_service/
├── main.py
├── metadata.yaml
├── .astrbot-plugin/
│   └── i18n/
│       ├── zh-CN.json
│       └── en-US.json
├── pages/              # 别名目录（本仓库使用）
│   └── t2i/            # 目录名 = 页面名 view_name
│       ├── index.html  # 必须存在，入口
│       └── app.js      # 可选，同目录其他静态资源可被相对路径引用
└── views/              # 首选目录；与 pages/ 同时存在时优先 views/
```

发现逻辑（`plugin_page_service.py`）：

- `PLUGIN_PAGE_ROOT_DIR_NAMES = ("views", "pages")`（`:29`）、`PLUGIN_PAGE_ENTRY_FILE_NAME = "index.html"`（`:31`）；`resolve_plugin_pages_root()` 按顺序取**第一个存在**的目录（`:463-472`）。
- `discover_plugin_pages()`（`:474-505`）遍历该目录下的**一级子目录**（按名字小写排序），对每个子目录做 `normalize_plugin_page_name()`（`:436-448`，拒绝空名、`.`/`..`、以 `.` 开头、含 `/` 或 `\`），然后要求 `index.html` 是文件（`:496-497`）；不满足就跳过。
- 插件根目录来自 `StarMetadata.root_dir_name` + 插件存储目录（`get_plugin_root_dir()`，`:450-461`）。所以本插件在服务器上是 `<AstrBot>/data/plugins/astrbot_plugin_t2i_service/pages/t2i/index.html`。
- 后端列表接口：`GET /api/v1/plugins/<plugin_id>`（`plugins.py:918`）与 `GET /api/v1/plugins/views?plugin_id=...`（`:885`，旧别名 `/plugins/pages` 在 `:886`）返回 `pages: [page_name, ...]`（`astrbot/dashboard/services/plugin_service.py:203`）与 `components` 里的 `type: "page"` 项。

### 1.2 前端路由与 URL

- Dashboard 使用 **hash 路由**：`createWebHashHistory`（`dashboard/src/router/index.ts:9`）。
- 页面路由：`name: 'PluginView'`，`path: '/plugin-view/:pluginName/:pageName'`（`dashboard/src/router/MainRoutes.ts:100-102`），组件 `views/PluginViewPage.vue`；旧路径 `/plugin-page/:pluginName/:pageName` 重定向到同名路由（`:106-108`）。
- 因此本插件页面的实际 URL 是：

```text
http://<dashboard-host>:<port>/#/plugin-view/astrbot_plugin_t2i_service/t2i
```

- 入口位置（WebUI 里从哪进）：
  - 插件详情页 → 组件分组“页面”（`dashboard/src/views/extension/PluginDetailPage.vue:350` 起 `componentGroupOrder` 第一项就是 `"page"`），点条目 `router.push({name:'PluginView', ...})`（同文件 `:514-524`）。
  - 左侧边栏“扩展功能 → 插件”分组下的子项，路由 `/plugin-view/<pluginName>/<pageName>`（`dashboard/src/composables/usePluginSidebarItems.ts:80`）。
  - 页面按当前语言显示标题：优先 `views.<name>.title`，再 `pages.<name>.title`，最后目录名（`dashboard/src/utils/pluginI18n.js:56-87`；后端页面初始上下文同样顺序，`plugin_page_service.py:193-198`）。

### 1.3 iframe 与静态资源（重要）

- `PluginViewPage.vue:651`：`<iframe sandbox="allow-scripts allow-forms allow-downloads" ...>`。
  **没有 `allow-same-origin`** → iframe 内文档是 opaque origin：读不到 Dashboard 的 cookie / `localStorage`，不能直接调用需要登录态的 `/api/v1/...`。
- 页面 URL 是带 token 的路径：`/api/v1/plugins/<plugin_name>/views/<view_name>/_t/<jwt>/`（`plugin_page_service.py:549-567`），相对路径资源（`app.js`、`style.css`）会自动继承该 token，无需改写（同文件 `:562-564` 注释）。
- 服务端在返回 HTML 时注入桥脚本 `<script src="/api/plugin/page/bridge-sdk.js"></script>`（`process_plugin_page_html()`，`:582-625`；若 HTML 里已写了该脚本，则改写 query 而不重复注入）；并给 `<html>` 打上 `data-theme`（`:100-120`）。
- 安全响应头（`build_security_headers()`，`:397-412`）：
  - `Content-Security-Policy: frame-ancestors 'self'; object-src 'none'; base-uri 'self'`（非 launcher 模式；`ASTRBOT_LAUNCHER=1` 时去掉 `frame-ancestors` 与 `X-Frame-Options`）
  - `X-Frame-Options: SAMEORIGIN`、`Cache-Control: no-store`、`Referrer-Policy: no-referrer`、`X-Content-Type-Options: nosniff`、`Cross-Origin-Resource-Policy: cross-origin`、`Access-Control-Allow-Origin: *`
  - **没有 `script-src`/`style-src`/`connect-src` 限制**，即内联 `<script>`/`<style>` 与外链 CDN 都能加载；但仍建议单文件内联，避免外部依赖。

---

## 2. i18n：`.astrbot-plugin/i18n/<lang>.json`

- 加载器：`PluginManager._load_plugin_i18n()`（`astrbot/core/star/star_manager.py:567-608`），目录固定 `.astrbot-plugin/i18n/`：
  - 只读 `.json`；**locale = 文件名主干，没有白名单**（`:579-581`），长度 ≤ 32；单文件 > 1 MB 跳过（`:584-588`）；必须是 JSON object（`:593-594`）；用 `utf-8-sig` 读取（`:591`，带 BOM 也可以）。
  - WebUI 支持的语言目录：`dashboard/src/i18n/locales/` = `en-US`、`ja-JP`、`ru-RU`、`zh-CN`（Dashboard 自身缺语言时回退 `zh-CN`，`dashboard/src/i18n/composables.ts:27-33`）。
- 页面相关键结构（当前 v4.29.1 源码）：

```json
{
  "metadata": {
    "display_name": "文转图服务 (T2I Service)",
    "short_desc": "AstrBot 内置文转图渲染服务",
    "desc": "在 AstrBot 进程内提供 HTML/模板转图片渲染服务……"
  },
  "views": {
    "t2i": { "title": "文转图服务", "description": "查看服务、浏览器与端点状态" }
  },
  "pages": {
    "t2i": { "title": "文转图服务", "description": "查看服务、浏览器与端点状态" }
  }
}
```

- 键解析位置：
  - 页面初始上下文（`window.AstrBotPluginView.getContext().pageTitle`）：先 `views.<name>.title`，再 `pages.<name>.title`，最后目录名（`plugin_page_service.py:193-198`）。
  - 插件列表/详情/侧边栏的后端字段 `i18n_key` 固定是 **`pages.<name>`**（`plugin_page_service.py:655`），前端 `getPluginPageI18nBase()` 也优先用它（`dashboard/src/utils/pluginI18n.js:29-41`）→ **列表标题只读 `pages.<name>.title`**。
  - 结论：**两个键都写上**最稳（`pages.*` 保列表，`views.*` 保页面内标题，且后者在 v4.29 文档里是首选命名）。
- 缺失回退：没有对应语言、字段缺失或文件不存在时——
  - 插件名/描述回退 `metadata.yaml` 的 `display_name`/`short_desc`/`desc`；
  - 页面标题回退 **目录名**（`plugin_page_service.py:196-198`；`pluginI18n.js:79-87`）；
  - 描述回退页面对象自带的 `description`/`desc`，再回退目录名（`PluginDetailPage.vue:502-506`）。
  - 页面内可以用 `bridge.t("views.t2i.title", "文转图服务")` 自行翻译：`translate()` 按 **dot-path** 取值，回退顺序是 `当前 locale → zh-CN → en-US → 调用方 fallback`（`astrbot/dashboard/plugin_page_bridge.js:98-113`）；`bridge.getI18n()` 返回**整个** plugin i18n（按 locale 分组，`plugin_page_service.py:200-207`）。
- 注意：i18n 在插件加载时读入（`star_manager.py:562`），但页面上下文会用 `_load_plugin_i18n()` 重新读盘（`plugin_page_service.py:172-175`），改 i18n 文件后**刷新页面即生效**，无需重载插件。
- 本插件现状：`.astrbot-plugin/i18n/` 目录**为空** → 当前页面标题只会显示目录名 `t2i`。本次任务只允许写 3 个文件，故未创建语言文件；建议后续补 `zh-CN.json`/`en-US.json`（内容见上）。

---

## 3. `context.register_web_api(route, handler, methods, desc)` 精确语义

声明（`astrbot/core/star/context.py:764-786`）：

```python
def register_web_api(self, route: str, view_handler: WebApiHandler,
                     methods: list[str], desc: str) -> None:
    """相同路由 + 相同 methods 已注册时替换，否则追加。"""
```

- `registered_web_apis` 是 `Context` 的**类级列表**（`context.py:133`），全局共享、进程内持久；插件 reload 不会自动清空它（源码中没有任何按插件清理的逻辑）。
- route 是**相对于 `/api/v1/plugins/extensions/` 之后的自定义路径**，即插件自己负责把插件名写进 route。匹配时（`plugins.py:209-221`）取 `request_path = "/" + subpath`，subpath 就是 URL 里 `<plugin_name>/<endpoint>` 那一段，然后 `re.fullmatch` 你注册的 route：
  - `_plugin_api_route_pattern()`（`plugins.py:196-207`）支持 `<name>`（匹配单段，`[^/]+`）与 `<path:name>`（匹配剩余全部，`.*`）。
  - 所以必须写成 `/astrbot_plugin_t2i_service/status`；写 `/status` 永远匹配不到。
- 分发（`_call_plugin_extension()`，`plugins.py:233-252`）：
  - `view_handler(**path_params)` —— route 里的 `<name>` 会作为**关键字参数**传给你的 handler（`plugins.py:245,248`）。本插件的 `web_status(self)` 不带参数，只在 route 无占位符时成立。
  - handler **可以是同步函数也可以是 async**（`_call_view()` 用 `inspect.isawaitable` 判断，`astrbot/dashboard/asgi_runtime.py:466-470`），但注册时应写 `async def`。
  - 返回值：
    - `dict`/`list` → 原样 `JSONResponse`（`asgi_runtime.py:499-500`）——**不会自动套 `{status:"ok"}` 信封**；
    - `(content, status_code, headers)` 元组受支持（`asgi_runtime.py:479-497`）;
    - Starlette `Response`（`json_response`/`error_response`/`file_response` 都是）原样返回。
  - 未匹配到路由：返回 `{"status": "error", "message": "未找到该路由", "data": {}}`（`plugins.py:241-242`）。
- HTTP 方法：GET/POST/PUT/PATCH/DELETE 都在 `/api/v1/plugins/extensions/{plugin_path:path}` 上开放（`plugins.py:398,407,416,425,434`），鉴权 `Depends(require_plugin_scope)`。
- 拿 request：`from astrbot.api.web import request`（`astrbot/api/web.py:323` 的代理对象，`PluginRequest` 定义在 `:166`）。字段：`method/path/headers/cookies/content_type/client_host/path_params/plugin_name/username/query`（`query` 是 `PluginMultiDict`，有 `get(key, default, type)`/`getlist`）；方法：`await body()`/`await json(default)`/`await form()`/`await files()`；上传文件 `PluginUploadFile`（`:97`）有 `save/read/write/seek/close`。
  - **注意**：只有在插件 Web API handler 内部调用 `request` 才有效，否则抛 `RuntimeError("astrbot.api.web.request is only available inside a plugin Web API handler.")`（`:255-269`）。也可以像本插件那样通过 route 占位符拿参数，完全不碰 `request`。
- 返回工具（`astrbot/api/web.py`）：`json_response(data=None, *, status_code=200, headers=None)`（`:343`）、`error_response(message, *, status_code=400, data=None, headers=None)`（`:366`，body = `{"status":"error","message":...,"data":...}`）、`file_response`（`:391`）、`stream_response`（`:417`）。

### 3.1 页面 JS 侧：完整 URL 是什么

桥请求 → 父窗口 axios（`PluginViewPage.vue`）：

```js
const normalized = normalizePluginEndpoint(endpoint);       // :100-121 去掉前导/、拒绝 .. 与空段
const url = `/api/v1/plugins/extensions/${encodeURIComponent(pluginName)}/${normalized}`;  // :122-124
const response = await axios.get(url, { params });           // :383 / :395(post) :419(upload) :437(download)
if (response.data?.status === "error") throw new Error(response.data.message);
resolve(response.data?.data ?? response.data);               // :384-387 自动拆 data 信封
```

即：**页面里写 `bridge.apiGet("status")`，浏览器实际请求 `GET /api/v1/plugins/extensions/astrbot_plugin_t2i_service/status`**。

对照表（本插件已注册的 7 个接口）：

| 页面内桥调用 | 真实 HTTP |
| --- | --- |
| `bridge.apiGet("status")` | `GET /api/v1/plugins/extensions/astrbot_plugin_t2i_service/status` |
| `bridge.apiGet("install/state")` | `GET /api/v1/plugins/extensions/astrbot_plugin_t2i_service/install/state` |
| `bridge.apiPost("install")` | `POST /api/v1/plugins/extensions/astrbot_plugin_t2i_service/install` |
| `bridge.apiPost("render-test")` | `POST /api/v1/plugins/extensions/astrbot_plugin_t2i_service/render-test` |
| `bridge.apiPost("apply-endpoint")` | `POST /api/v1/plugins/extensions/astrbot_plugin_t2i_service/apply-endpoint` |
| `bridge.apiPost("restart")` | `POST /api/v1/plugins/extensions/astrbot_plugin_t2i_service/restart` |
| `bridge.apiGet("status-card")` | `GET /api/v1/plugins/extensions/astrbot_plugin_t2i_service/status-card` |

鉴权怎么带：**不用页面自己带**。请求由父窗口（同源 Dashboard 页面）用 axios 发出，自动带 Dashboard session；插件页面 iframe 只 postMessage（`plugin_page_bridge.js:2` channel `astrbot-plugin-page`，`:44` 限定 targetOrigin）。静态资源另走路径内 JWT `asset_token`。

**不要**在页面里直接 `fetch("/api/v1/plugins/extensions/...")`：iframe 无 `allow-same-origin`，请求不带凭证，必然 401（且 `apiGet` 的返回值经父窗口 `data?.data ?? data` 拆包，直接 fetch 拿到的是未拆包的 envelope）。

---

## 4. 页面前端能力与最小示例

- **没有**内置 Vue/TDesign/Element 等组件库可用（页面是插件自己的静态 HTML，由 `/api/v1/plugins/<plugin>/views/<view>/_t/<token>/` 直接返回，不走 Dashboard 前端框架）。要么写纯 HTML+CSS+原生 JS，要么自己引入 CDN（CSP 不限制 script-src/style-src/connect-src，见 §1.3）。
- 可用的宿主能力只有桥注入的 `window.AstrBotPluginView`（`plugin_page_bridge.js:207-286`）：

| API | 说明 |
| --- | --- |
| `await bridge.ready()` | iframe 与父窗口握手完成（bridge 末尾自动 `send("ready")`，`:288` 附近） |
| `bridge.getContext()` | `{pluginName, displayName, pageName, pageTitle, locale, i18n, isDark}`（`plugin_page_service.py:200-208`） |
| `bridge.getLocale()` / `bridge.getI18n()` / `bridge.t(key, fallback)` | 语言与翻译 |
| `bridge.onContext(fn)` | 语言/主题切换时回调（返回取消订阅函数） |
| `await bridge.apiGet(endpoint, params)` | GET，返回已拆 `data` 信封的 JSON |
| `await bridge.apiPost(endpoint, body)` | POST JSON |
| `await bridge.upload(endpoint, file)` | multipart 上传（父窗口以 `FormData` 发出，字段名 `file`） |
| `bridge.download(endpoint, params, filename)` | 触发浏览器下载 |
| `await bridge.subscribeSSE(endpoint, handlers, params)` / `bridge.unsubscribeSSE(id)` | SSE；`handlers = {onMessage,onError,onOpen?}`，401 时不能用原生 EventSource |
| 错误语义 | 后端返回 `{"status":"error"}` 或 HTTP 非 2xx → **promise reject**，`err.message` 即后端 `message`（`PluginViewPage.vue:384-389`） |

- 最小可运行骨架（本仓库 `pages/t2i/index.html` 即按此实现，含轮询）：完整文件见 [`pages/t2i/index.html`](../pages/t2i/index.html)。核心片段：

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>文转图服务</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; padding: 16px; background: #121417; color: #e6e8eb;
           font: 14px/1.6 system-ui, "Segoe UI", "PingFang SC", sans-serif; }
    .card { background: #1b1f24; border: 1px solid #2a3038; border-radius: 12px;
            padding: 16px; margin-bottom: 12px; }
    .card h2 { margin: 0 0 12px; font-size: 15px; color: #9ecbff; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }
    .k { color: #8b98a5; } .v { color: #e6e8eb; word-break: break-all; }
    .ok { color: #4ade80; } .bad { color: #f87171; }
    pre { background: #0f1216; border-radius: 8px; padding: 10px; overflow: auto; }
  </style>
</head>
<body>
  <div class="card">
    <h2 id="title">文转图服务</h2>
    <div class="grid" id="summary"></div>
  </div>
  <div class="card">
    <h2>原始状态 JSON</h2>
    <pre id="raw">加载中…</pre>
  </div>

  <script>
    // 关键：必须走桥（iframe sandbox 没有 allow-same-origin，直接 fetch 不带登录态）
    const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;

    async function loadStatus() {
      if (!bridge) { // 直接在普通标签页打开时才会走到这里
        document.getElementById("raw").textContent =
          "未检测到 AstrBot 页面桥（请从 WebUI 插件页面打开）。";
        return;
      }
      try {
        const data = await bridge.apiGet("status"); // GET .../extensions/<plugin>/status
        render(data);
      } catch (err) {
        document.getElementById("raw").textContent = "加载失败：" + err.message;
      }
    }

    function render(d) {
      document.getElementById("raw").textContent = JSON.stringify(d, null, 2);
      const rows = [
        ["内嵌服务", d.server?.running ? "运行中" : "未运行", d.server?.running],
        ["服务端点", d.server?.endpoint || "-"],
        ["累计请求", String(d.server?.requests ?? 0)],
        ["playwright", d.browser?.pip_playwright ? "已安装" : "未安装", d.browser?.pip_playwright],
        ["Chromium", d.browser?.installed ? "已安装" : "未安装", d.browser?.installed],
        ["浏览器进程", `${d.browser?.processes ?? 0} 个 / ${d.browser?.process_memory_mb ?? 0} MB`],
        ["AstrBot 端点", d.endpoint?.astrbot_t2i_endpoint || "（未设置）"],
      ];
      document.getElementById("summary").innerHTML = rows.map(([k, v, state]) => {
        const cls = state === undefined ? "" : state ? "ok" : "bad";
        return `<div><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
      }).join("");
    }

    (async () => {
      if (bridge?.ready) { try { await bridge.ready(); } catch (_) {} }
      if (bridge?.onContext) {
        bridge.onContext((c) => {
          if (c?.i18n) document.getElementById("title").textContent =
            bridge.t("views.t2i.title", c.pageTitle || "文转图服务");
        });
      }
      await loadStatus();
      setInterval(loadStatus, 5000); // 轮询 status；安装进度用 install/state
    })();
  </script>
</body>
</html>
```

> 目录命名结论：本仓库保持 **`pages/t2i/`**（源码里 `pages` 是正式支持的别名目录，且仓库已存在该目录）。若想贴齐上游新命名，可整体改名为 `views/t2i/`，URL、路由、i18n 键都不变；**不要**同时保留两个目录（`views/` 会屏蔽 `pages/`）。

---

## 5. 权限 / 开关 / metadata.yaml

- **metadata.yaml 不需要声明 pages/views**：页面完全由目录扫描发现（`plugin_page_service.py:474-505`）。可选的顶层 `views:` 或 `pages:` 列表（会被写进 `StarMetadata.views`，`star_manager.py:556-563`）不参与发现。
- 插件必须**启用**（`activated`）才能打开页面：`get_plugin_page_entry_config()` 对未启用插件抛“插件未启用”（`plugin_page_service.py:229-230`）。
- 权限：接口与页面入口都要 Dashboard 登录 + 插件作用域（`require_plugin_scope`，`plugins.py:398-441`、`:885-916`）；页面静态资源用 JWT `asset_token`（`serve_page_content` 的 token 校验在 `plugin_page_service.py:146-165`）。
- 页面出现在 WebUI 的两个位置：插件详情页的“页面”分组，以及左侧“扩展功能 → 插件”分组（见 §1.2）。没有独立的“Pages 开关”配置项。
- 本插件 `metadata.yaml` 目前**没有** `views`/`pages` 字段，也无需添加；但建议把 `astrbot_version` 从 `">=4.16,<5"` 提到 `">=4.24.2,<5"`（Pages 机制引入版本）。

---

## 6. `@filter.command` 与 Web API 共存、reload 行为

- 两者互不干扰：命令走 `star_handlers_registry`，Web API 走 `Context.registered_web_apis`。
- **重复注册不会报错**：`register_web_api()` 对 `route` 与 `methods` 都比对，`methods` 列表相等才替换（`context.py:782-786`）。注意比对的是 `methods == api[2]`（列表相等），所以 `["GET"]` 与 `["get"]` 会被视为两条不同记录——统一大写。
- reload 时：`registered_web_apis` 是类级列表且**不随插件卸载清理**（源码无清理逻辑），重载后新实例用同样的 route+methods 覆盖旧 handler，因此不会重复；但**改名/删除的 route 会残留**到进程重启（指向已终止实例的 bound method）。如果要改 route 名，重启 AstrBot 更干净。
- 本插件的 `_web_apis_registered` 是实例级哨兵（`main.py:104,384-385`），能防同实例内重复注册；reload 后是新实例，会重新覆盖注册 —— 行为正确，无需额外处理。
- `initialize()` 在 handler 注册完成后被 `await`（`star_manager.py:1422-1424`）；它抛异常会导致整个插件加载失败（`:1430-1450` 记录 `----- Failed to load plugin ... -----`）。因此像 main.py 那样把 `_register_web_apis()` 包在 try/except 里是正确做法。

---

## 7. 复核 `main.py` 里用到的接口（结论：全部可用，2 处建议改进）

| 用法 | 结论 |
| --- | --- |
| `from astrbot.api import AstrBotConfig, logger`（`main.py:34`） | ✅ 正确。`astrbot/api/__init__.py` 的 `__all__` 导出 `AstrBotConfig`，`logger` 是会把日志路由到 `astrbot.plugin.<插件名>` 的代理 |
| `from astrbot.api.web import error_response, json_response`（`main.py:37`） | ✅ 正确（`astrbot/api/web.py:343,366`） |
| `from astrbot.api.event import AstrMessageEvent, filter`（`main.py:35`）、`from astrbot.api.star import Context, Star, register`（`:36`） | ✅ 正确 |
| `from astrbot.core.message.message_event_result import MessageChain` | ⚠️ **main.py 目前没有 import MessageChain**（只有文档里提到过一次）。若之后要用，建议改成公开路径 `from astrbot.api.event import MessageChain`（`astrbot/api/event/__init__.py:1-6` 重新导出）；直接用 `astrbot.core.*` 内部路径可用但不受兼容保证 |
| `from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path`（`main.py:38-40`） | ⚠️ 属于内部路径（能用，AstrBot 自身在用）。公开替代是 `astrbot.api.star` 的 `StarTools.get_data_dir()`（`astrbot/core/star/star_tools.py:47-70`：单插件环境返回 `data/plugin_data/<name>`，多插件同理）；本插件用参数名方式也 OK |
| `register_web_api(f"/astrbot_plugin_t2i_service/status", self.web_status, ["GET"], "文转图服务状态")`（`main.py:386-389`） | ✅ 正确：route 带插件名前缀，匹配 `/astrbot_plugin_t2i_service/status`；handler 无参数，仅当 route 无 `<占位符>` 时成立 |
| handler 无参数、返回 `json_response(...)`（`main.py:416-529`） | ✅ 正确。返回 `Response` 直接下发；body 用 `{"status":"ok","data":{...}}` 信封也正确——桥会拆 `data` |
| `core_config["t2i_endpoint"]=...; core_config.save_config()`（`main.py:180-183,481-485`） | ✅ 语义正确（`AstrBotConfig` 是 dict 子类，`save_config()` 同步落盘，`astrbot/core/config/astrbot_config.py:282-292`）。⚠️ 建议：这里在 **async** handler / 协程里调用同步落盘会阻塞事件循环，改用 `await core_config.save_config_async()`（同文件 `:294-310`，内部 `asyncio.to_thread`）。另外 AstrBot 主进程缓存了策略，改了 `t2i_strategy` 后需重启才生效——插件自己的 note 已正确说明 |
| `web_render_test` 失败时返回 `json_response({"status":"error", ...})`（`main.py:462-464`） | ⚠️ 能让桥 reject（消息取 `message`），但 HTTP 仍是 200。语义上更推荐 `error_response(detail, status_code=500/503)`，与文件里其他分支一致 |
| `_web_apis_registered` 哨兵 + reload | ✅ 见 §6 |
| 页面骨架调用的返回结构 | ✅ 已按桥的拆包规则实现：`apiGet("status")` 直接得到 `_status_payload()`；`apiGet("install/state")` 得到 `install_state.as_dict() + installed`（`main.py:437-446`，status ∈ `idle/running/done/failed`，`t2i_plugin/browser.py:35-38`） |
| `status-card`（`main.py:529-538`） | ⚠️ **不是** `image/png` 字节流：route 描述写的是"状态卡片图片"，但实现返回 `json_response({"status":"ok","data":{"image":"data:image/png;base64,..."}})`。所以页面要 `const {image} = await bridge.apiGet("status-card")` 再塞给 `<img src>`；失败时是 `error_response(..., status_code=503)` |

---

## 8. 无法从源码确认 / 需注意的点

- **无法确认**：`views` 命名是哪个小版本引入的（`changelogs/v4.24.2.md:10` 只记录 `pages/`；v4.29 的文档/源码已把 `views` 列为首选）。因此本仓库继续用 `pages/` 最安全，`metadata.astrbot_version` 下界用 `>=4.24.2`。
- **无法确认**：插件页面是否有“未登录直接访问”场景（token 校验依赖 `dashboard.jwt_secret`；若 Dashboard 未配置 `jwt_secret`，`issue_plugin_page_asset_token()` 返回 `None`，页面入口会拿不到 `content_path`——见 `plugin_page_service.py:674-690`）。实践上 Dashboard 默认会生成该密钥。
- 页面内**不能**使用 `localStorage`/`sessionStorage`（opaque origin 会抛异常），需要持久化状态请走插件自己的 Web API + 插件数据目录。

---

## 9. 实测验证（可复现）

在一台**隔离实例**上跑了端到端验证，镜像与运行中的 `astrbot`/`t2i-envlab` 相同（`1e5379633318`，AstrBot **v4.29.2**），自己的 data 目录、自己的端口，不碰线上容器：

```bash
docker run -d --name pagespec-verify -p 127.0.0.1:16385:6185 \
  -v /tmp/pagespec-verify/data:/AstrBot/data 1e5379633318 \
  sh -lc 'cd /AstrBot && exec python main.py'
# 插件放在 data/plugins/astrbot_plugin_t2i_service/（即本仓库的副本）
# 鉴权：用该实例 data/cmd_config.json 里的 dashboard.jwt_secret 签一个 {"username": ...} 的 HS256 JWT，作为 Bearer
```

| 请求（带 Dashboard JWT） | 实测结果 | 说明 |
| --- | --- | --- |
| `GET /api/plugin/page/entry?name=astrbot_plugin_t2i_service&page=t2i` | **200** `{"status":"ok","data":{"name":"t2i","title":"t2i","i18n_key":"pages.t2i","content_path":"/api/v1/plugins/astrbot_plugin_t2i_service/views/t2i/_t/<jwt>/?asset_token=<jwt>"}}` | ✅ 页面确实由插件根的 **`pages/t2i/index.html`** 发现（注意静态资源路径段统一是 `views/`，与目录名无关） |
| `GET <content_path>` | **200**，返回本仓库 `pages/t2i/index.html` 的 HTML（`<title>文转图服务`…） | ✅ 页面内容可被 iframe 直接加载 |
| `GET /api/plugin/page/t2i/index.html` | **404** `{"status":"error","message":"Not Found"}` | ❌ 这个"直链"**不存在** |
| `GET /api/plugin/page/astrbot_plugin_t2i_service/t2i/index.html` | **404** | 同上，框架没有"按页面名直接取 index.html"的路由 |
| `GET /api/v1/plugins/extensions/astrbot_plugin_t2i_service/status` | **200** `{"status":"ok","data":{"plugin_name":…,"server":{…},"browser":{…},"renderer":{…},"endpoint":{…}}}` | ✅ 桥最终打的就是这条；返回体就是 handler 的 `json_response`（HTTP 200 + `status/data` 信封） |
| `GET /api/plug/astrbot_plugin_t2i_service/status` | **200**，同上 | ✅ 旧前缀别名也可用 |
| `GET /api/plugin/page/api/status`（+ `X-Plugin-Page-Token`）/ `?token=` | **404** | ❌ 桥**不是**走 `/api/plugin/page/api/...`；页面 JS 里不要自己拼这个路径 |
| `GET /api/plugin/page/bridge-sdk.js` | 存在（`dashboard/api/plugins.py:1473`） | 注入的桥脚本；在页面里用全局 `window.AstrBotPluginView` 即可 |

在**运行中的 v4.29.3 `astrbot` 容器**（`:6185`，用其 `dashboard.jwt_secret` 签的 Bearer）上复测同样结论：`/api/plugin/page/entry?name=astrbot_plugin_model_status&page=status` → 200 且 `content_path=/api/v1/plugins/astrbot_plugin_model_status/views/status/_t/<jwt>/…`；`/api/plugin/page/t2i/index.html` → 404；`/api/v1/plugins/extensions/astrbot_plugin_t2i_service/status` → 200 `{"status":"error","message":"未找到该路由"}`（挂载点存在，只是该容器没装本插件）。

**被否证的说法**（不要照做）：把页面放到 `<插件>/data/plugin_pages/<page>/index.html`、再用 `/api/plugin/page/<page>/index.html` 访问。两端源码里都不存在 `data/plugin_pages`（`grep -rn "data/plugin_pages\|plugin_pages/"` 在容器 `/AstrBot` 的 py/js/md 中 **0 命中**；`ls /AstrBot/data/` 也没有该目录），实测 URL 也是 404。页面放**插件根的 `pages/`（或 `views/`）**才是唯一正确位置。

**i18n 现状**：本仓库 `.astrbot-plugin/i18n/` 是**空目录**，所以入口 `title` 回退成页面名 `"t2i"`（`i18n_key` 为 `pages.t2i`）。要让侧边栏/标签页显示中文名，需要新增 `.astrbot-plugin/i18n/zh-CN.json`（以及可选的 `en-US.json`），内容形如 `{"pages":{"t2i":{"title":"文转图服务","description":"AstrBot 内置文转图渲染服务"}}}`——该文件不在本次改动范围内，留给 lead 决定。

