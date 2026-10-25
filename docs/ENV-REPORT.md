# ENV-REPORT — 一次性 AstrBot 渲染环境（t2i-envlab）

> 目标：为后续「插件内 Playwright 文转图」集成测试准备一个可复现的一次性容器环境，并量化渲染基线。
> 状态：**环境已就绪（READY）**；容器 `t2i-envlab` 基于生产同镜像运行中，生产容器 `astrbot` 未做任何改动。
> 产出日期：2026-10-25　执行者：envlab　镜像：`sha256:1e5379633318…`（= 生产 `astrbot` 正在使用的 image ID）

---

## 0. TL;DR

| 项 | 结果 |
| --- | --- |
| 环境是否可用 | ✅ `playwright 1.64.1` + `Chrome for Testing 156.0.8078.4`，`ldd` 缺库 **0**，`probe_env.py` 判定 `READY: True` |
| 真实模板渲染 | ✅ 官方 `base.html` 渲染成功：中文 179 字、shiki 代码高亮 1 块、KaTeX 2 处、表格/引用/emoji 全部正常，**控制台错误 0、请求失败 0** |
| 安装耗时 | pip 113.6s + 浏览器 132s + 系统库 194s ≈ **7.3 分钟**（含下载） |
| 安装体积 | 浏览器磁盘 **678 MB**（du 711 M），系统库 **112 MB**（56 包），页面 PNG ≈ 0.41 MB |
| 冷启动渲染 | **6.65 s**（浏览器冷启动 54 ms，首次 HTML 内容 3.60 s，截图 0.35 s） |
| 热渲染 | **0.93–0.98 s / 张**（中位 0.96 s；其中内容 0.69–0.72 s、截图 0.24–0.25 s） |
| 进程 RSS | 启动后 364 MB，渲染峰值 **576 → 633 MB**（6 个 chrome 进程；python 侧 58 MB） |
| ⚠️ 关键风险 | `base.html` **硬依赖外网 CDN**：断网时 `marked` 加载失败 → 正文空白（截图 27 KB 白页），30 s 超时后才放弃 |

---

## 1. 容器与镜像

### 1.1 镜像

| 项 | 值 |
| --- | --- |
| 镜像 | `soulter/astrbot:latest` 的本地副本（现已无 tag，显示为 `<none>`） |
| Image ID | `sha256:1e5379633318c59a3af6a6c437ccb06e8e1de016fc5b5886eebcd70652b84c02` |
| Image digest | `sha256:1fe5c90c56aa4158e887431dbbf67d619bb819c5042dbdcf5df8d7f03d9579a7` |
| 大小 | 1.96 GB（容器可写层 628 MB / virtual 2.59 GB） |
| Base OS | Debian 13 (trixie) |
| Python | 3.12.15（镜像自带 `/usr/local/bin/python`；系统另有 3.13） |
| AstrBot | 镜像内 `astrbot.__version__ = 4.29.2`；**生产容器里同一镜像报 4.29.3**（见 §9 已知差异） |
| 既有优势 | 镜像已自带 `fonts-noto-cjk`（Noto Sans/Serif/Mono CJK 全字重，覆盖简繁日韩）、`ffmpeg`、`git`、`nodejs 24` |

> ⚠️ 注意：宿主上 `docker pull soulter/astrbot:latest` 现在会拿到 **另一个** image（`00f3db550126`，与生产在跑的 `1e5379633318` 不同）。复现本环境**必须用 image ID 固定**，不要用 `:latest`。

### 1.2 创建命令（实际执行）

```bash
docker run -d --name t2i-envlab \
  -e TZ=Asia/Shanghai \
  -e PLAYWRIGHT_BROWSERS_PATH=/AstrBot/data/ms-playwright \
  -e PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright \
  -e PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
  -v /vol1/1000/Docker/AstrBot/t2i-envlab-data:/AstrBot/data \
  -v /vol1/@appdata/deepseek.harness/home/astrbot_plugin/astrbot_plugin_t2i_service:/opt/plugin:ro \
  -v /vol1/@appdata/deepseek.harness/home/AstrBot:/opt/astrbot-src:ro \
  --entrypoint sleep \
  sha256:1e5379633318c59a3af6a6c437ccb06e8e1de016fc5b5886eebcd70652b84c02 \
  infinity
```

