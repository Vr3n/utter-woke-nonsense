# Logging & Progress — Implementation Plan

Drafted: 2026-05-23
Status: Reviewed, ready for implementation

---

## 1. Model — `apps/landing/models.py`

### Fields to add to `LandingUpload`

```python
entry_task_id             = models.CharField(max_length=255, blank=True, default="")
parse_progress_current     = models.PositiveSmallIntegerField(default=0)
parse_progress_total       = models.PositiveSmallIntegerField(default=3)
parse_progress_description = models.CharField(max_length=100, default="Pending")
```

### Property to add

```python
@property
def parse_progress_percent(self):
    if not self.parse_progress_total:
        return 0
    return int((self.parse_progress_current / self.parse_progress_total) * 100)
```

### Rationale

- `entry_task_id` stores the chain entry-point task ID for debugging (Flower, result backend). The name makes it clear this is the head of the chain, not a singular task reference — all per-attempt task IDs live in `LandingZoneTask.celery_task_id`.
- The three progress fields are the single source of truth the htmx poll reads. `total` defaults to 3 (matching the 0–3 scale) and never changes at runtime.
- `parse_progress_percent` keeps arithmetic out of the template — the Cotton progress component expects a plain integer 0–100.

---

## 2. Migration

Generate `0003` — all 4 columns have defaults, no data migration needed.

---

## 3. Admin — `apps/landing/admin.py`

Add `entry_task_id` and parse progress fields to `LandingUploadAdmin.readonly_fields` for visibility.

---

## 4. Celery config — `config/celery.py`

Wire `after_setup_task_logger` signal with `TaskFormatter`:

```python
from celery.signals import after_setup_task_logger
from celery.app.log import TaskFormatter

@after_setup_task_logger.connect
def setup_task_logger(logger, **kwargs):
    for handler in logger.handlers:
        handler.setFormatter(TaskFormatter(
            "%(asctime)s [%(levelname)s] %(task_name)s[%(task_id)s] "
            "upload=%(upload_id)s step=%(step)s — %(message)s"
        ))
```

This is worker-level infrastructure config, not application logic — belongs in `celery.py`.

---

## 5. Tasks — `apps/landing/tasks.py`

### 5a. Logging setup

```python
from celery.utils.log import get_task_logger
logger = get_task_logger(__name__)
```

### 5b. `parse_file` — ProgressRecorder + paired DB writes + logging

Add a `recorder = ProgressRecorder(self)` after loading `upload`. At each of 4 steps, **both** the DB fields and `recorder.set_progress()` are updated together:

```
Step 0/3:  description="Uploading"             current=0   # after LandingZoneTask RUNNING
Step 1/3:  description="Parsing the file"      current=1   # before strategy.parse()
Step 2/3:  description="Converting to Parquet" current=2   # before ctx.execute()
Step 3/3:  description="Done"                  current=3   # after success, before SUCCEEDED
```

Each DB write uses `upload.save(update_fields=[...])`. Each `recorder.set_progress()` passes the exact description string.

Logging at each point with `log_ctx = {"upload_id": upload_id, "step": "parse"}`:
- `logger.info("Starting parse", extra=log_ctx)`
- `logger.info("Strategy: %s", strategy.__class__.__name__, extra=log_ctx)`
- `logger.info("Staging written", extra=log_ctx)`
- `logger.warning("Retrying (attempt %d): %s", attempt, exc, extra=log_ctx)`
- `logger.error("Failed permanently: %s", exc, extra=log_ctx)`

Do NOT log inside `PipelineTask.record_failure` — log at the task call site.

### 5c. `store_parquet` — logging only

No ProgressRecorder (sub-second file move). Log with `log_ctx = {"upload_id": upload_id, "step": "store"}`:
- `logger.info("Starting store", extra=log_ctx)`
- `logger.info("Moved to final path", extra=log_ctx)`
- `logger.warning("Retrying (attempt %d): %s", attempt, exc, extra=log_ctx)`
- `logger.error("Failed permanently: %s", exc, extra=log_ctx)`

