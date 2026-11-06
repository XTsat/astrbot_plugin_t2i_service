<div align="center">
<h1>AstrBot 文转图服务 (T2I Service)</h1>
<p><strong>在 AstrBot 进程内提供 HTML/模板转图片渲染服务，可作为 AstrBot 的文转图端点，无需再单独部署容器</strong></p>
<p><sub>文转图 &nbsp;&nbsp; t2i &nbsp;&nbsp; 渲染 &nbsp;&nbsp; text2image</sub></p>
<p><strong>中文</strong> &nbsp;/&nbsp; <a href="README_en.md">English</a></p>
</div>

## 功能

在 AstrBot 进程内把 HTML / Jinja2 模板渲染成图片，作为 AstrBot 的文转图端点使用；渲染能力移植自 [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service)。

- **不用另起容器**：渲染跑在 AstrBot 进程内，没有额外的 Docker、端口映射和镜像要维护。
- **浏览器一键到位**：配置页打开「下载 Chromium 浏览器」并保存即在后台安装（约 200 MB，默认走 npmmirror）；系统里已有 Chromium 时也可以直接指过去复用。
- **缺库自动补装**：浏览器装好了却启动不了（缺 `libnspr4` 一类系统运行库）时会自动补装，环境不支持时给出可手动执行的命令。
- **字体自己带**：不必把字体装进系统，在插件页面里下载预置字体、按链接下载或上传自己的字体文件即可。
- **模板点一下就换**：「T2i 模板」栏列出每个模板的真实效果图、来源与体积，点「应用」就接到 AstrBot 的转图模板上。
- **端点不用手抄**：可选由插件把自己地址写进 AstrBot 的文转图配置，不用再手动抄一遍。
- **渲染时机自己定**：在插件配置页直接改 AstrBot 的「文本转图像输出」总开关与转图字数阈值，不用再翻 AstrBot 主配置。
- **渲染不中断**：本地渲染暂时不可用时，请求自动透传给备用端点，聊天里的文转图照常出图。
- **状态一眼看清**：`/t2i_status` 汇总服务、浏览器、系统依赖、端点与渲染统计；`/t2i_test` 现场出一张卡片。
- **接口与上游一致**：AstrBot 主程序和任何兼容客户端都不用改代码。

| 对比项 | 官方公共端点 | 自建独立服务 | 本插件 |
| --- | --- | --- | --- |
| 额外部署 | 无 | 需要 Docker | 无 |
| 通信路径 | 公网 | 本机 / 内网 | AstrBot 进程内 |
| Chromium 供给 | 远端负责 | 自己维护镜像 | 配置页一键下载 |
| 与 AstrBot 一起升级 | 无关 | 需手动同步 | 随 AstrBot 一起 |

字体与转图模板都在插件页面里管理，入口：WebUI 插件详情页 →「文转图服务面板」（页内分「概览」「T2i 模板」「对接项目」三栏）。

### 渲染字体

模板里写了 `Noto Serif SC`、`Inter` 这类字体名，服务器上未必装了；缺字体时模板会退回默认字体，排版就走样。字体供给也由插件管起来：**不用改模板，也不用往系统字体目录里装**。

**概览**

- **上传自己的字体**：页面选文件上传，会先校验是不是真的字体文件；单个文件上限由 `fonts.upload_limit_mb` 控制。
- **按链接下载**：粘贴字体直链即可（支持 `.ttf` / `.otf` / `.ttc`）。
- **确认生效**：「渲染预览图」把同一句话按每个字体各渲一次——哪一行字形没变化，就说明那个字体没生效；可只选几个字体对比，也可填自己想对比的字词。
- **删改即时生效**：新增或删除字体后插件会自动重启渲染进程，也可以点顶部「重新加载字体」。

**对接项目**