要点：

- **不改镜像**：`--entrypoint sleep infinity` 让容器待机，不启动 AstrBot 主进程（`python main.py`），避免占用生产接入/网络。
- **持久化**：`/AstrBot/data` → 宿主 `/vol1/1000/Docker/AstrBot/t2i-envlab-data`，浏览器（≈678 MB）与截图都落在卷里，容器重建不必重下。
- **只读挂载**：插件仓库挂到 `/opt/plugin:ro`，AstrBot 源码挂到 `/opt/astrbot-src:ro`；因此容器内跑 Python 必须设 `PYTHONPYCACHEPREFIX=/tmp/pycache`（否则写 `__pycache__` 报只读错误），输出只能写 `/AstrBot/data` 或 `/tmp`。
- 资源限制：未设内存/CPU 上限（宿主 4 核 / 24 GB，`/AstrBot/data` 所在卷剩余 314.5 GB）。

---

## 2. Playwright / Chromium 安装记录

一键脚本：[`tools/envlab/setup_envlab.sh`](../tools/envlab/setup_envlab.sh)（幂等，可重复执行）。

| 阶段 | 命令 | 用时 | 体积 | 说明 |
| --- | --- | --- | --- | --- |
| pip 包 | `pip install playwright==1.64.1 -i https://mirrors.aliyun.com/pypi/simple/` | 113.6 s | wheel 48.5 MB（≈437 kB/s） | 顺带装 `pyee 14.0.0`；`greenlet 3.5.6` 镜像已有 |
| 浏览器 | `python -m playwright install chromium`（`PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright`） | 132 s | 下载 313.3 MiB（191.1+120.0+2.2） | 解压后磁盘 **678 MB**：`chromium-1248` 401.4 MB + `chromium_headless_shell-1248` 271.9 MB + `ffmpeg-1013` 5.1 MB |
| 系统库 | `apt-get update && python -m playwright install-deps chromium` | 194 s（首次）/ 253 s（重跑） | 安装 **112.3 MB** | 显式 32 包，实际新增 56 包 |

**镜像源结论（实测）**

- pip：**`mirrors.aliyun.com/pypi` 可用（437 kB/s）**；`pypi.tuna.tsinghua.edu.cn` 返回的 wheel URL 会 403（`the repository ... is not allowed`），不要用 tuna。
- 浏览器 CDN：**`https://cdn.npmmirror.com/binaries/playwright`**（也支持旧式 `https://npmmirror.com/mirrors/playwright/`），区间测速 4.4 MB/s，整包均速 ≈2.4 MB/s。
- apt：默认 `deb.debian.org` 即可（索引 10 MB 约 9 s，≈1 MB/s），无需换清华源。
- 直连 `files.pythonhosted.org` 反而有 3.1 MB/s，但镜像源已够快且可预测。

**版本绑定**：`chromium-1248` 是 `playwright 1.64.1` 配套的构建。升级 playwright 后必须重跑 `playwright install chromium`（否则报 “Executable doesn't exist”）。

> 与先前调研的差异：背景调研基于 playwright 1.56 / chromium revision 1194 / 浏览器 0.9–1 GB；实际 `latest` 镜像生态拉到的是 **1.64.1 / chrome 156.0.8078.4 / 1248**，体积 **678 MB**（headless shell 是主力，体积更小）。

---

## 3. 缺失系统库与补齐

补齐前对 `chrome` 做 `ldd`，缺失 10 个动态库（生产镜像同样缺，`ldconfig -p` 查不到）：

| 缺失 .so | 由哪个包提供 |
| --- | --- |
| `libXcomposite.so.1` | libxcomposite1 |
| `libXdamage.so.1` | libxdamage1 |
| `libatk-1.0.so.0` | libatk1.0-0t64 |
| `libatk-bridge-2.0.so.0` | libatk-bridge2.0-0t64 |
| `libatspi.so.0` | libatspi2.0-0t64 |
| `libcups.so.2` | libcups2t64 |
| `libnspr4.so` | libnspr4 |
| `libnss3.so` / `libnssutil3.so` / `libsmime3.so` | libnss3 |

