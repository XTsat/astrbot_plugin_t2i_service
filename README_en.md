<div align="center">
<h1>AstrBot T2I Service</h1>
<p><strong>HTML/template to image rendering inside the AstrBot process, ready to serve as AstrBot's text-to-image endpoint with no extra container; includes Chromium download, status detection and orphan-process cleanup.</strong></p>
<p><sub>text-to-image &nbsp;&nbsp; t2i &nbsp;&nbsp; rendering</sub></p>
<p><a href="README.md">中文</a> &nbsp;/&nbsp; <strong>English</strong></p>
</div>

## Features

The rendering engine is ported from [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service): no extra container to deploy, AstrBot's text-to-image works out of the box, and HTML / Jinja2 templates are rendered into images inside AstrBot itself.

- **No extra container**: the render service runs inside the AstrBot process — no Docker, no port mapping, no image to maintain.
- **Browser in one step**: turn on **Download Chromium** in the config and save (npmmirror mirror by default, ~200 MB); an existing system Chromium can be pointed at instead.
- **Missing libraries installed for you**: when Chromium is downloaded but cannot launch (missing `libnspr4` and friends), the plugin finds out which libraries are missing and installs them; if the environment does not allow it, you get the exact command to run.
- **No endpoint to copy by hand**: optionally the plugin writes its own address into AstrBot's text-to-image config, together with the text-to-image output switch.
- **Rendering stays available**: while local rendering is unavailable (for example before Chromium is installed), requests are proxied to the fallback endpoint and the image is stored locally before it is handed to AstrBot.
- **Status at a glance**: `/t2i_status` reports the service, the browser, system libraries, the endpoint and render stats; `/t2i_test` renders a card on the spot so you can see whether the chain works.
- **Same interface as upstream**: AstrBot and any compatible client need no code changes.

| | Public endpoint | Self-hosted service | This plugin |
| --- | --- | --- | --- |
| Extra deployment | none | Docker required | none |
| Traffic path | public network | local / LAN | inside the AstrBot process |
| Chromium supply | remote side | maintain your own image | one-click download in the config |
| Upgrades with AstrBot | unrelated | manual | follows AstrBot |

## Commands

**Installing needs no command**: turn on **Download Chromium** in the WebUI plugin config page and save — the download and install start in the background (npmmirror by default, ~200 MB), the switch turns itself off when it finishes, and **Chromium ready** on the same page shows the result. The commands below are the chat-side entry points for the same capabilities; either route works.

| Command | Permission | Description |
| --- | --- | --- |
| `/t2i_status` | everyone | Embedded service address, Chromium and system library state, render stats, plus AstrBot's current text-to-image endpoint and output switch |
| `/t2i_install` | everyone | Download and install Chromium; when the browser is present but system libraries are missing it installs those instead, streaming progress as it goes |
| `/t2i_test` | everyone | Render a self-check card and send it back to verify the whole chain |
| `/t2i_restart` | everyone | Restart the embedded render service and release Chromium |

For a first run entirely in chat, go `/t2i_status` → `/t2i_install` → `/t2i_test`: see what is missing, install it, then render one image to confirm.

## Configuration

The plugin config page shows the five groups below; the **Default** column is what takes effect right after installation.

### Install Chromium (`browser`)

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Download Chromium (`download_chromium`) | switch | off | Turn it on and save to download in the background — no command needed; the switch turns itself off once the download ends (success or failure), and turning it on again when Chromium is ready does not re-download |
| Chromium ready (`chromium_ready`) | switch | off | Written by the plugin from the real browser state, display only: on means you can render right away; manual edits are overwritten on the next start |
| Browser install directory (`browsers_path`) | text | empty | Empty uses `browsers/` inside the plugin data directory, which survives reinstalling the plugin |
| Playwright download host (`download_host`) | text | `https://cdn.npmmirror.com/binaries/playwright` | npmmirror by default, faster in mainland China; leave empty for the official CDN |
| Custom Chromium executable (`executable_path`) | text | empty | Fill in to reuse a Chromium that already exists on the system or in another container; the plugin then skips downloading |
| Install missing system libraries automatically (`auto_install_deps`) | switch | on | Installs missing libraries on demand; needs root plus `apt-get`/`dnf`, and degrades to a hint when unavailable |
| Clean up leftover Chromium on startup (`cleanup_orphans`) | switch | on | A killed plugin may leave browser processes behind; they are reclaimed on the next start |
| Browser install timeout (`install_timeout_seconds`) | number | `900` | Raise it on a slow network; in seconds |