- **预置字体一键下载**：清单按家族列出（Noto Sans/Serif 简繁日、Inter、JetBrains Mono、霞鹜文楷等），点「下载」即开始，装完不用重启插件。
- **按模板一键下载整套**：对接 [astrbot_plugin_qq_group_daily_analysis](https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis/blob/main/docs/T2I_FONTS.md)（群聊日常分析）的报告模板，选模板名（如「排版」「剪贴簿」「春节」「ATRI」）即可一次装齐，缺哪几个会标出来。

### 应用文转图模板

「T2i 模板」栏把 AstrBot 的转图模板做成卡片：每张有真实效果图、来源、体积与可用变量，右侧是「应用」按钮，图片可点开放大。

- **一键应用**：点「应用」即把该模板设为 AstrBot 的当前模板，并接好策略与端点；第一次应用后要重启一次 AstrBot，之后换模板即时生效。
- **换回来**：在 AstrBot 配置页把当前模板改回自带模板即可，插件不覆盖你的选择。
- **模板从哪来**：卡片标「内置」（AstrBot 自带）/「插件」（随本插件发布）/「自定义」（你自己建的）；同名以你自己的为准。
- **自带两套日记风模板**：「今日の手記」与「今日の日記」，取自 [astrbot_plugin_atrifeed](https://github.com/Heximiao/astrbot_plugin_atrifeed)（AGPL-3.0）：日期、星期、字数按出图那一刻自动填，正文按 Markdown 排版，没有外网也照常出图。它们原本要的 `Yu Mincho`、`楷体`、`微软雅黑` 是 Windows / macOS 的商业字体，插件不提供下载，可在「对接项目」里一键装相近的开放字体替代；应用时若字体缺失，弹窗会先说明，可「下载字体并应用」或「忽略，直接应用」。
- **自己建模板**：「新建模板」可用空白骨架、复制现有模板或粘贴自己的 HTML，模板名只能用字母、数字、下划线和短横线；建好后可编辑内容、改「显示名称」、点「预览当前内容」边改边看、点「重新渲染」出最新效果图，也可以删除（自带模板的改动副本删掉后会恢复原版）。底部「预览用样例文本」可换成你常发的消息，只影响卡片预览。

## 指令

**安装不必发指令**：在 WebUI 的插件配置页打开「下载 Chromium 浏览器」并保存，就会在后台开始下载安装，装完开关自动关闭，同页的「Chromium 已就绪」显示结果。下面的指令是同一套能力的聊天入口，用哪个都行。

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `/t2i_status` | 所有人 | 查看服务地址、浏览器与依赖状态、渲染统计，以及 AstrBot 当前的端点、输出开关与字数阈值 |
| `/t2i_install` | 管理员 | 下载安装 Chromium；已装好但缺系统运行库时改为补装依赖 |
| `/t2i_test` | 所有人 | 渲染一张自检卡片并回图，验证整条链路 |
| `/t2i_restart` | 管理员 | 重启内嵌渲染服务并释放 Chromium |
| `/t2i_fonts` | 所有人 | 查看字体目录现状，并渲染一张字体预览图 |

首次使用想全程在聊天里搞定，就按 `/t2i_status` → `/t2i_install` → `/t2i_test` 走一遍。

## 配置

配置项在 WebUI 插件配置页按下面八组展示；每组常用项直接列出，其余项点开折叠。「默认值」就是首次安装后实际生效的值。

### 安装 Chromium 浏览器（`browser`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 下载 Chromium 浏览器（`download_chromium`） | 开关 | 关 | 打开并保存即后台下载安装，结束自动关闭 |
| Chromium 已就绪（`chromium_ready`） | 开关 | 关 | 仅供查看，手改会被覆盖 |

<details>
<summary>安装来源与高级项（6 项）</summary>

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 浏览器安装目录（`browsers_path`） | 文本 | 空 | 留空用插件数据目录，重装不丢 |
| Playwright 浏览器下载源（`download_host`） | 文本 | `https://cdn.npmmirror.com/binaries/playwright` | 默认 npmmirror；留空用官方 CDN |
| 自定义 Chromium 可执行文件路径（`executable_path`） | 文本 | 空 | 复用已有 Chromium 时填写，填了不再下载 |
| 检测到 Chromium 缺少系统运行库时自动补装（`auto_install_deps`） | 开关 | 开 | 缺库自动补装（需 root 与 `apt-get`/`dnf`） |
| 插件启动时清理残留 Chromium（`cleanup_orphans`） | 开关 | 开 | 回收上次被强杀后残留的浏览器进程 |
| 浏览器安装超时（`install_timeout_seconds`） | 数字 | `900` | 网络较慢时可调大，单位秒 |

</details>

### 内嵌渲染服务（`server`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 启用内嵌渲染服务（`enabled`） | 开关 | 开 | 关闭后只保留指令，不再监听端口 |
| 监听端口（`port`） | 数字 | `8999` | 与上游一致；改端口后同步 AstrBot 端点 |

<details>
<summary>连接与透传（4 项）</summary>

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 监听地址（`host`） | 文本 | `127.0.0.1` | 同进程回环即可；对外暴露时改 `0.0.0.0` 并设 Token |
| 鉴权 Token（`auth_token`） | 文本 | 空 | 留空不校验；填写后请求需带 `Bearer` Token |
| 本机渲染不可用时透传上游公共端点（`fallback_enabled`） | 开关 | 开 | 本地不可用时透传备用端点，文转图不中断 |
| 透传的备用端点（`fallback_endpoint`） | 文本 | `https://t2i.rcfortress.site/text2img` | 需含 `/text2img`；可填自建服务地址 |

</details>

### 接入 AstrBot 文转图（`endpoint`）

这一组都是接入路径上会用到的项，全部直接列出。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 本插件当前地址（`current_service_url`） | 文本 | 空 | 自动填入供抄写；手改会被覆盖 |
| 启动时自动写入 AstrBot 文转图端点（`auto_configure`） | 开关 | 关 | 启动时写入端点并把策略设为 `remote` |
| 写入的端点地址（`service_url`） | 文本 | 空 | AstrBot 走别的主机/端口访问时才填 |

### AstrBot 文转图输出（`output`）

在插件配置页直接调 AstrBot 什么时候把回复转成图片；保存后由插件写进 AstrBot 主配置，不用再去 AstrBot 自己的设置页改。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 文本转图像输出（`enabled`） | 开关 | 关 | 对应 AstrBot 的 `t2i`；打开后较长的回复转成图片再发，保存即生效 |
| 转图字数阈值（`word_threshold`） | 数字 | `150` | 对应 `t2i_word_threshold`，超过该字数才转图，最小 50；改完要重启 AstrBot 才按新阈值生效 |

> 首次安装或升级时，开关与阈值先沿用 AstrBot 现有设置，之后以插件配置为准；另有一个插件自动维护的隐藏标记（`core_synced`），无需手动修改。

### 渲染行为（`render`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 并发渲染数（`concurrency`） | 数字 | `2` | 同时渲染的页面数量上限，超出排队；建议 1–4 |
| 以 `--no-sandbox` 启动 Chromium（`no_sandbox`） | 开关 | 开 | root 部署必须开；非 root 且要沙箱时可关 |

<details>
<summary>其余渲染选项（5 项）</summary>

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 空闲多久后关闭 Chromium（`idle_timeout_seconds`） | 数字 | `300` | 空闲关闭以释放内存；`0` 表示不关 |
| 插件启动时预热浏览器（`prelaunch`） | 开关 | 关 | 首条请求不等冷启动，代价是常驻进程 |
| 默认输出格式（`default_type`） | 下拉 | `jpeg` | 请求未指定 `options.type` 时使用 |
| JPEG 默认质量（`default_quality`） | 数字 | `40` | `0`–`100`，仅对 jpeg 生效 |
| 默认整页截图（`full_page`） | 开关 | 开 | 关闭后只截可视区，长文可能被截断 |

</details>

### 图片清理（`storage`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 渲染图片保留时长（`image_lifetime_hours`） | 数字 | `24` | 过期图片自动删除的时长 |
| 清理间隔（`cleanup_interval_minutes`） | 数字 | `60` | 仅作展示参考，清理按分钟级检查 |

### 渲染字体（`fonts`）

字体文件的下载、上传、删除都在「文转图服务面板」里做，配置页只留开关与来源设置。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 启用字体供给（`enabled`） | 开关 | 开 | 关掉后不加载字体，页面下载与上传接口停用 |

<details>
<summary>下载与生效（3 项）</summary>

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| GitHub 下载加速前缀（`github_proxy`） | 文本 | 空 | 内置镜像都不通时才需填自己的前缀 |
| 单个字体文件上限（`upload_limit_mb`） | 数字 | `128` | 上传与链接下载的单文件上限 |
| 字体变动后重启浏览器（`restart_browser_on_change`） | 开关 | 开 | 装完字体重启渲染进程才生效 |

</details>

### 转图模板（`templates`）

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 插件模板数据刷新间隔（`refresh_minutes`） | 数字 | `15` | 天气、心情配图与台词的换批间隔（1–1440 分钟）；改完重新「应用」生效 |

## 其它

### 安装与首次使用

1. 在 AstrBot WebUI 的插件市场安装本插件，或把本仓库放进 `data/plugins/astrbot_plugin_t2i_service`（目录名必须与插件名一致）。
2. 重启 AstrBot，确认「启用内嵌渲染服务」为开、「监听端口」为 `8999`（或你想要的端口）。
3. 在插件配置页打开「下载 Chromium 浏览器」并保存，后台开始下载安装（约 200 MB，默认走 npmmirror）；也可以在聊天里发 `/t2i_install`。容器缺系统运行库时会接着补装。
4. 发 `/t2i_test` 验证出图；想让插件自动接管 AstrBot 的端点，再打开「启动时自动写入 AstrBot 文转图端点」。

### 让 AstrBot 用上本插件

插件提供与上游一致的文转图端点，AstrBot 需要知道它的地址：

- **查地址**：配置页「接入 AstrBot 文转图」里的「本插件当前地址」就是要填进 AstrBot「文本转图像服务 API 地址」的值（默认 `http://127.0.0.1:8999/text2img`，改监听地址或端口后自动更新）。
- **自动**：打开「启动时自动写入 AstrBot 文转图端点」，插件会写入该地址并切到远程策略；输出总开关与转图字数阈值在「AstrBot 文转图输出」里改（见上面的配置说明）。
- **手动**：在 AstrBot 的配置里确认下面三项，改完重启生效：

```jsonc
{
  "t2i": true,
  "t2i_strategy": "remote",
  "t2i_endpoint": "http://127.0.0.1:8999/text2img"
}
```

### 运行环境与依赖

- **Python 3.11 及以上**（AstrBot 的最低版本要求由插件元数据声明，安装时会自动拦截）。
- **`playwright`（固定 1.64.1）**：随插件依赖自动安装，无需手动处理。
- **Chromium**：由插件自己下载管理（约 200 MB 磁盘）；也可以指向系统里已有的浏览器。
- **自动补装系统运行库**需要 AstrBot 进程为 root，且容器内有 `apt-get` 或 `dnf`。
- **内存**：每个并发渲染占用一个浏览器页面，`render.concurrency` 越大占用越高。

### HTTP 接口

需要自己调用时（AstrBot 会自动调用，一般不操心）：

- `POST /text2img/generate` —— 请求体带 `html`，或 `tmpl` + `tmpldata`，可选 `options`
- `GET /text2img/data/{image_id}` —— JSON 模式下取回图片
- `GET /healthz` —— 健康检查

接口与上游一致，完整契约见上游文档 [`docs/standalone/README.md`](docs/standalone/README.md)。

### 独立服务模式（可选）

只在**不想用插件、想单独跑一个服务**时才有用（比如给多台 AstrBot 共用，或与 AstrBot 分机器部署）：

```bash
docker build -t astrbot-t2i-service .
docker run -d -p 8999:8999 astrbot-t2i-service
```

环境变量、S3/R2 存储、Prometheus 指标等与上游一致，见 [`docs/standalone/README.md`](docs/standalone/README.md) 与 [`docs/standalone/MONITORING.md`](docs/standalone/MONITORING.md)。独立服务在 AstrBot 之外时，AstrBot 侧的「文本转图像服务 API 地址」要写**对方可达**的地址，并在服务端配好鉴权，别把端口裸露在公网。

### 常见问题

**Chromium 下载失败或很慢**
默认已走 npmmirror；也可以改「Playwright 浏览器下载源」，或把「浏览器安装目录」指到已有目录。开关关了但「Chromium 已就绪」仍为关，说明这次没下成，去 `/t2i_status` 看状态与日志尾巴。

**渲染报 `error while loading shared libraries: libnspr4.so` 或 `BrowserType.launch: Target page, context or browser has been closed`**
容器缺 Chromium 的系统运行库。发 `/t2i_install` 自动补装（需 root 与 `apt-get`/`dnf`）；环境不满足时会给出可手动执行的命令。

**启动浏览器报 `No usable sandbox!`**
官方镜像以 root 运行，保持「以 `--no-sandbox` 启动 Chromium」为开即可。

**备用端点报错或出图很慢**
Chromium 没装好时请求会透传给备用端点（默认官方公共端点在境外，排队久了会返回 `render queue wait exceeded`）。装好本地 Chromium 后就不再走它；也可把备用端点改成自建服务，或关掉透传。

**端口被占用**
改「监听端口」，再重新打开「启动时自动写入 AstrBot 文转图端点」，或手动更新 `t2i_endpoint`。

**重启 AstrBot 后文转图报错**
确认 `t2i_endpoint` 仍指向插件的监听地址；改了端口忘记同步时，`/t2i_status` 会显示插件的实际端点。

**改了转图字数阈值为什么不生效**
AstrBot 在启动时缓存这个阈值，保存插件配置后要重启一次 AstrBot 才对后续消息生效；总开关与补发方式保存即生效。

**装了字体但模板还是用了别的字体**
先在面板「概览」点「渲染预览图」：几行长得一样说明这些字体都没生效。常见原因是渲染进程还没重启（点「重新加载字体」），或文件其实是改名的网页/压缩包（上传与下载都会校验文件头）。另外 `Noto Sans SC` 与系统里的 `Noto Sans CJK SC` 是两套名字，不能互替。

**换了模板，出图还是老样子**
第一次「应用」后要重启一次 AstrBot；之后换模板即时生效。重启后仍没变，看「T2i 模板」栏顶部状态标签，显示「未接入」就重新点一次「应用」。

**占用多少磁盘**
Chromium 约 200 MB；渲染图默认保留 24 小时后清理；字体按家族累加，一整套模板字体通常 20–60 MB。

**日志在哪**
与插件其它日志归在一处，在 AstrBot 日志里按插件名或 `[t2i]` 前缀过滤；渲染失败时会记下原因与堆栈。

### 开发与测试

```bash
pip install -r requirements-standalone.txt pytest pytest-asyncio
pytest tests/ -q
```

插件测试不需要真实 Chromium（渲染器为假实现）；真实渲染的端到端验证脚本在 `tools/envlab/`。

### 许可证与鸣谢

Apache-2.0，与上游一致。渲染核心移植自 [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service)，仓库保留上游 `LICENSE`，移植与改动说明见 [`NOTICE`](NOTICE)。