补齐命令（已在容器内执行，退出码 0，补齐后 `ldd | grep 'not found'` 计数 = 0）：

```bash
apt-get update
python -m playwright install-deps chromium          # 等价于下面 32 个包
# 或手工最小集合：
apt-get install -y --no-install-recommends \
  libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 libcairo2 \
  libcups2t64 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0t64 libnspr4 libnss3 \
  libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
  libxkbcommon0 libxrandr2 xvfb fonts-noto-color-emoji fonts-unifont libfontconfig1 \
  libfreetype6 xfonts-scalable fonts-liberation fonts-ipafont-gothic \
  fonts-wqy-zenhei fonts-tlwg-loma-otf fonts-freefont-ttf
```

说明：

- `install-deps` 会顺带装 `xvfb` —— **我们不需要 X**：`headless=True` 时 playwright 走 `chrome-headless-shell`，不依赖 Xvfb。
- 顺带装的 `fonts-wqy-zenhei` / `fonts-noto-color-emoji` 对中文与 emoji 有用；不过镜像本身已有 `fonts-noto-cjk`，中文不是瓶颈。
- 验证：`probe_env.py` 输出 `missing libs: none`、`READY: True`、`cjk fonts: 18 families`。

---

## 4. PLAYWRIGHT_BROWSERS_PATH（必须改）

- 生产 AstrBot 容器里 **没有** `/root/.cache/ms-playwright`，且 `/root/.cache` **不在挂载卷**——默认路径装的浏览器会随容器重建消失，还会把 678 MB 写进可写层。
- 因此**安装时与运行时都必须**设置：

```bash
PLAYWRIGHT_BROWSERS_PATH=/AstrBot/data/ms-playwright          # 必须：指向挂载卷
PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright   # 仅安装时需要
```

- 目录结构（安装后）：
  ```text
  /AstrBot/data/ms-playwright/
  ├── chromium-1248/                401.4 MB  chrome-linux64/chrome
  ├── chromium_headless_shell-1248/ 271.9 MB  chrome-headless-shell-linux64/chrome-headless-shell
  └── ffmpeg-1013/                    5.1 MB
  ```
- 运行时若不设 `PLAYWRIGHT_BROWSERS_PATH`，playwright 会去找 `/root/.cache/ms-playwright` 并直接报错：**这是本环境最容易踩的坑**。
- 版本目录名（`chromium-1248`）与 playwright 版本强绑定，所以**同时 pin 住 playwright 版本**（本仓库 `pyproject.toml` 允许 `>=1.54.0`，插件上线时应写死或约束到已验证版本）。

---

## 5. 渲染基线（真实 `base.html`）

### 5.1 方法

脚本：[`tools/envlab/render_baseline.py`](../tools/envlab/render_baseline.py)（只依赖 `playwright`，不 import 仓库插件模块）。

- 模板：容器内 `/opt/astrbot-src/astrbot/core/utils/t2i/template/base.html`（11553 B，宿主 `/vol1/@appdata/deepseek.harness/home/AstrBot/astrbot/core/utils/t2i/template/base.html`）。
- 与生产对齐的两点：
  1. 用 Jinja2 渲染 `{{ text | safe }}` / `{{ version }}`；
  2. 注入 `template/shiki_runtime.iife.js`（2.35 MiB）。AstrBot 在 `astrbot/core/utils/t2i/network_strategy.py:74 inject_shiki_runtime()` 里把这段 IIFE 插到 `</head>` 前；脚本对渲染后的 HTML 做等价注入。
- 视口：900×1200、`device_scale_factor=2`、`full_page=True`（即输出 1800 px 宽）。
- 每次渲染都重新 `set_content()` 全量 HTML（模拟“一个页面反复渲染”的最朴素用法），`page.wait_for_function` 等待 `#content` 出现子节点后强制 reflow，再截图。
- 冷/热定义：`cold` = 新建 browser+context 后第一张；`warm` = 同一 page 上的后续渲染。

