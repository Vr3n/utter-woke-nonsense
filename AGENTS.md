# Agents

This is a Django Project which is mainly Data Driven.

- The Celery orchestrates Data ingestion.
- Tailwindv4 standalone without npm or js handles the styling.
- django-cotton components.
- django-htmx for htmx related handling.

We only use `uv` related commands.

## LandingUpload status transitions

```
PENDING     set on upload creation (view)
PROCESSING  set by dispatch_pipeline() before chain.delay(). use .update() not .save().
RETRYING    set in the except block of either task when retries remain
FAILED      set in the except block when self.request.retries >= self.max_retries
COMPLETED   set by store_parquet on successful file move
```

Both parse_file and store_parquet own their own FAILED/RETRYING transitions.
Neither task sets PROCESSING — that belongs to the dispatcher.
Always re-raise after setting status so autoretry_for can inspect the exception.
Never swallow exceptions after a status transition.

## File move semantics

`store_parquet` must use `Path.rename()` (or `os.rename()`), not `shutil.copy`.
`staging/` and the final datasource path are intentionally under the same
`DATASOURCE_ROOT` so the move is atomic at the OS level. a copy+delete is not
atomic — a worker crash between the two leaves a partial file at the destination.
never make `staging/` configurable or point it at a separate mount.

## store_parquet — non-retryable exceptions

FileExistsError   destination already exists. immutability violation. investigate.
FileNotFoundError staging file is gone. re-dispatch the full pipeline.

Neither belongs in autoretry_for. both are investigation signals, not transient errors.

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

parse_file writes these at every recorder.set_progress() call via
save(update_fields=[...]). Both the DB fields and ProgressRecorder are
updated together at each step — never one without the other.

On failure: do NOT update parse_progress_description to the error message.
Leave description at the last known step. The progress_fragment.html template
reads upload.status to decide terminal rendering.
The error detail lives in LandingZoneTask.error_message, not on the upload.

## HTMX Progress Polling — implementation pattern

The progress UI uses HTMX partial polling. No JavaScript. No celery-progress JS helper.
The DB fields (parse_progress_current, parse_progress_total, parse_progress_description)
are the single source of truth the poll view reads. ProgressRecorder writes to the
Celery result backend in parallel — but the htmx poll reads Django DB only.

### Single-template structure

landing/partials/progress_fragment.html — single file for all states.

The template conditionally includes hx-trigger="every 600ms" when is_terminal is False.
When terminal (completed/failed), no hx-trigger is emitted — polling stops naturally.
No client-side event handling, no HX-Trigger headers, no HTTP 286.

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
in a separate .update() call (status=PROCESSING is set before the chain).
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

## Filename convention

final parquet filename: {data_label}_{upload_timestamp}.parquet
upload_timestamp is LandingUpload.upload_timestamp (auto_now_add, set at record
creation in the view). it reflects when the warehouse received the data, not when
the parquet was written to disk.

do not change this to a processing timestamp, a UUID, or any other identifier.
the filename is intentional filesystem-level lineage — readable without DB access.