### 5d. `dispatch_pipeline` — set entry_task_id

Move the `.update()` call to **after** `chain().delay()` so the result ID is available:

```python
def dispatch_pipeline(upload_id: int):
    result = chain(parse_file.s(upload_id), store_parquet.s()).delay()
    LandingUpload.objects.filter(id=upload_id).update(
        status=LandingUpload.Status.PROCESSING,
        entry_task_id=result.id,
    )
```

---

## 6. Views — `apps/landing/views.py`

### 6a. New progress fragment view

Single template, no template switching — passes `is_terminal` boolean:

```python
def upload_progress_fragment(request, upload_id):
    upload = get_object_or_404(
        LandingUpload.objects.only(
            "parse_progress_current", "parse_progress_total",
            "parse_progress_description", "status",
        ),
        id=upload_id,
        save_master__user=request.user,
    )

    return render(request, "landing/partials/progress_fragment.html", {
        "upload": upload,
        "is_terminal": (
            upload.parse_progress_current >= upload.parse_progress_total
            or upload.status in (
                LandingUpload.Status.COMPLETED,
                LandingUpload.Status.FAILED,
            )
        ),
    })
```

No `HX-Trigger: done` header — terminal state detected by `{% if not is_terminal %}` in the template (omit-trigger pattern).

### 6b. `upload_status_poll` — add last_error context

```python
def upload_status_poll(request, save_slug, snapshot_type, upload_id):
    upload = get_object_or_404(
        LandingUpload.objects.prefetch_related("task_rows"),
        id=upload_id,
        save_master__slug=save_slug,
    )

    last_error = (
        upload.task_rows
        .filter(status=LandingZoneTask.Status.FAILED)
        .order_by("-attempt")
        .first()
    )

    return render(request, "landing/partials/upload_status.html", {
        "save": upload.save_master,
        "snapshot_type": snapshot_type,
        "upload": upload,
        "last_error": last_error,
    })
```

The `.filter().order_by().first()` chain hits the prefetch cache — zero additional DB queries.

---

## 7. URLs — `apps/landing/urls.py`

```python
path("hx/progress/<int:upload_id>/", views.upload_progress_fragment, name="upload_progress"),
```

The progress endpoint is a leaf — it only needs `upload_id`. Authorization is guarded by `save_master__user` in the view.

---

## 8. Templates

### 8a. `templates/landing/partials/progress_fragment.html` (new)

Single file handling all three states. `hx-trigger` omitted when terminal (stop polling naturally).

```html
<div id="upload-progress-{{ upload.id }}"
     {% if not is_terminal %}
     hx-get="{% url 'upload_progress' upload.id %}"
     hx-trigger="every 600ms"
     {% endif %}
     hx-swap="outerHTML">
  {% if upload.status == "completed" %}
    <div class="mb-2 flex items-center gap-2">
      <div class="h-2 w-2 rounded-full bg-green-400"></div>
      <span class="text-xs uppercase tracking-widest text-green-400">Done</span>
    </div>
    <c-ui.progress value="100" color="purple" />

  {% elif upload.status == "failed" %}
    <div class="mb-2 flex items-center gap-2">
      <div class="h-2 w-2 rounded-full bg-red-400"></div>
      <span class="text-xs uppercase tracking-widest text-red-400">
        {{ upload.parse_progress_description }}
      </span>
    </div>
    <c-ui.progress value="{{ upload.parse_progress_percent }}" color="purple" />

  {% else %}
    <div class="mb-2 flex items-center justify-between">
      <span class="text-xs uppercase tracking-widest text-zinc-500">
        {{ upload.parse_progress_description }}
      </span>
      <span class="text-xs font-mono text-zinc-600">
        {{ upload.parse_progress_current }}/{{ upload.parse_progress_total }}
      </span>
    </div>
    <c-ui.progress value="{{ upload.parse_progress_percent }}" color="purple" />
  {% endif %}
</div>
```

On failure, description shows the last known step (e.g. "Parsing the file"), not the error message. Error detail lives in the outer status template via `last_error`.

