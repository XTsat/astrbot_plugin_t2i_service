# Changelog

## [Unreleased]

> 浏览器装好了却渲染不起来，现在插件自己查缺哪个系统库并补装。

### 新增

- **配置页一键下载 Chromium**：在插件配置里打开「下载 Chromium 浏览器」开关并保存即开始下载，不需要再发 `/t2i_install`；下载结束后开关自动关闭，装好的浏览器状态下一次打开配置页即不再显示这个开关。
- **缺系统运行库自动补装**：Chromium 已下载但缺系统库（报 `error while loading shared libraries: libnspr4.so` 一类错误）时，`/t2i_install` 会自动补装依赖，装完自动复查是否还有缺库；环境不满足（非 root、无 `apt-get`/`dnf`）时只给出可手动执行的命令，不报错。
- 系统依赖可见：`/t2i_status`、状态卡片与插件页面新增「系统依赖」一行，缺哪个库直接列出来，并显示补装进度与结果。
- 配置页直接给出本插件地址（`endpoint.current_service_url`）：随监听地址与端口自动更新，抄进 AstrBot 的「文本转图像服务 API 地址」即可，不用自己拼 `http://127.0.0.1:8999/text2img`。

### 修复

- 备用端点改为可用的官方公共端点 `https://t2i.rcfortress.site/text2img`：原默认端点已无可用后端，透传会直接 503；配置里仍留着旧默认值的会在启动时自动换成新端点（自己填的地址不动）。
- 透传的图片改为下载到本地再返回：以前把上游的图片 id 原样回给 AstrBot，取图必然 404；现在返回本地 id，AstrBot 能正常拿到图片。
- 渲染失败不再甩一大段浏览器启动日志：缺系统运行库时提示缺哪个库、怎么修。
- 安装与补装进行中的提示带上进度：`/t2i_install` 撞上进行中的任务时继续推送进度，不再只回一句「已有任务」；`/t2i_test` 与页面自检失败时会说明是正在补装、还是该发哪条命令来修。
- 插件重载后端口被上一次加载占用、状态显示「服务未运行」的问题：新加载的实例会先停掉遗留的同端口服务再绑定；端口被别的程序占用时直接提示重启 AstrBot。
- 浏览器是否可用改为对着实际渲染所用的浏览器二进制判断，避免显示「已安装」却一渲染就崩。

### 变更

- 缺系统库自动补装（`browser.auto_install_deps`）默认值从关闭改为开启：检测到缺库时启动即补，正常环境不会触发。

## 1.0.0

首个插件版本：以 [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service) 为基底，把文转图渲染服务移植为 AstrBot 进程内插件。

### 新增

- 插件入口 `main.py`：AstrBot 生命周期挂载、`/t2i_status` `/t2i_install` `/t2i_test` `/t2i_restart` 四个命令、7 个 Web API。
- `t2i_plugin/` 适配层：
  - `config.py`：嵌套配置解析与默认值、`AstrBotConfig` 兼容；
  - `browser.py`：Chromium 检测 / 下载（镜像源、进度、超时）/ 孤儿清理 / `/proc` 进程扫描；
  - `server.py`：基于 aiohttp 的内嵌渲染服务，与上游 `/text2img/generate`、`/text2img/data/{id}`、`/healthz` 契约一致，支持鉴权与失败透传；
  - `card.py`：状态卡片与自检卡片渲染。
- WebUI 插件页面 `pages/t2i/index.html`（状态、下载进度、渲染测试、应用端点、重载）。
- `_conf_schema.json` 配置模式（server / render / browser / endpoint / storage 五组）。
- `metadata.yaml` 插件元数据（状态卡片与插件页面展示的版本以此为唯一来源；插件「更新」从其中的仓库地址拉代码）。
- 独立服务模式保留：`service/main.py` + `Dockerfile` + `requirements-standalone.txt`。
- 测试：`tests/test_plugin_*.py`（配置、浏览器供给、内嵌服务、卡片、插件入口）与上游测试迁移，共 64 个用例。
- 文档：`docs/APIS.md`、`docs/PAGES-API.md`、`docs/ENV-REPORT.md`、`tools/envlab/` 验证脚本。
- Apache-2.0 合规：保留上游 `LICENSE`，新增 `NOTICE`。

### 变更（相对上游）

- `src/*` → `t2i_core/*`，配置由环境变量改为插件配置对象；独立服务模式仍读环境变量。
- `main.py` → `service/main.py`（独立服务入口）。
- 上游 README / MONITORING 文档移入 `docs/standalone/`。
- `requirements.txt` 拆分：插件运行时仅 `playwright`，独立服务依赖移入 `requirements-standalone.txt`。

### 实测

- 真实 Chromium 1248 端到端：冷启动首图 1.08 s，预热 0.63 s，JPEG 约 7 KB，状态卡片 PNG 约 267 KB。
- AstrBot 自身 `NetworkRenderStrategy` 调用插件端点成功返回 JPEG。
