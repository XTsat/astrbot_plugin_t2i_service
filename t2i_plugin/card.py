"""渲染用的 HTML 卡片：测试卡片与状态卡片。

两者都是自包含 HTML（内联 CSS、无外链资源），因此即使渲染环境完全断网也能
正常出图；这也是 /t2i_test 用来验证「文转图链路是否真的打通」的前提。
"""

from __future__ import annotations

import html as _html
import time
from pathlib import Path

_BASE_STYLE = """
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 28px 30px;
    width: 720px;
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC",
                 "Segoe UI", Roboto, sans-serif;
    background: linear-gradient(150deg, #101725 0%, #1b2740 55%, #23324f 100%);
    color: #e8eefc;
  }
  .card {
    background: rgba(255, 255, 255, 0.045);
    border: 1px solid rgba(140, 180, 255, 0.22);
    border-radius: 18px;
    padding: 22px 24px 20px;
    box-shadow: 0 18px 40px rgba(0, 0, 0, 0.35);
  }
  .head { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
  .dot { width: 12px; height: 12px; border-radius: 50%; flex: none; }
  .ok { background: #38d39f; box-shadow: 0 0 12px rgba(56, 211, 159, 0.85); }
  .bad { background: #ff6b6b; box-shadow: 0 0 12px rgba(255, 107, 107, 0.85); }
  .warn { background: #ffc857; box-shadow: 0 0 12px rgba(255, 200, 87, 0.85); }
  h1 { font-size: 21px; margin: 0; font-weight: 650; letter-spacing: 0.3px; }
  .sub { color: #9fb2d4; font-size: 12.5px; margin: 6px 0 18px; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  td { padding: 8px 0; vertical-align: top; border-bottom: 1px dashed rgba(150, 180, 230, 0.16); }
  tr:last-child td { border-bottom: none; }
  td.k { color: #9fb2d4; width: 168px; white-space: nowrap; }
  td.v { color: #eaf1ff; word-break: break-all; font-weight: 500; }
  .mono { font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; font-size: 12.5px; }
  .pill {
    display: inline-block; padding: 2px 10px; border-radius: 999px;
    font-size: 12px; font-weight: 600;
  }
  .pill.ok { background: rgba(56, 211, 159, 0.16); color: #6ff0c0; }
  .pill.bad { background: rgba(255, 107, 107, 0.16); color: #ffa3a3; }
  .pill.warn { background: rgba(255, 200, 87, 0.16); color: #ffd98a; }
  .foot {
    margin-top: 18px; padding-top: 12px; border-top: 1px solid rgba(150, 180, 230, 0.16);
    color: #8fa3c6; font-size: 11.5px; display: flex; justify-content: space-between;
  }
  .swatch { display: flex; gap: 6px; margin-top: 14px; }
  .swatch i { height: 6px; border-radius: 3px; flex: 1; }
"""


def _esc(value: object) -> str:
    return _html.escape("" if value is None else str(value))


def _pill(ok: bool, text_ok: str, text_bad: str) -> str:
    return (
        f'<span class="pill {"ok" if ok else "bad"}">'
        f"{_esc(text_ok if ok else text_bad)}</span>"
    )


def _document(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{_esc(title)}</title><style>{_BASE_STYLE}</style></head>\n"
        f"<body>{body}</body></html>"
    )


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _version_label(raw: object) -> str:
    # metadata.yaml 的 version 允许写 1.0.0 或 v1.0.0，卡片统一补成 vX.Y.Z，避免出现 vv1.0.0
    text = str(raw or "").strip()
    if not text:
        return ""
    return text if text.startswith("v") else f"v{text}"


