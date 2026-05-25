# Dataset Management — Implementation Plan

## Overview

Add a "Datasets" section in the save-scoped navigation (alongside Squad, Scouting, Matchdays) with two pages:

1. **Listing page** (`/<save_slug>/datasets/`) — shows all pipeline activity for a save in 3 sections, with real-time SSE updates and performance metrics
2. **Detail page** (`/<save_slug>/datasets/<upload_id>/`) — shows the full pipeline chain as an interactive horizontal graph with statistics

Built as a new `apps/datasets/` Django app with an append-only `DatasetEvent` model for SSE streaming. The listing query uses Django ORM `Subquery` annotations (not a SQL view). Query performance is captured inline via `CaptureQueries` and rendered as a subtle badge.

### Why a new app

Putting dataset views in `apps/landing/` would bloat it with dashboard logic. Putting them in `apps/core/` would violate its purpose as a shared-infrastructure app. A dedicated `apps/datasets/` is self-documenting and follows Django's app-per-domain convention.

### Why ORM annotations instead of a SQL view

The listing query joins `LandingUpload` → `LandingZoneTask` (parse + store) → `BronzeIngestion` → `BronzeIngestionTask` — about 15 scalar `Subquery` annotations. A SQL view with `LATERAL JOIN` would be architecturally cleaner (4 subqueries instead of 15) but:

- Data volume per save is small (hundreds of rows, not millions) — both approaches finish in <50ms
- A SQL view breaks silently when columns are renamed — the migration succeeds but the view fails at query time. The ORM approach fails at test time with a clear error
- The ORM approach can be replaced with a view later if performance becomes a problem, without changing the template or the calling code

### Why `DatasetEvent` instead of polling existing task models

The existing `LandingZoneTask` and `BronzeIngestionTask` models have mutable status fields and no reliable `created_at` — the SSE reader would need complex dedup logic and cursor management. `DatasetEvent` is an append-only log: tasks write one row per milestone, the SSE view reads rows newer than `Last-Event-ID`. 10 model lines + 6 `create()` calls replace a fragile multi-table poller.

---

## Architecture

```
Browser (HTMX + SSE)
    │
    ├── GET /<save_slug>/datasets/              → dataset_list view
    │       └── SSE /hx/datasets/sse/save/<slug/  → listing_event_stream
    │
    ├── GET /<save_slug>/datasets/<id>/          → dataset_detail view
    │       └── SSE /hx/datasets/sse/<id>/        → event_stream
    │
    └── HTMX partials (row swap, node expand)

Celery tasks
    │
    ├── store_parquet (landing/tasks.py)
    │       ├── success → DatasetEvent(event_type='store_completed')
    │       └── failure → DatasetEvent(event_type='store_failed')
    │
    ├── ingest_to_bronze (bronze/tasks.py)
    │       ├── success → DatasetEvent(event_type='bronze_completed')
    │       └── failure → DatasetEvent(event_type='bronze_failed')
    │
    └── parse_file (landing/tasks.py)
            ├── success → DatasetEvent(event_type='parse_completed')
            └── failure → DatasetEvent(event_type='parse_failed')
```

---

## Files to Create

Don't create these files yourself, scaffold these with `uv manage.py createapp <appname>`
adapt the above `uv`

### Python

| File                        | Purpose                                                                  |
| --------------------------- | ------------------------------------------------------------------------ |
| `apps/datasets/__init__.py` | Package marker                                                           |
| `apps/datasets/models.py`   | `DatasetEvent` model                                                     |
| `apps/datasets/views.py`    | `dataset_list`, `dataset_detail`, `event_stream`, `listing_event_stream` |
| `apps/datasets/urls.py`     | 4 URL patterns                                                           |
| `apps/datasets/perf.py`     | `CaptureQueries` context manager                                         |

### Templates

| File                                               | Purpose                                        |
| -------------------------------------------------- | ---------------------------------------------- |
| `templates/pages/datasets/list.html`               | Full listing page — metrics + 3 sections       |
| `templates/pages/datasets/detail.html`             | Full detail page — breadcrumb + stats + graph  |
| `templates/datasets/partials/listing_row.html`     | Single table row with 4 status dots + metadata |
| `templates/datasets/partials/dataset_section.html` | Section header with count badge + rows         |
| `templates/datasets/partials/graph_node.html`      | Single pipeline node card                      |
| `templates/datasets/partials/node_detail.html`     | Expanded attempt history for a step            |
| `templates/datasets/partials/perf_badge.html`      | Query performance badge fragment               |

