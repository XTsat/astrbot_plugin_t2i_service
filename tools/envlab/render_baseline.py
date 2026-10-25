#!/usr/bin/env python3
"""Baseline Chromium render benchmark for AstrBot's real text-to-image template.

Renders AstrBot's production `base.html` (Markdown -> HTML -> PNG) with a
Playwright Chromium and records cold/warm latency, browser RSS and a DOM
sanity report (CJK text, code blocks, tables, KaTeX, Shiki).

Deliberately depends on **playwright only** (jinja2 is used if present, with a
plain substitution fallback) so it can validate the environment before any
plugin/service code exists.

Typical use inside the disposable container:

    docker exec t2i-envlab python /opt/plugin/tools/envlab/render_baseline.py \
        --template /opt/astrbot-src/astrbot/core/utils/t2i/template/base.html \
        --out /AstrBot/data/shots --runs 3

Exit code 0 means: browser launched, template rendered, image written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

DEFAULT_TEMPLATE = "/opt/astrbot-src/astrbot/core/utils/t2i/template/base.html"
DEFAULT_OUT = "/AstrBot/data/shots"
BASE_VERSION_FALLBACK = "4.29.2"

SAMPLE_MARKDOWN = """\
# AstrBot 文转图基线测试 / T2I baseline

这是一段中文正文，用于验证 CJK 字体回退是否正常：天地玄黄，宇宙洪荒，AstrBot 把 Markdown 渲染成图片。
Mixed 中英混排 with numbers 12345 and symbols -> => & <tag>.

## 二级标题：Markdown 能力