def build_test_card(
    quiet: bool = False,
    server_running: bool = True,
    endpoint: str = "",
    chromium_installed: bool = False,
    chromium_path: str = "",
    renderer_connected: bool = False,
) -> str:
    """构造测试卡片；quiet=True 用于插件启动时预热（内容更精简）。"""
    healthy = server_running and chromium_installed
    if quiet:
        return _document(
            "t2i prewarm",
            '<div class="card"><div class="head">'
            '<span class="dot ok"></span><h1>Chromium 预热</h1></div>'
            '<div class="sub">此页面仅用于启动浏览器进程，可忽略。</div></div>',
        )

    chromium_name = Path(chromium_path).name if chromium_path else "（未找到）"
    body = f"""
<div class="card">
  <div class="head">
    <span class="dot {"ok" if healthy else "bad"}"></span>
    <h1>文转图服务自检</h1>
  </div>
  <div class="sub">AstrBot 插件内嵌渲染服务 · 若本图正常显示，说明链路已打通</div>
  <table>
    <tr><td class="k">内嵌服务</td><td class="v">
      {"运行中" if server_running else "未运行"}
      {_pill(server_running, "RUNNING", "STOPPED")}
      <div class="mono">{_esc(endpoint or "（无监听地址）")}</div>
    </td></tr>
    <tr><td class="k">Chromium</td><td class="v">
      {"已安装" if chromium_installed else "未安装"}
      {_pill(chromium_installed, "READY", "MISSING")}
      <div class="mono">{_esc(chromium_name)}</div>
    </td></tr>
    <tr><td class="k">浏览器连接</td><td class="v">
      {_pill(renderer_connected, "已连接（复用在跑）", "空闲（首次渲染时启动）")}
    </td></tr>
    <tr><td class="k">默认输出</td><td class="v">JPEG · quality 40 · full_page</td></tr>
    <tr><td class="k">渲染时间</td><td class="v mono">{_esc(_now())}</td></tr>
  </table>
  <div class="swatch">
    <i style="background:#38d39f"></i><i style="background:#5aa9ff"></i>
    <i style="background:#ffc857"></i><i style="background:#ff6b6b"></i>
    <i style="background:#b18cff"></i>
  </div>
  <div class="foot">
    <span>如果能看到渐变背景与彩色色块，说明图片渲染正常。</span>
    <span class="mono">t2i_service</span>
  </div>
</div>"""
    return _document("文转图服务自检", body)


def build_status_card(payload: dict) -> str:
    """把 _status_payload() 的字典渲染成一张状态卡片。"""
    server = payload.get("server", {}) or {}
    browser = payload.get("browser", {}) or {}
    renderer = payload.get("renderer", {}) or {}
    endpoint = payload.get("endpoint", {}) or {}
    install = browser.get("install", {}) or {}

    deps = browser.get("deps", {}) or {}
    missing_libs = browser.get("missing_libs") or []

    running = bool(server.get("running"))
    installed = bool(browser.get("installed"))
    connected = bool(renderer.get("connected"))

    if not installed:
        deps_pill = '<span class="mono">装好 Chromium 后检查</span>'
    elif missing_libs:
        deps_pill = (
            _pill(False, "齐全", "缺少")
            + ' <span class="mono">'
            + _esc("、".join(missing_libs))
            + "</span>"
        )
    else:
        deps_pill = _pill(True, "齐全", "缺少")
    if deps.get("status") not in (None, "", "idle"):
        deps_pill += (
            ' <span class="mono">· 补装 '
            + _esc(deps.get("status", ""))
            + " "
            + _esc(deps.get("message", ""))
            + "</span>"
        )

    def row(key: str, value: str, mono: bool = False) -> str:
        cls = "v mono" if mono else "v"
        return f'<tr><td class="k">{_esc(key)}</td><td class="{cls}">{value}</td></tr>'

    uptime = payload.get("plugin_uptime", 0) or 0
    rows = [
        row(
            "内嵌服务",
            f"{_pill(running, 'RUNNING', 'STOPPED')} "
            f'<span class="mono">{_esc(server.get("endpoint", ""))}</span>',
        ),
        row(
            "累计请求",
            f'{_esc(server.get("requests", 0))} 次 '
            f'（透传备用端点 {_esc(server.get("fallback_used", 0))} 次）',
        ),
        row(
            "Python playwright",
            _pill(bool(browser.get("pip_playwright")), "已安装", "未安装"),
        ),
        row(
            "Chromium",
            f"{_pill(installed, '已安装', '未安装')} "
            + (
                f'<span class="mono">{_esc(Path(browser.get("chromium_path", "")).name)}</span>'
                if browser.get("chromium_path")
                else ""
            ),
        ),
        row("系统依赖", deps_pill),
        row(
            "浏览器进程",
            f'{_esc(browser.get("processes", 0))} 个 · '
            f'{_esc(browser.get("process_memory_mb", 0))} MB',
        ),
        row(
            "浏览器连接",
            _pill(connected, "已连接（复用）", "空闲（首次渲染启动）"),
        ),
        row(
            "安装任务",
            f'{_esc(install.get("status", "unknown"))} · {_esc(install.get("message", ""))}',
        ),
        row(
            "AstrBot 端点",
            f'<span class="mono">{_esc(endpoint.get("astrbot_t2i_endpoint") or "（未设置）")}</span>'
            f' 策略：{_esc(endpoint.get("astrbot_t2i_strategy") or "未知")}'
            f' 输出开关：{_esc("开" if endpoint.get("astrbot_t2i_enabled") else "关")}',
        ),
        row("浏览器目录", _esc(browser.get("browsers_path", "")), mono=True),
        row("插件运行时长", f"{uptime / 60:.1f} 分钟"),
    ]
    if server.get("last_error"):
        rows.append(row("最近错误", _esc(server["last_error"])))

    body = f"""
<div class="card">
  <div class="head">
    <span class="dot {"ok" if running and installed else "warn" if running else "bad"}"></span>
    <h1>文转图服务状态</h1>
  </div>
  <div class="sub">
    插件 {_esc(payload.get("plugin_name", ""))} {_esc(_version_label(payload.get("plugin_version", "")))}
    · 快照时间 {_esc(_now())}
  </div>
  <table>{"".join(rows)}</table>
  <div class="foot">
    <span>生成自 AstrBot 插件内嵌渲染服务</span>
    <span class="mono">t2i_service</span>
  </div>
</div>"""
    return _document("文转图服务状态", body)