### Static

| File                    | Source                                             |
| ----------------------- | -------------------------------------------------- |
| `static/js/htmx-sse.js` | `curl https://unpkg.com/htmx-ext-sse@2.0.1/sse.js` |

---

## Files to Edit

| File                                 | Change                                                                          |
| ------------------------------------ | ------------------------------------------------------------------------------- |
| `config/settings.py`                 | Add `"apps.datasets"` to `INSTALLED_APPS`                                       |
| `config/urls.py`                     | Add `path('', include('apps.datasets.urls'))`                                   |
| `templates/base.html`                | Load `htmx-sse.js` after `htmx.min.js`                                          |
| `templates/pages/save_detail.html`   | Add Datasets nav tab after Matchdays                                            |
| `templates/pages/save_snapshot.html` | Add Datasets nav tab after Matchdays                                            |
| `apps/landing/tasks.py`              | Insert 4 `DatasetEvent.create()` calls (parse success/fail, store success/fail) |
| `apps/bronze/tasks.py`               | Insert 2 `DatasetEvent.create()` calls (bronze success/fail)                    |

---

## Step-by-step Implementation

### Step 1: Create `apps/datasets/` package

Create the directory structure and all Python files.

```python
# apps/datasets/__init__.py
```

**Reasoning:** Package marker. Standard Django app convention.

---

### Step 2: `apps/datasets/models.py` — DatasetEvent

```python
from django.db import models

class DatasetEvent(models.Model):
    """
    Append-only event log decoupling the Celery pipeline from the SSE-driven UI.

    Tasks write one row per pipeline milestone (parse_completed, store_failed,
    bronze_completed, etc.). The SSE view reads rows newer than the client's
    Last-Event-ID using a single indexed query on (upload_id, created_at).

    This indirection avoids coupling the SSE reader to the mutable status
    fields of LandingZoneTask / BronzeIngestionTask, and avoids polling
    multiple tables with different lifecycle semantics.

    Events are never mutated or deleted. Old rows can be pruned by a
    future management command if the table grows large.
    """
    class EventType(models.TextChoices):
        PARSE_COMPLETED = "parse_completed", "Parse Completed"
        PARSE_FAILED = "parse_failed", "Parse Failed"
        STORE_COMPLETED = "store_completed", "Store Completed"
        STORE_FAILED = "store_failed", "Store Failed"
        BRONZE_COMPLETED = "bronze_completed", "Bronze Completed"
        BRONZE_FAILED = "bronze_failed", "Bronze Failed"

    upload = models.ForeignKey(
        "landing.LandingUpload",
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    payload = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["upload", "created_at"]),
        ]
```

**Reasoning:**

- `ForeignKey` with `CASCADE` — events belong to an upload; if the upload is deleted the events are meaningless
- `event_type` as `CharField` with `TextChoices` — controlled vocabulary, typed for IDE autocomplete, readable in the database
- `payload` as `JSONField` — flexible enough to hold duration, row counts, errors, or future metadata without schema changes. Django's `JSONField` on PostgreSQL uses `jsonb` internally — queryable, indexable, and efficient
- `created_at` with `db_index=True` — the SSE view queries `WHERE upload_id = X AND id > last_event_id ORDER BY id`. The composite index `(upload, created_at)` covers this exactly
- `ordering = ["-created_at"]` — most recent events first, used by the admin and any serialization
- Docstring explains _why_ this model exists, not just what columns it has — critical for future developers who might wonder "why not just use the task table?"

Run `python manage.py makemigrations datasets` to create the migration.

**Why not use a `PositiveIntegerField` for the SSE cursor.** Django's `id` (auto-incrementing `BigAutoField`) is monotonic and available on every model. Using `id__gt=last_event_id` is simpler and more reliable than tracking `created_at` with microsecond precision (clock skew, duplicate timestamps).

---

### Step 3: `apps/datasets/perf.py` — CaptureQueries

```python
import time
from django.db import connection

class CaptureQueries:
    def __enter__(self):
        self.baseline = len(connection.queries)
        self.start = time.monotonic()
        return self

    def __exit__(self, *args):
        self.count = len(connection.queries) - self.baseline
        self.duration = (time.monotonic() - self.start) * 1000

    @property
    def queries(self):
        return connection.queries[self.baseline:]
```

**Reasoning:**

