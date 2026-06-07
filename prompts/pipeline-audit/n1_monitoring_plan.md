# N-1: Monitoring Instrumentation

**Status:** ⏸️ Planning — ready to implement

| Field        | Value                    |
| ------------ | ------------------------ |
| **Layer**    | All                      |
| **Severity** | Enhancement              |
| **Effort**   | 3-5 days                 |
| **Status**   | Ready for implementation |

---

## 1. Goal

Add Prometheus metrics instrumentation to every pipeline task, expose a `/metrics/` endpoint from Django, deploy Prometheus + Grafana + celery-exporter as Docker services, and provide a pre-built Grafana dashboard for pipeline observability.

---

## 2. Research summary

### 2.1 RED method for pipeline monitoring

Industry best practice for task-oriented systems (Celery, queues, batch jobs)
follows the **RED method**: Rate, Errors, Duration.

This maps cleanly onto our five planned metrics:

| RED dimension | What it measures                                   | Our metric                                                     |
| ------------- | -------------------------------------------------- | -------------------------------------------------------------- |
| **Rate**      | Throughput — how many rows flow through each layer | `pipeline_rows_processed_total`, `pipeline_rows_dropped_total` |
| **Errors**    | Failures & retries — how often tasks break         | `pipeline_task_failures_total`, `pipeline_task_retries_total`  |
| **Duration**  | Latency — how long tasks take                      | `pipeline_task_duration_seconds` (Histogram)                   |

Source:

