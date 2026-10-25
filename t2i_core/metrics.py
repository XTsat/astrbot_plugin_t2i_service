from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

try:  # prometheus-client 是可选的：AstrBot 插件模式默认不安装它
    from prometheus_client import REGISTRY, Counter, Gauge, Histogram
    from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

    PROMETHEUS_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - 取决于运行环境
    REGISTRY = None  # type: ignore[assignment]
    Counter = Gauge = Histogram = None  # type: ignore[assignment]
    CounterMetricFamily = GaugeMetricFamily = None  # type: ignore[assignment]
    PROMETHEUS_AVAILABLE = False


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# 缺少 prometheus-client 时强制关闭指标，所有埋点退化为 no-op。
METRICS_ENABLED = PROMETHEUS_AVAILABLE and _env_flag("METRICS_ENABLED")


class _DisabledMetric:
    """Prometheus metric compatible no-op used when metrics are disabled."""

    def labels(self, *args, **kwargs):
        return self

    def inc(self, *args, **kwargs):
        return None

    def dec(self, *args, **kwargs):
        return None

    def set(self, *args, **kwargs):
        return None

    def observe(self, *args, **kwargs):
        return None


def _metric(metric_type, *args, **kwargs):
    if not METRICS_ENABLED:
        return _DisabledMetric()
    return metric_type(*args, **kwargs)


HTTP_REQUESTS = _metric(
    Counter,
    "t2i_http_requests_total",
    "HTTP requests handled by the t2i service.",
    ("method", "route", "status_code"),
)
HTTP_REQUEST_DURATION = _metric(
    Histogram,
    "t2i_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
HTTP_REQUESTS_IN_PROGRESS = _metric(
    Gauge,
    "t2i_http_requests_in_progress",
    "HTTP requests currently being handled.",
    ("method",),
)

RENDER_REQUESTS = _metric(
    Counter,
    "t2i_render_requests_total",
    "Completed screenshot renders.",
    ("result", "format", "scale"),
)
RENDER_DURATION = _metric(
    Histogram,
    "t2i_render_duration_seconds",
    "Screenshot render duration in seconds.",
    ("result", "scale"),
    buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55, 90),
)
RENDER_IN_PROGRESS = _metric(
    Gauge,
    "t2i_render_in_progress",
    "Screenshot renders currently in progress.",
)
RENDER_ACTIVE_PAGES = _metric(
    Gauge,
    "t2i_render_active_pages",
    "Chromium pages currently open for screenshot rendering.",
)
RENDER_INPUT_BYTES = _metric(
    Histogram,
    "t2i_render_input_bytes",
    "Size of HTML or template input in bytes.",
    ("source",),
    buckets=(1_024, 4_096, 16_384, 65_536, 262_144, 1_048_576, 4_194_304),
)
RENDER_OUTPUT_BYTES = _metric(
    Histogram,
    "t2i_render_output_bytes",
    "Size of generated images in bytes.",
    ("format",),
    buckets=(
        16_384,
        65_536,
        262_144,
        1_048_576,
        4_194_304,
        16_777_216,
        67_108_864,
    ),
)
RENDER_VIEWPORT_PIXELS = _metric(
    Histogram,
    "t2i_render_viewport_pixels",
    "Effective viewport pixels after applying the device scale factor.",
    ("scale",),
    buckets=(
        250_000,
        500_000,
        1_000_000,
        2_000_000,
        4_000_000,
        8_000_000,
        16_000_000,
        32_000_000,
    ),
)

