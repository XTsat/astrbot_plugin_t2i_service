<div align="center">
<h1>AstrBot 文转图服务 (T2I Service)</h1>
<p><strong>在 AstrBot 进程内提供 HTML/模板转图片渲染服务，可作为 AstrBot 的文转图端点，无需再单独部署容器</strong></p>
<p><sub>文转图 &nbsp;&nbsp; t2i &nbsp;&nbsp; 渲染 &nbsp;&nbsp; text2image</sub></p>
<p><strong>中文</strong> &nbsp;/&nbsp; <a href="README_en.md">English</a></p>
</div>

## 功能

渲染能力移植自 [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service)：不用再单独部署一个容器，AstrBot 的文转图开箱即用，HTML / Jinja2 模板在 AstrBot 自己的进程里渲染成图片。

- **不用另起容器**：渲染服务跑在 AstrBot 进程内，没有额外的 Docker、端口映射和镜像要维护。
- **浏览器一键到位**：在配置页打开「下载 Chromium 浏览器」并保存即开始安装（默认走 npmmirror 镜像，约 200 MB）；系统里已有 Chromium 时也可以直接指过去复用。
- **缺库自动补装**：Chromium 装好了却启动不了（缺 `libnspr4` 一类的系统运行库）时，插件会查出缺哪个库并自动补装；环境不支持时给出可手动执行的命令。
- **字体自己带**：渲染缺字体不用装进系统，插件页面里点几下就能下载预置字体、按链接下载任意字体、或上传自己手上的字体文件，装完直接渲染一张预览图确认生效。
- **端点不用手抄**：可选由插件把自己地址写进 AstrBot 的文转图配置，连「文本转图像输出」总开关一起打开。
- **渲染不中断**：本地渲染暂时不可用（例如浏览器还没装好）时，请求自动透传给备用端点，图片落到本地后再交给 AstrBot。
- **状态一眼看清**：`/t2i_status` 汇总服务、浏览器、系统依赖、端点与渲染统计；`/t2i_test` 现场出一张卡片，链路通不通当场就能看到。
- **接口与上游一致**：AstrBot 主程序和任何兼容客户端都不用改代码。

| 对比项 | 官方公共端点 | 自建独立服务 | 本插件 |
| --- | --- | --- | --- |
| 额外部署 | 无 | 需要 Docker | 无 |
| 通信路径 | 公网 | 本机 / 内网 | AstrBot 进程内 |
| Chromium 供给 | 远端负责 | 自己维护镜像 | 配置页一键下载 |
| 与 AstrBot 一起升级 | 无关 | 需手动同步 | 随 AstrBot 一起 |

## 指令

**安装不必发指令**：在 WebUI 的插件配置页打开「下载 Chromium 浏览器」并保存，就会在后台开始下载安装（默认走 npmmirror 镜像，约 200 MB），装完开关自动关闭，同页的「Chromium 已就绪」显示安装结果。下面的指令是同一套能力的聊天入口，用哪个都行。

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `/t2i_status` | 所有人 | 查看内嵌服务地址、Chromium 与系统依赖状态、渲染统计，以及 AstrBot 当前的文转图端点与输出开关 |
| `/t2i_install` | 所有人 | 下载安装 Chromium；浏览器已装好但缺系统运行库时改为补装依赖，过程中持续推送进度 |
| `/t2i_test` | 所有人 | 渲染一张自检卡片并回图，验证从请求到出图的整条链路 |
| `/t2i_restart` | 所有人 | 重启内嵌渲染服务并释放 Chromium |
| `/t2i_fonts` | 所有人 | 查看字体目录现状，并渲染一张字体预览图 |

首次使用想全程在聊天里搞定，就按 `/t2i_status` → `/t2i_install` → `/t2i_test` 走一遍：先看缺什么，装完再出一张图确认。

## 渲染字体

模板里写了 `Noto Serif SC`、`Inter` 这类字体名，但服务器上未必装了；缺字体时模板会退回默认字体，排版就走样。插件把字体供给也管起来：字体文件放在插件数据目录的 `fonts/` 下，渲染时自动被 Chromium 读到，**不用改模板，也不用往系统字体目录里装**。