- Zero dependencies — uses Django's built-in `connection.queries` (available when `DEBUG=True`)
- Context manager ensures baseline capture and diff are scoped to the specific view logic, not polluted by middleware queries
- `time.monotonic()` is immune to system clock changes
- Duration in milliseconds for readability in the badge
- Used as a simple decorator `with CaptureQueries() as perf:` around the ORM query in each view
- The `queries` property is for debugging — can be logged or inspected but isn't rendered by default

**Why not django-silk or django-debug-toolbar.**

- debug-toolbar injects into HTML, breaks with HTMX partial swaps, conflicts with the CRTs-canline `z-index`, and bundles jQuery
- Silk stores every request in the database, adds middleware overhead, and captures HTMX polling noise
- `CaptureQueries` is 12 lines, zero config, works in any environment, and renders the data right next to the query results

---

### Step 4: `apps/datasets/urls.py`

```python
from django.urls import path
from . import views

urlpatterns = [
    path("<slug:save_slug>/datasets/", views.dataset_list, name="save_datasets"),
    path("<slug:save_slug>/datasets/<int:upload_id>/", views.dataset_detail, name="dataset_detail"),
    path("hx/datasets/sse/<int:upload_id>/", views.event_stream, name="dataset_event_stream"),
    path("hx/datasets/sse/save/<slug:save_slug>/", views.listing_event_stream, name="dataset_listing_stream"),
]
```

**Reasoning:**

- Page URLs (`save_datasets`, `dataset_detail`) follow the existing pattern: `/<save_slug>/<section>/` and `/<save_slug>/<section>/<id>/`
- SSE URLs are prefixed `hx/datasets/sse/` — consistent with the existing `hx/` prefix convention in `apps/landing/urls.py`
- Two SSE endpoints: one scoped to a single upload (detail page), one scoped to a save's entire event stream (listing page). The listing endpoint could be further scoped to only events for uploads visible on the current page, but that optimization is premature

---

### Step 5: `config/urls.py` — wire in datasets URLs

```python
path('', include('apps.datasets.urls')),
```

**Reasoning:** Same mount point as `apps/landing/urls.py` and `apps/bronze/urls.py`. The dataset URLs start with `hx/` or `<slug:save_slug>/` so there's no route conflict.

---

### Step 6: `config/settings.py` — register app

```python
INSTALLED_APPS = [
    ...
    # Django User Apps
    "apps.core",
    "apps.landing",
    "apps.bronze",
    "apps.datasets",  # <-- add here
]
```

---

### Step 7: SSE JS — download extension

```bash
curl -o static/js/htmx-sse.js https://unpkg.com/htmx-ext-sse@2.0.1/sse.js
```

**Reasoning:** Vendored (not CDN-linked) so the app works without internet, in Docker, and in air-gapped environments. Loaded after `htmx.min.js` in `base.html`:

```html
<script src="{% static 'django_htmx/htmx.min.js' %}" defer></script>
<script src="{% static 'js/htmx-sse.js' %}" defer></script>
```

`defer` preserves order — `htmx-sse.js` registers the SSE extension on the global `htmx` object, which must exist first.

---

### Step 8: `apps/datasets/views.py` — dataset_list (listing page)

**View logic:**

