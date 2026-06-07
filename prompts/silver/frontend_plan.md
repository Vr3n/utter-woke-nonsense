# Silver Frontend — Implementation Plan

## Goal

Add a silver processing section to the data management upload status page.
If bronze completed but silver hasn't been processed, show a "Process Silver"
button. Mirror the bronze_status pattern for processing/completed/failed states,
but use an event-driven approach (no polling) when no silver record exists.

## Relationship chain

```
LandingUpload (upload_id)
  └── BronzeIngestion (bronze_ingestion via .landing_upload_id)
        └── SilverIngestion (via .bronze_ingestion)
```

Silver is looked up from `upload_id` via:

1. `BronzeIngestion.objects.filter(landing_upload_id=upload_id).order_by("-started_at").first()`
2. `SilverIngestion.objects.filter(bronze_ingestion=bronze).order_by("-started_at").first()`

## Visibility rule

Silver section lives inside the `{% elif upload.status == "completed" %}` block
of `upload_status.html`. Content renders conditionally based on bronze+silver
state:

| Bronze exists | Bronze completed | Silver exists | Silver terminal | What renders                                               |
| ------------- | ---------------- | ------------- | --------------- | ---------------------------------------------------------- |
| No            | —                | No            | —               | Empty placeholder div (no content, no trigger)             |
| Yes           | No               | No            | —               | Empty placeholder div (no content, no trigger)             |
| Yes           | Yes              | No            | —               | Zinc dot + "Silver: Pending" + **"Process Silver" button** |
| Yes           | Yes              | Yes           | No              | Amber pulse + description + progress bar                   |
| Yes           | Yes              | Yes           | Yes (completed) | Green dot + row count                                      |
| Yes           | Yes              | Yes           | Yes (failed)    | Red dot + error + **"Retry Silver" button**                |

## Event-driven polling mechanism

**No polling when no silver record exists.** Zero background queries.

### Trigger matrix

| State                                          | `hx-trigger`                        |
| ---------------------------------------------- | ----------------------------------- |
| Silver is None (initial render only)           | `load, check-silver-{id} from:body` |
| Silver exists, not terminal                    | `every 2s`                          |
| Silver exists, terminal                        | (none — omitted)                    |
| Dispatch guard returned (bronze not completed) | `check-silver-{id} from:body`       |

### Flow: page load, no silver record

