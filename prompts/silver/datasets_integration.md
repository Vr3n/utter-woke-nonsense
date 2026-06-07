# Datasets Silver Integration — Implementation Plan

## Goal

Add silver layer visibility and dispatch control to the datasets listing and
detail pages. Currently these pages show Source → Parse → Store → Bronze but
have no silver awareness at all (no dots, no pipeline node, no dispatch).

## Design decisions (resolved via interview)

| Decision | Resolution |
|---|---|
| Section overlap | Mutually exclusive — one upload in exactly one section |
| Section headings | Active / Failed · Awaiting Ingestion · Awaiting Transformation · Fully Processed |
| Silver partial guard on detail page | Only render when bronze completed |
| Extra annotations on listing row | Just `silver_status` — no row counts/IDs |
| Pipeline duration | Extend to silver; per-node durations in graph |
| Node detail silver handler | `.first()` with explicit `Http404` (robust across retries) |
| Pipeline graph layout | Single row, responsive |
| `_phase_dots` | Ignore — dead code, out of scope |
| Detail page silver polling | Every 2s (same as bronze landing page) |
| Pipeline Health metric | Redefined: `silver_completed / total * 100`; keep name |
| P95 latency | Extend to silver via COALESCE |

---

## Files to modify

### 1. `apps/datasets/views.py`

#### Imports
```python
from apps.silver.models import SilverIngestion
```

#### `dataset_list` — mutually exclusive section classification

Add `silver_status` to annotations (same subquery pattern as `bronze_status`):

```python
silver = SilverIngestion.objects.filter(
    bronze_ingestion__landing_upload=OuterRef("pk")
).order_by("-started_at")
```

```python
.annotate(
    # ... existing annotations ...
    silver_status=Subquery(silver.values("status")[:1]),
)
```

Replace the classification loop with mutually exclusive sections:

```python
active_failed = []
awaiting_ingestion = []
awaiting_transformation = []
fully_processed = []

for u in uploads:
    if u.status != "completed":
        active_failed.append(u)
    elif u.silver_status == "completed":
        fully_processed.append(u)
    elif u.bronze_status == "completed":
        awaiting_transformation.append(u)
    else:
        awaiting_ingestion.append(u)
```

Pipeline Health recalculated:
```python
fully_processed_count = len(fully_processed)
health = round((fully_processed_count / total_uploads * 100)) if total_uploads else 0
```

P95 latency extends to silver:
```sql
SELECT EXTRACT(EPOCH FROM (
  COALESCE(si.completed_at, bi.completed_at, lu.upload_timestamp) - lu.upload_timestamp
))
FROM bronze_bronzeingestion bi
JOIN landing_landingupload lu ON lu.id = bi.landing_upload_id
LEFT JOIN silver_silveringestion si ON si.bronze_ingestion_id = bi.id
WHERE lu.save_master_id = %s
  AND (lu.status = 'completed')
  AND lu.upload_timestamp IS NOT NULL
ORDER BY 1
```

Context updates:
```python
"active_failed": active_failed,
"awaiting_ingestion": awaiting_ingestion,
"awaiting_transformation": awaiting_transformation,
"fully_processed": fully_processed,
```

Remove `completed_landing` and `completed_bronze` from context.

#### `dataset_detail` — fetch silver ingestion

```python
silver_ingestion = None
if bronze_ingestion is not None:
    silver_ingestion = (
        SilverIngestion.objects
        .filter(bronze_ingestion=bronze_ingestion)
        .order_by("-started_at")
        .first()
    )
```

Add `silver_attempts`:
```python
silver_attempts = (
    sorted(list(silver_ingestion.task_rows.all()), key=lambda t: t.attempt)
    if silver_ingestion
    else []
)
```

Extend `pipeline_duration` to silver:
```python
pipeline_end = None
if silver_ingestion and silver_ingestion.completed_at:
    pipeline_end = silver_ingestion.completed_at
elif bronze_ingestion and bronze_ingestion.completed_at:
    pipeline_end = bronze_ingestion.completed_at

pipeline_duration = None
if pipeline_end:
    delta = pipeline_end - upload.upload_timestamp
    pipeline_duration = round(delta.total_seconds(), 1)
```

Context additions:
```python
"silver_ingestion": silver_ingestion,
"silver_attempts": silver_attempts,
```

