# Landing Zone — Implementation Plan

## Overview

Refactor the landing zone from a monolithic `process_upload` task into a two-step
Celery chain (`parse_file` → `store_parquet`) with a separate execution ledger
(`LandingZoneTask`) and an explicit retry/status machine.

### Pipeline shape

```
View  →  dispatch_pipeline(upload_id)
              │
              ▼
         chain(
             parse_file.s(upload_id),     # CSV/HTML → staging parquet
             store_parquet.s(upload_id)   # staging → final path
         ).delay()
```

Only `upload_id` (an int) passes through Redis between steps. No DataFrames,
dicts, or bytes cross the broker. The filesystem is the checkpoint — staging
file at `_staging/{upload_id}.parquet`.

---

## Status Machine

### `LandingUpload.status`

```
    ┌──────────┐
    │ PENDING  │  ← view creates the record
    └────┬─────┘
         │ dispatch_pipeline() via .update()
         ▼
    ┌────────────┐
    │ PROCESSING │
    └────┬───────┘
         │
    ┌────┴────────┐    yes, retries remain     ┌───────────┐
    │ task fails? ───────────────────────────→ │ RETRYING  │
    └────┬────────┘                             └─────┬─────┘
         │ no                                          │ next attempt starts
         ▼                                             ▼
    ┌───────────┐                                 ┌────────────┐
    │ COMPLETED │  ← store_parquet succeeded      │ PROCESSING │  (loop)
    └───────────┘                                 └────────────┘

    If task fails with self.request.retries >= self.max_retries:
        PROCESSING ──→ FAILED  (terminal)
```

Transitions:
- `PENDING` → set on upload creation (view)
- `PROCESSING` → set by `dispatch_pipeline()` before `chain().delay()`. Use `.update()` not `.save()`
- `RETRYING` → set in either task's `except` block when retries remain
- `FAILED` → set in either task's `except` block when retries exhausted
- `COMPLETED` → set by `store_parquet` on successful file move

Neither task sets `PROCESSING`. That belongs to the dispatcher.
Always re-raise after setting status so `autoretry_for` can inspect.
Never swallow exceptions after a status transition.

### `LandingZoneTask.status`

Per-attempt lifecycle:

```
pending → running → succeeded
                 → retrying → (next row with attempt+1 → running)
                          → failed
```

---

## Models

### `LandingUpload` — changes

| Action | Field |
|--------|-------|
| **Remove** | `celery_task_id` |
| **Remove** | `error_message` |
| **Add** | `RETRYING` to `Status.choices` |

New status choices:
```
PENDING, PROCESSING, RETRYING, COMPLETED, FAILED
```

### `LandingZoneTask` — new model

| Field | Type | Notes |
|-------|------|-------|
| `upload` | FK → `LandingUpload` | |
| `celery_task_id` | `CharField(255)`, indexed | from `self.request.id` |
| `step` | `CharField(20)` | choices: `parse`, `store` |
| `status` | `CharField(20)` | choices: `pending`, `running`, `retrying`, `succeeded`, `failed` |
| `attempt` | `PositiveIntegerField` | starts at 1, incremented per retry |
| `worker_hostname` | `CharField(255)`, blank/nullable | from `self.request.hostname` |
| `error_type` | `CharField(255)`, blank | exception class name e.g. `ValueError` |
| `error_message` | `TextField`, blank | full exception string |
| `started_at` | `DateTimeField`, nullable | set on transition to `running` |
| `finished_at` | `DateTimeField`, nullable | set on `succeeded`, `failed`, or `retrying` |

Unique constraint: `(upload, step, attempt)`

`duration_seconds` — `@property`, computed from timestamps. Never a stored field.

#### LandingZoneTask lifecycle per task invocation

```
1. Create row:  pending, attempt=N, started_at=null
2. Update row:  running, started_at=now
3a. On success: succeeded, finished_at=now
3b. On failure (retries remain): retrying, finished_at=now, error_type, error_message
3c. On failure (exhausted):      failed,   finished_at=now, error_type, error_message
```

`attempt = self.request.retries + 1` — `retries` starts at 0 on the first
execution, so attempt 1 = 0 + 1, attempt 2 = 1 + 1, etc.

Row is created as the **first** action in each task, before any I/O. This
guarantees every dispatched task has at least one ledger entry. A row with
`started_at` and no `finished_at` is how you detect hung tasks.

---

## Tasks

### Shared base class

```python
class PipelineTask(celery.Task):
    def record_failure(self, upload, task_row, exc):
        if self.request.retries < self.max_retries:
            upload.status = LandingUpload.Status.RETRYING
            task_row.status = LandingZoneTask.Status.RETRYING
        else:
            upload.status = LandingUpload.Status.FAILED
            task_row.status = LandingZoneTask.Status.FAILED
        upload.save(update_fields=["status"])
        task_row.error_type = type(exc).__name__
        task_row.error_message = str(exc)
        task_row.finished_at = timezone.now()
        task_row.save(update_fields=["status", "error_type", "error_message", "finished_at"])
```