```bash
docker exec t2i-envlab bash -lc 'export PYTHONPYCACHEPREFIX=/tmp/pycache; \
  cd /opt/plugin/tools/envlab && python render_baseline.py \
    --template /opt/astrbot-src/astrbot/core/utils/t2i/template/base.html \
    --shiki-runtime /opt/astrbot-src/astrbot/core/utils/t2i/template/shiki_runtime.iife.js \
    --out /AstrBot/data/shots --prefix baseline --runs 3 \
    --json /AstrBot/data/shots/baseline-metrics.json'
```

### 5.2 结果（注入 shiki，生产等价）

| 指标 | 冷启动 | 热渲染（n=2） |
| --- | --- | --- |
| 端到端 | **6646 ms** | — |
| ├ browser launch | 54.4 ms | — |
| ├ context+page | 39.1 ms | — |
| ├ HTML 内容就绪 | 3599.3 ms | 722.7 / 685.3 ms |
| └ 截图 | 348.0 ms | 253.8 / 238.8 ms |
| 单张总计 | 3955 ms | **984.9 / 926.3 ms（中位 955.6 ms）** |
| 仅截图（内容不变） | — | 251.8 ms |
| chrome RSS（6 进程） | 575.7 MB | 599.7 → 633.3 MB |
| 启动后 RSS | 363.8 MB | — |
| python 进程 RSS | 58.2 MB | — |

截图产物：1800×4226 px、**431 KB** PNG（`tools/envlab/shots/baseline-*.png`）。

页内诊断（三次一致）：`shiki=object`、`shiki_blocks=1`、`code_blocks=1`、`katex_nodes=2`、`cjk_chars=179`、`misans_loaded=true`、`content_height=1933`、`console_errors=[]`、`external_failures=[]`。

### 5.3 对比：不注入 shiki

| 指标 | 冷 | 热 |
| --- | --- | --- |
| HTML 内容就绪 | 2917 ms | **19–31 ms** |
| 单张总计 | — | **263–269 ms** |
| RSS | 518–532 MB | — |

**结论**：热渲染的 700 ms 几乎全部来自那 2.35 MiB shiki runtime —— 每次 `set_content()` 都会重新解析执行一遍内联脚本。若插件每张图都全量 `set_content`，单图成本 ≈ 1 s；若能“一次加载页面、后续只替换 `#content` 并手动触发高亮”，热渲染可回落到 ≈ 260 ms（截图本身 ~240 ms 已接近下限）。这是插件侧可优化的最大单点。

> 安装好之后浏览器 (chrome-headless-shell) 的 `launch` 只有 50–70 ms，说明进程冷启动开销很小；冷渲染的 3.6 s 主要是 **CDN 首次拉取**（`marked`、KaTeX、MiSans 字体），与 CPU 无关。

---

## 6. ⚠️ 外网 CDN 硬依赖（离线 = 白屏）

`base.html` 从 CDN 加载 7 个资源（实测拦截到的唯一 URL 列表）：

| URL | 用途 |
| --- | --- |
| `https://cdn.jsdelivr.net/npm/marked/marked.min.js` | **Markdown 渲染核心（缺它整页空白）** |
| `https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.css` | 数学公式样式 |
| `https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/katex.min.js` | 数学公式 |
| `https://cdn.jsdelivr.net/npm/katex@0.16.10/dist/contrib/auto-render.min.js` | 公式自动渲染 |
| `https://cdn.jsdelivr.net/npm/misans@4.1.0/lib/Normal/MiSans-Regular.min.css` | 中文 Web 字体 |
| `https://cdn.jsdelivr.net/npm/misans@4.1.0/lib/Normal/MiSans-Bold.min.css` | 中文 Web 字体（粗） |
| `https://fonts.googleapis.com/css2?family=Outfit:wght@400..700&display=swap` | 西文字体 Outfit |

复现实验（`--block-external` 拦截所有 http(s) 请求）：

```bash
docker exec t2i-envlab bash -lc 'export PYTHONPYCACHEPREFIX=/tmp/pycache; \
  cd /opt/plugin/tools/envlab && python render_baseline.py \
    --template /opt/astrbot-src/astrbot/core/utils/t2i/template/base.html \
    --out /AstrBot/data/shots-offline --prefix offline --runs 2 --block-external \
    --json /AstrBot/data/shots-offline/offline-metrics.json'
```