### Embedded render service (`server`)

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Enable the embedded render service (`enabled`) | switch | on | When off, only the commands remain and no port is opened (deploy a standalone service in that case) |
| Listen address (`host`) | text | `127.0.0.1` | The plugin runs inside AstrBot, so loopback is enough; switch to `0.0.0.0` only when a machine or container outside AstrBot must reach it, and set an auth token as well |
| Listen port (`port`) | number | `8999` | Matches upstream; after changing it, update AstrBot's text-to-image endpoint too |
| Auth token (`auth_token`) | text | empty | Empty means no check; when set, requests must carry `Authorization: Bearer <token>` |
| Proxy to the upstream public endpoint when local rendering fails (`fallback_enabled`) | switch | on | For example before Chromium is ready, requests are forwarded to the fallback endpoint so text-to-image keeps working |
| Fallback endpoint (`fallback_endpoint`) | text | `https://t2i.rcfortress.site/text2img` | Must include `/text2img`; the image is downloaded locally before it is returned, and a self-hosted URL works too |

### Wire into AstrBot (`endpoint`)

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Current plugin address (`current_service_url`) | text | empty | Written by the plugin from the listen address and port (default `http://127.0.0.1:8999/text2img`); copy it into AstrBot's text-to-image API address. Display only — manual edits are overwritten on the next start |
| Write AstrBot's text-to-image endpoint on startup (`auto_configure`) | switch | off | Writes the address above and sets the strategy to `remote`; restart AstrBot to apply |
| Also turn on the text-to-image output switch (`enable_t2i_output`) | switch | on | Nothing is rendered while AstrBot's output switch (`t2i`) stays off; this makes the auto-write turn it on. Turn it off to change the address only |
| Endpoint address to write (`service_url`) | text | empty | Only needed when AstrBot reaches the plugin through another host or port; empty derives it from the listen address |

### Rendering (`render`)

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Concurrent renders (`concurrency`) | number | `2` | Upper bound of pages rendered at the same time; extra requests queue. 1–4 is recommended, larger values eat memory |
| Launch Chromium with `--no-sandbox` (`no_sandbox`) | switch | on | The official AstrBot image runs as root, where Chromium only starts with `--no-sandbox`; turn it off for a non-root deployment that wants the sandbox |
| Close Chromium after being idle (`idle_timeout_seconds`) | number | `300` | Frees process memory; the next render pays a ~1–2 s cold start. `0` never closes it |
| Warm the browser up on startup (`prelaunch`) | switch | off | The first request skips the cold start, at the cost of one Chromium process staying resident |
| Default output format (`default_type`) | dropdown | `jpeg` | Used when the request has no `options.type`; `jpeg` is smaller, `png` is sharper |
| Default JPEG quality (`default_quality`) | number | `40` | `0`–`100`, jpeg only; 40–80 is a good balance |
| Full-page screenshots by default (`full_page`) | switch | on | Off captures only the viewport (1080p tall by default), so long text may be cut off |

### Image cleanup (`storage`)

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| Image retention (`image_lifetime_hours`) | number | `24` | AstrBot copies images away after fetching them; expired images on the plugin side are deleted on a timer |
| Cleanup interval (`cleanup_interval_minutes`) | number | `60` | Informational only: the plugin actually checks every minute and deletes expired files |

## Install and first run

1. Install the plugin from the AstrBot WebUI plugin market, or drop this repository into `data/plugins/astrbot_plugin_t2i_service` (the directory name must match the plugin name).
2. Installation only `pip install`s `playwright` (~40 MB Python package); Chromium is **not** downloaded automatically.
3. Restart AstrBot and confirm **Enable the embedded render service** is on and **Listen port** is `8999` (or a port of your choice).
4. Turn on **Download Chromium** in the plugin config and save to download and install in the background (~200 MB, npmmirror by default); `/t2i_install` in chat does the same. If the container lacks system libraries, the same flow installs them next.
5. Run `/t2i_test` to verify rendering; text-to-image is ready once it succeeds. To let the plugin take over AstrBot's endpoint, also turn on **Write AstrBot's text-to-image endpoint on startup**.

Rendered images land in the plugin data directory `data/plugin_data/astrbot_plugin_t2i_service/data/` and are deleted after 24 hours by default; Chromium lands in `browsers/` in the same directory.

## Pointing AstrBot at this plugin

The plugin exposes the same text-to-image endpoint as upstream, and AstrBot has to know its address:

- **Find the address**: **Current plugin address** under **Wire into AstrBot** is exactly what belongs in AstrBot's text-to-image API address (default `http://127.0.0.1:8999/text2img`, updated automatically when you change the listen address or port).
- **Automatic**: turn on **Write AstrBot's text-to-image endpoint on startup**; the plugin writes that address and switches to the remote strategy. AstrBot's text-to-image output switch (`t2i`) is controlled by **Also turn on the text-to-image output switch** (on by default) — nothing is rendered while the output switch stays off.
- **Manual**: in AstrBot's `data/cmd_config.json`:

```jsonc
{
  "t2i": true,
  "t2i_strategy": "remote",
  "t2i_endpoint": "http://127.0.0.1:8999/text2img"
}
```