#### `node_detail_partial` — add silver step

```python
if step == "silver":
    silver = SilverIngestion.objects.filter(
        bronze_ingestion__landing_upload=upload,
    ).prefetch_related("task_rows").order_by("-started_at").first()
    if silver is None:
        raise Http404("No silver ingestion for this upload")
    attempts = sorted(list(silver.task_rows.all()), key=lambda t: t.attempt)
```

Add `from django.http import Http404` to imports.

#### `listing_row_partial` — add silver annotation

```python
silver = SilverIngestion.objects.filter(
    bronze_ingestion__landing_upload=OuterRef("pk")
).order_by("-started_at")
```

Add `silver_status=Subquery(silver.values("status")[:1])` to the annotation block.

---

### 2. `templates/datasets/partials/listing_row.html`

Add a silver status dot after the bronze dot:

```html
{% comment %}Silver dot{% endcomment %}
<span class="inline-block w-2.5 h-2.5 rounded-full
  {% if upload.silver_status == 'completed' %}bg-emerald-400
  {% elif upload.silver_status == 'failed' %}bg-red-400
  {% elif upload.silver_status == 'skipped' %}bg-zinc-600
  {% elif upload.silver_status == 'processing' or upload.silver_status == 'retrying' %}bg-amber-400
  {% elif upload.silver_status is None and upload.bronze_status == 'completed' %}bg-zinc-600
  {% else %}bg-zinc-700{% endif %}"
  title="Silver: {{ upload.silver_status|default:'pending' }}">
</span>
```

---

### 3. `templates/pages/datasets/list.html`

Replace the three section blocks (Active/Failed, Completed Landing Zones, Completed Bronze) with four mutually exclusive sections:

```html
{% comment %}Section 1: Active / Failed{% endcomment %}
<div class="mb-8">
  <div class="mb-4 flex items-center gap-3 border-b border-zinc-800 pb-3">
    <h2 class="text-sm uppercase tracking-[0.2em] text-red-400 font-bold">Active / Failed</h2>
    <c-ui.badge>{{ active_failed|length }}</c-ui.badge>
  </div>
  {% if active_failed %}
    <div class="border border-zinc-800 divide-y divide-zinc-800/50">
      {% for upload in active_failed %}
        {% include "datasets/partials/listing_row.html" %}
      {% endfor %}
    </div>
  {% else %}
    <div class="border border-zinc-800 bg-zinc-950 px-6 py-8 text-center">
      <span class="text-sm uppercase tracking-[0.2em] text-zinc-700">No items</span>
    </div>
  {% endif %}
</div>

{% comment %}Section 2: Awaiting Ingestion{% endcomment %}
<div class="mb-8">
  <div class="mb-4 flex items-center gap-3 border-b border-zinc-800 pb-3">
    <h2 class="text-sm uppercase tracking-[0.2em] text-zinc-400 font-bold">Awaiting Ingestion</h2>
    <c-ui.badge>{{ awaiting_ingestion|length }}</c-ui.badge>
  </div>
  {% if awaiting_ingestion %}
    <div class="border border-zinc-800 divide-y divide-zinc-800/50">
      {% for upload in awaiting_ingestion %}
        {% include "datasets/partials/listing_row.html" %}
      {% endfor %}
    </div>
  {% else %}
    <div class="border border-zinc-800 bg-zinc-950 px-6 py-8 text-center">
      <span class="text-sm uppercase tracking-[0.2em] text-zinc-700">No items</span>
    </div>
  {% endif %}
</div>

{% comment %}Section 3: Awaiting Transformation{% endcomment %}
<div class="mb-8">
  <div class="mb-4 flex items-center gap-3 border-b border-zinc-800 pb-3">
    <h2 class="text-sm uppercase tracking-[0.2em] text-amber-400 font-bold">Awaiting Transformation</h2>
    <c-ui.badge>{{ awaiting_transformation|length }}</c-ui.badge>
  </div>
  {% if awaiting_transformation %}
    <div class="border border-zinc-800 divide-y divide-zinc-800/50">
      {% for upload in awaiting_transformation %}
        {% include "datasets/partials/listing_row.html" %}
      {% endfor %}
    </div>
  {% else %}
    <div class="border border-zinc-800 bg-zinc-950 px-6 py-8 text-center">
      <span class="text-sm uppercase tracking-[0.2em] text-zinc-700">No items</span>
    </div>
  {% endif %}
</div>

{% comment %}Section 4: Fully Processed{% endcomment %}
<div class="mb-8">
  <div class="mb-4 flex items-center gap-3 border-b border-zinc-800 pb-3">
    <h2 class="text-sm uppercase tracking-[0.2em] text-emerald-400 font-bold">Fully Processed</h2>
    <c-ui.badge>{{ fully_processed|length }}</c-ui.badge>
  </div>
  {% if fully_processed %}
    <div class="border border-zinc-800 divide-y divide-zinc-800/50">
      {% for upload in fully_processed %}
        {% include "datasets/partials/listing_row.html" %}
      {% endfor %}
    </div>
  {% else %}
    <div class="border border-zinc-800 bg-zinc-950 px-6 py-8 text-center">
      <span class="text-sm uppercase tracking-[0.2em] text-zinc-700">No items</span>
    </div>
  {% endif %}
</div>
```

