# AGENTS.md — AstrBot 插件模板

> 本文件是任何 AI 编码代理（agent）进入 **AstrBot 插件项目** 时的**必读**工作说明书。
> 它约定插件的架构、代码规范、文档维护规则。任何对代码、配置、行为的改动，都必须同时遵守本文档的规则。
>
> 适用项目：`astrbot_plugin_*` 命名的 AstrBot 插件（Python）。
>
> **模板版本**：v0.1.0（2026-10-21） —— 本行标记本文件对应的 `astrbot-plugin` 模板版本。回填项目内容时**不要改动本行**；只有从上游合并到新版本时才把它更新为合并到的版本。
> **来源**：本文件是 [agent-templates](https://github.com/XTsat/agent-templates) 的 `templates/astrbot-plugin/AGENTS.md` 副本，最新版见该仓库 `main` 分支。
> 用户说「更新 AGENTS」时按此流程：① 打开来源仓库该模板的 `AGENTS.md`，比对头部「模板版本」与本地是否一致——一致说明已是最新，直接结束，不必拉取逐节比对 → ② 不一致才拉取来源模板最新版 → ③ 与本地逐节比对 → ④ 合并（保留本项目已回填的插件名、命令前缀、目录结构与项目特有约定）→ ⑤ 展示 diff 与改动清单，等用户确认后再落盘，并把头部「模板版本」同步改为合并到的版本。**禁止整文件覆盖**。
>
> **本项目（已回填）**：`astrbot_plugin_t2i_service`「文转图服务」——把 [`astrbot-t2i-service`](https://github.com/AstrBotDevs/astrbot-t2i-service)（FastAPI + Playwright）移植进 AstrBot 进程，用 aiohttp 暴露与上游完全一致的 `/text2img` 契约；同一仓库保留独立服务（Docker/FastAPI）形态。落地差异集中在第 1–6 章，**第 7–8 章的文档与提交流程原样适用**。

---

## 1. 项目概览

**AstrBot 插件**是基于 `astrbot.api.star` 框架的 Python 插件，以 `Star` 子类为核心，通过装饰器注册指令组、事件监听器、过滤器，在 AstrBot 运行时中加载。

本插件对外提供的是「AstrBot 进程内的文转图端点」：AstrBot 主程序把 `t2i_endpoint` 指向本插件监听的地址即可渲染 HTML/Jinja2 模板，不需要再单独部署容器。渲染、存储、指标都来自移植过来的核心层，插件层只负责 AstrBot 生命周期适配。

核心文件清单：

| 文件 / 目录 | 必选 | 职责 |
|------|------|------|
| `main.py` | ✅ | Star 插件入口：生命周期、4 个命令、周期清理、端点写入 |
| `metadata.yaml` | ✅ | 插件元数据（name/version/author/desc/repo/astrbot_version/tags），**唯一版本源** |
| `_conf_schema.json` | ✅ | WebUI 配置 Schema（`server` / `render` / `browser` / `endpoint` / `storage` / `fonts` 六段） |
| `README.md` + `README_en.md` | ✅ | 用户文档，中文版为权威源 |
| `CHANGELOG.md` | ✅ | 变更日志 |
| `LICENSE` + `NOTICE` | ✅ | Apache-2.0；`NOTICE` 记录上游基底与移植说明，**不可删** |
| `requirements.txt` | ✅ | 插件模式运行时依赖（当前只有固定版本的 `playwright`） |
| `t2i_plugin/` | ✅ | 插件适配层：`config` / `browser` / `server` / `card` / `fonts` / `font_web` |
| `t2i_core/` | ✅ | 渲染核心（自上游移植）：`render` / `api` / `storage` / `metrics` / `util`，**零 AstrBot 依赖** |
| `service/main.py` | ✅ | 独立服务（FastAPI）入口；插件模式下不执行 |
| `tests/` | ✅ | pytest 套件（核心层 + 插件层），CI 主线 |
| `.astrbot-plugin/i18n/` | 可选 | 元数据与页面 i18n（`zh-CN.json` / `en-US.json`），已存在 |
| `views/` | 可选 | 插件内嵌页面（AstrBot 会自动发现 `views/<名字>/index.html`）；当前只有 `fonts/`（「文转图服务面板」：概览 / 对接项目两栏切换，共用一次清单请求） |
| `docs/` | 可选 | 接口文档（`APIS.md`）、环境基线（`ENV-REPORT.md`）、独立服务文档与 README 配图 |
| `tools/envlab/` | 可选 | 环境基线与抓图脚本，非运行时依赖 |
| `pyproject.toml` | 可选 | 开发/独立服务依赖与 pytest 配置 |
| `requirements-standalone.txt` | 可选 | 独立服务额外依赖 |
| `Dockerfile` + `.github/workflows/` | 可选 | 独立服务镜像与「测试 + 发布镜像」CI |
| `logo.png` | ⚠️ 暂无 | AstrBot 的图标文件名固定为 `logo.png`；缺失只是 WebUI 无图标，不影响加载 |
| `uv.lock` | ⚠️ 滞后 | 当前与依赖声明不同步，**不作为安装依据**；需要时重新生成 |

---

## 2. 目录结构与架构

### 2.1 项目结构

本仓库是 `astrbot-t2i-service` 的 fork，仓库根目录**本身就是**插件目录；部署时整仓放入 AstrBot 的 `data/plugins/astrbot_plugin_t2i_service`（目录名必须等于 `metadata.yaml` 的 `name`）。

```
astrbot_plugin_t2i_service/
├── main.py                 # Star 插件入口（命令 / 生命周期）
├── metadata.yaml           # 插件元数据（唯一版本源）
├── _conf_schema.json       # 插件配置 Schema
├── CHANGELOG.md
├── README.md / README_en.md
├── LICENSE / NOTICE
├── requirements.txt        # 插件模式依赖
├── requirements-standalone.txt
├── pyproject.toml
├── t2i_plugin/             # 插件适配层（AstrBot 相关逻辑只出现在这里与 main.py）
│   ├── config.py           # 配置模型：AstrBotConfig → 嵌套 dataclass
│   ├── browser.py          # Chromium 检测 / 下载 / 孤儿清理 / 进程扫描
│   ├── server.py           # aiohttp 内嵌服务，对齐上游 /text2img 契约
│   └── card.py             # 状态卡片 / 自检卡片 HTML
├── t2i_core/               # 渲染核心（零 AstrBot 依赖）
│   ├── render.py           # Playwright 渲染、截图参数、模板引擎
│   ├── api.py              # 上游 FastAPI 应用（独立服务模式）
│   ├── storage.py          # 本地 / S3 图片存储
│   ├── metrics.py          # Prometheus 指标
│   └── util.py             # 文件命名、过期清理
├── service/main.py         # 独立服务入口（uvicorn）
├── .astrbot-plugin/i18n/   # zh-CN.json / en-US.json
├── tests/
├── docs/
│   └── standalone/         # 上游 README / MONITORING 原文
└── tools/envlab/           # 环境基线脚本与抓图
```

### 2.2 架构倾向

本项目已选定 **分层多文件**，并在整个生命周期内保持不变（`main.py` 已 >500 行，且核心层要与独立服务共用，**不要再往单文件风格回退**）。

依赖方向严格单向：

```
main.py ──► t2i_plugin/{config,browser,card,server} ──► t2i_core/{render,storage,metrics,util,api}
service/main.py ────────────────────────────────────► t2i_core
```

- `t2i_core/` 是移植层，**禁止** import `astrbot`、`t2i_plugin`、`main`（唯一例外是文档字符串里提到上游）。破坏这条就等于砍掉独立服务形态，`tests/test_api.py`、`test_metrics.py` 也会一起失效。
- `t2i_plugin/` 允许 import `t2i_core`，但**不 import `main.py`**（避免循环）。
- 只有 `main.py` 接触 AstrBot API（`astrbot.api.*`、`astrbot.core.utils.astrbot_path`）。

新增代码落点：

| 新增内容 | 落点 |
|------|------|
| 聊天命令 / 生命周期钩子 | `main.py`（命令方法按要求分节注释） |
| 配置项 | `t2i_plugin/config.py` 的 dataclass 字段 + `_conf_schema.json` + README 配置表 |
| 浏览器供给（检测、下载、清理） | `t2i_plugin/browser.py` |
| 卡片用的 HTML 片段 | `t2i_plugin/card.py` |
| 字体下载/上传/fontconfig 供给 | `t2i_plugin/fonts.py` |
| 文转图服务面板的 Web API | `t2i_plugin/font_web.py` |
| 字体页面（插件内嵌页面） | `views/fonts/`（一个页面，tab 切换「概览」与「对接项目」，「对接项目」栏里再用二级 tab 显示上游项目名；栏位切换按 DOM 里的 tab/panel 配对自动接管，加栏位只改 HTML，不要再拆视图；配色只跟随桥写到 `<html data-theme>` 的浅/深色，文案一律走 `pages.fonts.*` 的 i18n，不在 JS 里写死中文） |
| HTTP 契约、鉴权、失败透传 | `t2i_plugin/server.py` |
| 渲染与截图行为、模板处理 | `t2i_core/render.py` |
| 图片存储与过期 | `t2i_core/storage.py`、`t2i_core/util.py` |
| 监控指标 | `t2i_core/metrics.py` |

### 2.3 双形态约束

任何改动都必须让两种形态同时成立，且 HTTP 契约**只能有一份**：

| 形态 | 入口 | 服务实现 | 浏览器 |
|---|---|---|---|
| 插件（默认） | `main.py` | `t2i_plugin/server.py`（aiohttp） | 插件自己管理，装在插件数据目录 |
| 独立服务 | `service/main.py` | `t2i_core/api.py`（FastAPI） | 镜像内 Playwright 自带 |

- 对外接口（`POST /text2img/generate`、`GET /text2img/data/{id}`、`GET /healthz`）以 `docs/APIS.md` 为准；AstrBot 客户端会自行把 endpoint 补成 `.../text2img` 再拼 `/generate`，**不要**在插件层私自改路径或请求/响应结构。
- 插件层新增能力时，先问一句「独立服务模式会不会因此坏掉」；会坏就下沉到 `t2i_core` 或做成可选。

---

## 3. 技术栈与依赖

- **Python 3.11+**（`.python-version` 与 `pyproject.toml` 的 `requires-python` 均为 3.11；CI 也用 3.11）。
- **AstrBot**：`metadata.yaml` 声明 `astrbot_version: ">=4.16,<5"`；用到的 API 是 `astrbot.api.star`（`Context`、`Star`）、`astrbot.api.event`（`filter`、`AstrMessageEvent`）、`astrbot.api`（`AstrBotConfig`、`logger`）、`astrbot.api.web`（`json_response`、`error_response`）、`astrbot.core.utils.astrbot_path.get_astrbot_plugin_data_path`。
- **插件层可依赖 AstrBot 运行时自带的包**：`aiohttp`、`jinja2`、`loguru`、`pydantic`、`typing_extensions`。**不要**在 `requirements.txt` 里重复声明它们，也不要为了它们写降级分支。
- **浏览器**：`playwright==1.64.1` 固定版本——Playwright 的版本与浏览器构建目录（`chromium-1248`）绑定，改动前先看 `docs/ENV-REPORT.md` 的实测数据。
- **依赖声明三处，改动必须同步**：`requirements.txt`（插件运行时）、`requirements-standalone.txt`（独立服务）、`pyproject.toml`（开发/工具）。
- **惰性 import**：`playwright` 不在模块顶层导入（未安装时插件仍要能加载并给出提示），实际渲染路径里按需导入。
- **禁止**引入与本功能无关的依赖。

---

## 4. 核心代码规范

### 4.1 插件骨架

```python
from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from t2i_plugin.config import T2IConfig

PLUGIN_NAME = "astrbot_plugin_t2i_service"
CLEANUP_TICK_SECONDS = 60


class T2IServicePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None) -> None:
        super().__init__(context)
        self.context = context
        self.settings = T2IConfig.from_raw(dict(config or {}))

    @filter.command("t2i_status")
    async def cmd_status(self, event: AstrMessageEvent):
        yield event.plain_result(self._status_text())
```

> 本项目**不使用 `@register` 装饰器**：AstrBot v3.5.19 之后插件类会被自动识别，名称/版本/描述一律来自 `metadata.yaml`。插件版本也**不在代码里手写常量**，需要展示时从 `metadata.yaml` 读取（见 7.1）。

### 4.2 命名约定

| 元素 | 约定 |
|------|------|
| 模块级常量 | `UPPER_SNAKE`（`PLUGIN_NAME`、`CLEANUP_TICK_SECONDS`） |
| 私有方法 | `_` 前缀 + `snake_case`（`_status_payload`、`_apply_endpoint_config`） |
| Web API 方法 | （本插件当前不注册 Web API；若未来新增，用 `web_` 前缀） |
| 命令方法 | `cmd_` 前缀（`cmd_status`、`cmd_install`） |
| 类名 | `PascalCase`（`T2IServicePlugin`、`BrowserManager`、`T2IHttpServer`） |
| 文件名 | `snake_case`（`browser.py`、`_conf_schema.json`） |

### 4.3 类型与语法

- Python 3.11 现代联合类型写法（`dict | None`、`list[dict]`、`tuple[str, str]`）
- 函数签名**必须完整类型注解**（参数 + 返回类型）；`yield` 返回结果的命令/事件处理方法标注返回类型（现有命令方法尚未全部补齐，新增的必须补）
- 禁止 `Any` 作为类型注解（除非调用方签名强制要求）
- 禁止 `# type: ignore`（无理由的类型作弊）
- 跨模块文件建议 `from __future__ import annotations`（现有模块已普遍如此）

### 4.4 注释规范

- 注释规范见第 7 章「文档维护规范」的 [7.3 注释规范](#73-注释规范)

### 4.5 日志

- 统一用 `loguru.logger`（AstrBot 内部同款，插件层与核心层一致），**每条必须带 `[t2i]` 前缀**；后台安装任务用 `[t2i-install]` 区分
- 错误信息带 emoji 前缀（如 `❌`、`⚠️`）
- 精确捕获异常类型（`asyncio.TimeoutError`、`OSError`、`ValidationError`），禁止空 `except: pass`（除非注释说明理由）
- 异常分类记录：ValueError/ValidationError = 非法参数，OSError = 文件/IO 错误，`PlaywrightNotInstalledError` = 环境未就绪；单条失败不影响主流程
- 高频轮询（清理、连接检测）用 `logger.debug`，不要用 `info` 刷屏

### 4.6 错误处理与防御性编程

- 面对不会按预期工作的输入必须优雅 fallback：配置缺项 → 默认值；浏览器没装 → 透传 `fallback_endpoint`
- 外部 IO 必须 try/except 并记录日志，**不允许静默吞异常**
- 配置值防御式校验：`max(1, int(...))`、端口范围校验、`(TypeError, ValueError)` → 默认值
- 降级链路必须清晰：内嵌渲染 → 透传备用端点 → 明确报错；每级都要能在 `/t2i_status` 里看到

### 4.7 并发

- 异步方法使用 `async/await`，**禁止阻塞事件循环**
- 渲染并发由 `asyncio.Semaphore(concurrency)` 控制（`t2i_core/render.py`），每个并发对应一个 Chromium 页面；不要在别处另起渲染通道绕过它
- 阻塞动作（文件清理、磁盘扫描、子进程安装）走 `asyncio.to_thread` 或子进程
- 后台任务（安装、周期清理）必须能被 `_closing` 一类标志在插件卸载时终止，避免卸载后还在跑

### 4.8 回复消息

- 统一 `yield event.plain_result(...)`
- 用户可读输出用中文 + emoji 图标风格，长文本按行拼接（见 `_status_text`）

### 4.9 权限控制

- 管理类命令加 `@filter.permission_type(filter.PermissionType.ADMIN)`
- **本项目现状偏差（待收紧）**：现有 4 个命令尚未加权限过滤，其中 `/t2i_install`（下载约 200 MB）与 `/t2i_restart`（重启服务）明显属于管理操作。**新增命令凡涉及下载、重启、写配置的，必须直接加 ADMIN**；收紧存量命令属于用户可见的行为变更，需要先在提交说明里提出并由用户确认。

---

## 5. 配置与数据文件

### 5.1 配置 Schema（_conf_schema.json）

顶层六段，与服务分层一一对应：

| 段 | 关键项 | 说明 |
|---|---|---|
| `server` | `enabled`、`host`、`port`、`auth_token`、`fallback_enabled`、`fallback_endpoint` | 内嵌 HTTP 服务 |
| `render` | `concurrency`、`no_sandbox`、`idle_timeout_seconds`、`prelaunch`、`default_type`、`default_quality`、`full_page` | 渲染行为 |
| `browser` | `browsers_path`、`download_host`、`executable_path`、`auto_install_deps`、`cleanup_orphans`、`install_timeout_seconds` | 浏览器供给 |
| `endpoint` | `auto_configure`、`service_url`、`current_service_url`（展示用，插件回写） | 是否写 AstrBot 主配置 |
| `storage` | `image_lifetime_hours`、`cleanup_interval_minutes` | 图片留存与清理 |
| `fonts` | `enabled`、`github_proxy`、`upload_limit_mb`、`restart_browser_on_change` | 字体供给（下载/上传在插件页面的「文转图服务面板」里做） |

- 新增配置项 → 先在 `_conf_schema.json` 补 schema（type/hint/default/options），再在 `t2i_plugin/config.py` 加 dataclass 字段与默认值，最后补 README 配置表。
- 配置项在文档（README 配置表、CHANGELOG）中引用时，**中文名在前、英文 key 在后**：统一写成「中文名（`key`）」形式，例如「并发渲染数（`render.concurrency`）」「鉴权令牌（`server.auth_token`）」；中文名取自 `_conf_schema.json` 的 `hint` 字段，`key` 用反引号包裹。

### 5.2 配置读取模式

- **唯一入口**是 `T2IConfig.from_raw(dict(self._raw_config))`，产出嵌套 dataclass（`ServerConfig` / `RenderConfig` / `BrowserConfig` / `EndpointConfig` / `StorageConfig` / `FontsConfig`）。业务代码读 `self.settings.render.concurrency`，**不要**到处 `self.config.get(...)`。
- `_conf_schema.json` 是默认值的对外契约，`T2IConfig` 的默认值必须与它一致——`tests/test_plugin_config.py` 会守住这条。
- 插件配置只读：`main.py` 把 AstrBot 下发的配置字典交给 `T2IConfig.from_raw`，自身不再散读；插件不写回自己的配置（`_conf_schema.json` 的默认值即最终默认值）。
- **例外之一（`browser.download_chromium` / `browser.chromium_ready`）**：AstrBot 配置页只有开关 + 保存、没有按钮控件，所以「一键下载 Chromium」做成一次性开关——`main.py` 的 `_update_plugin_config` 把它收回，并回写浏览器就绪状态供配置页显示是否已就绪（开关本身常显，不用 `condition` 隐藏）。
- **例外之二（`endpoint.current_service_url`）**：配置页没有只读文本控件，用户看不到插件实际会写入哪个地址，所以启动时由 `_sync_service_url_field()` 把算好的地址回写到该字段；它只用于展示，不参与 `resolve_service_url()` 解析，用户手改的值下次启动会被覆盖。
- 除以上三项外不要再新增回写。

### 5.3 数据文件

- 数据目录：`get_astrbot_plugin_data_path() / PLUGIN_NAME`（AstrBot 官方路径函数，可用 `ASTRBOT_ROOT` 覆盖），插件卸载重装不丢。
- 图片、浏览器都落在该目录下（浏览器目录可用配置 `browser.browsers_path` 另行指定）。
- 写文件用 `encoding="utf-8"` + `ensure_ascii=False`；原子写入先写 `.tmp` 再 replace。
- 过期图片由 `cleanup_expired_files` 按 `storage.image_lifetime_hours` 清理，清理间隔由 `CLEANUP_TICK_SECONDS` 决定。

### 5.4 写 AstrBot 主程序配置

只有「端点自动写入」这一处会改 AstrBot 主配置：

```python
core_config = self.context.get_config()
core_config["t2i_endpoint"] = url
core_config["t2i_strategy"] = "remote"
await _save_config(core_config)   # 优先 save_config_async，旧版回退同步 save_config
```

不要绕过 `_save_config` 直接 `save_config()`，否则新版 AstrBot 上会出现「写进去了但没落盘」的问题。

---

## 6. 指令注册与命令清单

### 6.1 指令注册模式

本项目不使用指令组（`command_group`），四个命令都是扁平注册：

```python
@filter.command("t2i_status")
async def cmd_status(self, event: AstrMessageEvent):
    """查看文转图服务与 Chromium 状态。"""
    yield event.plain_result(self._status_text())
```

### 6.2 命令表

| 命令 | 权限 | 说明 |
|------|------|------|
| `/t2i_status` | 全部 | 查看服务地址、浏览器状态、渲染统计与端点配置 |
| `/t2i_install` | 全部（见 4.9） | 下载并安装 Chromium；`/t2i_install mirror` 走国内镜像 |
| `/t2i_test` | 全部 | 渲染自检卡片并回图，验证整条链路 |
| `/t2i_restart` | 全部（见 4.9） | 重启内嵌渲染服务并释放 Chromium |

---

## 7. 文档维护规范（强制，不可省略）

> 本项目的**硬性要求**：任何改动在合并前，必须同步维护 `README.md` 与 `CHANGELOG.md`。未同步文档 = 任务未完成。

### 7.1 CHANGELOG 维护

#### 流程

- 日常改动先记在 `## [Unreleased]` 段（顶部固定，文件里没有就新增）。**发版时**再把内容移动到带版本号的新段。
- **先记后合**：记录阶段同一工作周期内的改动各自独立记入 `[Unreleased]`（同一功能的多处调整也先各记一条，避免遗漏）；提交或发版前的整理阶段统一过一遍 `[Unreleased]`，把描述同一件事的多条合并成一条，不相关改动保持独立（判断标准：用户会不会把它当成同一件事，细则见下方「写作视角」）。
- **发版日期**：版本段日期 `(YYYY-MM-DD)` 填**当前日期**（以发版当天的系统时间为准，例如 `2026-09-22`）。
- 现存 `## 1.0.0` 段是首个版本的遗留写法（无日期、含「变更（相对上游）」这类开发者视角），**不改历史**；之后的新版本段一律按下面的格式写。

#### 格式

统一使用 **分组式**（与 scaffold 的 `CHANGELOG.md.template` 一致）：按分类用 `###` 子标题分组，条目用「标题 + 冒号 + 说明」，**仅重点条目/重点内容才加粗**：

```markdown
# Changelog

## [Unreleased]

> 一句话总结这个版本（可选）

### 新增
- 功能 A：说明
- **功能 B**：说明

### 修复
- 问题 C：说明

## v0.3.0 (2026-07-06)

> 一句话总结这个版本（可选）

### 新增
- 功能 D：说明
```

分组子标题分类：`新增` / `修复` / `变更` / `移除` / `性能`（只保留本次有内容的分类，无内容的分类不写空标题）

**加粗规则**：条目默认不加粗；写 changelog 时先识别本次改动中的**重点改动**（核心新功能、重要修复、破坏性变更等），只对重点改动用 `**加粗**` 标题强调，其余条目一律不加粗。

**版本总结**：版本标题（`## [Unreleased]` / `## vX.Y.Z (YYYY-MM-DD)`）与分类子标题之间，可加一行 `> 引用` 形式的一句话总结（概括该版本/该批改动的主旨），可选，不强制。

#### 写作视角（面向用户）

CHANGELOG 的读者是**插件使用者**（服务器管理员、群成员），不是开发者。条目只写「用户能感知到的变化」，不写内部实现：

- **写用户视角的结果**：用户能做什么、会看到什么、行为/默认值/配置有什么不同。
- **禁止内部实现细节**：类名/函数名、方法拆分、数据结构、算法、缓存与并发策略、日志埋点、依赖升级、重构过程，一律不写。
- **禁止出现代码标识符与文件路径**（如 `_status_payload`、`t2i_plugin/server.py`）；用户直接接触的**命令名**、**配置项 key** 除外。
- **无用户感知的改动不记入 CHANGELOG**：纯内部重构、格式化、注释与测试调整等，写了用户也读不懂、也用不上。
- **只写相对上一版的变化**：每条回答「这个版本和用户手上的版本比，变了什么」，只讲差异；不复述仍然有效的旧功能，不写「继续优化」「体验更好」这类没有信息量的空话。
- **同一个变化合并成一条**：同一功能的多处调整、跨多个提交的同一件事，在提交或发版前的整理阶段合并为一条；同一内容不要在不同分类或不同版本段里重复出现。
- **一句话说清改了什么**：按「做了什么 + 用户得到什么」写完，不加背景、动机与实现过程，也不用复杂形容；用户扫一眼就能对上自己关心的功能。
- 判断标准：使用者读完一条，能决定「要不要升级、升级后有什么不同」。

正反例：

```markdown
### 新增
- ✅ 自动写入 AstrBot 文转图端点（`endpoint.auto_configure`）：开启后无需手工改配置
- ❌ 新增 `web_apply_endpoint` 并在 `main.py` 里重构 `_save_config` 调用链

### 修复
- ✅ 修复插件重启后浏览器进程残留、内存不释放的问题
- ❌ 修复 `_proc_entries` 未过滤 zombie 状态导致 `chromium_processes()` 计数偏大
```

#### 版本号（准备发布时才确认）

- **日常开发不碰版本号**：只往 `[Unreleased]` 追加条目，`metadata.yaml` 的 `version` 保持原值；每个功能、每个修复各自是一次独立提交，提交里不带版本号变更。
- **准备发布时才确认并递增一次**：一次发版汇总了多个独立的功能/修复提交，等合并到指定版本、把 `[Unreleased]` 内容移入 `## vX.Y.Z (YYYY-MM-DD)` 的同一次操作里，才确认版本号（SemVer 见 8.2）、写进下方的**唯一版本源文件**、打 `git tag vX.Y.Z`。**不能每改一点就递增**——否则版本号会随提交次数突进，与实际发布节奏脱节。
- **唯一版本源**：`metadata.yaml` 的 `version` 字段（`vX.Y.Z` 格式，AstrBot 读取的插件版本）。需要「插件版本」时一律以它为准。

| 版本源文件 | 位置 |
|------|------|
| `metadata.yaml` | `version` 字段（`vX.Y.Z` 格式） |

下列位置属于**辅助信息，不是版本源**，不决定版本号（需要「插件版本」时以 `metadata.yaml` 为准）：`README.md` / `README_en.md` 的版本徽章与正文版本字样、`.astrbot-plugin/i18n` 中的版本文案、页面与状态卡片里展示的版本号。本项目**不使用 `@register`、也不手写版本常量**：`main.py` 在运行时从 `metadata.yaml` 读取版本，因此代码与页面展示天然跟随版本源。日常提交不动这些位置；准备发布时确认 README 徽章与 i18n 文案同步到同一版本号，不得残留旧版本号或自相矛盾的版本字样。

#### 多会话并行开发

**⚠️ 更新 `CHANGELOG.md` 前必须先读取当前文件内容**，识别并保留其他会话已写入的既有条目（含 `[Unreleased]` 下未提交的功能），只追加自己的条目，**严禁整文件覆盖或删除他人记录**。

### 7.2 README 维护

#### 7.2.1 写作视角（面向用户）

README 的读者是**插件使用者**（服务器管理员、群成员），不是开发者。只写用户能直接用到的东西：

- **该写的**：能做什么（能力清单）、怎么装、怎么配（配置表）、怎么用（指令表与示例）、出问题怎么办（常见问题、前置条件与限制）。
- **不该写的**：实现原理与设计理念、架构分层与模块划分、算法与数据结构、内部调用流程、类名/函数名/文件路径、性能优化手段、依赖内部实现。
- **代码只贴用户要用的最小片段**：安装命令、配置项、指令示例；不贴内部实现代码。
- **技术细节翻译成用户语言**：不写「用 `asyncio.Semaphore` 限制并发」，写「同时渲染的页面数量上限（`render.concurrency`），超出后排队」。
- 判断标准：使用者只读 README，就能独立完成安装 → 配置 → 使用 → 排障。

> 本项目 README 现状已大体符合，但有一张「官方端点 / 独立容器 / 本插件」对比表与一段上游背景说明，属于功能级对比，可以保留；**不要再往里加架构图、模块职责或渲染流程说明**——那些内容属于 `docs/` 与 `NOTICE`。

#### 7.2.2 头部的表头格式

每个插件维护 **两个** README（中文 `README.md` + 英文 `README_en.md`），**中文版为权威版本（源）**，任何修改先落中文版、再同步到英文版（方向规则见 7.2.5）。表头格式如下（**元素间不留空行**）：

**中文版（`README.md`）：**

```html
<div align="center">
<h1>DisplayName</h1>
<p><strong>插件描述（desc）</strong></p>
<p><sub>标签1 &nbsp;&nbsp; 标签2 &nbsp;&nbsp; 标签3</sub></p>
<p><strong>中文</strong> &nbsp;/&nbsp; <a href="README_en.md">English</a></p>
</div>
```

**英文版（`README_en.md`）：**

```html
<div align="center">
<h1>Plugin Name</h1>
<!-- 英文 h1 取插件名后缀首字母大写：astrbot_plugin_model_status → Model Status -->
<p><strong>Plugin description</strong></p>
<p><sub>tag1 &nbsp;&nbsp; tag2 &nbsp;&nbsp; tag3</sub></p>
<p><a href="README.md">中文</a> &nbsp;/&nbsp; <strong>English</strong></p>
</div>
```

命名规则：

- **中文版 h1**：直接取 `metadata.yaml` 的 `display_name` 字段（本项目为 `文转图服务 (T2I Service)`，取中文主名「文转图服务」）。
- **英文版 h1**：取插件名 `astrbot_plugin_xxx` 中的 `xxx` 部分，拆分为单词后每个首字母大写。本项目为 `T2I Service`。
- **desc**：中文版用 `metadata.yaml` 的 `desc` 字段；英文版做地道翻译。
- **标签（tags）**：取自 `metadata.yaml` 的 `tags` 字段（若有），用 `&nbsp;&nbsp;` 分隔。中文版用中文标签，英文版用英文标签。

> 本项目现有两个 README 使用 `# 标题` + 一行语言切换的简化头部，与上面的 HTML 头部等价但更朴素；**改头部时保持现有形态即可，不要为了套模板去重排已有文档**。新增插件页面或新插件才从上面的 HTML 头部起步。

#### 7.2.3 README 四大结构

README 正文严格按以下 4 大板块组织，但不强制使用 `##` 标题层级，可灵活组合：

| 板块 | 内容 | 说明 |
|------|------|------|
| **一、功能/介绍** | 功能介绍、特性列表、用户使用场景、截图/演示 | 独立板块 |
| **二、指令** | 命令表格（命令、权限、说明）、使用示例 | 独立板块 |
| **三、配置** | 配置项表格（配置项、类型、默认值、说明）、`_conf_schema.json` 说明 | 独立板块 |
| **四、其它** | 安装、快速开始、运行环境与依赖要求、贡献指南、常见问题、许可证、鸣谢等 | 剩余所有内容放这里 |

注意点：

- 板块 4 是兜底区域，不强制子标题顺序，但**安装/快速开始**建议放在最前。
- **板块一/板块四只写用户视角**：写「运行环境与依赖要求」而非框架选型理由、架构分层、模块职责；实现原理一律不进 README（写作视角见 7.2.1）。
- 更新后通读一遍 README，确保与新行为一致（示例：默认值、超时说明、数据目录、上游归属说明等）。

#### 7.2.4 何时必须更新 README

| 改动类型 | 必须更新的板块 |
|----------|--------------|
| 功能新增/删除 | 一、功能/介绍 |
| 使用方式/命令变化 | 二、指令 |
| 配置项变化 | 三、配置 |
| 依赖/版本要求变化 | 四、其它（安装/依赖部分） |
| 已知问题/行为变化 | 四、其它（常见问题部分） |

**何时不需要更新 README：**

- 纯内部重构（行为完全不变）、样式调整、bug 修复（除非修复改变了用户可见行为）

#### 7.2.5 双语文档同步

- 中文版用 `README.md`，英文版用 `README_en.md`。
- **中文为主（单向同步）**：中文版是**权威版本（源）**，英文版跟随中文版。所有改动**先落中文版，再从中文同步到英文版**；**禁止反向**——不得先改英文版再回填中文，不得用英文版覆盖中文版。
- 章节结构必须保持一致，新增章节两个文件都要加。
- 中文版使用自然中文，英文版使用地道英文，**禁止**逐字机翻、禁止留下半翻译的句子。
- 头部的语言切换链接互相指向对方文件。
- 修改时逐条 diff，确保无内容遗漏或错位。
- `docs/standalone/` 下的上游文档（`README.md`、`README_zh-CN.md`、`README_ja.md`、`MONITORING.md`）是**第三方原文**，只跟随上游更新，不参与本项目的双语同步。

#### 7.2.6 格式与多会话约束

- **禁止无意义的换行**：HTML 头部区块内元素间不留空行；正文长句不要人为软换行（一段文字保持一行，由 Markdown 自动折行显示）；空行只用于分隔标题、表格、列表、代码块等结构性区块。
- **多会话并行修改**：更新 `README.md` / `README_en.md` 前必须先读取当前文件内容，识别并保留其他会话已写入的既有内容，只追加/修改自己的部分，**严禁整文件覆盖或删除他人记录**。

### 7.3 注释规范

- **注释只写 non-obvious reason**：解释「为什么这么做」，而不是「做了什么」（代码本身应自文档化）
  - 坏：`# 设置超时时间为30秒`（代码已写 `timeout = 30`）
  - 好：`# 插件目录入 sys.path，因为 AstrBot 以包路径加载 main.py，子模块不一定可导入`
- **禁止残留 intermediate attempts**：不保留被注释掉的旧代码、TODO 但未完成的方案、调试输出
- 方法级 docstring 用中文说明用途/参数/返回（仅对 `_` 开头私有方法可省略，如果用途足够明显）
- 代码分区用 `# ---- 分区名 ----` 分隔条（`main.py` 已按此组织：状态信息 / 命令 / 生命周期）

### 7.4 PR 描述规范

- **PR 描述只写最终行为**：描述最终完成了什么，而不是过程中尝试了什么
- 禁止提及：尝试过的方案、被否定的设计、从未合入的中间状态
- 只写 diff 里看不出来的取舍理由（如「选 aiohttp 而不是再起 FastAPI，因为要复用 AstrBot 已有事件循环，省一个端口与进程」）

---

## 8. 提交与发布流程

### 8.1 提交规范

- 提交信息遵循 **Conventional Commits**：`<type>(<scope>): <中文短句式 subject>`（如 `feat: 开放卡片配色配置项`、`fix: 失败透传后补重试`、`docs: 补充配置说明`）
- **禁止**将未完成的功能、多轮迭代的中间状态、被废弃的方案写进提交信息
- **提交前必须先向用户展示，等用户确认后再执行 commit**，展示内容缺一不可：
  - 待提交文件清单
  - 改动概要（如 `git diff --stat`）
  - **提交标题，尽量给 2–3 个候选**（同一改动的不同侧重点：做了什么 / 为什么改 / 是否带 scope），逐条注明侧重供用户挑选并标出推荐项；确实只有一种合理写法时才只给一个
  - 用户选定标题（或自拟）后才 commit；涉及推送时另附提交 hash、提交信息、目标分支与改动概要
- 推荐顺序：完成功能代码 → 跑通验证 → 更新 CHANGELOG 与 README → 提交
- 文档更新与代码改动**在同一提交中完成**（不单独拆「补文档」提交）
- 只有用户明确要求时才 commit / push
- **授权一次性，不延续**（「上一轮说过推送」≠ 本轮可以提交）：
  - 用户的确认**只对当次动作生效**；下一次改动必须重新展示改动内容与提交标题，重新确认
  - 展示后改动内容又变了（用户提了新要求、你又改了文件，哪怕只改一行），**原确认立即失效**，必须重新展示并再次确认
  - 下列情形**都不算授权**：上一轮 / 上一个任务 / 上一个会话说过「提交」「推送」；会话恢复（resume / fork）后继承的历史授权；用户对功能或代码表示满意；用户正在讨论其它需求；用户未回复
  - 授权范围逐项独立：确认 commit ≠ 授权 push；确认推送某分支 ≠ 授权后续继续提交或推送；确认一个提交 ≠ 授权 `--amend` 或追加提交
  - 执行前自检：能指出用户**本轮**对**本次改动**的明确同意原话；指不出来就先问，不要执行

### 8.2 版本号规范

- 采用 **SemVer**：新功能 → `minor` 递增；bug 修复/小改进 → `patch` 递增
- **只在准备发布时递增**（规则见 7.1）：单个功能/修复提交不碰版本号；一次发版汇总多个独立提交、合并到指定版本时才确认版本号，改 `metadata.yaml` 的 `version`，并把 README 徽章、i18n 等辅助位置同步到同一版本号——逐次递增会让版本号突进
- 发版一致性：`metadata.yaml` version = CHANGELOG 新版本段号 = `git tag vX.Y.Z`，tag 推送同样需用户确认

### 8.3 测试与验证

- 本仓库**有测试**，改动后必须让它通过：
  - 全量：`python -m pytest tests -q`
  - 只跑插件层：`python -m pytest tests/test_plugin_*.py -q`
  - 语法自检：`python -m compileall -q main.py t2i_core t2i_plugin service tests`（CI 也跑这一步）
- 插件层测试会 import AstrBot（`astrbot.api.*`），环境里没有 AstrBot 时整组失败——这不是代码问题；用 `docs/ENV-REPORT.md` 里的容器基线跑。
- 涉及真实渲染（浏览器、镜像源、下载）的改动，至少手工跑一次 `tools/envlab/render_baseline.py` 或容器内的集成脚本，并把关键数字写进提交说明。
- 本项目**没有 ruff 配置**，不要引入格式化提交；保持与周边代码的既有风格一致即可。

---

## 9. 最终检查清单（每次任务完成前）

- [ ] 架构：新代码放在 2.2 表格指定的落点；`t2i_core/` 没有新增 `astrbot` 依赖
- [ ] 双形态：插件模式与独立服务模式都还能起来，HTTP 契约未被插件层私有改动
- [ ] 注释：只写 non-obvious reason，无残留中间状态、无被注释掉的旧代码
- [ ] 日志：`[t2i]` 前缀，异常分类记录，无空 catch，高频轮询用 debug
- [ ] 配置：新增配置项已在 `_conf_schema.json`、`t2i_plugin/config.py` 与 README 配置表三处同步
- [ ] 权限：新增的下载/重启/写配置类命令已加 `@filter.permission_type(filter.PermissionType.ADMIN)`
- [ ] CHANGELOG：`[Unreleased]` 已追加条目，**只写相对上一版的变化**、同一变化已合并为一条（无重复条目），条目面向用户（无内部实现细节、无复杂形容即可看懂），保留了他人的既有记录
- [ ] README：涉及用户可见变更时已同步更新（中文先落，再同步英文），且面向用户书写（无实现原理与内部细节）
- [ ] 版本号：日常任务不递增版本号；仅发版任务改 `metadata.yaml` 的 `version`（与 CHANGELOG 新版本段、tag 一致），并把 README 徽章、i18n 等辅助位置同步到同一版本号
- [ ] 提交信息：Conventional Commits 格式（`type: 中文短句式`）
- [ ] 提交前展示：已给出待提交文件清单、`git diff --stat`、2–3 个候选提交标题（逐条注明侧重并标出推荐项），并取得用户对本次改动的明确确认（一次性授权，未沿用上一轮）
- [ ] 本地测试：`python -m pytest tests -q` 通过，`python -m compileall` 无语法错误