BROWSER_CONNECTED = _metric(
    Gauge,
    "t2i_browser_connected",
    "Whether the managed Chromium browser is connected (1 or 0).",
)
BROWSER_CONTEXTS = _metric(
    Gauge,
    "t2i_browser_contexts",
    "Number of managed Chromium browser contexts.",
)
BROWSER_STARTS = _metric(
    Counter,
    "t2i_browser_starts_total",
    "Chromium browser process starts.",
)
BROWSER_RESTARTS = _metric(
    Counter,
    "t2i_browser_restarts_total",
    "Chromium browser restarts after a rendering failure.",
    ("reason",),
)
RATE_LIMIT_REJECTIONS = _metric(
    Counter,
    "t2i_rate_limit_rejections_total",
    "Requests rejected by the in-process rate limiter.",
)
IMAGE_STORAGE_OPERATIONS = _metric(
    Counter,
    "t2i_image_storage_operations_total",
    "Image storage operations.",
    ("operation", "result"),
)
IMAGE_STORAGE_DURATION = _metric(
    Histogram,
    "t2i_image_storage_duration_seconds",
    "Image storage operation duration in seconds.",
    ("operation",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
CLEANUP_RUNS = _metric(
    Counter,
    "t2i_cleanup_runs_total",
    "Periodic cleanup runs.",
    ("result",),
)
CLEANUP_FILES = _metric(
    Counter,
    "t2i_cleanup_files_total",
    "Expired local files removed by periodic cleanup.",
)
CLEANUP_DURATION = _metric(
    Histogram,
    "t2i_cleanup_duration_seconds",
    "Periodic cleanup duration in seconds.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


def _read_number(path: Path) -> float | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _read_key_values(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            values[fields[0]] = float(fields[1])
        except ValueError:
            continue
    return values


def _process_table() -> dict[int, tuple[int, str, int]]:
    """Return pid -> (ppid, searchable name/cmdline, resident bytes)."""
    table: dict[int, tuple[int, str, int]] = {}
    page_size = os.sysconf("SC_PAGE_SIZE")

    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return table

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            status: dict[str, str] = {}
            for line in (
                (entry / "status")
                .read_text(encoding="utf-8", errors="replace")
                .splitlines()
            ):
                key, separator, value = line.partition(":")
                if separator:
                    status[key] = value.strip()
            ppid = int(status["PPid"].split()[0])
            name = status.get("Name", "")
            cmdline = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", errors="replace")
            )
            statm = (entry / "statm").read_text(encoding="ascii").split()
            resident_bytes = int(statm[1]) * page_size
            table[int(entry.name)] = (ppid, f"{name} {cmdline}".lower(), resident_bytes)
        except (FileNotFoundError, PermissionError, KeyError, ValueError, IndexError):
            # Processes can disappear while /proc is being traversed.
            continue
    return table


def _descendants(
    table: dict[int, tuple[int, str, int]], root_pid: int
) -> Iterable[tuple[int, str, int]]:
    pending = [root_pid]
    seen = {root_pid}
    while pending:
        parent = pending.pop()
        for pid, details in table.items():
            if pid in seen or details[0] != parent:
                continue
            seen.add(pid)
            pending.append(pid)
            yield details


class RuntimeCollector:
    """Collect container and Chromium memory without high-cardinality PID labels."""

    cgroup_root = Path("/sys/fs/cgroup")

    def describe(self):
        # Avoid traversing /proc while the registry is importing this collector.
        return []

    def collect(self):
        current = _read_number(self.cgroup_root / "memory.current")
        maximum = _read_number(self.cgroup_root / "memory.max")

        current_metric = GaugeMetricFamily(
            "t2i_cgroup_memory_current_bytes",
            "Current memory charged to the t2i container cgroup.",
        )
        if current is not None:
            current_metric.add_metric([], current)
        yield current_metric

        maximum_metric = GaugeMetricFamily(
            "t2i_cgroup_memory_limit_bytes",
            "Memory limit of the t2i container cgroup.",
        )
        if maximum is not None:
            maximum_metric.add_metric([], maximum)
        yield maximum_metric

        ratio_metric = GaugeMetricFamily(
            "t2i_cgroup_memory_usage_ratio",
            "Current cgroup memory divided by its configured limit.",
        )
        if current is not None and maximum:
            ratio_metric.add_metric([], current / maximum)
        yield ratio_metric

        memory_stat = _read_key_values(self.cgroup_root / "memory.stat")
        breakdown_metric = GaugeMetricFamily(
            "t2i_cgroup_memory_stat_bytes",
            "Selected cgroup v2 memory.stat values.",
            labels=["type"],
        )
        for key in ("anon", "file", "kernel", "shmem", "sock"):
            if key in memory_stat:
                breakdown_metric.add_metric([key], memory_stat[key])
        yield breakdown_metric

        events_metric = CounterMetricFamily(
            "t2i_cgroup_memory_events",
            "Cgroup v2 memory events since the container started.",
            labels=["event"],
        )
        for event, value in _read_key_values(
            self.cgroup_root / "memory.events"
        ).items():
            events_metric.add_metric([event], value)
        yield events_metric

        table = _process_table()
        children = list(_descendants(table, os.getpid()))
        chromium = [
            process
            for process in children
            if "chrom" in process[1] or "headless_shell" in process[1]
        ]

        child_count_metric = GaugeMetricFamily(
            "t2i_child_processes",
            "Number of descendant processes owned by the t2i worker.",
        )
        child_count_metric.add_metric([], len(children))
        yield child_count_metric

        child_memory_metric = GaugeMetricFamily(
            "t2i_child_process_resident_memory_bytes",
            "Resident memory used by descendant processes.",
        )
        child_memory_metric.add_metric([], sum(process[2] for process in children))
        yield child_memory_metric

        chromium_count_metric = GaugeMetricFamily(
            "t2i_chromium_processes",
            "Number of Chromium descendant processes.",
        )
        chromium_count_metric.add_metric([], len(chromium))
        yield chromium_count_metric

        chromium_memory_metric = GaugeMetricFamily(
            "t2i_chromium_resident_memory_bytes",
            "Resident memory used by Chromium descendant processes.",
        )
        chromium_memory_metric.add_metric([], sum(process[2] for process in chromium))
        yield chromium_memory_metric


RUNTIME_COLLECTOR = RuntimeCollector()
if METRICS_ENABLED:
    REGISTRY.register(RUNTIME_COLLECTOR)