界面入口：WebUI 插件详情页 →「渲染字体」页面。页面里分两栏切换：**概览**（字体库统计与任务进度，外加上传、贴链接、删除、预览自检）和 **对接项目**（按对接项目的排版需求整理的整套模板字体与单家族清单，栏里再用二级栏位切到具体项目名），正文里也给了互相跳转的链接（`fonts.enabled` 关着时页面会提示）。页面跟着 AstrBot 控制台走：配色随控制台的浅色/深色主题切换，文案随界面语言切换（中文/英文）；顶部一排小标签显示字体是否已生效（功能关着时显示已关闭）与进行中的任务，加载失败时给出原因和「重试」。

**概览**（统计、任务、上传、链接下载、删除、预览）

- **上传自己的字体**：页面直接选文件上传，会先校验是不是真的字体文件；单个文件上限由 `fonts.upload_limit_mb` 控制。
- **按链接下载**：把自己找到的字体直链粘进来即可（支持 `.ttf` / `.otf` / `.ttc`）。
- **确认生效**：「渲染预览图」会用已装字体渲染一张对照卡片，同一句话按每个字体各渲一次——哪一行字形没变化，就说明那个字体没生效。卡片上方列出了已装字体的名字，点选几个就只对比这几个，一个都不点就是全部已装字体；右上角的清晰度可选标准/高清/超清，越清晰图片越大。
- **删改即时生效**：新增或删除字体后插件会自动重启渲染进程（Chromium 只在启动时读字体），也可以点页面顶部的「重新加载字体」。

**对接项目**（预置清单、模板整套）

- **预置字体一键下载**：清单按家族列出（Noto Sans/Serif 简繁日、Inter、JetBrains Mono、霞鹜文楷等），点「下载」即开始，进度条和日志实时显示，装完不用重启插件。
- **按模板一键下载整套**：对接项目 [astrbot_plugin_qq_group_daily_analysis](https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis/blob/main/docs/T2I_FONTS.md)（群聊日常分析）的报告模板各自需要哪几个字体，这里已经对应好了——选它的模板名（如「排版」「剪贴簿」「春节」「ATRI」）就能一次装齐，缺哪几个也直接标出来。

字体目录结构是「一个家族一个子目录」，`OFL.txt` 之类的授权文件跟字体放在一起，方便分发时说明来源。国内直连 GitHub 极慢，预置字体默认走内置的镜像与加速前缀，二十多 MB 的中文字体也能正常下完；有自己的加速前缀可以在配置页填写。

## 配置

配置项在 WebUI 插件配置页按下面五组展示，「默认值」就是首次安装后实际生效的值。

### 安装 Chromium 浏览器（`browser`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 下载 Chromium 浏览器（`download_chromium`） | 开关 | 关 | 打开并保存配置即在后台下载安装，不需要发指令；下载结束后开关自动关闭（成功或失败都关），已就绪时打开也不会重复下载 |
| Chromium 已就绪（`chromium_ready`） | 开关 | 关 | 插件按本地浏览器实际状态自动写入，仅供查看：开启表示可以直接渲染，手动修改会在下次启动时被覆盖 |
| 浏览器安装目录（`browsers_path`） | 文本 | 空 | 留空则放在插件数据目录的 `browsers/`，卸载重装插件不会丢 |
| Playwright 浏览器下载源（`download_host`） | 文本 | `https://cdn.npmmirror.com/binaries/playwright` | 默认走 npmmirror 镜像，国内下载更快；留空则用官方 CDN |
| 自定义 Chromium 可执行文件路径（`executable_path`） | 文本 | 空 | 想复用系统或其它容器里已有的 Chromium 时填写，填了就不再下载浏览器 |
| 检测到 Chromium 缺少系统运行库时自动补装（`auto_install_deps`） | 开关 | 开 | 缺库时自动补装，需要 root 与 `apt-get`/`dnf`；环境不支持只提示，不报错 |
| 插件启动时清理残留 Chromium（`cleanup_orphans`） | 开关 | 开 | 插件被强杀后可能留下浏览器进程，下次启动时回收 |
| 浏览器安装超时（`install_timeout_seconds`） | 数字 | `900` | 网络较慢时可调大，单位秒 |

