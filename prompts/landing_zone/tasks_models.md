# Agent Instructions

You are working on a Django-based data warehouse for Football Manager simulation analysis.
The system ingests CSV/HTML exports from Football Manager, converts them to Parquet,
and stores them in a layered warehouse (Landing → Bronze → Silver → Gold).
The stack is Django + Celery + Redis + Parquet. Read this file fully before touching any code.

---

## Architecture

### Layer responsibilities

```
Landing Zone   raw file archive. parquet snapshots. no cleaning. no transformation.
Bronze Layer   raw ingestion into DB with lineage metadata. no normalization.
Silver Layer   cleaned, typed, normalized. (not yet built)
Gold Layer     analytics-ready aggregates. (not yet built)
```

### Snapshot types

| Type | Grain |
|---|---|
| `squad_snapshot` | one player's state per squad snapshot per simulation date |
| `scouting_snapshot` | one scouted player per scouting snapshot per simulation date |
| `squad_matchstats_snapshot` | one player's stats per match |

### Landing zone storage path

```
LANDING_ZONE_ROOT/
  {snapshot_type}/
    {save_master.slug}/
      {ingame_date}/
        {data_label}_{upload_timestamp}.parquet
```

Incoming uploads stage at `LANDING_ZONE_ROOT/_incoming/{upload_id}/{source_file_name}`.

---

## Core principles

These are non-negotiable. Reason about every change against this list.

- **Immutability** — existing records are never mutated. corrections are new ingestion batches.
- **Idempotency** — duplicate uploads are rejected via `source_file_hash` (SHA-256). every task must be safe to retry with the same input.
- **Append-only** — bronze rows are only inserted, never updated or deleted.
- **Lineage** — every row traces back to its source file, upload event, and simulation context.
- **Temporal history** — `ingame_date` + `upload_timestamp` together define the observation point. never drop either.
- **No logic in bronze** — bronze does not clean values, normalize currencies, split columns, remove units, or derive trends. raw observations only.

---

## Models

### `LandingUpload`

The upload event record. one row per file upload attempt.

Key fields:
- `save_master` → FK to `core.SaveMaster` (the FM save universe)
- `snapshot_type` → one of the three snapshot types above
- `source_file_hash` → SHA-256, unique constraint. duplicate detection lives here.
- `ingame_date` → the in-game date when the data was extracted from the simulation
- `data_label` → user-defined label e.g. `winter_scouting`, `u23_squad`
- `status` → `pending / processing / completed / failed`
- `parquet_path` → populated after successful conversion
- `incoming_path()` → method, not a field. returns the staging path.

Do not add `celery_task_id` or `error_message` back to this model.
Task tracking belongs in `LandingZoneTask`.

### `LandingZoneTask`

One row per task attempt. the execution ledger.

Key fields:
- `upload` → FK to `LandingUpload`
- `celery_task_id` → indexed
- `step` → `parse / store` (convert is folded into parse — see Tasks section)
- `status` → `pending / running / retrying / succeeded / failed`
- `attempt` → retry counter, starts at 1
- `worker_hostname` → from `self.request.hostname`. lineage for infra debugging.
- `error_type` → exception class name e.g. `ValueError`, `OSError`
- `error_message` → full exception string
- `started_at` / `finished_at` → both nullable datetimes
- `duration_seconds` → `@property`, computed from timestamps. never a stored field.

Unique constraint on `(upload, step, attempt)`. enforces idempotency at DB level.

---

## Tasks

### Pipeline shape

```
parse_file.s(upload_id)  →  store_parquet.s(upload_id)
```

Two tasks in a Celery `chain`. the only thing that passes through Redis between steps
is `upload_id` (an int). never pass DataFrames, dicts, or bytes through the broker.

### Handoff mechanism

`parse_file` writes the converted Parquet file to a **staging path** on disk.
`store_parquet` moves it from staging to the final landing zone path.
The filesystem is the checkpoint. Redis carries only the ID.

### `parse_file`

- reads from `upload.incoming_path()`
- detects extension (`.csv` / `.html`) and selects strategy
- writes parquet to staging: `LANDING_ZONE_ROOT/_staging/{upload_id}.parquet`
- `autoretry_for=(ValueError, pd.errors.ParserError, pd.errors.EmptyDataError)`
- `max_retries=3`, `retry_backoff=True`, `retry_jitter=True`
- does NOT retry on `MemoryError` or `OOM` signals — do not add these to `autoretry_for`

### `store_parquet`

- reads staging path, moves to final landing zone path (mkdir parents)
- updates `upload.parquet_path` and `upload.status = COMPLETED`
- `autoretry_for=(OSError, IOError)`
- `max_retries=3`, `retry_backoff=True`, `retry_jitter=True`
- safe to add `acks_late=True` — this step is idempotent (moving an already-written file is safe to repeat)

### Retry rules (from GitGuardian + Celery docs)

- always use specific exceptions in `autoretry_for`. never use bare `Exception`.
- never set `max_retries=None`.
- never set `task_retry_on_worker_lost=True`.
- `retry_backoff=True` + `retry_jitter=True` on any task touching I/O or external services.
- future AI API tasks must also have `rate_limit` set (e.g. `"10/m"`).

### Progress reporting

Use `celery-progress` (`ProgressRecorder`). set progress at task boundaries:
- `0/2` — starting parse
- `1/2` — parse done, storing
- `2/2` — done

The frontend polls `/api/progress/{task_id}/`. do not build a custom polling endpoint.

### Dispatcher

Views call `dispatch_pipeline(upload_id)`, not individual tasks directly.

```python
def dispatch_pipeline(upload_id: int):
    chain(parse_file.s(upload_id), store_parquet.s(upload_id)).delay()
```

---

## What bronze does NOT do

If you are writing code that touches bronze models, stop and check:
is this code cleaning a value? normalizing a currency? splitting a column?
removing a unit? calculating anything? deriving a trend?

If yes — that code belongs in silver, not bronze. bronze is raw observation + lineage metadata only.

---

## File handling

- never delete files from `_incoming` on failure. leave them for retry and debugging.
- staging files (`_staging/`) may be cleaned up after successful `store_parquet`.
- final parquet paths are immutable once written.
- if a file already exists at the final path, do not overwrite. raise an error and investigate.

---

## What to do when something is unclear

1. check `layer_architectures.md` for the intent behind any layer or field.
2. check `models.py` for the current state of the data model.
3. check `tasks.py` for the current pipeline implementation.
4. if still unclear, prefer the more conservative option (less transformation, more preservation).

do not invent new snapshot types, new pipeline steps, or new status values without
explicit instruction. the taxonomy is deliberate.

---

## Future work (do not implement yet, but do not break the path to it)

- Bronze DB ingestion — parquet → bronze Django models, append-only
- Silver layer — cleaned, typed data derived from bronze
- AI API endpoints — will be appended to the chain after `store_parquet`
- `soft_time_limit` + separate retry queue for oversized files (GitGuardian pattern)
