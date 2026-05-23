# Landing Zone Implementation Details

## Architecture

The landing zone pipeline was refactored from a monolithic `process_upload` task
into a two-step Celery chain with a separate execution ledger.

### Pipeline

```
View  →  dispatch_pipeline(upload_id)
              │
              ▼
         chain(
             parse_file.s(upload_id),     # CSV/HTML → staging parquet
             store_parquet.s()            # staging → final path
         ).delay()
```

Only `upload_id` (an int) passes through Redis between steps. The filesystem is
the checkpoint — staging file at `staging/{upload_id}.parquet`.

### New Files

| File | Change |
|------|--------|
| `apps/landing/models.py` | Removed `celery_task_id`, `error_message` from `LandingUpload`. Added `RETRYING` status. Created `LandingZoneTask` model. |
| `apps/landing/tasks.py` | Complete rewrite. `PipelineTask` base class, `parse_file`, `store_parquet`, `dispatch_pipeline`. |
| `apps/landing/views.py` | Calls `dispatch_pipeline()` instead of `process_upload.delay()`. Returns 202. |
| `apps/landing/admin.py` | Added `LandingZoneTaskInline` (tabular) on `LandingUploadAdmin`. Added standalone `LandingZoneTaskAdmin` (read-only). |
| `apps/landing/tests.py` | Complete rewrite — 19 tests covering the new architecture. |
| `apps/landing/migrations/0002_...py` | Migration for model changes. |
| `AGENTS.md` | Added conventions: status machine, file move semantics, non-retryable exceptions, progress tracking, filename convention. |

### Status Machine

```
LandingUpload.status:
    PENDING → PROCESSING → RETRYING → PROCESSING → COMPLETED
                         ↘ FAILED (retries exhausted)

LandingZoneTask.status (per attempt):
    pending → running → succeeded
                     → retrying → (next attempt)
                              → failed
```

### Base Task Class

`PipelineTask(celery.Task)` provides `record_failure(self, upload, task_row, exc)`
which both `parse_file` and `store_parquet` use via `base=PipelineTask`.

### File Paths

All under `DATASOURCE_ROOT`:

| Purpose | Path |
|---------|------|
| Raw uploads | `incoming/{upload_id}/{source_file_name}` |
| Staging parquet | `staging/{upload_id}.parquet` |
| Final archive | `{snapshot_type}/{save_master.slug}/{ingame_date}/{data_label}_{upload_timestamp}.parquet` |

### Non-retryable Exceptions (store_parquet)

- `FileExistsError` — destination already exists. Immutability violation.
- `FileNotFoundError` — staging file is gone. Re-dispatch pipeline.

Neither is in `autoretry_for`. Both are investigation signals.

### Filename Convention

`{data_label}_{upload_timestamp}.parquet` — the timestamp is
`LandingUpload.upload_timestamp` (ingestion event time, auto_now_add).
Filesystem-level lineage, readable without DB access.