---

### 4. `templates/pages/datasets/detail.html`

#### Pipeline graph — add silver node after bronze

Replace the closing `</div>` of the pipeline-graph inner flex, adding after the bronze node and its connector:

```html
<div class="pipeline-connector">
  <svg width="48" height="2" viewBox="0 0 48 2" class="text-zinc-700">
    <line x1="0" y1="1" x2="44" y2="1" stroke="currentColor" stroke-width="1.5"/>
    <polygon points="44,0 48,1 44,2" fill="currentColor"/>
  </svg>
</div>

{% with latest_silver=silver_attempts|last %}
<c-datasets.graph_node
  step="silver"
  label="Silver"
  status="{% if silver_ingestion %}{{ silver_ingestion.status }}{% else %}pending{% endif %}"
  duration="{% if latest_silver.duration_seconds %}{{ latest_silver.duration_seconds }}s{% else %}—{% endif %}"
  upload_id="{{ upload.id }}" />
{% endwith %}
```

#### Stats panel — show silver rows

In the "Rows" cell, add silver info after bronze:

```html
{% if silver_ingestion and silver_ingestion.inserted_row_count is not None %}
  <span class="text-zinc-600 mx-1">||</span>
  <span class="text-violet-400">{{ silver_ingestion.inserted_row_count }}</span>
  {% if silver_ingestion.dropped_row_count %}
    <span class="text-xs text-zinc-600">(-{{ silver_ingestion.dropped_row_count }})</span>
  {% endif %}
{% endif %}
```

#### Silver dispatch + status partial

After the pipeline graph section and before `node-detail`, add:

```html
{% comment %}Silver dispatch + status{% endcomment %}
{% if bronze_ingestion and bronze_ingestion.status == "completed" %}
  <div class="mb-6">
    {% include "silver/partials/silver_status.html" with upload_id=upload.id %}
  </div>
{% endif %}
```

---

### 5. `templates/cotton/datasets/graph_node.html`

Add a silver step icon case after the bronze case:

```html
{% elif step == "silver" %}
<svg class="w-4 h-4 {% if status == 'completed' %}text-emerald-400{% elif status == 'failed' %}text-red-400{% else %}text-zinc-600{% endif %}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/>
</svg>
```

---

## Summary of context variable changes

| Old | New | Template file |
|---|---|---|
| `completed_landing` | Removed — replaced by mutually exclusive sections | `list.html` |
| `completed_bronze` | Removed — replaced by mutually exclusive sections | `list.html` |
| — | `awaiting_ingestion` | `list.html` |
| — | `awaiting_transformation` | `list.html` |
| — | `fully_processed` | `list.html` |
| — | `silver_ingestion` | `detail.html` |
| — | `silver_attempts` | `detail.html` |

No existing context key is renamed — only removed and replaced.

---

## Test considerations

- Existing `active_failed` group membership unchanged
- Upload in `fully_processed` is a subset of what was previously in `completed_bronze`
- Node detail for "silver" step returns 404 when no SilverIngestion exists (`.first()` + explicit `Http404`)
- Pipeline duration extends through silver when silver is completed; falls back to bronze otherwise
- P95 latency query extended with LEFT JOIN silver — existing bronze-only P95 values will change slightly (longer tail)