### 内嵌渲染服务（`server`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 启用内嵌渲染服务（`enabled`） | 开关 | 开 | 关闭后只保留指令，不再监听端口（此时请自行部署独立服务） |
| 监听地址（`host`） | 文本 | `127.0.0.1` | 插件与 AstrBot 同进程，保持回环即可；只有要被 AstrBot 之外的机器或容器访问时才改成 `0.0.0.0`，并建议同时设置鉴权 Token |
| 监听端口（`port`） | 数字 | `8999` | 与上游服务保持一致；换端口后记得同步 AstrBot 的文转图端点地址 |
| 鉴权 Token（`auth_token`） | 文本 | 空 | 留空表示不校验；填写后请求需带 `Authorization: Bearer <token>` |
| 本机渲染不可用时透传上游公共端点（`fallback_enabled`） | 开关 | 开 | 如 Chromium 还没装好时，请求自动转发到备用端点，保证聊天里的文转图不中断 |
| 透传的备用端点（`fallback_endpoint`） | 文本 | `https://t2i.rcfortress.site/text2img` | 需包含 `/text2img`；图片会先下载到本地再返回，也可填自建服务地址 |

### 接入 AstrBot 文转图（`endpoint`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 本插件当前地址（`current_service_url`） | 文本 | 空 | 插件按监听地址与端口自动写入（默认 `http://127.0.0.1:8999/text2img`），抄进 AstrBot 的「文本转图像服务 API 地址」即可；仅供查看，手改会在下次启动时被覆盖 |
| 启动时自动写入 AstrBot 文转图端点（`auto_configure`） | 开关 | 关 | 开启后启动时把端点写成上面展示的地址，并把策略设为 `remote`；重启 AstrBot 生效 |
| 自动写入时同时打开「文本转图像输出」总开关（`enable_t2i_output`） | 开关 | 开 | 总开关（`t2i`）关着时文转图不会工作，打开本项让自动写入一并打开它；只想改地址就关掉 |
| 写入的端点地址（`service_url`） | 文本 | 空 | 仅在 AstrBot 通过别的主机名/端口访问本插件时才需要填写；留空则按监听地址自动生成 |

### 渲染行为（`render`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 并发渲染数（`concurrency`） | 数字 | `2` | 同时渲染的页面数量上限，超出后排队；建议 1–4，过大会明显吃内存 |
| 以 `--no-sandbox` 启动 Chromium（`no_sandbox`） | 开关 | 开 | AstrBot 官方镜像以 root 运行，Chromium 必须带 `--no-sandbox` 才能启动；非 root 部署且在意沙箱隔离时可以关掉 |
| 空闲多久后关闭 Chromium（`idle_timeout_seconds`） | 数字 | `300` | 关闭后释放进程内存，下次渲染冷启动约 1–2 秒；填 `0` 表示从不主动关闭 |
| 插件启动时预热浏览器（`prelaunch`） | 开关 | 关 | 打开后第一条请求不用等冷启动，代价是常驻一个 Chromium 进程 |
| 默认输出格式（`default_type`） | 下拉 | `jpeg` | 请求未指定 `options.type` 时使用；`jpeg` 体积更小，`png` 更清晰 |
| JPEG 默认质量（`default_quality`） | 数字 | `40` | `0`–`100`，仅对 jpeg 生效，40–80 比较均衡 |
| 默认整页截图（`full_page`） | 开关 | 开 | 关闭后只截可视区域（默认 1080p 高度），长文本可能被截断 |

### 图片清理（`storage`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 渲染图片保留时长（`image_lifetime_hours`） | 数字 | `24` | AstrBot 取走图片后会自行转存，插件侧过期的图片会被定时删除 |
| 清理间隔（`cleanup_interval_minutes`） | 数字 | `60` | 仅供展示参考：插件实际每分钟检查一次并删除过期文件 |

### 渲染字体（`fonts`）