```python
def dataset_list(request, save_slug):
    save = get_object_or_404(SaveMaster.objects.select_related("game_version"), slug=save_slug)
    saves = SaveMaster.objects.select_related("game_version").order_by("-updated_at")
    active_team = save.managed_teams.filter(is_active=True).select_related("team").first()

    parse_task = LandingZoneTask.objects.filter(upload=OuterRef('pk'), step='parse').order_by('-attempt')
    store_task = LandingZoneTask.objects.filter(upload=OuterRef('pk'), step='store').order_by('-attempt')
    bronze = BronzeIngestion.objects.filter(landing_upload=OuterRef('pk')).order_by('-started_at')
    bronze_task = BronzeIngestionTask.objects.filter(ingestion__landing_upload=OuterRef('pk')).order_by('-ingestion__started_at', '-attempt')

    with CaptureQueries() as perf:
        uploads = list(LandingUpload.objects.filter(save_master=save).annotate(
            parse_status=Subquery(parse_task.values('status')[:1]),
            parse_error_type=Subquery(parse_task.values('error_type')[:1]),
            parse_error_message=Subquery(parse_task.values('error_message')[:1]),
            store_status=Subquery(store_task.values('status')[:1]),
            store_error_type=Subquery(store_task.values('error_type')[:1]),
            store_error_message=Subquery(store_task.values('error_message')[:1]),
            bronze_status=Subquery(bronze.values('status')[:1]),
            bronze_id=Subquery(bronze.values('id')[:1]),
            bronze_source_rows=Subquery(bronze.values('source_row_count')[:1]),
            bronze_ingested_rows=Subquery(bronze.values('ingested_row_count')[:1]),
            bronze_error_type=Subquery(bronze.values('error_type')[:1]),
            bronze_error_message=Subquery(bronze.values('error_message')[:1]),
            bronze_task_status=Subquery(bronze_task.values('status')[:1]),
            bronze_task_error_type=Subquery(bronze_task.values('error_type')[:1]),
            bronze_task_error_message=Subquery(bronze_task.values('error_message')[:1]),
        ))

    # Partition into 3 sections
    active_failed = []
    completed_landing = []
    completed_bronze = []

    for u in uploads:
        upload_ok = u.status == 'completed'
        bronze_ok = u.bronze_status == 'completed'
        bronze_exists = u.bronze_id is not None

        if not upload_ok or not bronze_ok or (upload_ok and not bronze_exists):
            active_failed.append(u)
        elif upload_ok and not bronze_ok:
            completed_landing.append(u)
        else:
            completed_bronze.append(u)

    # Metrics
    total_uploads = len(uploads)
    completed_count = sum(1 for u in uploads if u.status == 'completed')
    health = round((completed_count / total_uploads * 100)) if total_uploads else 0
    backlog = sum(1 for u in uploads if u.status in ('pending', 'processing', 'retrying'))
    data_volume = sum(u.file_size_bytes or 0 for u in uploads)

    # P95 latency
    p95 = compute_p95_latency(save)  # helper: aggregates over completed bronze rows

    context = {
        'save': save,
        'saves': saves,
        'active_team': active_team,
        'active_failed': active_failed,
        'completed_landing': completed_landing,
        'completed_bronze': completed_bronze,
        'perf': perf,
        'health': health,
        'backlog': backlog,
        'data_volume': data_volume,
        'p95_latency': p95,
    }
    return render(request, "pages/datasets/list.html", context)
```

**Why `list()` forces evaluation.** The ORM annotations are lazy — `list()` evaluates the queryset inside `CaptureQueries` so the subquery execution is captured. Without `list()`, the queries would execute during template rendering, making the performance numbers inaccurate.

**Why Python partition instead of SQL.** The annotated fields (`bronze_status`, `bronze_id`) are subquery results that can't be referenced in `WHERE` across all three sections in a single query without wrapping in a subquery. Partitioning in Python with a single evaluated list is simpler and the data volume (hundreds, not millions) means memory is not a concern.

**P95 latency helper:**

```python
def compute_p95_latency(save):
    from django.db.models import F, ExpressionWrapper, DurationField
    from django.db.models.functions import ExtractEpoch
    qs = BronzeIngestion.objects.filter(
        landing_upload__save_master=save,
        status='completed',
        completed_at__isnull=False,
        landing_upload__upload_timestamp__isnull=False,
    ).annotate(
        latency_s=ExtractEpoch(F('completed_at') - F('landing_upload__upload_timestamp'))
    ).order_by('latency_s').values_list('latency_s', flat=True)

    latencies = list(qs)
    if not latencies:
        return None
    idx = int(len(latencies) * 0.95)
    return latencies[idx]
```

**Reasoning:** Pure Django ORM — no raw SQL. Computes latency in seconds, sorts, picks the 95th percentile. PostgreSQL handles the arithmetic efficiently.

---

### Step 9: `templates/pages/datasets/list.html`

```
┌────────────────────────────────────────────────────────────┐
│ [VR3N]    [Active Save ▼]    [+ Create Save]              │
│                                                            │
│ Overview  Squad  Scouting  Matchdays  *Datasets*          │
│                                                            │
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                       │
│ │ 87%  │ │  3   │ │ 2.4GB│ │ 47s  │   ← metric_cards     │
│ │Health│ │Backlg│ │Volume│ │P95   │                       │
│ └──────┘ └──────┘ └──────┘ └──────┘                       │
│                                                            │
│ ── Active / Failed (8) ─────────────────────────────      │
│                                                            │
│  ● ● ● ●  squad_snapshot  winter_scout  2.1 MB  12:34    │
│  ● ● ● ○  matchstats      gameday_42    1.2 MB  12:30    │
│  ● ○ ○ ○  scouting        nov_data      500 KB  11:15    │
│                                                            │
│ ── Completed Landing Zones (14) ───────────────────       │
│                                                            │
│  ● ● ● ○  squad_snapshot  summer_export  3.2 MB  10:00   │
│  ● ● ● ○  matchstats      gameday_41     1.8 MB  09:45   │
│                                                            │
│ ── Completed Bronze (12) ──────────────────────────       │
│                                                            │
│  ● ● ● ●  squad_snapshot  summer_export  23k rows 10:01  │
│  ● ● ● ●  matchstats      gameday_41     15k rows 09:46  │
│                                                            │
│                                         12q · 34ms         │
└────────────────────────────────────────────────────────────┘
```