结果：14 次请求被拦截（7 个唯一 URL），**每次渲染都等满 30 s 超时**，`cjk_chars=0`、正文完全空白，PNG 仅 27 KB（纯背景）→ `tools/envlab/shots/offline-*.png`。

**影响与建议**

- 生产/内网若直连 `cdn.jsdelivr.net` 与 `fonts.googleapis.com`，首图会慢（实测 3–3.6 s，且 jsDelivr 在国内经常抽风）；完全断网则**直接白屏**，且要等满超时才失败。
- AstrBot 只把 shiki 运行时本地化了（`template/shiki_runtime.iife.js`），`marked`/KaTeX/MiSans 仍是外链。插件若要在离线/内网环境跑，需要：
  1. 自建模板：把 `marked.min.js`、`katex.*`、MiSans CSS+字体打包进插件资源或 `/AstrBot/data`，用 `TemplateManager` 自定义模板替换外链；
  2. 或者容器内跑 DNS/HTTP 代理把 3 个域名指向本地静态目录；
  3. 渲染超时要按“CDN 不可达”设短（例如 10 s）并给出可读错误，而不是让用户等 30 s 后拿到白图。

---

## 7. 在容器里跑插件集成测试：推荐命令清单

### 7.1 两种验证方式的分工

| 方式 | 命令载体 | 覆盖什么 | 成本 | 何时用 |
| --- | --- | --- | --- | --- |
| **A. 宿主单测（无浏览器）** | 宿主 `python -m pytest tests/`（宿主 venv，无 playwright） | 纯逻辑：markdown 预处理、模板变量、API/参数拼装、错误分支、metrics | 秒级 | 每次改代码的默认回归；CI 主线 |
| **B. 容器内集成渲染** | `docker exec t2i-envlab …` | 真浏览器真模板：HTML/CSS/字体/高亮/公式是否真的渲染出来、耗时与 RSS、PNG 尺寸 | 单张 1 s / 冷启动 7 s | 改渲染链路、改模板、升级 playwright、发版前 |

A 无法发现“浏览器跑不起来 / 缺 so / 字体豆腐块 / CDN 挂了”这类问题；B 才能。二者缺一不可。

### 7.2 可直接复制的命令