### 8b. `templates/landing/partials/upload_status.html` — rewrite

**pending / processing / retrying** — amber pulsing dot + progress fragment:

```html
{% if upload.status == "pending" or upload.status == "processing" or upload.status == "retrying" %}
<div id="upload-status-{{ upload.id }}"
     hx-trigger="every 2s"
     hx-get="{% url 'upload_status_poll' save_slug=save.slug snapshot_type=snapshot_type upload_id=upload.id %}"
     hx-target="this"
     hx-swap="outerHTML">
  <div class="border border-zinc-800 bg-zinc-950/80 p-6">
    <div class="flex items-center gap-3 mb-4">
      <div class="h-2 w-2 rounded-full bg-amber-400 animate-pulse"></div>
      <span class="text-sm uppercase tracking-widest text-zinc-400">
        {% if upload.status == "processing" %}Processing...
        {% elif upload.status == "retrying" %}Retrying...
        {% else %}Upload queued...{% endif %}
      </span>
    </div>
    {% include "landing/partials/progress_fragment.html" %}
  </div>
</div>

{% elif upload.status == "completed" %}
<div class="border border-zinc-800 bg-zinc-950/80 p-6">
  <div class="flex items-center gap-3 mb-4">
    <div class="h-2 w-2 rounded-full bg-green-400"></div>
    <span class="text-sm uppercase tracking-widest text-zinc-400">Upload completed</span>
  </div>
  {% include "landing/partials/progress_fragment.html" %}
</div>

{% elif upload.status == "failed" %}
<div class="border border-zinc-800 bg-zinc-950/80 p-6">
  <div class="flex items-center gap-3 mb-4">
    <div class="h-2 w-2 rounded-full bg-red-400"></div>
    <span class="text-sm uppercase tracking-widest text-red-400">Upload failed</span>
  </div>
  {% include "landing/partials/progress_fragment.html" %}
  {% if last_error %}
    <div class="mt-3 border-t border-zinc-800 pt-3 text-xs font-mono text-zinc-600">
      <span class="text-zinc-500">{{ last_error.error_type }}</span>: {{ last_error.error_message }}
    </div>
  {% endif %}
</div>
{% endif %}
```

The `hx-trigger="every 2s"` on the outer div continues poll during processing/retrying. When the outer partial swaps to the completed/failed branch, there is no `hx-trigger` — both terminal states show static content. The inner progress fragment (600ms) is nested inside the processing branch and is independently managed by htmx.

---

## 9. Tests — `apps/landing/tests.py`

### Update existing tests
- `test_parse_file_converts_csv_tostaging_parquet` — assert progress fields are set to 3/"Done"
- `test_parse_file_converts_html_tostaging_parquet` — same
- `test_parse_file_creates_landing_zone_task_on_start` — also assert initial progress (0/"Uploading")
- `test_dispatch_pipeline_completes_full_chain` — assert `entry_task_id` is not blank

### New tests

| Test | What it verifies |
|---|---|
| `test_progress_fields_update_at_each_step` | All 4 steps write correct current/description to DB |
| `test_progress_fragment_returns_polling_during_process` | GET while processing returns non-terminal template (has `hx-trigger`) |
| `test_progress_fragment_returns_done_when_complete` | GET on completed upload returns terminal (no `hx-trigger`) |
| `test_progress_fragment_returns_done_when_failed` | GET on failed upload returns terminal |
| `test_progress_fragment_guards_cross_user` | 404 for another user's upload |
| `test_status_poll_shows_last_error_on_failure` | `last_error` in context has correct type/message |
| `test_retrying_status_renders_in_template` | Template shows "Retrying..." text |

No need to mock `ProgressRecorder` — it writes to the in-memory result backend in eager mode.

---

## 10. AGENTS.md — sections to add/update

