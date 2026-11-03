<div align="center">
<h1>AstrBot T2I Service</h1>
<p><strong>HTML/template to image rendering inside the AstrBot process, ready to serve as AstrBot's text-to-image endpoint with no extra container; includes Chromium download, status detection and orphan-process cleanup.</strong></p>
<p><sub>text2image &nbsp;&nbsp; t2i &nbsp;&nbsp; rendering</sub></p>
<p><a href="README.md">中文</a> &nbsp;/&nbsp; <strong>English</strong></p>
</div>

## Features

Renders HTML / Jinja2 templates into images inside the AstrBot process and serves them as AstrBot's text-to-image endpoint; the rendering engine is ported from [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service).

- **No extra container**: rendering runs inside the AstrBot process — no Docker, no port mapping, no image to maintain.
- **Browser in one step**: turn on **Download Chromium** in the config and save to install it in the background (~200 MB, npmmirror by default); an existing system Chromium can be pointed at instead.
- **Missing libraries installed for you**: when the browser is installed but cannot launch (missing `libnspr4` and friends), the plugin installs them; if the environment does not allow it, you get the exact command to run.
- **Fonts bundled with the plugin**: no system-wide font installation — download the preset families, paste a link, or upload your own file from the plugin page.
- **Templates in one click**: the **T2I templates** tab shows a real render, the source and the size of every template; hit **Apply** and AstrBot's text-to-image template switches to it.
- **No endpoint to copy by hand**: optionally the plugin writes its own address into AstrBot's text-to-image config, together with the output switch.
- **Rendering stays available**: while local rendering is unavailable, requests are proxied to the fallback endpoint so text-to-image keeps working in chat.
- **Status at a glance**: `/t2i_status` reports the service, the browser, system libraries, the endpoint and render stats; `/t2i_test` renders a card on the spot.
- **Same interface as upstream**: AstrBot and any compatible client need no code changes.

| | Public endpoint | Self-hosted service | This plugin |
| --- | --- | --- | --- |
| Extra deployment | none | Docker required | none |
| Traffic path | public network | local / LAN | inside the AstrBot process |
| Chromium supply | remote side | maintain your own image | one-click download in the config |
| Upgrades with AstrBot | unrelated | manual | follows AstrBot |

Fonts and text-to-image templates are managed on the plugin page at WebUI plugin details → **T2I Service Panel** (three tabs: Overview, T2I templates, Upstream project).

### Fonts

Templates ask for family names such as `Noto Serif SC` or `Inter`, which a server may not have; without them the template falls back to a default font and the layout drifts. This plugin supplies the fonts too: **no template edits and no system-wide font installation**.

**Overview**

- **Upload your own font**: pick a file on the page; it is validated as a real font, and `fonts.upload_limit_mb` caps the size.
- **Download from a link**: paste any direct font URL (`.ttf` / `.otf` / `.ttc`).
- **Check it actually works**: **Render preview** draws the same sentence once per family — a row that looks like the others tells you that font is not in effect; pick a few families to compare only those, or type the characters you want to compare.
- **Changes apply immediately**: adding or removing fonts restarts the render process; **Reload fonts** does the same on demand.

**Upstream project**