### `parse_file`

```
Signature: parse_file(self, upload_id: int)
```

1. Create `LandingZoneTask(upload, step="parse", attempt=N, status="pending")`
2. Update `LandingZoneTask`: status="running", started_at=now
3. Read from `upload.incoming_path()`
4. Detect extension (`.csv` / `.html`) and select conversion strategy
5. Write parquet to staging at `_staging/{upload_id}.parquet`
   (converter already creates parent dir with `mkdir(parents=True, exist_ok=True)`)
6. Update `LandingZoneTask`: status="succeeded", finished_at=now
7. Return `upload_id` for next task in chain

On failure:
```python
except Exception as exc:
    self.record_failure(upload, task_row, exc)
    raise  # critical — lets autoretry_for inspect the exception
```

`autoretry_for=(ValueError, pd.errors.ParserError, pd.errors.EmptyDataError)`
`max_retries=3`, `retry_backoff=True`, `retry_jitter=True`
Do NOT add `MemoryError` or OOM signals.

### `store_parquet`

```
Signature: store_parquet(self, upload_id: int)
```

1. Create `LandingZoneTask(upload, step="store", attempt=N, status="pending")`
2. Update `LandingZoneTask`: status="running", started_at=now
3. Check staging file exists at `_staging/{upload_id}.parquet` — if missing, raise `FileNotFoundError`
4. Construct final path:
   ```
   LANDING_ZONE_ROOT / {snapshot_type} / {save_master.slug} / {ingame_date} / {data_label}_{upload_timestamp}.parquet
   ```
5. Check if final path already exists — if so, raise `FileExistsError`
   (immutability violation — never overwrite)
6. `Path(staging_path).rename(dest_path)` — atomic move, same filesystem
   (`_staging/` and snapshot dirs are both under `LANDING_ZONE_ROOT`)
7. Update `upload.parquet_path` and `upload.status = COMPLETED` (`save(update_fields=[...])`)
8. Update `LandingZoneTask`: status="succeeded", finished_at=now
9. (Optional) clean up staging file — already moved, so nothing to do

On failure: same pattern as `parse_file` (status flip + landing zone task update + raise).

`autoretry_for=(OSError, IOError)` — not `FileExistsError` or `FileNotFoundError`.
`max_retries=3`, `retry_backoff=True`, `retry_jitter=True`
Safe to add `acks_late=True` — this step is idempotent.

**Non-retryable exceptions:**
- `FileExistsError` — destination already exists. Immutability violation. Investigate.
- `FileNotFoundError` — staging file is gone. Re-dispatch the full pipeline.
Neither belongs in `autoretry_for`. Both are investigation signals.

### `dispatch_pipeline`

```python
def dispatch_pipeline(upload_id: int):
    LandingUpload.objects.filter(id=upload_id).update(
        status=LandingUpload.Status.PROCESSING
    )
    chain(parse_file.s(upload_id), store_parquet.s(upload_id)).delay()
```

Lives in `tasks.py`. Synchronous function, not a task. Co-located with the tasks
it orchestrates. Only exceptions it can raise are DB errors or broker
connectivity — both should surface as 500s.

### Progress reporting

- `celery-progress` (`ProgressRecorder`) is used **inside `parse_file` only**,
  for internal parse progress on large files. Reports on `parse_file`'s own task ID.
- Pipeline-level progress is tracked via `LandingUpload.status` and
  `LandingZoneTask` rows — not via `celery-progress`.