**Status dot colors per phase:**

- Green (`bg-emerald-400`) — completed / succeeded
- Amber (`bg-amber-400`) — processing / running / retrying
- Red (`bg-red-400`) — failed
- Gray (`bg-zinc-700`) — pending / not reached / no record

**Template structure:**

- Extends `base.html`
- Uses `<c-layout.app_shell>`, `<c-navigation.header>`, `<c-navigation.nav_tab>` (Datasets active)
- Metrics row: 4 `<c-metrics.metric_card>` components
- Each section: `<c-datasets.dataset_section>` with `title`, `count`, `rows`, `variant="danger|default|success"`
- Each row: `<c-datasets.listing_row>` with `upload`, all annotated fields
- SSE: `<div hx-ext="sse" sse-connect="{% url 'dataset_listing_stream' save_slug=save.slug %}" sse-swap="dataset-event">` wrapping the sections
- Perf badge: included once at the bottom

**Why not django-tables2.** The user explicitly rejected it because individual row updates (SSE-driven) don't work with django-tables2's table-level swap model. Custom HTML rows give full control over the SSE swap target: each row has `id="upload-row-{{ upload.id }}"` and the SSE handler replaces exactly that element.

---

### Step 10: `apps/datasets/views.py` — dataset_detail (detail page)

```python
def dataset_detail(request, save_slug, upload_id):
    save = get_object_or_404(SaveMaster, slug=save_slug)
    saves = SaveMaster.objects.select_related("game_version").order_by("-updated_at")

    upload = get_object_or_404(
        LandingUpload.objects.prefetch_related(
            "task_rows",
            "bronze_ingestions__task_rows",
        ).select_related("save_master"),
        id=upload_id,
        save_master=save,
    )

    bronze_ingestion = upload.bronze_ingestions.first()

    # Build per-step attempt lists
    parse_attempts = [t for t in upload.task_rows.all() if t.step == 'parse']
    store_attempts = [t for t in upload.task_rows.all() if t.step == 'store']
    bronze_attempts = list(bronze_ingestion.task_rows.all()) if bronze_ingestion else []

    context = {
        'save': save,
        'saves': saves,
        'upload': upload,
        'bronze_ingestion': bronze_ingestion,
        'parse_attempts': parse_attempts,
        'store_attempts': store_attempts,
        'bronze_attempts': bronze_attempts,
    }
    return render(request, "pages/datasets/detail.html", context)
```

**Reasoning:**

- `prefetch_related` loads all task rows and bronze task rows in 3 queries (one per relation) — avoids N+1. The data per upload is small (a handful of task rows), so a single prefetch per relation is optimal
- Attempt lists are sorted in Python (already ordered by `attempt` via `UniqueConstraint` + natural ordering) — no extra DB sorting
- The detail page doesn't use annotations because it has direct access to the prefetched task rows. The graph nodes derive their status from the latest attempt per step (first in the sorted list)

---

### Step 11: `templates/pages/datasets/detail.html`

```
┌────────────────────────────────────────────────────────────┐
│ [VR3N]    [Active Save ▼]    [+ Create Save]              │
│                                                            │
│ Overview  Squad  Scouting  Matchdays  *Datasets*          │
│                                                            │
│ ← Datasets  /  squad_snapshot_20260101                    │
│                                                            │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│ │ Source   │ │ Snapshot │ │ Size     │ │ Rows     │     │
│ │ jan.html │ │ Squad    │ │ 2.1 MB   │ │ 23k→23k  │     │
│ └──────────┘ └──────────┘ └──────────┘ └──────────┘     │
│ ┌──────────┐ ┌──────────┐                                  │
│ │ Pipeline │ │ Hash     │                                  │
│ │ 12.4s    │ │ a3f2...  │                                  │
│ └──────────┘ └──────────┘                                  │
│                                                            │
│ ── Pipeline Graph ──────────────────────────────────      │
│                                                            │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌─── │
│  │ 📄 Source│ ──→ │ 🔧 Parse │ ──→ │ 💾 Store │ ──→ │ 🗄️ │
│  │  ● ready │     │  ● 0.3s  │     │  ● 0.1s  │     │ ●  │
│  │          │     │          │     │          │     │ 11. │
│  └──────────┘     └──────────┘     └──────────┘     └─── │
│                                                            │
│  ┌─ Parse (attempt 2) ────────────────────────────┐      │
│  │ status: succeeded       worker: celery@w1      │      │
│  │ started: 12:34:01       finished: 12:34:02     │      │
│  │ duration: 1.2s                                 │      │
│  └────────────────────────────────────────────────┘      │
│                                                            │
│                                         12q · 34ms         │
└────────────────────────────────────────────────────────────┘
```