- **Preset families, one click**: the list is grouped by family (Noto Sans/Serif for SC/TC/JP, Inter, JetBrains Mono, LXGW WenKai and more); hit download and it installs, no restart needed afterwards.
- **Whole template in one click**: for [astrbot_plugin_qq_group_daily_analysis](https://github.com/SXP-Simon/astrbot_plugin_qq_group_daily_analysis/blob/main/docs/T2I_FONTS.md) (QQ group daily analysis) pick a template name (Layout, Scrapbook, Spring Festival, ATRI ...) to install everything it needs, with anything still missing marked.

### Text-to-image templates

The **T2I templates** tab turns AstrBot's text-to-image templates into cards: each shows a real render, its source, size and available variables, with an **Apply** button on the right; click an image to zoom in.

- **One-click apply**: **Apply** makes that template AstrBot's active one and wires the strategy and endpoint for you; the first apply needs one AstrBot restart, after which switching is instant.
- **Go back**: change the active template to a stock one in AstrBot's config page; the plugin will not override your choice.
- **Where templates come from**: each card is marked **Built-in** (shipped with AstrBot), **Plugin** (shipped with this plugin) or **Custom** (your own); on a name clash yours wins.
- **Two diary templates ship with the plugin**: 今日の手記 and 今日の日記, taken from [astrbot_plugin_atrifeed](https://github.com/Heximiao/astrbot_plugin_atrifeed) (AGPL-3.0): the date, weekday and character count fill themselves in at render time, the body is rendered as Markdown, and they work without internet access. The faces they were designed for (`Yu Mincho`, KaiTi, Microsoft YaHei) are commercial fonts the plugin does not download — install the similar open families under **Upstream project** instead; if they are missing on apply, the dialog explains it and offers **Download fonts and apply** or **Ignore and apply**.
- **Build your own**: **New template** starts from a blank scaffold, a copy of an existing template or pasted HTML, and names may only use letters, digits, underscores and dashes. Templates can then be edited, given a display name, previewed while editing, re-rendered and deleted (removing an edited copy of a shipped template restores the original). **Preview sample text** at the bottom swaps the text used by the previews only.

## Commands

**Installing needs no command**: turn on **Download Chromium** in the WebUI plugin config page and save — the download and install start in the background, the switch turns itself off when it finishes, and **Chromium ready** on the same page shows the result. The commands below are the chat-side entry points for the same capabilities; either route works.

| Command | Permission | Description |
| --- | --- | --- |
| `/t2i_status` | everyone | Service address, browser and system library state, render stats, plus AstrBot's current endpoint and output switch |
| `/t2i_install` | administrators | Download and install Chromium; installs missing system libraries instead when needed |
| `/t2i_test` | everyone | Render a self-check card and send it back to verify the whole chain |
| `/t2i_restart` | administrators | Restart the embedded render service and release Chromium |
| `/t2i_fonts` | everyone | Show the font directory and render a font preview card |

For a first run entirely in chat, go `/t2i_status` → `/t2i_install` → `/t2i_test`.

## Configuration

The plugin config page shows the seven groups below; each group lists its common options and folds the rest. The **Default** column is what takes effect right after installation.

### Install Chromium (`browser`)

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Download Chromium (`download_chromium`) | switch | off | Turn it on and save to install in the background; the switch turns itself off when done |
| Chromium ready (`chromium_ready`) | switch | off | Display only; manual edits are overwritten |

<details>
<summary>Install source and advanced options (6)</summary>

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Browser install directory (`browsers_path`) | text | empty | Empty uses the plugin data directory, which survives reinstalling |
| Playwright download host (`download_host`) | text | `https://cdn.npmmirror.com/binaries/playwright` | npmmirror by default; empty uses the official CDN |
| Custom Chromium executable (`executable_path`) | text | empty | Fill in to reuse an existing Chromium; the plugin then skips downloading |
| Install missing system libraries automatically (`auto_install_deps`) | switch | on | Installs missing libraries on demand (needs root plus `apt-get`/`dnf`) |
| Clean up leftover Chromium on startup (`cleanup_orphans`) | switch | on | Reclaims browser processes left behind by a killed plugin |
| Browser install timeout (`install_timeout_seconds`) | number | `900` | Raise it on a slow network; in seconds |

</details>

### Embedded render service (`server`)

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Enable the embedded render service (`enabled`) | switch | on | When off, only the commands remain and no port is opened |
| Listen port (`port`) | number | `8999` | Matches upstream; update AstrBot's endpoint after changing it |

<details>
<summary>Connection and fallback (4)</summary>

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Listen address (`host`) | text | `127.0.0.1` | Loopback is enough; use `0.0.0.0` with a token when exposing it |
| Auth token (`auth_token`) | text | empty | Empty means no check; when set, requests must carry a `Bearer` token |
| Proxy to the upstream public endpoint when local rendering fails (`fallback_enabled`) | switch | on | Forwards to the fallback endpoint so text-to-image keeps working |
| Fallback endpoint (`fallback_endpoint`) | text | `https://t2i.rcfortress.site/text2img` | Must include `/text2img`; a self-hosted URL works too |

</details>

### Wire into AstrBot (`endpoint`)

Every option here is needed on the wiring path, so they are all listed directly.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Current plugin address (`current_service_url`) | text | empty | Filled in for copying; manual edits are overwritten |
| Write AstrBot's text-to-image endpoint on startup (`auto_configure`) | switch | off | Writes the endpoint and sets the strategy to `remote` |
| Also turn on the text-to-image output switch (`enable_t2i_output`) | switch | on | Turns the output switch on too, without which nothing renders |
| Endpoint address to write (`service_url`) | text | empty | Only needed when AstrBot reaches the plugin via another host/port |

### Rendering (`render`)

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Concurrent renders (`concurrency`) | number | `2` | Upper bound of pages rendered at once; extra requests queue. 1–4 is recommended |
| Launch Chromium with `--no-sandbox` (`no_sandbox`) | switch | on | Required for root deployments; turn off only for non-root with a sandbox |

<details>
<summary>Other rendering options (5)</summary>

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Close Chromium after being idle (`idle_timeout_seconds`) | number | `300` | Closes it to free memory; `0` never closes it |
| Warm the browser up on startup (`prelaunch`) | switch | off | Skips the cold start on the first request, at the cost of a resident process |
| Default output format (`default_type`) | dropdown | `jpeg` | Used when the request has no `options.type` |
| Default JPEG quality (`default_quality`) | number | `40` | `0`–`100`, jpeg only |
| Full-page screenshots by default (`full_page`) | switch | on | Off captures only the viewport, so long text may be cut off |

</details>

### Image cleanup (`storage`)

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Image retention (`image_lifetime_hours`) | number | `24` | How long rendered images are kept before deletion |
| Cleanup interval (`cleanup_interval_minutes`) | number | `60` | Informational only; cleanup runs on a minute-level check |

### Fonts (`fonts`)

Downloading, uploading and removing font files all happen on the **T2I Service Panel** page; the config page only keeps the switch and source settings.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Enable font supply (`enabled`) | switch | on | When off, fonts are not loaded and the page's download/upload endpoints stop working |

<details>
<summary>Download and activation (3)</summary>

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| GitHub download acceleration prefix (`github_proxy`) | text | empty | Only needed when the built-in mirrors all fail |
| Per-file upload limit (`upload_limit_mb`) | number | `128` | Size cap for uploads and link downloads |
| Restart the browser after font changes (`restart_browser_on_change`) | switch | on | New fonts only apply after the render process restarts |

</details>

### Templates (`templates`)

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Plugin template refresh interval (`refresh_minutes`) | number | `15` | How often the weather, mood photo and catchphrase change (1–1440 minutes); press **Apply** again to take effect |

## Other

### Install and first run

1. Install the plugin from the AstrBot WebUI plugin market, or drop this repository into `data/plugins/astrbot_plugin_t2i_service` (the directory name must match the plugin name).
2. Restart AstrBot and confirm **Enable the embedded render service** is on and **Listen port** is `8999` (or a port of your choice).
3. Turn on **Download Chromium** in the plugin config and save to install it in the background (~200 MB, npmmirror by default); `/t2i_install` in chat does the same. Missing system libraries are installed next.
4. Run `/t2i_test` to verify rendering; to let the plugin take over AstrBot's endpoint, also turn on **Write AstrBot's text-to-image endpoint on startup**.

### Pointing AstrBot at this plugin

The plugin exposes the same text-to-image endpoint as upstream, and AstrBot has to know its address:

- **Find the address**: **Current plugin address** under **Wire into AstrBot** is exactly what belongs in AstrBot's text-to-image API address (default `http://127.0.0.1:8999/text2img`, updated automatically when you change the listen address or port).
- **Automatic**: turn on **Write AstrBot's text-to-image endpoint on startup**; the plugin writes that address and switches to the remote strategy. AstrBot's output switch is controlled by **Also turn on the text-to-image output switch** (on by default).
- **Manual**: make sure AstrBot's config has the three values below, then restart:

```jsonc
{
  "t2i": true,
  "t2i_strategy": "remote",
  "t2i_endpoint": "http://127.0.0.1:8999/text2img"
}
```

### Requirements

- **Python 3.11 or newer** (AstrBot's minimum version is declared in the plugin metadata and enforced at install time).
- **`playwright` (pinned to 1.64.1)**: installed with the plugin dependencies, nothing to do by hand.
- **Chromium**: downloaded and managed by the plugin (~200 MB of disk); an existing system browser works too.
- **Automatic library installation** needs the AstrBot process to run as root with `apt-get` or `dnf` in the container.
- **Memory**: every concurrent render holds one browser page, so a larger `render.concurrency` costs more memory.

### HTTP API

For your own integrations (AstrBot calls these itself, so you rarely touch them):

- `POST /text2img/generate` — body with `html`, or `tmpl` + `tmpldata`, plus optional `options`
- `GET /text2img/data/{image_id}` — fetch images in JSON mode
- `GET /healthz` — health check

The interface matches upstream; the full contract is in the upstream docs at [`docs/standalone/README.md`](docs/standalone/README.md).

### Standalone mode (optional)

Only matters when you skip the plugin and run a **separate service** (to share one service between several AstrBot instances, or to host it on another machine):

```bash
docker build -t astrbot-t2i-service .
docker run -d -p 8999:8999 astrbot-t2i-service
```

Environment variables, S3/R2 storage and Prometheus metrics behave as upstream — see [`docs/standalone/README.md`](docs/standalone/README.md) and [`docs/standalone/MONITORING.md`](docs/standalone/MONITORING.md). When the service lives outside AstrBot, AstrBot's text-to-image API address must be an address AstrBot can actually reach, and the service should have an auth token set — never expose the bare port to the internet.

### FAQ

**Chromium download is slow or fails**
npmmirror is used by default; you can change **Playwright download host**, or point **Browser install directory** at an existing directory. If the switch closed while **Chromium ready** is still off, that run failed — check `/t2i_status` for the state and the log tail.

**Rendering fails with `error while loading shared libraries: libnspr4.so` or `BrowserType.launch: Target page, context or browser has been closed`**
The container is missing Chromium's system libraries. Run `/t2i_install` to install them (needs root with `apt-get`/`dnf`); when the environment does not allow it, the plugin prints the command to run manually.

**Launching the browser fails with `No usable sandbox!`**
The official image runs as root — keep **Launch Chromium with `--no-sandbox`** on.

**Fallback endpoint errors or slow images**
Until Chromium is ready, requests go to the fallback endpoint (the official public endpoint runs abroad and answers `render queue wait exceeded` when the queue is long). Once local Chromium is installed the fallback is no longer used; you can also point it at your own service or turn the proxy off.

**Port already in use**
Change **Listen port**, then re-enable **Write AstrBot's text-to-image endpoint on startup**, or update `t2i_endpoint` manually.

**Text-to-image broken after an AstrBot restart**
Check that `t2i_endpoint` still targets the plugin's listen address; `/t2i_status` shows the address the plugin actually uses.

**A font is installed but the template still uses something else**
Click **Render preview** under **Overview**: if several rows look identical, none of those fonts are in effect. The usual causes are a render process that has not restarted yet (**Reload fonts**) or a file that is really a renamed web page or archive (uploads and downloads both check the font header). Note also that `Noto Sans SC` and a system package's `Noto Sans CJK SC` are different names; one cannot stand in for the other.

**I applied a template but the output still looks the same**
The first apply needs one AstrBot restart; switching afterwards is instant. If nothing changed, check the chips at the top of the **T2I templates** tab — if the plugin's template is not in use, hit **Apply** again.

**Disk usage**
Chromium takes ~200 MB; rendered images are kept 24 hours; fonts add up per family — a full template set usually costs 20–60 MB.

**Where are the logs**
They sit together with the plugin's other logs — filter by the plugin name or the `[t2i]` prefix; a failed render records the reason and the traceback.

### Development

```bash
pip install -r requirements-standalone.txt pytest pytest-asyncio
pytest tests/ -q
```

Plugin tests need no real Chromium (the renderer is faked); end-to-end runs with a real browser live in `tools/envlab/`.

### License and credits

Apache-2.0, same as upstream. The rendering core is ported from [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service); the upstream `LICENSE` is kept and the porting notes live in [`NOTICE`](NOTICE).
