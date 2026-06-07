# SSE Real-Time Fix — Implementation Plan

Fixes the bug where the frontend stays stuck in "processing" mode when a Celery task fails.

## Root Cause

- `hx-swap="none"` on `#detail-sse` and `#listing-sse` prevents the htmx SSE extension from swapping JSON event data as DOM. The data is silently dropped.
- Pipeline graph nodes render once on page load and never update — there is NO polling mechanism for the graph at all.
- Listing row status dots are static after initial render — no polling or event-driven update path.
- Bronze/silver status partials (`bronze_status.html`, `silver_status.html`) DO poll every 2s and correctly reflect failures — the existing polling infrastructure works for these. The gap is everything else.
- `sse-swap="dataset-event"` on the SSE containers currently registers an EventSource listener but the swap does nothing (`hx-swap="none"`). The `htmx:sseMessage` event IS dispatched after the no-op swap — this is the hook we use for the JS-based routing handler.

## Architecture

```
Celery task completes/fails
    │
    ├── writes to DB (status, error fields)
    ├── creates DatasetEvent row
    │
    ▼
SSE async _event_generator polls DB, yields JSON event
    │
    ▼
htmx SSE extension (v2.x, EventSource-based) receives "dataset-event"
    │
    ├── attempts swap with hx-swap="none" (does nothing)
    ├── dispatches "htmx:sseMessage" on #detail-sse / #listing-sse
    │   (event bubbles to document.body)
    │
    ▼
app.js handler parses e.detail.data (JSON), extracts upload_id
    │
    ├── only triggers elements that belong to THIS upload_id
    │   (no broadcast to all 20 rows)
    │
    ▼
htmx.trigger(target, "sse-update") on specific DOM element(s)
    │
    ▼
Element with hx-trigger="sse-update from:body" fires GET
    │
    ▼
Server returns fresh partial HTML → swap updates the DOM
```

**Key design decisions** (resolved during review, reasoning below each):

| #   | Decision                                                      | Choice                                                                  | Why                                                                                                                                                                                                                                                                                                                                   |
| --- | ------------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | SSE generator under ASGI                                      | Convert to async generator with `sync_to_async` for ORM                 | Django's `StreamingHttpResponse` calls `__aiter__()` under ASGI. Sync generators with ORM queries raise `SynchronousOnlyOperation`. Async generator with `sync_to_async` is the documented Django 4.2+ pattern (ticket #33735).                                                                                                       |
| D2  | SSE container attributes                                      | Keep `sse-swap="dataset-event"` + `hx-swap="none"` as-is                | `sse-swap` is what registers the EventSource listener for the `dataset-event` event. Without it, no listener exists and `htmx:sseMessage` never fires — the JS handler never processes events. `hx-swap="none"` prevents the JSON data from corrupting the DOM.                                                                       |
| D3  | Bronze/silver trigger conditionality                          | Conditional on `is_terminal` — same pattern as current polling          | When `is_terminal=True`, no further state transitions occur, so no HTMX attributes are needed. `sse-update from:body` is added alongside `every 2s` only when `not is_terminal`. The JS handler's `getAttribute("hx-get")` guard prevents no-op triggers on terminal elements.                                                        |
| D4  | Bronze/silver: always-include `hx-get`?                       | Rejected. Keep current conditional structure.                           | Including `hx-get` on terminal elements would cause unnecessary GET requests on late events. The guard check in JS is cleaner.                                                                                                                                                                                                        |
| D5  | Listing row update endpoint                                   | Existing `listing_row_partial` view (already annotated with subqueries) | No new view needed. The endpoint exists at `hx/datasets/row/<int:upload_id>/` and provides `upload` (with status annotations) and `save` context.                                                                                                                                                                                     |
| D6  | Listing row SSE trigger                                       | `hx-trigger="sse-update from:body"` on `<a>` tag                        | Won't break click navigation — HTMX only intercepts default events. `hx-trigger` explicitly specifies `sse-update from:body`, so no click handler is attached. The Process button's POST behavior is unaffected.                                                                                                                      |
| D7  | Pipeline graph: dedicated view or `hx-select`?                | Dedicated view                                                          | `hx-select="#pipeline-graph"` on the full `dataset_detail` endpoint would render the ENTIRE detail page on every 4s poll (statistics panel, retry buttons, bronze/silver status includes) just to extract the graph. A dedicated view fetches only graph data and renders only the graph partial — substantially lighter per-request. |
| D8  | Pipeline graph: `outerHTML` or `innerHTML` swap?              | `innerHTML` — wrapper stays in `detail.html` only                       | With `outerHTML`, the response must include the wrapper div with HTMX attributes, duplicating it between `detail.html` and the partial response. `innerHTML` keeps the wrapper in `detail.html` forever — no duplication, no risk of attributes drifting out of sync. The partial template returns only graph nodes.                  |
| D9  | SSE event → `hx-trigger="sse:dataset-event"` on all elements? | Rejected in favor of JS routing handler                                 | `hx-trigger="sse:dataset-event"` on every row would cause 20 simultaneous GET requests on every SSE event (one per row). The JS handler routes by `upload_id` — only the affected row(s) fire a request.                                                                                                                              |
| D10 | Listing page section movement                                 | Not addressed (pre-existing limitation)                                 | When an upload transitions between status groups (Active/Failed → Fully Processed), the row updates in-place via `outerHTML` but stays in its original section. This is a pre-existing limitation shared with the Process button. Fixing it requires either a periodic full-page poll or JS DOM movement logic — out of scope.        |