1. Silver partial renders with `hx-trigger="load, check-silver-{id} from:body"`
2. `load` fires once (one-shot initialization) → `hx-get` to `silver_status_poll`
3. **If silver record exists** (auto-dispatch already completed): response has `hx-trigger="every 2s"` → normal 2s polling begins
4. **If no silver record exists** (auto-dispatch hasn't run yet, or failed): response has `hx-trigger="check-silver-{id} from:body"` only (no `load`) → element sits idle, waiting for event

### Flow: bronze completes (auto-dispatch fires `check-silver`)

1. `bronze_status_poll` view detects `bronze.status == PipelineStatus.COMPLETED`
2. Adds `HX-Trigger: check-silver-{upload_id}` header to response
3. Event fires on bronze-status div, bubbles up to `body`
4. Silver partial (listening with `from:body`) receives event → `hx-get` to `silver_status_poll`
5. Silver record now exists → response returns `hx-trigger="every 2s"` → polling begins

### Flow: manual "Process Silver" button

1. User clicks button → `hx-post` to `dispatch_silver` view
2. View creates SilverIngestion (PROCESSING), calls `dispatch_silver_ingestion`
3. Response renders silver partial: processing state + `hx-trigger="every 2s"`
4. HTMX swaps the partial → polling begins automatically

### Flow: "Retry Silver" on failed

1. Same endpoint as "Process Silver" — creates a NEW SilverIngestion record
2. Old failed record stays for audit trail
3. Same response flow as manual dispatch

### Race condition: event fires before Celery writes silver record

This is a ~100ms window between event fire and DB write.

- Silver view responds to `load` or `check-silver` event
- If no silver record found: response has `hx-trigger="check-silver-{id} from:body"` only
- No more events fire (bronze is terminal, stopped polling)
- User sees "Pending" — clicks the **"Process Silver" button** as fallback

No retry loop. The button is the safety valve for the race window.

## Bronze view change

Add to `apps/bronze/views.py:bronze_status_poll`:

```python
response = render(request, "bronze/partials/bronze_status.html", context)

if bronze is not None and bronze.status == PipelineStatus.COMPLETED:
    response["HX-Trigger"] = f"check-silver-{upload_id}"

return response
```

This fires the event every time the bronze view returns COMPLETED.
Since COMPLETED is terminal (bronze stops polling), the event fires exactly once.

## New files

### 1. `apps/silver/views.py`

Two views:

#### `silver_status_poll(request, upload_id)`

- Method: GET
- Looks up BronzeIngestion for `upload_id` (`.order_by("-started_at").first()`)
- If bronze found, looks up SilverIngestion for it
- `is_terminal`: silver exists AND status in (COMPLETED, FAILED)
- Context: `{"silver": silver, "bronze": bronze, "upload_id": upload_id, "is_terminal": is_terminal}`
- Template: `silver/partials/silver_status.html`

#### `dispatch_silver(request, upload_id)`

- Method: POST only (`@require_POST`)
- Looks up BronzeIngestion for `upload_id`
- **Guard 1 — bronze not found**: return 404 with HTMX-compliant error partial
- **Guard 2 — bronze not completed**: return silver partial with info banner:
  ```html
  <div
    class="mt-3 border-t border-amber-800 pt-3 text-xs font-mono text-amber-300"
  >
    Bronze is currently PROCESSING. Silver will process automatically once
    bronze completes.
  </div>
  ```
  plus `hx-trigger="check-silver-{id} from:body"` — re-checks when bronze completes
- **Guard 3 — silver already PROCESSING or COMPLETED**: return current status partial (no-op)
- Otherwise: call `dispatch_silver_ingestion(bronze)` from `.tasks`
- Return silver partial (processing) with `hx-trigger="every 2s"`

### 2. `apps/silver/urls.py`

```python
from django.urls import path
from . import views

app_name = "silver"

urlpatterns = [
    path("hx/silver/status/<int:upload_id>/", views.silver_status_poll, name="silver_status"),
    path("hx/silver/dispatch/<int:upload_id>/", views.dispatch_silver, name="dispatch_silver"),
]
```

### 3. `apps/silver/templates/silver/partials/silver_status.html`

Single partial covering all states. No JavaScript.

#### Template structure

```html
<div
  id="silver-status-{{ upload_id }}"
  {%
  if
  not
  is_terminal
  %}
  {%
  if
  silver
  is
  not
  None
  %}
  hx-get="{% url 'silver:silver_status' upload_id=upload_id %}"
  hx-trigger="every 2s"
  hx-swap="outerHTML"
  {%
  elif
  bronze
  is
  not
  None
  and
  bronze.status=""
  ="completed"
  %}
  hx-get="{% url 'silver:silver_status' upload_id=upload_id %}"
  hx-trigger="load, check-silver-{{ upload_id }} from:body"
  hx-swap="outerHTML"
  {%
  endif
  %}
  {%
  endif
  %}
>
  <div class="mt-4 border-t border-zinc-800 pt-4">
    {% if silver is None %} {% if bronze is not None and bronze.status ==
    "completed" %} {# ====== No silver yet, bronze ready ====== #}
    <div class="flex items-center gap-3 mb-3">
      <div class="h-2 w-2 rounded-full bg-zinc-600"></div>
      <span class="text-xs uppercase tracking-widest text-zinc-500"
        >Silver: Pending</span
      >
    </div>
    <button
      class="..."
      hx-post="{% url 'silver:dispatch_silver' upload_id=upload_id %}"
      hx-target="#silver-status-{{ upload_id }}"
      hx-swap="outerHTML"
    >
      Process Silver
    </button>
    {% endif %} {# else: render nothing inside the div (silver cannot proceed)
    #} {% elif silver.status == "completed" %} {# ====== Completed ====== #}
    <div class="flex items-center gap-3 mb-4">
      <div class="h-2 w-2 rounded-full bg-green-400"></div>
      <span class="text-xs uppercase tracking-widest text-green-400"
        >Silver: Completed</span
      >
    </div>
    {% if silver.inserted_row_count is not None %}
    <div class="text-xs font-mono text-zinc-500">
      {{ silver.inserted_row_count }} rows ingested
    </div>
    {% endif %} {% elif silver.status == "failed" %} {# ====== Failed ====== #}
    <div class="flex items-center gap-3 mb-4">
      <div class="h-2 w-2 rounded-full bg-red-400"></div>
      <span class="text-xs uppercase tracking-widest text-red-400"
        >Silver: Failed</span
      >
    </div>
    {% if silver.error_message %}
    <div
      class="mt-3 border-t border-zinc-800 pt-3 text-xs font-mono text-zinc-600"
    >
      <span class="text-zinc-500">{{ silver.error_type }}</span>: {{
      silver.error_message }}
    </div>
    {% endif %}
    <button
      class="..."
      hx-post="{% url 'silver:dispatch_silver' upload_id=upload_id %}"
      hx-target="#silver-status-{{ upload_id }}"
      hx-swap="outerHTML"
    >
      Retry Silver
    </button>

    {% else %} {# ====== Processing / Pending / Retrying ====== #}
    <div class="flex items-center gap-3 mb-3">
      <div class="h-2 w-2 rounded-full bg-amber-400 animate-pulse"></div>
      <span class="text-xs uppercase tracking-widest text-zinc-400"
        >Silver processing</span
      >
    </div>
    <div class="mb-2 animate-fade-slide-up">
      <span class="text-xs text-zinc-500"
        >{{ silver.parse_progress_description }}</span
      >
    </div>
    <div class="mb-1 flex items-center justify-between">
      <span class="text-xs font-mono text-zinc-600">
        {{ silver.parse_progress_current }}/{{ silver.parse_progress_total }}
      </span>
    </div>
    <c-ui.progress value="{{ silver.parse_progress_percent }}" color="purple" />
    {% endif %} {% if dispatch_message %} {# ====== Non-blocking informational
    banner (dispatch guard) ====== #}
    <div
      class="mt-3 border-t border-amber-800 pt-3 text-xs font-mono text-amber-300"
    >
      {{ dispatch_message }}
    </div>
    {% endif %}
  </div>
</div>
```

#### Conditional hx-trigger logic explained

Three mutually exclusive branches:

1. **Silver exists, not terminal** → `every 2s` (standard poll)
2. **Silver is None, bronze completed** → `load, check-silver-{id} from:body` (event-driven)
3. **All other cases** → no hx-trigger at all (fully static, no requests)

The `is_terminal` flag (from view) controls the outer `{% if not is_terminal %}` —
when silver is terminal, no trigger is emitted regardless of which branch we're in.

#### dispatch_message context variable

Added to context in `dispatch_silver` view when guard 2 fires. Passed to template
and rendered as an amber info banner. Not present in normal `silver_status_poll`
responses.

## Files to modify

### 4. `templates/landing/partials/upload_status.html`

After line 31 (the bronze include), add:

```django
{% include "silver/partials/silver_status.html" with upload_id=upload.id %}
```

This lives inside the `{% elif upload.status == "completed" %}` block, so silver
status only appears after the landing upload is complete.

### 5. `config/urls.py`

Add silver URLs alongside bronze:

```python
path('', include('apps.bronze.urls')),   # existing
path('', include('apps.silver.urls')),   # new (after bronze)
```

### 6. `apps/bronze/views.py`

Add `check-silver-{upload_id}` HX-Trigger when bronze completes:

```python
response = render(request, "bronze/partials/bronze_status.html", {
    "bronze": bronze,
    "upload_id": upload_id,
    "is_terminal": is_terminal,
})

if bronze is not None and bronze.status == PipelineStatus.COMPLETED:
    response["HX-Trigger"] = f"check-silver-{upload_id}"

return response
```

## Edge cases

| Scenario                                               | Handling                                                                                                 |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| No BronzeIngestion exists                              | silver_status_poll returns silver=None, bronze=None → template renders empty placeholder div, no trigger |
| BronzeIngestion exists but not completed               | silver=None, bronze.status != "completed" → empty placeholder div, no trigger                            |
| Bronze completed, no SilverIngestion                   | silver=None, bronze.status == "completed" → Pending + button + event trigger                             |
| Silver processing                                      | Non-terminal, `every 2s` poll, progress bar                                                              |
| Silver completed                                       | Green dot + row count, terminal, no poll                                                                 |
| Silver failed                                          | Red dot + error + Retry button, terminal, no poll                                                        |
| Click "Process Silver" while bronze processing         | View returns amber banner + event trigger (re-check when bronze completes)                               |
| Click "Process Silver" while silver already processing | Idempotency guard — returns current partial, no-op                                                       |
| Multiple SilverIngestion records                       | Poll view `.order_by("-started_at").first()` → shows latest                                              |
| Silver auto-dispatched normally                        | Event from bronze → silver view finds record → starts 2s poll                                            |
| Race: auto-dispatch Celery write after event           | No "load" in response → button fallback                                                                  |

## Design decisions

1. **Manual dispatch creates a new SilverIngestion** (not a retry of the old one).
   Clean audit trail with per-attempt records. Idempotency guard in
   `transform_to_silver` marks duplicates as FAILED.

2. **No polling when no silver exists**. Event-driven via `HX-Trigger` from
   bronze view. The `load` trigger covers the one-shot initialization check;
   after that, the element sits idle awaiting `check-silver` events.

3. **"Process Silver" button is the fallback**. For auto-dispatch failures,
   race conditions, and retry-after-failure scenarios. Identical to the
   dispatch endpoint used during retry.

4. **Amber info banner style for dispatch messages**. Not the muted
   `text-zinc-600` used for errors — visually distinct, informational tone:
   `border-amber-800` + `text-amber-300`.

5. **Dispatches are synchronous in the view**. The view calls
   `dispatch_silver_ingestion()` which creates a DB record and fires
   `transform_to_silver.delay()`. The Celery task runs asynchronously.

6. **No changes to automatic dispatch in bronze/tasks.py**. The
   auto-dispatch at `bronze/tasks.py:215-216` continues to run. The manual
   button is a fallback.

## Non-goals

- No silver node in datasets pipeline graph
- No changes to bronze or landing auto-dispatch
- No changes to SilverIngestion model or tasks
- No SSE or WebSocket for real-time updates
- No changes to datasets listing or detail pages
