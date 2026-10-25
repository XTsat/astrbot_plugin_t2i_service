#!/usr/bin/env bash
# Prepare a disposable AstrBot container (soulter/astrbot) for Playwright/Chromium
# text-to-image rendering. Idempotent: re-running only installs what is missing.
#
# Designed to run INSIDE the container (see docs/ENV-REPORT.md for the docker run line):
#   docker exec t2i-envlab bash /opt/plugin/tools/envlab/setup_envlab.sh
#
# Everything that must survive container recreation lives under $DATA_DIR,
# which is expected to be a bind mount (e.g. /AstrBot/data -> host dir).
set -euo pipefail

DATA_DIR="${DATA_DIR:-/AstrBot/data}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
PLAYWRIGHT_VERSION="${PLAYWRIGHT_VERSION:-1.64.1}"   # pinned: repo pyproject allows >=1.54.0
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$DATA_DIR/ms-playwright}"
PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST:-https://cdn.npmmirror.com/binaries/playwright}"
export PLAYWRIGHT_BROWSERS_PATH PLAYWRIGHT_DOWNLOAD_HOST

log() { printf '\n=== %s ===\n' "$*"; }
ts() { date +%s; }

log "python / os"
python -V
. /etc/os-release && echo "os: $PRETTY_NAME"

T0=$(ts)
log "pip install playwright==$PLAYWRIGHT_VERSION (index: $PIP_INDEX_URL)"
if python -c "import playwright, sys; sys.exit(0 if __import__('importlib.metadata', fromlist=['x']).version('playwright') == '${PLAYWRIGHT_VERSION}' else 1)" 2>/dev/null; then
  echo "already installed: $(python -c 'import importlib.metadata as m; print(m.version("playwright"))')"
else
  pip install --no-cache-dir --index-url "$PIP_INDEX_URL" "playwright==${PLAYWRIGHT_VERSION}"
fi
echo "pip phase: $(( $(ts) - T0 ))s"

T1=$(ts)
log "playwright install chromium (browsers -> $PLAYWRIGHT_BROWSERS_PATH)"
if [ -d "$PLAYWRIGHT_BROWSERS_PATH" ] && find "$PLAYWRIGHT_BROWSERS_PATH" -maxdepth 1 -name 'chromium-*' | grep -q .; then
  echo "browsers already present: $(ls -d "$PLAYWRIGHT_BROWSERS_PATH"/chromium* 2>/dev/null | tr '\n' ' ')"
else
  python -m playwright install chromium
fi
echo "browser download phase: $(( $(ts) - T1 ))s"

T2=$(ts)
log "system libs for chromium (apt, via playwright install-deps)"
if ldd "$(find "$PLAYWRIGHT_BROWSERS_PATH" -maxdepth 4 -name chrome -type f | head -1)" 2>/dev/null | grep -q 'not found'; then
  apt-get update
  python -m playwright install-deps chromium
else
  echo "all shared libraries already resolvable"
fi
echo "deps phase: $(( $(ts) - T2 ))s"

log "verify"
python -m playwright --version
CHROME="$(find "$PLAYWRIGHT_BROWSERS_PATH" -maxdepth 4 -name chrome -type f | head -1)"
echo "chrome: $CHROME"
"$CHROME" --version
echo "missing libs: $(ldd "$CHROME" | grep -c 'not found' || true)"
du -sh "$PLAYWRIGHT_BROWSERS_PATH"
du -sh "$PLAYWRIGHT_BROWSERS_PATH"/*

log "done"
