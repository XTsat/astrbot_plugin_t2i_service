# Text-to-Image Service Plugin for AstrBot

Brings [astrbot-t2i-service](https://github.com/AstrBotDevs/astrbot-t2i-service) into the AstrBot process: no extra container, no port mapping — AstrBot's text-to-image feature works out of the box, rendering HTML/templates inside AstrBot itself.

English | [简体中文](README.md)

![Plugin status card](docs/assets/status-test-card.png)

## Why

AstrBot's text-to-image defaults to a remote endpoint (`t2i_strategy: remote` + `t2i_endpoint`). The public endpoint works but latency depends on your network; self-hosting means yet another container, port and image to maintain.

This plugin embeds the same render service directly into AstrBot:

- reuses AstrBot's own Python environment — no extra container or port mapping;
- renders in-process over loopback, no cross-host latency;
- ships a Chromium installer (mirror-friendly), status checks, orphan process cleanup and optional endpoint auto-configuration;
- keeps the upstream HTTP contract, so AstrBot and any compatible client need no code changes.

| | Public endpoint | Standalone container | This plugin |
| --- | --- | --- | --- |
| Extra deployment | none | Docker required | none |
| Latency | public network | LAN/local | in-process |
| Chromium supply | remote | maintain your own image | one-command install |
| Version sync with AstrBot | n/a | manual | same process |

## Install

1. Install from the AstrBot WebUI plugin market (or drop this repo into `data/plugins/astrbot_plugin_t2i_service`).
2. Installation only `pip install`s `playwright` (~40 MB); Chromium is **not** downloaded automatically.
3. Restart AstrBot and confirm `server.enabled` / `server.port` (default `127.0.0.1:8999`).
4. Run `/t2i_install` to download Chromium (~200 MB, npmmirror by default; `/t2i_install mirror` to switch).
5. Run `/t2i_test` to verify. To auto-wire AstrBot's endpoint, enable `endpoint.auto_configure` or click **Apply endpoint** on the WebUI plugin page.

> Rendered images land in the plugin data directory (`data/plugin_data/astrbot_plugin_t2i_service/data/`) and are cleaned after 24 hours.

## Commands

| Command | Description |
| --- | --- |
| `/t2i_status` | Service URL, browser state, render stats, concurrency |
| `/t2i_install` | Download and install Chromium (`/t2i_install mirror` for the CN mirror) |
| `/t2i_test` | Render a self-check card and send the image back |
| `/t2i_restart` | Restart the embedded service and close the browser |

## Configuration

Configuration groups (see `_conf_schema.json`):

### `server`

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Enable the embedded service |
| `host` | `127.0.0.1` | Listen address (keep loopback for local-only use) |
| `port` | `8999` | Listen port |
| `auth_token` | empty | When set, requires `Authorization: Bearer <token>` |
| `fallback_enabled` | `true` | Proxy to the upstream public endpoint when local rendering fails |
| `fallback_endpoint` | `https://t2i.soulter.top/text2img` | Fallback target |

### `render`

| Key | Default | Description |
| --- | --- | --- |
| `concurrency` | `2` | Concurrent renders (open pages) |
| `no_sandbox` | `true` | Launch Chromium with `--no-sandbox`; required when AstrBot runs as root |
| `idle_timeout_seconds` | `300` | Close Chromium after this idle period |
| `prelaunch` | `false` | Warm up the browser at startup |
| `default_type` | `jpeg` | Output format when the request omits `type` |
| `default_quality` | `40` | Default JPEG quality |
| `full_page` | `true` | Full-page screenshots by default |

### `browser`

| Key | Default | Description |
| --- | --- | --- |
| `browsers_path` | empty | Browser install dir; defaults to the plugin data dir `browsers/` |
| `download_host` | `https://cdn.npmmirror.com/binaries/playwright` | Playwright download host |
| `executable_path` | empty | Reuse an existing Chromium binary |
| `auto_install_deps` | `false` | Also install system deps (needs root + apt/dnf) |
| `cleanup_orphans` | `true` | Kill leftover Chromium on startup |
| `install_timeout_seconds` | `900` | Download timeout |

### `endpoint`

| Key | Default | Description |
| --- | --- | --- |
| `auto_configure` | `false` | Write the endpoint into AstrBot's config on startup |
| `service_url` | empty | Endpoint to write; auto-derived from the listen address when empty |

### `storage`

| Key | Default | Description |
| --- | --- | --- |
| `image_lifetime_hours` | `24` | Image retention |
| `cleanup_interval_minutes` | `60` | Cleanup interval |

## Wiring into AstrBot

- **Automatic**: enable `endpoint.auto_configure` (or click **Apply endpoint**), the plugin writes the endpoint and switches to the remote strategy.
- **Manual**: in AstrBot's `data/cmd_config.json`:

```jsonc
{
  "t2i_strategy": "remote",
  "t2i_endpoint": "http://127.0.0.1:8999/text2img"
}
```

Restart AstrBot afterwards. `/t2i_status` reports whether the configured endpoint matches the plugin.

## HTTP API

Routes are reachable under `/api/plug/astrbot_plugin_t2i_service` (legacy) and, from the plugin page, through
`/api/v1/plugins/extensions/astrbot_plugin_t2i_service/...` — the page runs in a sandboxed iframe and calls
`window.AstrBotPluginView.apiGet("status")`, which the dashboard proxies with its login session (verified):

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/{plugin}/status` | Aggregated service/browser status |
| `POST` | `/{plugin}/install` | Start Chromium download |
| `GET` | `/{plugin}/install/state` | Download progress and logs |
| `POST` | `/{plugin}/render-test` | Render the test card and return an image |
| `POST` | `/{plugin}/apply-endpoint` | Write the endpoint into AstrBot's config |
| `POST` | `/{plugin}/restart` | Reload the plugin |
| `GET` | `/{plugin}/status-card` | Status card as JSON with `data.image` base64 data URL |

Render endpoints (upstream-compatible contract):

- `POST /text2img/generate` — `html` or `tmpl` + `tmpldata` + `options`
- `GET /text2img/data/{image_id}` — fetch JSON-mode images
- `GET /healthz` — health check

See [`docs/APIS.md`](docs/APIS.md) and [`docs/PAGES-API.md`](docs/PAGES-API.md).

## WebUI page

The plugin ships a status page (`pages/t2i/index.html`): live service/browser status, Chromium download progress, render stats, plus **Render test**, **Apply endpoint** and **Reload plugin** buttons. It follows the dashboard light/dark theme; entry point is `#/plugin-view/astrbot_plugin_t2i_service/t2i`.

![Plugin status page](docs/assets/plugin-page.png)

## Measured

Verified end-to-end on the official AstrBot image with real Chromium 1248:

| Metric | Value |
| --- | --- |
| First render (cold, incl. browser launch) | 1.08 s |
| Warm render | 0.63 s |
| JPEG size (800×720, q40) | ~7 KB |
| Status card PNG (800×1080) | ~267 KB |
| AstrBot's own `NetworkRenderStrategy` call | works (client → plugin → Chromium → JPEG) |

![End-to-end render](docs/assets/integration-render.jpeg)

## Layout

| Path | Responsibility |
| --- | --- |
| `main.py` | Plugin entry: lifecycle, 4 commands, 7 Web APIs |
| `t2i_plugin/` | Adapter layer: config, Chromium supply, embedded HTTP server, cards |
| `t2i_core/` | Rendering core ported from upstream: Playwright render, storage, metrics |
| `service/main.py` | Standalone service entry (FastAPI, upstream capability preserved) |
| `pages/t2i/` | WebUI plugin page |
| `docs/` | Docs: `APIS.md`, `PAGES-API.md`, `ENV-REPORT.md`, `standalone/` |
| `tools/envlab/` | Containerized verification scripts |

## Standalone mode (optional)

Prefer a container? The upstream capability is intact:

```bash
docker build -t astrbot-t2i-service .
docker run -d -p 8999:8999 astrbot-t2i-service
```

Env vars, S3/R2 storage and Prometheus metrics behave as upstream — see [`docs/standalone/README.md`](docs/standalone/README.md) and [`docs/standalone/MONITORING.md`](docs/standalone/MONITORING.md).

## FAQ

**Chromium download is slow/fails** — use `/t2i_install mirror`, set `browser.download_host`, or point `browser.browsers_path` at an existing `PLAYWRIGHT_BROWSERS_PATH`.

**`No usable sandbox!`** — the official AstrBot image runs as root; keep `render.no_sandbox=true`.

**Port already in use** — change `server.port`, then re-apply the endpoint.

**Text-to-image broken after a restart** — check `t2i_endpoint` still targets the plugin port; `/t2i_status` flags a mismatch.

**Disk usage** — Chromium ~200 MB in the plugin data dir; rendered images are kept 24 h and cleaned periodically.

**Logs** — `[Plug]` prefix; successful renders do not log HTML content.

## Development

```bash
pip install -r requirements-standalone.txt pytest pytest-asyncio
pytest tests/ -q
```

- Plugin tests need no real Chromium (renderer is faked); end-to-end runs live in `tools/envlab/`.
- `requirements.txt` holds runtime deps only (`playwright`); standalone mode uses `requirements-standalone.txt`.

## License

Apache-2.0, same as upstream. The upstream `LICENSE` is kept; porting notes live in [`NOTICE`](NOTICE).