## Prerequisites confirmed

- `listing_row_partial` view exists at `apps/datasets/views.py:329` with URL `dataset_listing_row`
- `dispatch_bronze` view exists at `apps/bronze/views.py:37` with URL `bronze:dispatch_bronze`
- `config/asgi.py` already has `get_asgi_application()` — no changes needed
- htmx SSE extension is v2.x (`static/js/htmx-sse.js`, EventSource-based) — dispatches `htmx:sseMessage` on the SSE container after each event

## Step 1: Switch to ASGI

Django's `StreamingHttpResponse` with synchronous generators blocks a worker thread per SSE connection. Under ASGI, the generator releases the event loop between yields.

More critically: under ASGI, `StreamingHttpResponse` calls `__aiter__()` not `__iter__()`. If a sync generator with ORM queries is passed, Django raises `SynchronousOnlyOperation` (ticket #32797). The fix is to provide an async generator that wraps ORM calls in `sync_to_async`.

Reference: https://django.readthedocs.io/en/stable/topics/async.html

### Install daphne

```bash
uv add daphne
```

**Why daphne over uvicorn.** Daphne hooks directly into `manage.py runserver` with zero config changes — just add to `INSTALLED_APPS`. Uvicorn requires `uvicorn config.asgi:application` and doesn't auto-detect Django apps. For this migration step, daphne is the simplest path. Production can switch to uvicorn or gunicorn+uvicorn later.

### `config/settings.py`

Insert `"daphne"` at the top of `INSTALLED_APPS` — it must come before `django.contrib.staticfiles` to override `runserver`:

```python
INSTALLED_APPS = [
    "daphne",
    "django.contrib.staticfiles",
    # ... rest unchanged
]

ASGI_APPLICATION = "config.asgi.application"
```

**No changes to `config/asgi.py`.** Django's `startproject` already created it with `get_asgi_application()`. The `ASGI_APPLICATION` setting is what tells Django to use it.

## Step 2: Convert SSE generator to async

`_event_generator` currently polls the DB inside a sync generator with `time.sleep(0.5)`. Under ASGI, this must become an async generator with `sync_to_async` for ORM queries and `asyncio.sleep()` for the poll interval.

### `apps/datasets/views.py` — replace `_event_generator`

```python
import asyncio
import json
from asgiref.sync import sync_to_async

async def _event_generator(upload_id=None, save_slug=None):
    """Yield SSE-formatted DatasetEvent rows.
    Async generator — required under ASGI to avoid SynchronousOnlyOperation.
    """
    last_id = 0

    filters = {}
    if upload_id is not None:
        filters["upload_id"] = upload_id
    if save_slug is not None:
        filters["upload__save_master__slug"] = save_slug

    fetch_events = sync_to_async(
        lambda: list(
            DatasetEvent.objects.filter(**filters, id__gt=last_id)
            .select_related("upload")
            .order_by("id")
        )
    )

    while True:
        events = await fetch_events()

        for event in events:
            data = {
                "id": event.id,
                "upload_id": event.upload_id,
                "type": event.event_type,
                "payload": event.payload,
            }
            yield f"event: dataset-event\ndata: {json.dumps(data)}\n\n"
            last_id = event.id

        if not events:
            await asyncio.sleep(0.5)
```

**Why `sync_to_async(lambda: list(queryset))()`.** The queryset must be eagerly evaluated inside `sync_to_async` because the Django ORM is synchronous. `list()` forces evaluation within the sync thread. Using `sync_to_async` with `thread_sensitive=True` (the default) ensures threadlocals and DB connection handling work correctly.

### Update `event_stream` and `listing_event_stream` views

Both must become async views and pass the async generator to `StreamingHttpResponse`:

```python
async def event_stream(request, upload_id):
    response = StreamingHttpResponse(
        _event_generator(upload_id=upload_id),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache, no-store"
    response["X-Accel-Buffering"] = "no"
    return response


async def listing_event_stream(request, save_slug):
    response = StreamingHttpResponse(
        _event_generator(save_slug=save_slug),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache, no-store"
    response["X-Accel-Buffering"] = "no"
    return response
```

**Why both views must become async.** `StreamingHttpResponse` with an async iterator requires the view to be async. Django's ASGI handler calls `__aiter__()` on the response — if the iterator is async but the view is sync, Django wraps it in `sync_to_async`, which loses the non-blocking benefit.

## Step 3: Frontend SSE event processor

### `static/js/app.js` — add SSE event handler

The htmx SSE extension dispatches `htmx:sseMessage` custom event on the element with `sse-swap` when an SSE event arrives. This event bubbles to `document.body`. The handler parses the JSON event data and calls `htmx.trigger()` only on the affected upload's DOM elements — avoiding a broadcast to all rows:

```javascript
document.body.addEventListener("htmx:sseMessage", function (e) {
  try {
    var data = JSON.parse(e.detail.data);
    var uid = data.upload_id;

    // Listing page: update the affected row
    var row = document.getElementById("upload-row-" + uid);
    if (row && row.getAttribute("hx-get")) htmx.trigger(row, "sse-update");

    // Detail page: update pipeline graph (not upload-specific, harmless)
    var graph = document.getElementById("pipeline-graph");
    if (graph) htmx.trigger(graph, "sse-update");

    // Detail page: update bronze status (only if element has hx-get)
    var bronze = document.getElementById("bronze-status-" + uid);
    if (bronze && bronze.getAttribute("hx-get"))
      htmx.trigger(bronze, "sse-update");

    // Detail page: update silver status (only if element has hx-get)
    var silver = document.getElementById("silver-status-" + uid);
    if (silver && silver.getAttribute("hx-get"))
      htmx.trigger(silver, "sse-update");
  } catch (err) {
    // silently ignore malformed events (non-JSON data, missing fields)
  }
});
```

**Why `getAttribute("hx-get")` guard.** Elements in terminal state (completed/failed) have NO HTMX attributes — polling was removed. Without this guard, `htmx.trigger()` would dispatch an event on an element with no listener. Harmless but wasteful — the guard makes it explicit.

**Why not `hx-trigger="sse:dataset-event"`.** The htmx SSE extension supports `hx-trigger="sse:dataset-event"` which fires on every SSE event of that name. But this would fire for ALL rows on every event — 20 rows = 20 simultaneous requests. The JS handler routes events to only the affected upload's DOM elements.

## Step 4: Bronze/silver status partials — SSE triggers alongside polling

The bronze/silver partials keep their existing conditional structure: HTMX attributes (`hx-get`, `hx-trigger`, `hx-swap`) are only present when `not is_terminal`. The only change is adding `sse-update from:body` beside `every 2s`.

**Why not unconditional HTMX attributes.** If `hx-get` were always present, terminal elements would fire unnecessary GET requests on late-arriving SSE events. The JS handler's `getAttribute("hx-get")` guard already prevents this, but keeping the current conditional pattern is cleaner — no need to rely on the guard.

### `apps/bronze/templates/bronze/partials/bronze_status.html`

Only change: add `sse-update from:body` to the trigger:

```html
<div
  id="bronze-status-{{ upload_id }}"
  {%
  if
  not
  is_terminal
  %}
  hx-get="{% url 'bronze:bronze_status' upload_id=upload_id %}"
  hx-trigger="every 2s, sse-update from:body"
  hx-swap="outerHTML"
  {%
  endif
  %}
></div>
```

### `apps/silver/templates/silver/partials/silver_status.html`

Same pattern — add `sse-update from:body` alongside the existing triggers:

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
  hx-trigger="every 2s, sse-update from:body"
  hx-swap="outerHTML"
  {%
  elif
  bronze
  is
  not
  None
  %}
  hx-get="{% url 'silver:silver_status' upload_id=upload_id %}"
  hx-trigger="{% if not from_view %}load, {% endif %}check-silver-{{ upload_id }} from:body, sse-update from:body"
  hx-swap="outerHTML"
  {%
  endif
  %}
  {%
  endif
  %}
></div>
```

The `check-silver-{id}` trigger (set via `HX-Trigger` response header in `bronze_status_poll`) fires when bronze completes, triggering an immediate silver status check. This is preserved alongside `sse-update from:body`.

## Step 5: Make listing rows SSE-updatable

The `listing_row_partial` endpoint already exists at `apps/datasets/views.py:329` with URL `dataset_listing_row`. It returns a fully rendered `listing_row.html` with annotated status fields via subqueries. The only missing piece is HTMX attributes on the `<a>` tag that subscribe to SSE-driven updates.

### `templates/datasets/partials/listing_row.html`

Add `hx-get`, `hx-trigger`, and `hx-swap` to the `<a>` tag:

```html
<a
  href="{% url 'dataset_detail' save_slug=save.slug upload_id=upload.id %}"
  id="upload-row-{{ upload.id }}"
  class="flex items-center gap-6 px-6 py-4 transition-colors hover:bg-zinc-900/50 group"
  hx-get="{% url 'dataset_listing_row' upload_id=upload.id %}"
  hx-trigger="sse-update from:body"
  hx-swap="outerHTML"
></a>
```

**Why this doesn't break click navigation.** HTMX only intercepts the default event for each element type (click for `<a>`, submit for `<form>`). Since `hx-trigger` explicitly specifies `sse-update from:body`, HTMX does not attach a click handler. The `<a>` continues to navigate normally on click.

**Why not add `hx-trigger="every 30s"`.** SSE provides instant updates. Adding periodic polling would add unnecessary DB load for no benefit. Each row update triggers a single annotated subquery (already optimized).

## Step 6: Pipeline graph partial — dedicated view + innerHTML swap

### `templates/datasets/partials/pipeline_graph.html` (NEW)

Extract the graph rendering from `detail.html` lines 96-167 into this partial. Same context variables:

- `upload` — for `upload.status` (Source node)
- `parse_attempts` — latest attempt's status + duration (Parse node)
- `store_attempts` — latest attempt's status + duration (Store node)
- `bronze_ingestion` — for `bronze_ingestion.status` (Bronze node)
- `bronze_attempts` — latest attempt's duration
- `silver_ingestion` — for `silver_ingestion.status` (Silver node)
- `silver_attempts` — latest attempt's duration

The partial contains only the graph nodes and connectors — NO wrapper div. The wrapper lives in `detail.html`.

### `apps/datasets/views.py` — `pipeline_graph_partial` view

```python
from apps.bronze.models import BronzeIngestion
from apps.silver.models import SilverIngestion


def pipeline_graph_partial(request, save_slug, upload_id):
    upload = get_object_or_404(
        LandingUpload.objects.prefetch_related(
            "task_rows",
            "bronze_ingestions__task_rows",
            "bronze_ingestions__silver_ingestions__task_rows",
        ),
        id=upload_id,
        save_master__slug=save_slug,
    )

    bronze_ingestion = upload.bronze_ingestions.first()

    silver_ingestion = None
    silver_attempts = []
    if bronze_ingestion is not None:
        silver_ingestion = (
            bronze_ingestion.silver_ingestions
            .order_by("-started_at")
            .first()
        )
        if silver_ingestion is not None:
            silver_attempts = sorted(
                list(silver_ingestion.task_rows.all()),
                key=lambda t: t.attempt,
            )

    parse_attempts = sorted(
        [t for t in upload.task_rows.all() if t.step == "parse"],
        key=lambda t: t.attempt,
    )
    store_attempts = sorted(
        [t for t in upload.task_rows.all() if t.step == "store"],
        key=lambda t: t.attempt,
    )
    bronze_attempts = (
        sorted(
            list(bronze_ingestion.task_rows.all()),
            key=lambda t: t.attempt,
        )
        if bronze_ingestion
        else []
    )

    return render(request, "datasets/partials/pipeline_graph.html", {
        "upload": upload,
        "bronze_ingestion": bronze_ingestion,
        "silver_ingestion": silver_ingestion,
        "parse_attempts": parse_attempts,
        "store_attempts": store_attempts,
        "bronze_attempts": bronze_attempts,
        "silver_attempts": silver_attempts,
    })
```

**Why a dedicated view instead of `hx-select` on `dataset_detail`.** `hx-select="#pipeline-graph"` would render the ENTIRE detail page on every 4s poll — statistics panel, retry buttons, bronze/silver status includes, all their DB queries — just to extract the graph fragment. A dedicated view fetches only graph data (one query with `prefetch_related`) and renders only the graph template. Substantially lighter per-request.

**Duplication with `dataset_detail`.** The data-fetching logic mirrors ~20 lines from `dataset_detail`. This is intentional — the alternative (reusing the full page) is an order of magnitude more expensive. If the data requirements diverge in the future, the duplication is beneficial (they can evolve independently).

### `apps/datasets/urls.py`

```python
path(
    "hx/datasets/graph/<slug:save_slug>/<int:upload_id>/",
    views.pipeline_graph_partial,
    name="pipeline_graph_partial",
),
```

### `templates/pages/datasets/detail.html` — wire polling

Replace the static graph section with a wrapper div that has `hx-get` and `innerHTML` swap:

```html
<div
  id="pipeline-graph"
  hx-get="{% url 'pipeline_graph_partial' save_slug=save.slug upload_id=upload.id %}"
  hx-trigger="sse-update from:body, every 4s"
  hx-swap="innerHTML"
>
  {% include "datasets/partials/pipeline_graph.html" %}
</div>
```

**Why `innerHTML` instead of `outerHTML`.** The wrapper div stays in `detail.html` permanently — never replaced. Only the inner content (the graph nodes) is replaced on each poll. This avoids duplicating the HTMX attributes in the partial response. No risk of the wrapper attributes drifting out of sync between `detail.html` and the partial view.

**Why `every 4s` polling as fallback.** SSE connections can drop (network blip, server restart, tab backgrounded). The 4s poll ensures the graph eventually reflects reality even without SSE. During the initial page load before the first SSE event arrives, the graph shows the server-rendered initial state from the `{% include %}`.

## Edge Cases

### SSE event arrives after element is removed from DOM

If the user navigates away from the detail page while an SSE event fires, `getElementById` returns null and the handler silently skips it. No error, no console noise.

### Multiple rapid SSE events

HTMX queues requests per element. If three `sse-update` events fire on `#pipeline-graph` within 100ms, the first GET fires immediately, the second and third are queued. When the first response arrives and replaces the innerHTML, the queued events fire against the same wrapper (unchanged). No race condition.

### Listing row update while Process button swap is in-flight

The Process button POSTs to `silver:dispatch_silver` and swaps the row with the response (via `hx-target="#upload-row-{id}"`). If an SSE event also targets the same row during this, the SSE update queue waits for the Process POST to complete (HTMX serializes requests per element when `hx-swap="outerHTML"` replaces the element).

### Bronze/silver partial with `from_view` parameter

The silver partial has `from_view` context variable that controls whether the initial `load` trigger fires. When `from_view=True` (set by `silver_status_poll` and `dispatch_silver` views), the `load` trigger is suppressed because the response replaces an already-polling partial. When `from_view=False` (set by the detail page's initial include), `load` fires immediately. Adding `sse-update from:body` doesn't interfere with this logic.

### SSE connection on landing upload page

The landing upload page (`upload_status.html`) includes `bronze_status.html` and `silver_status.html` partials but does NOT have an SSE connection. The `sse-update from:body` trigger on these partials will never fire because no `htmx:sseMessage` event is dispatched. The partials continue to poll every 2s as before — no regression.

### Listing page section movement (known limitation — NOT addressed)

When an upload transitions between status groups on the listing page (e.g., "Active/Failed" → "Fully Processed"), the row updates in-place via `outerHTML` but stays in its original section. This is a pre-existing limitation shared with the existing Process button behavior. Full section reassignment requires either a periodic full-page poll (re-render all sections) or JS DOM movement logic — intentionally out of scope for this SSE fix.

## Files Changed

| File                                                       | Change                                                                                                                                                                   |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `pyproject.toml`                                           | Add `daphne` dependency                                                                                                                                                  |
| `config/settings.py`                                       | Add `"daphne"` to `INSTALLED_APPS` (first), add `ASGI_APPLICATION`                                                                                                       |
| `apps/datasets/views.py`                                   | Convert `_event_generator` to async generator with `sync_to_async`. Convert `event_stream` and `listing_event_stream` to async views. Add `pipeline_graph_partial` view. |
| `apps/datasets/urls.py`                                    | Add `pipeline_graph_partial` URL                                                                                                                                         |
| `static/js/app.js`                                         | Add `htmx:sseMessage` event handler with upload_id routing                                                                                                               |
| `apps/bronze/templates/bronze/partials/bronze_status.html` | Add `sse-update from:body` to `hx-trigger` (still conditional on `not is_terminal`)                                                                                      |
| `apps/silver/templates/silver/partials/silver_status.html` | Add `sse-update from:body` to `hx-trigger` (still conditional on `not is_terminal`)                                                                                      |
| `templates/datasets/partials/listing_row.html`             | Add `hx-get`/`hx-trigger="sse-update from:body"`/`hx-swap="outerHTML"`                                                                                                   |
| `templates/datasets/partials/pipeline_graph.html`          | **NEW** — extracted graph nodes from `detail.html` (no wrapper div)                                                                                                      |
| `templates/pages/datasets/detail.html`                     | Replace static graph nodes with `#pipeline-graph` wrapper with `hx-get`/`hx-trigger`/`hx-swap="innerHTML"`                                                               |

## Files NOT Changed

| File                                                 | Why                                                                        |
| ---------------------------------------------------- | -------------------------------------------------------------------------- |
| `config/asgi.py`                                     | Already has `get_asgi_application()`                                       |
| `config/wsgi.py`                                     | Keep for backward compat                                                   |
| `apps/landing/tasks.py`                              | No task logic changes                                                      |
| `apps/bronze/tasks.py`                               | No task logic changes                                                      |
| `apps/silver/tasks.py`                               | No task logic changes                                                      |
| `apps/datasets/views.py` (existing views)            | `dataset_list`, `dataset_detail` logic unchanged                           |
| `templates/pages/datasets/list.html`                 | Row updates are self-contained via `listing_row.html`; no section movement |
| `templates/pages/datasets/detail.html` SSE container | `sse-swap="dataset-event"` + `hx-swap="none"` kept as-is                   |

## Verification

1. **Start pipeline processing** — upload a file, navigate to detail page
2. **Force a task failure** — e.g., upload a malformed CSV
3. **Observe frontend updates**:
   - Graph nodes transition from amber (processing) to red (failed) within 4s
   - Bronze/silver status partials show failed state (red dot, error message, retry button)
   - Listing page row shows red dots for the failed steps
4. **Confirm graph polling stops** — open DevTools Network tab, verify no more `/hx/datasets/graph/` requests after all nodes are terminal
5. **Confirm listing row updates without full page reload** — start a new upload, navigate to list, observe row appear/update without refreshing
6. **Confirm SSE connection stays open** — check `EventSource` is connected in DevTools, verify events are received on Celery task state changes
7. **Confirm existing bronze/silver polling still works** — block SSE (e.g., disconnect network temporarily), verify partials still update via 2s polling
8. **`uv run pytest -x`** — all existing tests pass, no regression