**Back arrow:** `<a href="{% url 'save_datasets' save_slug=save.slug %}" class="...">← Datasets</a>` — uses an arrow unicode character + the section name. Consistent with the "back to listing" UX pattern.

**Breadcrumb:** Not a traditional breadcrumb bar, just the back link styled as navigation context. A full breadcrumb (`Save > Datasets > filename`) is unnecessary when the nav tabs already show the current position.

**SSE:** `<div hx-ext="sse" sse-connect="{% url 'dataset_event_stream' upload_id=upload.id %}" sse-swap="dataset-event">` wrapping the graph and stats. On each event, the target element (node, stats panel) is swapped with the updated partial from the SSE view.

---

### Step 12: `apps/datasets/views.py` — SSE views

**Single-upload stream (detail page):**

```python
import json
import time
from django.http import StreamingHttpResponse

def event_stream(request, upload_id):
    last_id = request.META.get('HTTP_LAST_EVENT_ID')
    last_id = int(last_id) if last_id else 0

    def generate():
        while True:
            events = DatasetEvent.objects.filter(
                upload_id=upload_id,
                id__gt=last_id,
            ).order_by('id')

            for event in events:
                data = {
                    'id': event.id,
                    'type': event.event_type,
                    'payload': event.payload,
                }
                yield f"event: dataset-event\ndata: {json.dumps(data)}\n\n"
                last_id = event.id

            if not events:
                time.sleep(0.5)

    return StreamingHttpResponse(generate(), content_type='text/event-stream')
```

**Save-scoped stream (listing page):**

```python
def listing_event_stream(request, save_slug):
    last_id = request.META.get('HTTP_LAST_EVENT_ID')
    last_id = int(last_id) if last_id else 0

    def generate():
        while True:
            events = DatasetEvent.objects.filter(
                upload__save_master__slug=save_slug,
                id__gt=last_id,
            ).select_related('upload').order_by('id')

            for event in events:
                data = {
                    'id': event.id,
                    'upload_id': event.upload_id,
                    'type': event.event_type,
                    'payload': event.payload,
                }
                yield f"event: dataset-event\ndata: {json.dumps(data)}\n\n"
                last_id = event.id

            if not events:
                time.sleep(0.5)

    return StreamingHttpResponse(generate(), content_type='text/event-stream')
```

**Why `time.sleep(0.5)` instead of async.** Django's `StreamingHttpResponse` uses a synchronous generator. The 500ms sleep is the poll interval — 2 checks/second per connection. With 100 concurrent connections, that's 200 DB queries/second — a trivial load for PostgreSQL. An async approach would require Django's ASGI mode and a different server.