```bash
# 0) 环境自检（应该看到 READY: True / missing libs: none）
docker exec t2i-envlab bash -lc '
  export PYTHONPYCACHEPREFIX=/tmp/pycache
  python /opt/plugin/tools/envlab/probe_env.py --json /AstrBot/data/probe-env.json | tail -5'

# 1) 真实模板基线渲染（冷/热耗时 + RSS + PNG + 页内诊断）
docker exec t2i-envlab bash -lc '
  export PYTHONPYCACHEPREFIX=/tmp/pycache
  cd /opt/plugin/tools/envlab && python render_baseline.py \
    --template /opt/astrbot-src/astrbot/core/utils/t2i/template/base.html \
    --shiki-runtime /opt/astrbot-src/astrbot/core/utils/t2i/template/shiki_runtime.iife.js \
    --out /AstrBot/data/shots --prefix baseline --runs 3 \
    --json /AstrBot/data/shots/baseline-metrics.json'

# 2) 离线回归（期望：白屏 + 7 个 CDN 拦截；用于守护“不要引入新的外部依赖”）
docker exec t2i-envlab bash -lc '
  export PYTHONPYCACHEPREFIX=/tmp/pycache
  cd /opt/plugin/tools/envlab && python render_baseline.py \
    --template /opt/astrbot-src/astrbot/core/utils/t2i/template/base.html \
    --out /AstrBot/data/shots-offline --prefix offline --runs 2 --block-external \
    --json /AstrBot/data/shots-offline/offline-metrics.json'

# 3) 在容器内跑插件单测（AstrBot 可 import，环境变量一次给全）
docker exec t2i-envlab bash -lc '
  export PYTHONPATH=/opt/astrbot-src:/opt/plugin \
         PYTHONPYCACHEPREFIX=/tmp/pycache \
         PLAYWRIGHT_BROWSERS_PATH=/AstrBot/data/ms-playwright
  cd /opt/plugin && python -m pytest tests -q'
  # 若容器内没有 pytest：pip install pytest -i https://mirrors.aliyun.com/pypi/simple/ （装进容器，不写仓库）

# 4) 用插件自己的渲染入口出一张图（把下面 <渲染函数> 换成 t2i_core/render.py 的实际入口）
docker exec t2i-envlab bash -lc '
  export PYTHONPATH=/opt/astrbot-src:/opt/plugin \
         PYTHONPYCACHEPREFIX=/tmp/pycache \
         PLAYWRIGHT_BROWSERS_PATH=/AstrBot/data/ms-playwright
  cd /opt/plugin && python - <<"PY"
import asyncio, sys
from pathlib import Path

MD = "# 中文标题\n\n表格 / 引用 / 行内 `code` / 公式 $E=mc^2$\n\n```python\nprint(\"hi\")\n```\n"

async def main():
    # 方案一：直接用 AstrBot 的本地渲染 API（不经过远端 t2i 端点）
    from astrbot.core.utils.t2i.template_manager import TemplateManager
    tm = TemplateManager()
    tmpl = tm.get_template("base")
    print("template bytes:", len(tmpl.encode()))
    # 方案二：换成插件内 playwright 渲染入口，例如
    #   from t2i_core.render import render_markdown_to_png
    #   out = await render_markdown_to_png(MD, Path("/AstrBot/data/plugin-check.png"))
    #   print("png:", out)

asyncio.run(main())
PY'

# 5) 把容器产物取回宿主（截图/指标）
docker cp t2i-envlab:/AstrBot/data/shots/. tools/envlab/shots/
docker cp t2i-envlab:/AstrBot/data/shots/baseline-metrics.json tools/envlab/shots/

# 6) 生产对称探针（只读；/opt/plugin 没有挂进生产容器，需先 docker cp）
docker cp tools/envlab/probe_env.py astrbot:/tmp/probe_env.py
docker exec astrbot bash -lc 'python /tmp/probe_env.py --browsers-path /nonexistent | tail -6'
# 预期：playwright 缺失、chrome 缺失、缺 10 个 .so → 说明生产容器本身跑不了浏览器
```

### 7.3 容器内执行的注意事项

- **只读挂载**：`/opt/plugin` 是 `ro`，Python 想写 `__pycache__` 会失败 → 必须 `PYTHONPYCACHEPREFIX=/tmp/pycache`（已在上面每条命令里设置）。
- **输出位置**：只能写 `/AstrBot/data/**`（持久化、可 `docker cp`）或 `/tmp`（容器内临时）；不要往仓库里写产物。
- **环境变量三件套**：`PLAYWRIGHT_BROWSERS_PATH`（必须）、`PYTHONPATH=/opt/astrbot-src:/opt/plugin`（要 import AstrBot/插件时才需要）、`PYTHONPYCACHEPREFIX`（只读挂载时必须）。
- **`headless=True`**：playwright 会自动选 `chrome-headless-shell` 并加 `--no-sandbox`；容器里不需要 Xvfb、不需要 `xvfb-run`。
- 每个 browser 实例 ≈ 6 个进程、≈0.6 GB RSS；并发渲染按此估算内存（宿主 24 GB 可放心开几个，生产容器内存要实测）。

---

## 8. 从零复现

```bash
# 1. 起容器（§1.2）
# 2. 装 playwright + 浏览器 + 系统库（幂等，约 7 分钟）
docker exec t2i-envlab bash /opt/plugin/tools/envlab/setup_envlab.sh
# 3. 自检
docker exec t2i-envlab bash -lc 'export PYTHONPYCACHEPREFIX=/tmp/pycache; python /opt/plugin/tools/envlab/probe_env.py | tail -6'
# 4. 基线渲染
docker exec t2i-envlab bash -lc 'export PYTHONPYCACHEPREFIX=/tmp/pycache; cd /opt/plugin/tools/envlab && python render_baseline.py --template /opt/astrbot-src/astrbot/core/utils/t2i/template/base.html --shiki-runtime /opt/astrbot-src/astrbot/core/utils/t2i/template/shiki_runtime.iife.js --out /AstrBot/data/shots --runs 3 --json /AstrBot/data/shots/baseline-metrics.json'
# 5. 取回产物
docker cp t2i-envlab:/AstrBot/data/shots/. tools/envlab/shots/
```

清理（确认不再需要后）：

```bash
docker rm -f t2i-envlab                      # 容器直接丢弃
rm -rf /vol1/1000/Docker/AstrBot/t2i-envlab-data   # 可选：清掉 678 MB 浏览器缓存
```

---

## 9. 已知问题 / 风险

1. **镜像版本漂移**：生产在跑 `1e5379633318`（AstrBot 4.29.3），而 `soulter/astrbot:latest` 现在指向 `00f3db550126`；本环境按生产 image ID 固定，但宿主上再次 `pull latest` 会得到不同版本。任何“准生产”验证都要 pin image ID。
2. **AstrBot 版本号不一致**：同一 image ID 下，生产容器里 `/AstrBot/pyproject.toml` 与 `astrbot.__version__` 都是 4.29.3，而 `t2i-envlab` 里是 4.29.2。说明生产容器可能在启动后经历过代码/版本文件更新（或 /AstrBot 被覆盖），**不能把“镜像 = 生产代码”当绝对等价**，涉及行为差异时以生产容器 `docker exec` 实测为准。
3. **CDN 硬依赖**（§6）：断网白屏、慢网首图 3.6 s，且失败前要等满超时。
4. **shiki runtime 重复解析**（§5.3）：全量 `set_content` 时热渲染 ~1 s；建议插件侧做“静态壳 + 局部更新”。
5. **生产容器缺 playwright 与浏览器**：插件若在 AstrBot 容器内直接跑浏览器，必须显式 `pip install playwright` + `playwright install chromium`（并把 `PLAYWRIGHT_BROWSERS_PATH` 指到 `/AstrBot/data`）；镜像里没有任何 playwright 相关包。
6. **磁盘**：浏览器 678 MB 必须落在 `/AstrBot/data`（卷），不要写进容器可写层（重建即丢，且让镜像层膨胀）。
7. `install-deps` 会额外装 `xvfb`（本 headless 方案用不到，可忽略，别因此误以为需要 X）。
8. 无人值守下 `apt` 偶发卡住的情况本次未出现；脚本已设 `set -euo pipefail`，失败会立即停。

---

## 10. 产出物

| 文件 | 说明 |
| --- | --- |
| [`tools/envlab/setup_envlab.sh`](../tools/envlab/setup_envlab.sh) | 容器内幂等安装脚本（pip / 浏览器 / 系统库 / 校验） |
| [`tools/envlab/probe_env.py`](../tools/envlab/probe_env.py) | 环境探针：python/playwright/chrome/缺库/CJK 字体/磁盘/`READY` 判定，支持 `--json` |
| [`tools/envlab/render_baseline.py`](../tools/envlab/render_baseline.py) | 真实模板渲染基线：冷/热耗时、RSS、PNG 尺寸、页内诊断、`--block-external` 离线回归、`--shiki-runtime` 生产等价注入 |
| [`tools/envlab/shots/baseline-cold.png`](../tools/envlab/shots/baseline-cold.png) 等 4 张 | 冷/热渲染截图（1800×4226，含中文、表格、引用、代码高亮、KaTeX、emoji） |
| [`tools/envlab/shots/baseline-metrics.json`](../tools/envlab/shots/baseline-metrics.json) | 上述基线的完整结构化指标 |
| [`tools/envlab/shots/offline-*.png`](../tools/envlab/shots/offline-cold.png) + `offline-metrics.json` | 断网白屏证据（7 个 CDN 资源被拦截） |
| 容器 | `t2i-envlab`（运行中，`docker exec t2i-envlab bash` 进入） |
| 持久化目录 | 宿主 `/vol1/1000/Docker/AstrBot/t2i-envlab-data` → 容器 `/AstrBot/data` |

**未做（按约束）**：未修改生产容器 `astrbot`、未改插件仓库其他文件、未 `git commit`、未写插件渲染代码。
