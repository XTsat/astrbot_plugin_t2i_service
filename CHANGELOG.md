# Changelog

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
- `metadata.yaml` 插件元数据。
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