_FONT_SPECIMEN_STYLE = """
  .fam { padding: 14px 0; border-bottom: 1px dashed rgba(150, 180, 230, 0.16); }
  .fam:last-child { border-bottom: none; }
  .fam .label {
    color: #9fb2d4; font-size: 12px; margin-bottom: 6px;
    font-family: "SFMono-Regular", Consolas, monospace;
  }
  .fam .sample { font-size: 24px; line-height: 1.5; color: #eaf1ff; word-break: break-word; }
  .fam .small { font-size: 14px; color: #c8d6ee; margin-top: 2px; }
  .empty { color: #9fb2d4; font-size: 13.5px; padding: 10px 0 4px; }
  .hint {
    margin-top: 16px; padding: 10px 12px; border-radius: 10px; font-size: 12px;
    background: rgba(255, 200, 87, 0.10); color: #ffd98a;
    border: 1px solid rgba(255, 200, 87, 0.22);
  }
"""

_SAMPLE_TEXT = "文转图字体预览 Aa Bb Cc 0123 ！“”"

# 预览卡按这个视口宽度渲染：比默认 800 窄，页面里按原尺寸显示不会被放大
FONT_SPECIMEN_WIDTH = 720


def _css_family(name: str) -> str:
    """把家族名放进单引号 CSS 字符串：引号与反斜杠会破坏样式，先剔掉。"""
    return name.replace("'", "").replace("\\", "").replace('"', "")


def build_font_specimen(families: list[str]) -> str:
    """构造字体预览卡片：逐个家族渲染同一句话，用来确认字体真的生效。

    只列出字体文件里读到的家族名（``font-family`` 认的名字），文件名对不上时
    这里会直接暴露出来。
    """
    blocks: list[str] = []
    for name in families:
        # 家族名来自字体文件本身，仍按 HTML 转义后再放进样式/FONT 标签
        safe = _esc(name)
        css = _esc(_css_family(name))
        blocks.append(
            '<div class="fam">'
            f'<div class="label">{safe}</div>'
            f'<div class="sample" style="font-family: \'{css}\'">{_esc(_SAMPLE_TEXT)}</div>'
            f'<div class="sample small" style="font-family: \'{css}\'; font-weight: 700">'
            f"{_esc(_SAMPLE_TEXT)} （粗体）</div>"
            "</div>"
        )
    if not blocks:
        body_inner = (
            '<div class="empty">还没有安装任何字体。请先在插件页「渲染字体」的「自己的字体」'
            "下载或上传字体。</div>"
        )
    else:
        body_inner = "".join(blocks)
    body = f"""
<div class="card">
  <div class="head">
    <span class="dot {"ok" if blocks else "warn"}"></span>
    <h1>字体预览</h1>
  </div>
  <div class="sub">
    同一句话按每个家族各渲染一次 · 共 {len(blocks)} 个家族 · {_esc(_now())}
  </div>
  {body_inner}
  <div class="hint">
    若某一行看起来和其它行完全相同，说明该字体没有真正生效：
    检查字体文件是否有效、Chromium 是否已经重启（字体变更后插件会自动重启）。
  </div>
  <div class="foot">
    <span>生成自 AstrBot 插件内嵌渲染服务</span>
    <span class="mono">t2i_service · fonts</span>
  </div>
</div>
<style>{_FONT_SPECIMEN_STYLE}</style>"""
    return _document("字体预览", body)
