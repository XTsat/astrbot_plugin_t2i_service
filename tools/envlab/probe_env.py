#!/usr/bin/env python3
"""Dump the facts an AstrBot/Playwright rendering environment must satisfy.

Pure stdlib (no playwright import) so it also runs on a *bare* container to prove
what is missing before setup:

    docker exec t2i-envlab python /opt/plugin/tools/envlab/probe_env.py
    docker exec t2i-envlab python /opt/plugin/tools/envlab/probe_env.py --json /AstrBot/data/probe.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

BROWSERS_PATH = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/AstrBot/data/ms-playwright")
INTERESTING_MODULES = [
    "playwright", "fastapi", "uvicorn", "jinja2", "boto3", "prometheus_client",
    "pytest", "httpx", "aiohttp", "PIL", "quart",
]
NEEDED_LIBS = [
    "libnss3.so", "libnspr4.so", "libatk-1.0.so.0", "libatk-bridge-2.0.so.0",
    "libatspi.so.0", "libcups.so.2", "libXcomposite.so.1", "libXdamage.so.1",
    "libgbm.so.1", "libxkbcommon.so.0", "libasound.so.2", "libpango-1.0.so.0",
    "libcairo.so.2", "libdrm.so.2", "libXrandr.so.2", "libXfixes.so.3",
]


def sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"<error: {exc}>"


def module_versions() -> dict:
    from importlib import metadata  # noqa: PLC0415

    found = {}
    for name in INTERESTING_MODULES:
        try:
            found[name] = metadata.version(name.replace("_", "-"))
        except Exception:  # noqa: BLE001
            found[name] = None
    return found


def ldconfig_map() -> dict:
    text = sh(["ldconfig", "-p"])
    out = {}
    for line in text.splitlines():
        match = re.search(r"(\S+)\s+\(libc6,x86-64\)\s+=>\s+(\S+)", line)
        if match:
            out.setdefault(match.group(1), match.group(2))
    return out


def find_chrome(browsers_path: str) -> dict:
    base = Path(browsers_path)
    info = {"browsers_path": browsers_path, "exists": base.is_dir(), "entries": {}}
    if not base.is_dir():
        return info
    for child in sorted(base.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            info["entries"][child.name] = {
                "size_mb": round(sum(f.stat().st_size for f in child.rglob("*") if f.is_file()) / 1024 / 1024, 1)
            }
    chrome = sorted(base.glob("chromium-*/chrome-linux*/chrome"))
    shell = sorted(base.glob("chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell"))
    if chrome:
        info["chrome"] = str(chrome[-1])
        info["chrome_version"] = sh([str(chrome[-1]), "--version"])
    if shell:
        info["headless_shell"] = str(shell[-1])
        info["headless_shell_version"] = sh([str(shell[-1]), "--version"])
    if shell:
        missing = [ln for ln in sh(["ldd", str(shell[-1])]).splitlines() if "not found" in ln]
        info["headless_shell_missing_libs"] = [m.split("=>")[0].strip() for m in missing]
    if chrome:
        missing = [ln for ln in sh(["ldd", str(chrome[-1])]).splitlines() if "not found" in ln]
        info["chrome_missing_libs"] = [m.split("=>")[0].strip() for m in missing]
    return info


def fonts() -> dict:
    listing = sh(["fc-list"])
    families = set()
    for line in listing.splitlines():
        parts = line.split(":")
        if len(parts) > 1:
            families.add(parts[1].strip())
    cjk = sorted(f for f in families if re.search(r"CJK|WenQuanYi|Zen ?Hei|Noto Sans SC|Source Han|Droid Sans Fallback|MiSans", f, re.I))
    return {
        "families_total": len(families),
        "cjk_families": cjk,
        "fc_list_ok": bool(listing) and "error" not in listing.lower(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default=None)
    parser.add_argument("--browsers-path", default=BROWSERS_PATH)
    args = parser.parse_args()

    libs = ldconfig_map()
    du = shutil.disk_usage("/")
    info = {
        "os": Path("/etc/os-release").read_text().strip().splitlines()[0] if Path("/etc/os-release").exists() else "?",
        "kernel": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "cpu_count": os.cpu_count(),
        "mem_total_mb": round(int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1)) / 1024, 1),
        "cwd": os.getcwd(),
        "env": {k: v for k, v in os.environ.items() if k.startswith(("PLAYWRIGHT", "PIP_", "TZ", "LANG"))},
        "modules": module_versions(),
        "libs_present": {name: bool(libs.get(name)) for name in NEEDED_LIBS},
        "browser": find_chrome(args.browsers_path),
        "fonts": fonts(),
        "disk_root": {"total_gb": round(du.total / 2**30, 1), "free_gb": round(du.free / 2**30, 1)},
    }
    try:
        data = shutil.disk_usage("/AstrBot/data")
        info["disk_data"] = {"total_gb": round(data.total / 2**30, 1), "free_gb": round(data.free / 2**30, 1)}
    except OSError:
        info["disk_data"] = None

    text = json.dumps(info, ensure_ascii=False, indent=2)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    print(text)

    missing = [name for name, present in info["libs_present"].items() if not present]
    ready = (
        info["modules"].get("playwright") is not None
        and not missing
        and info["browser"].get("chrome_version")
        and "Chrome" in (info["browser"].get("chrome_version") or "")
    )
    print("\n--- verdict ---")
    print(f"playwright      : {info['modules'].get('playwright')}")
    print(f"missing libs    : {missing or 'none'}")
    print(f"chrome          : {info['browser'].get('chrome_version') or 'missing'}")
    print(f"cjk fonts       : {len(info['fonts']['cjk_families'])} families")
    print(f"READY           : {ready}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