**Why `select_related('upload')` on the listing stream.** The listing stream filters by `upload__save_master__slug` — a FK traversal. `select_related` does a SQL `JOIN` instead of a lazy query, avoiding N+1 when accessing `event.upload_id` (which doesn't actually need it since `upload_id` is the FK column directly on the table). The `JOIN` is for the `WHERE upload__save_master__slug = ...` filter only. Actually, since we filter on the FK's FK, a `JOIN` is required regardless — Django's ORM handles it internally even without `select_related`. The `select_related` just avoids an extra query if we later access `event.upload` in the loop. Clean to include.

**Client-side SSE handler:**

```javascript
document.body.addEventListener("htmx:sseMessage", function (evt) {
  var data = JSON.parse(evt.detail.data);
  // data.upload_id, data.type, data.payload

  if (evt.detail.target.id === "listing-sse") {
    // Swap the specific row
    var row = document.getElementById("upload-row-" + data.upload_id);
    if (row) {
      htmx.ajax("GET", "/hx/datasets/row/" + data.upload_id + "/", {
        target: "#upload-row-" + data.upload_id,
        swap: "outerHTML",
      });
    }
  }
});
```

**Reasoning:** The SSE message carries the `upload_id` of the changed upload. The handler finds the DOM element by ID and triggers an HTMX GET to fetch the updated row partial. This is more efficient than embedding the full row HTML in the SSE payload — the partial is only a few KB and only fetched when something actually changed.

---

### Step 13: Node expansion HTMX

The graph node template includes an `hx-get` on the card:

```html
<div
  class="cursor-pointer"
  hx-get="{% url 'node_detail_partial' upload_id=upload.id step=step %}"
  hx-target="#node-detail"
  hx-swap="innerHTML"
>
  ... node content ...
</div>
```

When clicked, the partial `/hx/datasets/node/<upload_id>/<step>/` returns the attempt history HTML:

```python
def node_detail_partial(request, upload_id, step):
    upload = get_object_or_404(LandingUpload.objects.prefetch_related("task_rows"), id=upload_id)
    if step == 'bronze':
        bronze = get_object_or_404(BronzeIngestion.objects.prefetch_related("task_rows"), landing_upload=upload)
        attempts = list(bronze.task_rows.all())
    else:
        attempts = [t for t in upload.task_rows.all() if t.step == step]

    return render(request, "datasets/partials/node_detail.html", {
        "step": step,
        "attempts": attempts,
    })
```

**Why a separate URL for node detail.** The detail page is already 4 views (page + SSE + partial). Adding a 5th URL for the node expansion is clean and testable. The alternative (embedding all attempt data in the page and toggling visibility with CSS/JS) would make the initial page load heavier and break the progressive-enhancement pattern.

---

### Step 14: Wire DatasetEvent.create() into existing tasks

**`apps/landing/tasks.py` — 4 insertions:**

1. `parse_file` success — after `task_row.save(update_fields=["status", "finished_at"])` (after line 96):

```python
DatasetEvent.objects.create(
    upload=upload,
    event_type=DatasetEvent.EventType.PARSE_COMPLETED,
    payload={'attempt': attempt, 'duration_s': task_row.duration_seconds},
)
```

2. `parse_file` failure — after `record_failure` (after line 102):

```python
DatasetEvent.objects.create(
    upload=upload,
    event_type=DatasetEvent.EventType.PARSE_FAILED,
    payload={'attempt': attempt, 'error_type': task_row.error_type, 'error_message': task_row.error_message},
)
```

3. `store_parquet` success — after `task_row.save(update_fields=["status", "finished_at"])` (after line 175):

```python
DatasetEvent.objects.create(
    upload=upload,
    event_type=DatasetEvent.EventType.STORE_COMPLETED,
    payload={'attempt': attempt, 'duration_s': task_row.duration_seconds, 'parquet_path': str(dest_path)},
)
```

4. `store_parquet` failure — in each `except` block (lines 182, 184, 192) before re-raise:

```python
DatasetEvent.objects.create(
    upload=upload,
    event_type=DatasetEvent.EventType.STORE_FAILED,
    payload={'attempt': attempt, 'error_type': type(exc).__name__, 'error_message': str(exc)},
)
```

**`apps/bronze/tasks.py` — 2 insertions:**

5. `ingest_to_bronze` success — after `task_row.save(update_fields=["phase", "status", "finished_at"])` (after line 200):

```python
DatasetEvent.objects.create(
    upload=upload,
    event_type=DatasetEvent.EventType.BRONZE_COMPLETED,
    payload={
        'attempt': attempt,
        'duration_s': task_row.duration_seconds,
        'source_rows': source_count,
        'ingested_rows': ingested_count,
        'columns': column_count,
    },
)
```

6. `ingest_to_bronze` failure — in the non-retryable except block (after line 220) and in the generic except block (after line 229):

```python
DatasetEvent.objects.create(
    upload=upload,
    event_type=DatasetEvent.EventType.BRONZE_FAILED,
    payload={'attempt': attempt, 'error_type': type(exc).__name__, 'error_message': str(exc)},
)
```

**Why at the task level, not in `PipelineTask.record_failure`.** The `record_failure` base method doesn't know about `DatasetEvent` — it's in `apps.core.tasks` and shouldn't import from `apps.datasets` (circular dependency risk, and it would couple the base class to a specific UI concern). The inline `create()` calls keep the event knowledge at the task level where the detailed data (duration, row counts) is available.

**Why after `save()` and not before.** The event should be created after the DB state is committed so the SSE reader never sees an event whose corresponding task row doesn't exist yet. The 500ms poll interval means there's no real race condition in practice, but ordering by `id` guarantees the SSE reader sees events in the order the writer created them.

---

### Step 15: Templates — Aesthetic Details

All templates follow the existing design system:

- Background: `bg-zinc-950` panels, `bg-[#050505]` page background
- Borders: `border-zinc-800` (soft), `border-zinc-700` (emphasis)
- Text: `text-zinc-100` body, `text-zinc-400` muted, `text-zinc-600` secondary
- Accents: `text-violet-400`, `text-cyan-400`, `text-pink-400`
- Typography: uppercase with `tracking-[0.2em]` for labels, `tracking-widest` for headers
- Transitions: `transition-all`, `hover:opacity-80` on interactive elements
- CRTs-canline noise overlay and fractal noise inherited from `base.html`

**Status dot component (inline in listing_row.html):**

```html
{% for phase, status in phases %}
<span
  class="inline-block w-2.5 h-2.5 rounded-full
    {% if status == 'completed' or status == 'succeeded' %}bg-emerald-400
    {% elif status == 'processing' or status == 'running' or status == 'retrying' %}bg-amber-400
    {% elif status == 'failed' %}bg-red-400
    {% else %}bg-zinc-700{% endif %}"
  title="{{ phase }}: {{ status|default:'pending' }}"
>
</span>
{% endfor %}
```

**Graph node card:**

```html
<c-ui.panel
  class="border-l-4
  {% if status == 'completed' or status == 'succeeded' %}border-l-emerald-500
  {% elif status == 'processing' or status == 'running' or status == 'retrying' %}border-l-amber-500
  {% elif status == 'failed' %}border-l-red-500
  {% else %}border-l-zinc-700{% endif %}
  cursor-pointer transition-all hover:border-l-violet-500"
>
  ...
</c-ui.panel>
```

**Arrow connector (CSS between nodes):**

```css
.pipeline-arrow::after {
  content: "→";
  @apply text-zinc-700 text-2xl mx-2;
}
```

**Perf badge:**

```html
<span class="text-[10px] tracking-[0.3em] text-zinc-700 font-mono">
  {{ perf.count }}q · {{ perf.duration|floatformat:0 }}ms
</span>
```

---

## Edge Cases & Trade-offs

### Duplicate ingestion guard

When `ingest_to_bronze` detects a duplicate (line 138-151), it sets the ingestion to `FAILED` with `error_type = "DuplicateIngestion"`. No `DatasetEvent` is created because:

- The upload status is already `COMPLETED` from `store_parquet`
- The existing bronze record was created but immediately failed
- The SSE reader already emitted `store_completed` — the failed bronze event would confuse the UI
- Future BronzeIngestion records for the same upload would create their own events

### Non-retryable exceptions

`FileNotFoundError` and `FileExistsError` in `store_parquet` are re-raised without `record_failure` — the upload stays at `PROCESSING` and the task row stays at `RUNNING`. A `store_failed` `DatasetEvent` is still emitted so the UI shows the red Store node. This is intentional: the event tracks _what happened_, not the retry state machine.

### SSE connection lifecycle

The SSE view runs indefinitely. When the browser navigates away, the connection is dropped and the generator's `while True` loop is terminated by the WSGI server. No cleanup needed.

### Dedup SSE events on page refresh

When the page reloads, `last_event_id` starts at 0, so all events are replayed. The listing row partials are idempotent — replacing a row with the same data is harmless. The detail page graph nodes are also idempotent.

### Browser SSE reconnection

The HTMX SSE extension automatically reconnects after a dropped connection, sending `Last-Event-ID` with the last received event ID. The server uses this to resume from where the client left off.

---

## Verification Criteria

1. **Listing page loads** — 3 sections render with correct counts. 4 metric cards show correct values. Perf badge visible.
2. **Status dots are correct** — each upload row shows the correct color for each phase based on actual task/bronze status.
3. **Detail page loads** — breadcrumb, 6 stat cards, 4 graph nodes. Clicking a node expands attempt history below the graph.
4. **SSE updates listing** — start a new upload, navigate to datasets listing, observe the row appear in section 1 and move to section 2/3 as the pipeline progresses. Without page refresh.
5. **SSE updates detail** — navigate to detail page for an active upload, observe graph nodes update from amber→green as tasks complete.
6. **Performance badge shows realistic numbers** — ~15 queries, <100ms for a save with typical data volume.
7. **No regression** — existing upload forms, status polling, and bronze ingestion still work on Squad/Scouting/Matchdays pages.