Restart AstrBot afterwards. `/t2i_status` reports the current endpoint, strategy and output switch state.

## Requirements

- **AstrBot 4.16 or newer** (below 5.x): the plugin config, event and logging APIs it uses come from that range.
- **Python 3.11 or newer**.
- **`playwright` (pinned to 1.64.1)**: installed with the plugin dependencies, nothing to do by hand.
- **Chromium**: downloaded and managed by the plugin (~200 MB of disk); an existing system browser works too.
- **Automatic library installation** needs the AstrBot process to run as root with `apt-get` or `dnf` in the container.
- **Memory**: every concurrent render holds one browser page, so a larger `render.concurrency` costs more memory.

## HTTP API

For your own integrations (AstrBot calls these itself, so you rarely touch them):

- `POST /text2img/generate` — body with `html`, or `tmpl` + `tmpldata`, plus optional `options`
- `GET /text2img/data/{image_id}` — fetch images in JSON mode
- `GET /healthz` — health check

The interface matches upstream; the full contract is in the upstream docs at [`docs/standalone/README.md`](docs/standalone/README.md).

## Standalone mode (optional)

As a plugin, rendering happens inside the AstrBot process — there is no "plugin on the host, AstrBot in a container" combination. This section only matters when you skip the plugin and run a **separate service** instead (to share one service between several AstrBot instances, or to host it on another machine). The upstream capability is intact:

```bash
docker build -t astrbot-t2i-service .
docker run -d -p 8999:8999 astrbot-t2i-service
```

Environment variables, S3/R2 storage and Prometheus metrics behave as upstream — see [`docs/standalone/README.md`](docs/standalone/README.md) and [`docs/standalone/MONITORING.md`](docs/standalone/MONITORING.md).

When the standalone service lives outside AstrBot, AstrBot's text-to-image API address must be an address AstrBot can actually reach (same machine: `http://127.0.0.1:8999/text2img`; another container or host: the host's LAN IP or a domain), and the service should have an auth token set — never expose the bare port to the internet.

## FAQ

**Chromium download is slow or fails**
npmmirror is used by default; you can also change **Playwright download host**, or point **Browser install directory** at an existing browser directory. The **Download Chromium** switch turns itself off when the download ends: if it closed while **Chromium ready** is still off, that run failed — check the install state and log tail in `/t2i_status`.

**Rendering fails with `error while loading shared libraries: libnspr4.so` or `BrowserType.launch: Target page, context or browser has been closed`**
The container is missing Chromium's system libraries (the browser itself is downloaded, but `libnspr4`, `libnss3`, `libatk` ... are not). Run `/t2i_install` to install them (AstrBot must run as root with `apt-get`/`dnf` available); when the environment does not allow it, the plugin prints the command to run manually.

**Launching the browser fails with `No usable sandbox!`**
The official AstrBot image runs as root — keep **Launch Chromium with `--no-sandbox`** on.

**Fallback endpoint errors or slow images**
Until Chromium is ready, requests are proxied to **Fallback endpoint** (the official public endpoint `https://t2i.rcfortress.site/text2img` by default; it runs abroad and answers `render queue wait exceeded` when the queue is long; a retired default left over from an older version is replaced by the current one at startup, addresses you filled in yourself are kept). Once local Chromium is installed the fallback is no longer used; you can also point it at your own service or turn **Proxy to the upstream public endpoint when local rendering fails** off.

**Port already in use**
Change **Listen port**, then re-enable **Write AstrBot's text-to-image endpoint on startup**, or update AstrBot's `t2i_endpoint` manually.

**Text-to-image broken after an AstrBot restart**
Check that `t2i_endpoint` still targets the plugin's listen address; `/t2i_status` shows the address the plugin actually uses when the port was changed and not synced.

**Disk usage**
Chromium takes ~200 MB in the plugin data directory; rendered images are kept 24 hours and cleaned on a timer.

**Where are the logs**
Render and cleanup logs sit together with the plugin's other logs — filter the AstrBot log by the plugin name or the `[t2i]` prefix. Successful renders do not log HTML content. Every generation leaves an access line (`POST /text2img/generate → status`, with duration and request size); a failed render records the reason and the traceback next to it, so you can match the two by timestamp.

## Development

```bash
pip install -r requirements-standalone.txt pytest pytest-asyncio
pytest tests/ -q
```

- Plugin tests need no real Chromium (the renderer is faked); end-to-end runs with a real browser live in `tools/envlab/`.
- `requirements.txt` holds the plugin runtime dependency only (`playwright`); standalone mode uses `requirements-standalone.txt`.

## License and credits

Apache-2.0, same as upstream. The rendering core is ported from [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service); the upstream `LICENSE` is kept and the porting notes live in [`NOTICE`](NOTICE).