字体文件的下载、上传、删除都在插件页面的「渲染字体」里做，配置页只留开关与来源设置。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 启用字体供给（`enabled`） | 开关 | 开 | 关掉后不再生成字体配置、也不加载 `fonts/` 里的字体，字体页面的下载与上传接口一并停用 |
| GitHub 下载加速前缀（`github_proxy`） | 文本 | 空 | 留空时已自动尝试内置的 jsDelivr 镜像与两个加速前缀；只有它们都不通时才需要填自己的前缀（形如 `https://你的前缀/`） |
| 单个字体文件上限（`upload_limit_mb`） | 数字 | `128` | 上传与按链接下载的单文件大小上限，中文字体常见 10–30 MB |
| 字体变动后重启浏览器（`restart_browser_on_change`） | 开关 | 开 | Chromium 只在启动时读字体，装完字体需要重启渲染进程才生效；关掉后要等它空闲被回收 |

## 安装与首次使用

1. 在 AstrBot WebUI 的插件市场安装本插件，或把本仓库放进 `data/plugins/astrbot_plugin_t2i_service`（目录名必须与插件名一致）。
2. 安装过程只会 `pip install playwright`（约 40 MB 的 Python 包），**不会**自动下载 Chromium。
3. 重启 AstrBot，确认插件配置里「启用内嵌渲染服务」为开、「监听端口」为 `8999`（或你想要的端口）。
4. 在插件配置页打开「下载 Chromium 浏览器」并保存，即开始在后台下载安装（约 200 MB，默认走 npmmirror 镜像）；也可以在聊天里发送 `/t2i_install`。若容器缺系统运行库，安装流程会接着补装。
5. 发送 `/t2i_test` 验证出图，成功后文转图即可用。想让插件自动接管 AstrBot 的端点，再打开「启动时自动写入 AstrBot 文转图端点」。

渲染用的图片落在插件数据目录 `data/plugin_data/astrbot_plugin_t2i_service/data/`，默认保留 24 小时后自动清理；Chromium 落在同目录的 `browsers/`。

## 让 AstrBot 用上本插件

插件提供与上游一致的文转图端点，AstrBot 需要知道它的地址：

- **查地址**：配置页「接入 AstrBot 文转图」里的「本插件当前地址」就是要填进 AstrBot「文本转图像服务 API 地址」的值（默认 `http://127.0.0.1:8999/text2img`，改监听地址或端口后自动更新）。
- **自动**：打开「启动时自动写入 AstrBot 文转图端点」，插件会写入该地址并切到远程策略；「文本转图像输出」总开关（`t2i`）由「自动写入时同时打开『文本转图像输出』总开关」控制（默认打开）——总开关关着时文转图不会生效。
- **手动**：在 AstrBot 的 `data/cmd_config.json` 里确认：

```jsonc
{
  "t2i": true,
  "t2i_strategy": "remote",
  "t2i_endpoint": "http://127.0.0.1:8999/text2img"
}
```

改完重启 AstrBot 生效。`/t2i_status` 会显示当前端点、策略与「文本转图像输出」开关状态。

## 运行环境与依赖

- **AstrBot 4.16 及以上**（5.x 以下）：插件用到的插件配置、事件与日志接口来自该版本区间。
- **Python 3.11 及以上**。
- **`playwright`（固定 1.64.1）**：随插件依赖自动安装，无需手动处理。
- **Chromium**：由插件自己下载管理（约 200 MB 磁盘）；也可以指向系统里已有的浏览器。
- **自动补装系统运行库**需要 AstrBot 进程为 root，且容器内有 `apt-get` 或 `dnf`。
- **内存**：每个并发渲染占用一个浏览器页面，`render.concurrency` 越大占用越高。

## HTTP 接口

需要自己调用时（AstrBot 会自动调用，一般不操心）：

- `POST /text2img/generate` —— 请求体带 `html`，或 `tmpl` + `tmpldata`，可选 `options`
- `GET /text2img/data/{image_id}` —— JSON 模式下取回图片
- `GET /healthz` —— 健康检查

接口与上游一致，完整契约见上游文档 [`docs/standalone/README.md`](docs/standalone/README.md)。

## 独立服务模式（可选）

插件形态下渲染就跑在 AstrBot 进程里，不存在「插件在宿主、AstrBot 在容器」这种组合。下面这套只在**不想用插件、想单独跑一个服务**时才有用（比如给多台 AstrBot 共用，或与 AstrBot 分机器部署），上游能力完整保留：

```bash
docker build -t astrbot-t2i-service .
docker run -d -p 8999:8999 astrbot-t2i-service
```