- The frontend polls `/api/uploads/{upload_id}/status/` for pipeline state.
- Do not build a separate `/api/progress/{task_id}/` endpoint for pipeline tracking.
- Do not write progress updates across task boundaries (e.g. `store_parquet`
  writing to `parse_file`'s result key). Each task owns its own result backend entry.

---

## Views

### Upload flow

```
1.  Validate form (hash check in clean_source_file)
2.  Reject duplicate if source_file_hash already exists → 400
3.  Create LandingUpload record (status=PENDING)
4.  Write file to _incoming/{upload.id}/{source_file_name}
5.  dispatch_pipeline(upload.id)    ← no try/except
6.  Return 202 with upload.id
```

- Hash check happens in step 1/2 — before any DB record or disk write.
  A duplicate is rejected before touching anything.
- No `try/except` around `dispatch_pipeline`. Infrastructure failures (DB down,
  Redis unreachable) surface as 500s.
- Response status is **202 Accepted**, not 200. Processing is async.

### Status polling endpoint

```
GET /api/uploads/{upload_id}/status/
```

Returns `LandingUpload.status` + `LandingZoneTask` ledger rows. This is the
pipeline-level progress endpoint the frontend polls. It already exists as
`upload_status_poll` — adapt its response.

---

## File Paths

All under `LANDING_ZONE_ROOT` (single configurable root, single filesystem):

| Purpose | Path | Managed by |
|---------|------|------------|
| Raw uploads | `_incoming/{upload_id}/{source_file_name}` | View writes, never deleted on failure |
| Staging parquet | `_staging/{upload_id}.parquet` | `parse_file` writes, `store_parquet` moves |
| Final archive | `{snapshot_type}/{save_master.slug}/{ingame_date}/{data_label}_{upload_timestamp}.parquet` | `store_parquet` writes (via `Path.rename`) |

### Filename convention

Final filename: `{data_label}_{upload_timestamp}.parquet`

`upload_timestamp` is `LandingUpload.upload_timestamp` (set via `auto_now_add`
at record creation in the view). It reflects when the warehouse received the
data, not when the parquet was written to disk. This is filesystem-level lineage
— readable without DB access.

Do not change this to a processing timestamp, a UUID, or any other identifier.

### File move semantics

`store_parquet` must use `Path.rename()` (or `os.rename()`), not `shutil.copy`.
`_staging/` and the final landing zone path are intentionally under the same
`LANDING_ZONE_ROOT` so the move is atomic at the OS level. A copy+delete is not
atomic — a worker crash between the two leaves a partial file at the destination.

Never make `_staging/` configurable or point it at a separate mount.

---

## Admin

### `LandingUploadAdmin`

- Remove `celery_task_id` and `error_message` from `readonly_fields`
- Add `LandingZoneTaskInline` (`TabularInline`, `extra=0`, `can_delete=False`)
  showing the execution ledger for this upload

### `LandingZoneTaskAdmin` (standalone)

```python
@admin.register(LandingZoneTask)
class LandingZoneTaskAdmin(admin.ModelAdmin):
    list_display  = ["upload", "step", "attempt", "status", "error_type",
                     "started_at", "duration_seconds"]
    list_filter   = ["step", "status", "worker_hostname"]
    search_fields = ["upload__id", "celery_task_id", "error_type"]
    readonly_fields = ["upload", "celery_task_id", "step", "attempt", "status",
                       "worker_hostname", "error_type", "error_message",
                       "started_at", "finished_at", "duration_seconds"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
```

- `duration_seconds` expands via `@admin.display(description="Duration (s)")` wrapping the model property
- `has_add_permission=False` and `has_change_permission=False` make the
  read-only intent explicit at the permission level, not just the UI level
- The standalone listing is useful for ops debugging — filter all FAILED tasks
  across all uploads, or filter by `worker_hostname` when a specific worker is
  misbehaving

---

## Tests

Rewrite `tests.py` from scratch. Cover these surfaces:

| Test | What it checks |
|------|----------------|
| `parse_file` success (CSV) | Staging file written, `LandingZoneTask` created with `succeeded`, upload stays `PROCESSING` |
| `parse_file` success (HTML) | Same for HTML path |
| `parse_file` failure → retrying | `LandingZoneTask` error fields set, `finished_at` set, upload status = `RETRYING`, exception re-raised |
| `parse_file` failure → failed | Retries exhausted → upload status = `FAILED`, task row = `failed` |
| `store_parquet` success | File moved from staging to final, `upload.parquet_path` set, `upload.status = COMPLETED` |
| `store_parquet` FileExistsError | Destination exists → raises, not in `autoretry_for`, upload stays `PROCESSING` |
| `store_parquet` FileNotFoundError | Staging missing → raises, not in `autoretry_for` |
| `dispatch_pipeline` | Sets `PROCESSING` via `.update()`, creates chain, marks task started |
| View returns 202 | HTTP 202, `upload.id` in response |
| View duplicate rejection | Same hash → 400, no DB record created |
| View hash before write | Duplicate rejected before any DB record or disk I/O |
| Chain end-to-end | CSV upload → staging → final parquet → both task rows `succeeded` |

Use `CELERY_TASK_ALWAYS_EAGER = True` (already in `conftest.py`) for synchronous
task execution in tests.

---

## Implementation order

1. Update `LandingUpload` model (remove fields, add `RETRYING` status)
2. Create `LandingZoneTask` model
3. Generate migration
4. Rewrite `tasks.py` (`parse_file`, `store_parquet`, `dispatch_pipeline`)
5. Update `views.py` (202 response, hash before write, dispatch_pipeline)
6. Update `admin.py` (inline + standalone admin)
7. Update `tables.py` if needed
8. Update `AGENTS.md` with all documented conventions
9. Rewrite `tests.py`
10. Run tests, ruff, migration check
