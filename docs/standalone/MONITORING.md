# t2i monitoring

Metrics are disabled by default. Enable the Prometheus-compatible `GET /metrics`
endpoint and set a strong bearer token in production:

```env
METRICS_ENABLED=true
METRICS_TOKEN=<strong-random-token>
```

Send the same token when scraping:

```yaml
authorization:
  credentials_file: /path/to/token
```

When `METRICS_ENABLED` is unset or false, instrumentation uses no-op collectors
and `GET /metrics` returns `404`.

The metrics intentionally use bounded labels. Image IDs, template names, HTML,
URLs, process IDs, and exception messages are never used as labels.

## Key metrics

- `t2i_cgroup_memory_usage_ratio`: container memory pressure relative to its
  cgroup limit.
- `t2i_cgroup_memory_events_total{event="oom_kill"}`: OOM kills observed in the
  current container lifetime.
- `t2i_chromium_resident_memory_bytes`: aggregate resident memory for Chromium
  child processes.
- `t2i_render_in_progress` and `t2i_render_active_pages`: render concurrency.
- `t2i_render_duration_seconds`: render latency histogram by result and scale.
- `t2i_render_viewport_pixels`: effective viewport size after device scaling.
- `t2i_http_requests_total`: HTTP traffic by method, route template, and status.
- `t2i_image_storage_operations_total`: image storage successes and failures.

## Useful queries

```promql
# Memory usage as a percentage of the container limit
100 * t2i_cgroup_memory_usage_ratio

# Render rate by pod
sum by (pod) (rate(t2i_render_requests_total{result="success"}[5m]))

# p95 render latency
histogram_quantile(
  0.95,
  sum by (le) (rate(t2i_render_duration_seconds_bucket[10m]))
)

# HTTP 5xx ratio
sum(rate(t2i_http_requests_total{status_code=~"5.."}[5m]))
/
clamp_min(sum(rate(t2i_http_requests_total[5m])), 1e-9)

# Chromium share of container memory
t2i_chromium_resident_memory_bytes
/
clamp_min(t2i_cgroup_memory_current_bytes, 1)
```