环境变量、S3/R2 存储、Prometheus 指标等与上游一致，见 [`docs/standalone/README.md`](docs/standalone/README.md) 与 [`docs/standalone/MONITORING.md`](docs/standalone/MONITORING.md)。

独立服务部署在 AstrBot 之外时，AstrBot 侧的「文本转图像服务 API 地址」要写**对方可达**的地址（同机可用 `http://127.0.0.1:8999/text2img`，跨容器/跨机器用宿主机内网 IP 或域名），并在服务端配好鉴权，别把端口直接裸露在公网。

## 常见问题

**Chromium 下载失败或很慢**
默认已走 npmmirror 镜像；也可以改「Playwright 浏览器下载源」，或把「浏览器安装目录」指到已有的浏览器目录。配置页的「下载 Chromium 浏览器」开关在下载结束后会自动关闭：开关关掉但「Chromium 已就绪」仍为关，说明这次没下成，去 `/t2i_status` 看安装状态与日志尾巴。

**渲染报 `error while loading shared libraries: libnspr4.so` 或 `BrowserType.launch: Target page, context or browser has been closed`**
这是容器缺 Chromium 的系统运行库（浏览器本体已下载，但系统里没有它依赖的 `libnspr4`、`libnss3`、`libatk` 等）。发送 `/t2i_install` 即可自动补装（需要 AstrBot 进程为 root 且容器内有 `apt-get`/`dnf`）；环境不满足时会给出可手动执行的命令。

**启动浏览器报 `No usable sandbox!`**
AstrBot 官方镜像以 root 运行，保持「以 `--no-sandbox` 启动 Chromium」为开即可。

**备用端点报错或出图很慢**
Chromium 没装好时请求会透传给「透传的备用端点」（默认官方公共端点 `https://t2i.rcfortress.site/text2img`，在境外，排队久了会返回 `render queue wait exceeded`；旧版本留下的已停用端点会在启动时自动换成当前默认值，自己填的地址不动）。装好本地 Chromium 后就不再走它；也可以把备用端点改成自建服务地址，或关掉「本机渲染不可用时透传上游公共端点」。

**端口被占用**
改「监听端口」，然后重新打开「启动时自动写入 AstrBot 文转图端点」，或手动更新 AstrBot 的 `t2i_endpoint`。

**重启 AstrBot 后文转图报错**
确认 `t2i_endpoint` 仍指向插件的监听地址；改了端口忘记同步时，`/t2i_status` 会显示插件的实际端点。

**装了字体但模板还是用了别的字体**
先在「渲染字体」页的「概览」栏点「渲染预览图」（想只对比某几个字体就先点它们的名字）：每行样句对应一个字体，若几行长得一模一样，说明这些字体都没生效。常见原因是刚装完还没重启渲染进程（配置项「字体变动后重启浏览器」关掉时不会自动重启，可点页面上的「重新加载字体」），或者字体文件本身是改名的网页/压缩包——页面在上传与下载时都会校验字体文件头。另外，模板要的是 `Noto Sans SC`，系统里的 `Noto Sans CJK SC` 是另一套名字，两者不能互替。

**占用多少磁盘**
Chromium 约 200 MB（在插件数据目录），渲染图默认保留 24 小时并定时清理；字体按家族累加，装一整套模板字体通常 20–60 MB。

**日志在哪**
渲染与清理日志和插件其它日志归在一处，在 AstrBot 日志里按插件名或 `[t2i]` 前缀过滤即可；正常渲染不打印 HTML 内容。每次生成都会留一行访问记录（`POST /text2img/generate → 状态码`，带耗时与请求体大小）；渲染失败时会连失败原因和堆栈一起记下来，排查按时间点对这两行即可。

## 开发与测试

```bash
pip install -r requirements-standalone.txt pytest pytest-asyncio
pytest tests/ -q
```

- 插件测试不需要真实 Chromium（渲染器为假实现）；真实渲染的端到端验证脚本在 `tools/envlab/`。
- `requirements.txt` 只含插件运行时依赖（`playwright`）；独立服务模式使用 `requirements-standalone.txt`。

## 许可证与鸣谢

Apache-2.0，与上游一致。渲染核心移植自 [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service)，仓库保留上游 `LICENSE`，移植与改动说明见 [`NOTICE`](NOTICE)。