**粗体文本**、*斜体文本*、~~删除线~~、`inline code`、[链接文本](https://astrbot.app)。

- 无序列表第一项
- 无序列表第二项，包含 `code`
  1. 嵌套有序列表
  2. 第二项
- [x] 已完成任务
- [ ] 未完成任务

> 引用块：用于验证 blockquote 的边框与底色。
> 第二行引用。

### 代码块（Shiki 高亮）

```python
async def render(text: str) -> bytes:
    \"\"\"把文本渲染成图片。\"\"\"
    html = template.render(text=text, version="4.29.2")
    return await page.screenshot(full_page=True)
```

### 表格

| 指标 | 冷启动 | 热渲染 |
| --- | --- | --- |
| 耗时 | 以实测为准 | 以实测为准 |
| 内存 | RSS 采样 | RSS 采样 |

### 数学公式

行内公式 $E = mc^2$，独立公式：

$$\\int_0^1 x^2 dx = \\frac{1}{3}$$

---

结束行：emoji 😀🎉 与长链接 https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/utils/t2i/template/base.html
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def browser_procs(marker: str) -> tuple[int, int, list[str]]:
    """Sum VmRSS of every process whose cmdline mentions `marker` (browser tree).

    Returns (rss_bytes, process_count, sorted executable basenames).
    """
    total = 0
    count = 0
    exes: set[str] = set()
    needle = marker.encode()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
            if needle not in cmdline:
                continue
            status = (entry / "status").read_text()
        except OSError:
            continue
        match = re.search(r"^VmRSS:\s+(\d+) kB", status, re.MULTILINE)
        if match:
            total += int(match.group(1)) * 1024
            count += 1
        argv0 = cmdline.split(b"\x00")[0].decode(errors="replace")
        exes.add(Path(argv0).name)
    return total, count, sorted(exes)


def self_rss_bytes() -> int:
    try:
        text = Path("/proc/self/status").read_text()
    except OSError:
        return 0
    match = re.search(r"^VmRSS:\s+(\d+) kB", text, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else 0


def mb(value: int | float) -> float:
    return round(float(value) / (1024 * 1024), 2)


def render_template(source: str, data: dict) -> str:
    """Prefer real Jinja2 semantics; fall back to the two placeholders base.html uses."""
    try:
        import jinja2  # noqa: PLC0415  (optional dependency)
    except ImportError:
        out = source.replace("{{ version }}", str(data.get("version", "")))
        return out.replace("{{ text | safe }}", str(data.get("text", "")))
    env = jinja2.Environment(autoescape=False)
    return env.from_string(source).render(**data)


def inject_shiki(html: str, runtime_path: str) -> str:
    """Insert AstrBot's T2I shiki runtime before </head> so `pre.shiki` blocks appear.

    AstrBot injects the same IIFE into the template before Jinja rendering
    (astrbot/core/utils/t2i/network_strategy.py:74); we inject into the rendered
    HTML instead, which is equivalent and avoids Jinja ``{% raw %}`` handling.
    """
    runtime = Path(runtime_path).read_text(encoding="utf-8")
    runtime = re.sub(r"</(script)", r"<\\/\1", runtime, flags=re.IGNORECASE)
    script = f'<script id="astrbot-t2i-shiki-runtime">{runtime}</script>'
    match = re.search(r"</head\s*>", html, flags=re.IGNORECASE)
    if not match:
        return f"{script}\n{html}"
    return f"{html[: match.start()]}{script}\n{html[match.start():]}"


READY_JS = """() => {
  const c = document.getElementById('content');
  return !!c && c.children.length > 0;
}"""

DIAG_JS = """() => {
  const c = document.getElementById('content');
  const txt = c ? c.innerText : '';
  const qsa = (s) => document.querySelectorAll(s).length;
  let selectedFont = '';
  try {
    selectedFont = getComputedStyle(document.body).fontFamily;
  } catch (e) {}
  return {
    marked: typeof window.marked,
    katex: typeof window.katex,
    renderMathInElement: typeof window.renderMathInElement,
    shiki: typeof window.AstrBotT2IShiki,
    content_child_count: c ? c.children.length : 0,
    h1: qsa('#content h1'), h2: qsa('#content h2'), h3: qsa('#content h3'),
    paragraphs: qsa('#content p'),
    code_blocks: qsa('#content pre code'),
    shiki_blocks: qsa('#content pre.shiki'),
    tables: qsa('#content table'),
    list_items: qsa('#content li'),
    blockquotes: qsa('#content blockquote'),
    katex_nodes: qsa('#content .katex'),
    checkboxes: qsa('#content input[type=checkbox]'),
    heading_rule: qsa('#content h2')[0] ? getComputedStyle(document.querySelectorAll('#content h2')[0]).borderTopWidth : null,
    body_font_family: selectedFont,
    h1_font_family: qsa('#content h1') ? getComputedStyle(document.querySelectorAll('#content h1')[0]).fontFamily : '',
    font_status: document.fonts ? document.fonts.status : 'n/a',
    misans_loaded: document.fonts ? document.fonts.check('25px MiSans') : null,
    cjk_chars: (txt.match(/[\\u4e00-\\u9fff]/g) || []).length,
    latin_chars: (txt.match(/[A-Za-z]/g) || []).length,
    emoji_present: /[\\u{1F300}-\\u{1FAFF}]/u.test(txt),
    inner_text_len: txt.length,
    inner_text_head: txt.slice(0, 120).replace(/\\s+/g, ' '),
    content_height: c ? Math.round(c.getBoundingClientRect().height) : 0,
    doc_scroll_width: document.documentElement.scrollWidth,
    doc_scroll_height: document.documentElement.scrollHeight,
    dpr: window.devicePixelRatio
  };
}"""


async def measure(args) -> dict:
    from playwright.async_api import async_playwright  # noqa: PLC0415

    template_path = Path(args.template)
    source = template_path.read_text(encoding="utf-8")
    markdown = (
        Path(args.markdown_file).read_text(encoding="utf-8")
        if args.markdown_file
        else SAMPLE_MARKDOWN
    )
    html = render_template(source, {"version": args.version, "text": markdown})
    if args.shiki_runtime:
        if Path(args.shiki_runtime).is_file():
            html = inject_shiki(html, args.shiki_runtime)
            shiki_note = {"injected": True, "path": args.shiki_runtime}
        else:
            shiki_note = {"injected": False, "reason": "runtime file not found"}
    else:
        shiki_note = {"injected": False, "reason": "--shiki-runtime not given"}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    result: dict = {
        "template": str(template_path),
        "template_bytes": len(source.encode()),
        "html_bytes": len(html.encode()),
        "shiki_runtime": shiki_note,
        "out_dir": str(out_dir),
        "playwright": args.playwright_version,
        "viewport": {"width": args.width, "height": args.height,
                     "device_scale_factor": args.device_scale_factor},
        "full_page": not args.no_full_page,
        "browsers_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        "python": sys.version.split()[0],
        "phases_ms": {},
        "runs": [],
        "external_failures": [],
        "console_errors": [],
        "block_external": bool(args.block_external),
        "blocked_external_requests": [],
    }

    t_start = time.perf_counter()
    async with async_playwright() as pw:
        t = time.perf_counter()
        browser = await pw.chromium.launch(headless=True, args=args.chrome_arg)
        result["phases_ms"]["launch"] = round((time.perf_counter() - t) * 1000, 1)
        result["browser_version"] = browser.version

        t = time.perf_counter()
        context = await browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=args.device_scale_factor,
            locale="zh-CN",
        )
        page = await context.new_page()
        result["phases_ms"]["context+page"] = round((time.perf_counter() - t) * 1000, 1)

        def on_request_failed(request):
            result["external_failures"].append(
                {"url": request.url[:200], "failure": request.failure}
            )

        def on_console(message):
            if message.type == "error":
                result["console_errors"].append(message.text[:300])

        if args.block_external:
            async def _route(route):
                url = route.request.url
                if url.startswith(("http://", "https://")):
                    result["blocked_external_requests"].append(url[:200])
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", _route)

        page.on("requestfailed", on_request_failed)
        page.on("console", on_console)

        rss_probe, procs, exes = browser_procs(args.browser_marker)
        result["rss_after_launch"] = {
            "mb": mb(rss_probe), "processes": procs, "executables": exes
        }

        async def one_run(index: int, cold: bool) -> dict:
            entry = {"index": index, "cold": cold}
            t0 = time.perf_counter()
            await page.set_content(html, wait_until="load", timeout=args.timeout_ms)
            ready = True
            try:
                await page.wait_for_function(READY_JS, timeout=args.timeout_ms)
            except Exception:  # noqa: BLE001 - record instead of failing the whole run
                ready = False
            entry["render_ready"] = ready
            entry["content_ready_ms"] = round((time.perf_counter() - t0) * 1000, 1)

            t1 = time.perf_counter()
            diag = await page.evaluate(DIAG_JS)
            entry["diagnostics"] = diag
            entry["diagnostics_ms"] = round((time.perf_counter() - t1) * 1000, 1)

            t2 = time.perf_counter()
            shot = await page.screenshot(full_page=not args.no_full_page)
            entry["screenshot_ms"] = round((time.perf_counter() - t2) * 1000, 1)

            entry["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            png = out_dir / (f"{args.prefix}-{'cold' if cold else f'warm{index}'}.png")
            png.write_bytes(shot)
            entry["png"] = str(png)
            entry["png_bytes"] = len(shot)
            rss, procs_, exes_ = browser_procs(args.browser_marker)
            entry["rss_mb"] = mb(rss)
            entry["chrome_processes"] = procs_
            entry["browser_executables"] = exes_
            return entry

        result["runs"].append(await one_run(0, cold=True))
        # a screenshot-only repeat isolates the capture cost from page rendering
        t = time.perf_counter()
        shot = await page.screenshot(full_page=not args.no_full_page)
        result["phases_ms"]["screenshot_only_repeat"] = round((time.perf_counter() - t) * 1000, 1)
        (out_dir / f"{args.prefix}-repeat.png").write_bytes(shot)

        for i in range(1, args.runs):
            result["runs"].append(await one_run(i, cold=False))

        await context.close()
        await browser.close()

    result["phases_ms"]["end_to_end_cold"] = round((time.perf_counter() - t_start) * 1000, 1)
    result["self_rss_mb"] = mb(self_rss_bytes())

    warm = [r["total_ms"] for r in result["runs"] if not r["cold"] and r.get("render_ready")]
    if warm:
        result["warm_total_ms"] = {
            "min": round(min(warm), 1),
            "median": round(statistics.median(warm), 1),
            "max": round(max(warm), 1),
        }
    # deduplicate repeated failure URLs (CDN retries)
    seen = set()
    unique = []
    for item in result["external_failures"]:
        key = (item["url"], str(item["failure"]))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    result["external_failures"] = unique
    result["blocked_external_count"] = len(result["blocked_external_requests"])
    result["blocked_external_requests"] = sorted(set(result["blocked_external_requests"]))[:40]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", default=DEFAULT_TEMPLATE)
    parser.add_argument("--markdown-file", default=None)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--prefix", default="baseline")
    parser.add_argument("--runs", type=int, default=3, help="total renders (1 cold + rest warm)")
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument("--device-scale-factor", type=float, default=2.0)
    parser.add_argument("--no-full-page", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--version", default=BASE_VERSION_FALLBACK,
                        help="value for the template's {{ version }}")
    parser.add_argument("--chrome-arg", action="append", default=None,
                        help="extra chromium arg (repeatable)")
    parser.add_argument("--shiki-runtime", default=None,
                        help="AstrBot template/shiki_runtime.iife.js to inject (production parity)")
    parser.add_argument("--block-external", action="store_true",
                        help="abort every http(s) request: measures offline / CDN-less rendering")
    parser.add_argument("--browser-marker", default=None,
                        help="substring identifying browser processes for RSS sampling")
    parser.add_argument("--json", default=None, help="metrics output path")
    args = parser.parse_args()

    try:
        from importlib.metadata import version as pkg_version  # noqa: PLC0415
        args.playwright_version = pkg_version("playwright")
    except Exception:  # noqa: BLE001
        args.playwright_version = "unknown"

    if args.chrome_arg is None:
        args.chrome_arg = []
    marker = args.browser_marker or os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "ms-playwright"
    args.browser_marker = marker

    if not Path(args.template).is_file():
        print(f"template not found: {args.template}", file=sys.stderr)
        return 2

    started = time.perf_counter()
    result = asyncio.run(measure(args))
    result["wall_clock_ms"] = round((time.perf_counter() - started) * 1000, 1)

    json_path = Path(args.json) if args.json else Path(args.out) / f"{args.prefix}-metrics.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- human readable summary -------------------------------------------------
    print(f"template     : {result['template']} ({result['template_bytes']} B)")
    print(f"playwright   : {result['playwright']}  chromium: {result.get('browser_version')}")
    print(f"browsers path: {result['browsers_path']}")
    print(f"viewport     : {result['viewport']}  full_page={result['full_page']}")
    print(f"shiki runtime: {result['shiki_runtime']}")
    print(f"cold e2e     : {result['phases_ms']['end_to_end_cold']} ms "
          f"(launch {result['phases_ms']['launch']} ms, "
          f"context {result['phases_ms']['context+page']} ms, "
          f"screenshot-only repeat {result['phases_ms']['screenshot_only_repeat']} ms)")
    for run in result["runs"]:
        d = run["diagnostics"]
        print(f"run {run['index']} ({'cold' if run['cold'] else 'warm'}): "
              f"content {run['content_ready_ms']} ms + shot {run['screenshot_ms']} ms = "
              f"{run['total_ms']} ms | png {mb(run['png_bytes'])} MB | "
              f"rss {run['rss_mb']} MB / {run['chrome_processes']} proc | "
              f"h1={d['h1']} h2={d['h2']} pre={d['code_blocks']} table={d['tables']} "
              f"katex={d['katex_nodes']} cjk={d['cjk_chars']} ready={run['render_ready']}")
    if result.get("warm_total_ms"):
        print(f"warm total   : {result['warm_total_ms']}")
    print(f"self RSS     : {result['self_rss_mb']} MB")
    print(f"measured cjk : {result['runs'][0]['diagnostics']['cjk_chars']} chars, "
          f"body font: {result['runs'][0]['diagnostics']['body_font_family']}")
    if result.get("block_external"):
        print(f"blocked ext. : {result.get('blocked_external_count')} http(s) requests "
              f"({len(result['blocked_external_requests'])} unique)")
    if result["external_failures"]:
        print(f"failed external requests ({len(result['external_failures'])}):")
        for item in result["external_failures"]:
            print(f"  - {item['url']}  [{item['failure']}]")
    if result["console_errors"]:
        print(f"console errors ({len(result['console_errors'])}):")
        for text in result["console_errors"][:5]:
            print(f"  - {text}")
    print(f"metrics      : {json_path}")

    first = result["runs"][0]
    ok = first.get("render_ready") and first["diagnostics"]["cjk_chars"] > 0
    if not ok:
        print("RENDER CHECK FAILED: content did not become ready or no CJK text", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