1. [Prometheus best practices](https://prometheus.io/docs/practices/instrumentation/#counter-vs-gauge-vs-summary)
2. [Grafana RED method](https://grafana.com/blog/2018/08/02/the-red-method-how-to-instrument-your-services/)

### 2.2 celery-exporter

The [celery-exporter](https://github.com/danihodovic/celery-exporter) project (1.4k stars, actively maintained) exposes broker-level Celery metrics that our custom `prometheus-client` instrumentation cannot capture:

| Metric                        | Description                   | Grafana dashboard                                                                            |
| ----------------------------- | ----------------------------- | -------------------------------------------------------------------------------------------- |
| `celery_task_succeeded_total` | Counter per task name         | [Celery Tasks Overview](https://grafana.com/grafana/dashboards/17509-celery-tasks-overview/) |
| `celery_task_failed_total`    | Counter per task name         | (ID 17509)                                                                                   |
| `celery_task_retried_total`   | Counter per task name         |                                                                                              |
| `celery_task_runtime_bucket`  | Histogram of runtime per task |                                                                                              |
| `celery_queue_length`         | Gauge — broker queue depth    |                                                                                              |
| `celery_worker_up`            | Gauge — worker heartbeat      |                                                                                              |
| `celery_worker_tasks_active`  | Gauge — currently processing  |                                                                                              |

Using celery-exporter avoids reinventing Celery-internal metrics. Our custom metrics cover _pipeline-specific_ dimensions (row counts, snapshot types, unique_id drops) that celery-exporter has no visibility into.

There are **two pre-built Grafana dashboards** published for celery-exporter:

1. [Celery Tasks Overview](https://grafana.com/grafana/dashboards/17509-celery-tasks-overview/) (ID: 17509) — high-level: success rate, queue length, worker count, active tasks
2. [Celery Tasks by Task](https://grafana.com/grafana/dashboards/17508-celery-tasks-by-task/) (ID: 17508) — per-task breakdown: runtime histogram, failure rate, retries

We import dashboard ID 17509; ID 17508 can be added later if needed.

### 2.3 Current state

The project has:

- **Logging only** via `get_task_logger(__name__)` and `TaskFormatter` — no metrics export
- **DatasetEvent DB table** — append-only event log with pipeline milestones (8 event types)
- **`duration_seconds` properties** on all task/ingestion models — timestamps exist but unused by any monitoring system
- **Flower** already deployed on port 5555 with `--enable_events` — real-time Celery task view
- **Row counts** already computed and stored (`source_row_count`, `ingested_row_count`, `dropped_row_count`) — just need metric observation

Zero Prometheus metrics, zero Grafana dashboards, zero OpenTelemetry.

### 2.4 Architecture decision

```
Celery workers ──→ prometheus_client ──→ files in PROMETHEUS_MULTIPROC_DIR
                        │
Django HTTP workers ───→ prometheus_client ──→ /metrics/ endpoint
                        │
celery-exporter ────────→ /metrics/ endpoint (broker-level)
                        │
                   Prometheus (scrapes both every 15s)
                        │
                   Grafana (queries Prometheus, 2 dashboards)
```

**Multiprocess mode:** Prometheus `multiprocess` mode (via `PROMETHEUS_MULTIPROC_DIR` env var) is required because Celery workers fork multiple child processes. Each process writes to its own metrics file; the `/metrics/` view uses `MultiProcessCollector` to aggregate them. See [prometheus_client multiprocess docs](https://github.com/prometheus/client_python#multiprocess-mode-gunicorn).

**Why not django-prometheus:** The `django-prometheus` package provides request-level metrics (request count, latency, DB query count) but is not needed for this project's scope. We instrument only pipeline-specific metrics. Can be added later.

**Why not OpenTelemetry:** OTel provides vendor-neutral tracing, but this project doesn't need distributed tracing yet. Prometheus + Grafana is the right starting point. Can be added later when gold layer or cross-service tracing is needed.

---

## 3. Implementation plan

### 3.1 Files to create

| #   | File                                        | Purpose                                                                 |
| --- | ------------------------------------------- | ----------------------------------------------------------------------- |
| 1   | `apps/core/metrics.py`                      | Define all `prometheus_client` metric objects (4 Counters, 1 Histogram) |
| 2   | `prometheus/prometheus.yml`                 | Prometheus scrape config — Django `/metrics/` + celery-exporter         |
| 3   | `grafana/datasources/prometheus.yml`        | Auto-provisioned Prometheus datasource for Grafana                      |
| 4   | `grafana/dashboards/pipeline_overview.json` | Custom dashboard JSON (5 row-groups, ~12 panels)                        |

File #4 is the largest deliverable — a hand-authored Grafana dashboard JSON targeting our custom metrics.

### 3.2 Files to modify

| #   | File                    | Change                                                                                                                                           |
| --- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| 5   | `pyproject.toml`        | Add `prometheus-client>=0.21.0` to dependencies                                                                                                  |
| 6   | `config/settings.py`    | Add `PROMETHEUS_MULTIPROC_DIR` setting (default `/tmp/prometheus_metrics`)                                                                       |
| 7   | `config/urls.py`        | Add `path("metrics/", metrics_view, name="metrics")` **before** the save slug catch-all route                                                    |
| 8   | `apps/core/views.py`    | Add `metrics_view` — multiprocess-safe Prometheus exposition                                                                                     |
| 9   | `apps/core/tasks.py`    | Instrument `record_failure` — counter increments on RETRYING and FAILED paths                                                                    |
| 10  | `apps/bronze/tasks.py`  | Observe duration histogram + row processed counter in COMPLETED block; observe failure histogram in both non-retryable and generic except blocks |
| 11  | `apps/silver/tasks.py`  | Same as bronze + observe `pipeline_rows_dropped` counter                                                                                         |
| 12  | `apps/landing/tasks.py` | Observe duration histogram in `parse_file` and `store_parquet` terminal blocks                                                                   |
| 13  | `docker-compose.yml`    | Add 4 services: celery-exporter, prometheus, grafana + a shared tmpfs volume for multiproc metrics                                               |

### 3.3 Instrumentation details

#### 3.3.1 `apps/core/metrics.py`

```python
from prometheus_client import Counter, Histogram

pipeline_task_duration = Histogram(
    "pipeline_task_duration_seconds",
    "Task duration in seconds",
    ["task_name", "snapshot_type", "status"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800),
)

pipeline_task_retries = Counter(
    "pipeline_task_retries_total",
    "Total number of task retries",
    ["task_name"],
)

pipeline_task_failures = Counter(
    "pipeline_task_failures_total",
    "Total number of task failures",
    ["task_name", "error_type"],
)

pipeline_rows_processed = Counter(
    "pipeline_rows_processed_total",
    "Total rows ingested into a layer",
    ["layer", "snapshot_type"],
)

pipeline_rows_dropped = Counter(
    "pipeline_rows_dropped_total",
    "Total rows dropped during transformation",
    ["layer", "reason"],
)
```

#### 3.3.2 `record_failure` — counter increments

In `apps/core/tasks.py`:

```python
def record_failure(self, obj, task_row, exc):
    if self.request.retries < self.max_retries:
        obj.status = PipelineStatus.RETRYING
        task_row.status = TaskStatus.RETRYING
        pipeline_task_retries.labels(task_name=self.name).inc()
    else:
        obj.status = PipelineStatus.FAILED
        task_row.status = TaskStatus.FAILED
        pipeline_task_failures.labels(
            task_name=self.name,
            error_type=type(exc).__name__,
        ).inc()
    # ... rest of existing method unchanged
```

**Design rationale:** `task_name=self.name` uses the Celery task's registered name (e.g. `"apps.bronze.tasks.ingest_to_bronze"`), which is always available inside a `bind=True` task via `self.name`. No need to pass it explicitly.

**Why not observe duration here:** `record_failure` does not have access to `snapshot_type` or `task_row.duration_seconds` in all call sites. Duration observation is done at the task-level terminal blocks where both are available.

#### 3.3.3 Bronze task — success block

Inserted at `apps/bronze/tasks.py` after the existing `DatasetEvent.objects.create(...)` at line 193:

```python
pipeline_task_duration.labels(
    task_name="ingest_to_bronze",
    snapshot_type=upload.snapshot_type,
    status="completed",
).observe(task_row.duration_seconds)
pipeline_rows_processed.labels(
    layer="bronze",
    snapshot_type=upload.snapshot_type,
).inc(ingested_count)
```

#### 3.3.4 Bronze task — non-retryable except block

Inserted at `apps/bronze/tasks.py` after the existing `DatasetEvent.objects.create(...)` at line 229:

```python
pipeline_task_duration.labels(
    task_name="ingest_to_bronze",
    snapshot_type=upload.snapshot_type,
    status="failed",
).observe(task_row.duration_seconds)
```

#### 3.3.5 Bronze task — generic except block

`record_failure` already handles counters. Add the duration observation after `self.record_failure(...)` and before `DatasetEvent.objects.create(...)` at line 247:

```python
pipeline_task_duration.labels(
    task_name="ingest_to_bronze",
    snapshot_type=upload.snapshot_type,
    status="failed",
).observe(task_row.duration_seconds)
```

#### 3.3.6 Silver task — success block

Inserted at `apps/silver/tasks.py` after the existing `DatasetEvent.objects.create(...)` at line 689:

```python
pipeline_task_duration.labels(
    task_name="transform_to_silver",
    snapshot_type=bronze_ingestion.snapshot_type,
    status="completed",
).observe(task_row.duration_seconds)
pipeline_rows_processed.labels(
    layer="silver",
    snapshot_type=bronze_ingestion.snapshot_type,
).inc(inserted_count)
pipeline_rows_dropped.labels(
    layer="silver",
    reason="no_unique_id",
).inc(dropped_count)
```

#### 3.3.7 Silver task — non-retryable except block

Inserted at `apps/silver/tasks.py` after the existing `DatasetEvent.objects.create(...)` at line 723:

```python
pipeline_task_duration.labels(
    task_name="transform_to_silver",
    snapshot_type=bronze_ingestion.snapshot_type,
    status="failed",
).observe(task_row.duration_seconds)
```

#### 3.3.8 Silver task — generic except block

Inserted after `self.record_failure(...)` and before `DatasetEvent.objects.create(...)` at line 741:

```python
pipeline_task_duration.labels(
    task_name="transform_to_silver",
    snapshot_type=bronze_ingestion.snapshot_type,
    status="failed",
).observe(task_row.duration_seconds)
```

#### 3.3.9 Landing tasks

**`parse_file` success** — after `DatasetEvent.objects.create(...)` at `apps/landing/tasks.py:99`:

```python
pipeline_task_duration.labels(
    task_name="parse_file",
    snapshot_type=upload.snapshot_type,
    status="completed",
).observe(task_row.duration_seconds)
```

**`parse_file` failure** — after `self.record_failure(...)` and `DatasetEvent.objects.create(...)`:

```python
pipeline_task_duration.labels(
    task_name="parse_file",
    snapshot_type=upload.snapshot_type,
    status="failed",
).observe(task_row.duration_seconds)
```

**`store_parquet` success** — after `DatasetEvent.objects.create(...)` at `apps/landing/tasks.py:196`:

```python
pipeline_task_duration.labels(
    task_name="store_parquet",
    snapshot_type=upload.snapshot_type,
    status="completed",
).observe(task_row.duration_seconds)
```

**`store_parquet` failure** — three except blocks (FileNotFoundError, FileExistsError, generic). Each adds after its `DatasetEvent.objects.create(...)`:

```python
pipeline_task_duration.labels(
    task_name="store_parquet",
    snapshot_type=upload.snapshot_type,
    status="failed",
).observe(task_row.duration_seconds)
```

**Design rationale:** Landing tasks do not emit `rows_processed` because they operate on files, not rows. The bronze/silver layers own row counts.

### 3.4 `/metrics/` view

In `apps/core/views.py`, add:

```python
import os
from prometheus_client import generate_latest, REGISTRY, CONTENT_TYPE_LATEST
from prometheus_client.multiprocess import MultiProcessCollector
from django.http import HttpResponse

def metrics_view(request):
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        MultiProcessCollector(REGISTRY)
    latest = generate_latest(REGISTRY)
    return HttpResponse(latest, content_type=CONTENT_TYPE_LATEST)
```

**Multiprocess mode:** When `PROMETHEUS_MULTIPROC_DIR` is set (production/Celery workers), `MultiProcessCollector` aggregates metric files from all worker processes. When unset (development without Celery), the default `REGISTRY` is used directly.

**URL registration in `config/urls.py`:**

```python
from apps.core.views import home, metrics_view, save_create_partial, save_detail, save_list_partial

urlpatterns = [
    path("admin/", admin.site.urls),
    path("metrics/", metrics_view, name="metrics"),
    path("", home, name="home"),
    # ... rest unchanged
]
```

**Placement before the save slug catch-all** (`<slug:save_slug>/`) is critical — if `metrics/` were placed after, Django would try to match it as a save slug.

### 3.5 Prometheus scrape config

`prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "django"
    static_configs:
      - targets: ["host.docker.internal:8000"]
    metrics_path: "/metrics/"

  - job_name: "celery-exporter"
    static_configs:
      - targets: ["celery-exporter:9808"]
```

**`host.docker.internal`** works on Docker Desktop (macOS/Windows) and Linux with `--add-host host.docker.internal:host-gateway`. This lets Prometheus reach the Django dev server running on the host. In production, both would be containers on the same Docker network.

### 3.6 Docker services

Add to `docker-compose.yml`:

```yaml
x-app-base:
  # ... existing
  environment:
    # ... existing
    PROMETHEUS_MULTIPROC_DIR: /tmp/prometheus_metrics # add to all Celery containers

services:
  # ... existing

  celery-exporter:
    image: danihodovic/celery-exporter:latest
    container_name: fm-celery-exporter
    command:
      - --broker-url=redis://fm-redis:6379/1
      - --retry-interval=5
    ports:
      - "9808:9808"
    depends_on:
      - fm-redis
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:latest
    container_name: fm-prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus:/etc/prometheus
      - prometheus_data:/prometheus
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.retention.time=15d
    depends_on:
      - fm-celery-scheduler

  grafana:
    image: grafana/grafana:latest
    container_name: fm-grafana
    ports:
      - "3000:3000"
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "true"
      GF_SECURITY_ADMIN_PASSWORD: admin
    volumes:
      - ./grafana/datasources:/etc/grafana/provisioning/datasources
      - ./grafana/dashboards:/etc/grafana/provisioning/dashboards
      - grafana_data:/var/lib/grafana
    depends_on:
      - prometheus

volumes:
  prometheus_data:
  grafana_data:
```

**The `fm-celery-scheduler` and `fm-celery-beat`** containers need `PROMETHEUS_MULTIPROC_DIR` set. In `x-app-base.environment`, add the env var so all app containers pick it up.

The multiproc directory is ephemeral (tmpfs, not persistent). Each container restart starts fresh. This is correct — Prometheus tracks the scrape timestamp independently.

### 3.7 Grafana dashboard: Pipeline Overview

A custom dashboard with 5 row-groups and ~12 panels, provisioned via `grafana/dashboards/pipeline_overview.json`.

Panels and their PromQL:

| Panel                         | Type        | PromQL                                                                                                                                                            | Unit      |
| ----------------------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| **Success Rate**              | Gauge       | `sum(rate(pipeline_task_duration_seconds_count{status="completed"}[$__rate_interval])) * 100 / sum(rate(pipeline_task_duration_seconds_count[$__rate_interval]))` | Percent   |
| **Failed Tasks (1h)**         | Stat        | `sum(increase(pipeline_task_failures_total[1h]))`                                                                                                                 | Count     |
| **Retries (1h)**              | Stat        | `sum(increase(pipeline_task_retries_total[1h]))`                                                                                                                  | Count     |
| **Duration p95 by task**      | Time series | `histogram_quantile(0.95, sum(rate(pipeline_task_duration_seconds_bucket[$__rate_interval])) by (le, task_name))`                                                 | Seconds   |
| **Duration p99 by task**      | Time series | `histogram_quantile(0.99, sum(rate(pipeline_task_duration_seconds_bucket[$__rate_interval])) by (le, task_name))`                                                 | Seconds   |
| **Rows processed**            | Time series | `rate(pipeline_rows_processed_total[$__rate_interval])`                                                                                                           | Rows/s    |
| **Rows dropped**              | Time series | `rate(pipeline_rows_dropped_total[$__rate_interval])`                                                                                                             | Rows/s    |
| **Error rate by type**        | Time series | `rate(pipeline_task_failures_total[$__rate_interval])`                                                                                                            | Errors/s  |
| **Retry rate**                | Time series | `rate(pipeline_task_retries_total[$__rate_interval])`                                                                                                             | Retries/s |
| **Duration by task × status** | Heatmap     | `sum(rate(pipeline_task_duration_seconds_bucket[$__rate_interval])) by (le, task_name, status)`                                                                   | —         |
| **Recent failures**           | Table       | `topk(10, pipeline_task_failures_total offset 5m - pipeline_task_failures_total)`                                                                                 | Table     |

---

## 4. Testing plan

| Test                          | Type        | Location                | What it verifies                                                                   |
| ----------------------------- | ----------- | ----------------------- | ---------------------------------------------------------------------------------- |
| Metrics module imports        | Unit        | `apps/core/tests.py`    | All 5 metrics are `Counter`/`Histogram` instances                                  |
| `/metrics/` endpoint 200      | Django      | `apps/core/tests.py`    | GET /metrics/ → 200, content type, contains `pipeline_` prefix                     |
| Histogram labels match        | Unit        | `apps/core/tests.py`    | `pipeline_task_duration._labelnames` == `("task_name", "snapshot_type", "status")` |
| Counter increment call        | Unit        | `apps/core/tests.py`    | Mock `.inc()` on retries/failures counters, call `record_failure`                  |
| Bronze success metrics        | Integration | `apps/bronze/tests.py`  | Run `ingest_to_bronze`, assert metrics file contains expected labels               |
| Silver success + drop metrics | Integration | `apps/silver/tests.py`  | Run `transform_to_silver`, assert row count and drop counter values                |
| Landing parse metrics         | Integration | `apps/landing/tests.py` | Run `parse_file`, assert duration histogram observed                               |
| Full regression               | —           | `pytest`                | All 233 existing tests pass                                                        |

---

## 5. Files checklist

**Create (4):**

```
apps/core/metrics.py
prometheus/prometheus.yml
grafana/datasources/prometheus.yml
grafana/dashboards/pipeline_overview.json
```

**Modify (9):**

```
pyproject.toml              +prometheus-client>=0.21.0
config/settings.py          +PROMETHEUS_MULTIPROC_DIR
config/urls.py              +metrics/ route before catch-all
apps/core/views.py          +metrics_view function
apps/core/tasks.py          +counter increments in record_failure
apps/bronze/tasks.py        +histogram + row counter in 3 blocks
apps/silver/tasks.py        +histogram + row counter + drop counter in 3 blocks
apps/landing/tasks.py       +histogram in parse_file (2 blocks) + store_parquet (4 blocks)
docker-compose.yml          +celery-exporter + prometheus + grafana + tmpfs volume
```

---

## 6. Execution order

1. `pyproject.toml` — add `prometheus-client`
2. `apps/core/metrics.py` — define all metrics
3. `apps/core/views.py` — add `metrics_view`
4. `config/urls.py` — route `/metrics/`
5. `config/settings.py` — `PROMETHEUS_MULTIPROC_DIR`
6. `apps/core/tasks.py` — `record_failure` counters
7. `apps/bronze/tasks.py` — instrumentation
8. `apps/silver/tasks.py` — instrumentation
9. `apps/landing/tasks.py` — instrumentation
10. `prometheus/prometheus.yml` — scrape config
11. `grafana/datasources/prometheus.yml` — auto-provision
12. `grafana/dashboards/pipeline_overview.json` — dashboard
13. `docker-compose.yml` — new services
14. `pytest` — verify no regression