```markdown
## Logging

Use get_task_logger(__name__) from celery.utils.log in tasks.py.
TaskFormatter is configured via after_setup_task_logger signal in celery.py.
Every log call inside a task must include extra={"upload_id": ..., "step": ...}.
Do not log inside PipelineTask.record_failure — log at the task call site.

Log at these points — no more, no less:

| Point | Level | Message |
|---|---|---|
| Task starts | INFO | "Starting {step}" |
| Strategy selected | INFO | "Strategy: {cls}" |
| File written / moved | INFO | "Staging written" / "Moved to final path" |
| Retrying | WARNING | "Retrying (attempt {n}): {exc}" |
| Retries exhausted | ERROR | "Failed permanently: {exc}" |
| Non-retryable exception | ERROR | "Non-retryable: {exc_type} — investigate" |

## Progress descriptions

The exact strings for ProgressRecorder descriptions are:
  — "Uploading"
  — "Parsing the file"
  — "Converting to Parquet"
  — "Done"
Do not change these strings. The frontend renders them verbatim.

## HTMX Progress Polling — implementation pattern

The progress UI uses HTMX partial polling. No JavaScript. No celery-progress JS helper.
The DB fields (parse_progress_current, parse_progress_total, parse_progress_description)
are the single source of truth the poll view reads. ProgressRecorder writes to the
Celery result backend in parallel — but the htmx poll reads Django DB only.

### Single-template structure

landing/partials/progress_fragment.html — single file for all states.

The template conditionally includes `hx-trigger="every 600ms"` when `is_terminal` is False.
When terminal (completed/failed), no `hx-trigger` is emitted — polling stops naturally.
No client-side event handling, no HX-Trigger headers, no HTTP 286.

### What parse_file writes at each step

Every recorder.set_progress() call is paired with a DB write.
Both happen together — never one without the other.

Step 0/3: description="Uploading"          current=0
Step 1/3: description="Parsing the file"   current=1
Step 2/3: description="Converting to Parquet" current=2
Step 3/3: description="Done"               current=3

On failure: do NOT update parse_progress_description to the error message.
Leave description at the last known step. The progress_fragment.html template
reads upload.status to decide terminal rendering.
The error detail lives in LandingZoneTask.error_message, not on the upload.

### Wiring

The initial upload view (202 response) includes the polling partial
in its response — the upload_status.html template includes progress_fragment.html
inside the active status branches (pending/processing/retrying).

### What NOT to do

- Do not use celery-progress JS helper for the htmx poll. DB fields only.
- Do not use HX-Trigger: done response headers. Omit hx-trigger instead.
- Do not return HTTP 286 — omit-trigger is used for clarity.
- Do not poll /api/progress/{task_id}/. The htmx poll reads Django DB only.
- Do not put error_message text into parse_progress_description.
- Do not render progress UI in a full-page response. Partials only.

## entry_task_id

LandingUpload.entry_task_id stores the Celery task ID of the chain entry point
(parse_file). It is set by dispatch_pipeline after chain().delay() returns,
in the same .update() call as status=PROCESSING.
The name signals it's the head of the chain — per-attempt task IDs live in
LandingZoneTask.celery_task_id.

## Cotton progress component

Location: templates/cotton/ui/progress.html
Call: <c-ui.progress value="{{ upload.parse_progress_percent }}" color="purple" />

Both attributes are required — the component has no <c-vars> defaults.
value must be a plain integer 0–100 (no % sign). The component appends % itself.
color must be one of: pink, cyan, purple. Use purple — matches violet accent palette.

parse_progress_percent is a @property on LandingUpload:
    @property
    def parse_progress_percent(self):
        if not self.parse_progress_total:
            return 0
        return int((self.parse_progress_current / self.parse_progress_total) * 100)

Do not pass the percent value as a template expression that includes the % character.
Do not use the {% cotton %} native syntax — use <c-ui.progress /> HTML-like syntax
for consistency with the rest of the project templates.

## upload_status_poll view — error context

Fetch last_error in the view, not the template.
Use upload.task_rows.filter(status="failed").order_by("-attempt").first()
after prefetch_related("task_rows") — hits the cache, no extra query.
Pass as last_error to context. Template checks {% if last_error %} only.
```
