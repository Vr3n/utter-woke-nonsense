# Bronze Ingestion — Refined Implementation Plan

## Status

This document is the canonical reference for the bronze ingestion implementation.
Every architecture decision, pattern choice, and edge case below was stress-tested via the grill-with-docs process in `AGENTS.md`.
If you encounter a conflict between this document and any other source (old ingestion plan, docs/, adr/), this document wins.

---

## Architecture

### Pipeline trigger

```
store_parquet succeeds in landing zone
    │
    ├── LandingUpload.status = COMPLETED  (landing zone is done)
    ├── dispatch_bronze_ingestion(upload) ← called at end of store_parquet success path
    │       │
    │       ├── BronzeIngestion.objects.create(status=PROCESSING, ...)
    │       ├── result = ingest_to_bronze.delay(ingestion.id)
    │       └── BronzeIngestion.objects.filter(id=ingestion.id)
    │              .update(celery_task_id=result.id)
    │
    └── independent bronze Celery task graph
```

### Why separate dispatch, not chain

jhe chain approach (`chain(parse, store, ingest).delay()`) couples the Celery result graphs.
If `ingest_to_bronze` exhausts its retries, Celery marks the entire chain as failed — even though `parse_file` and `store_parquet` both succeeded.
This is confusing to read in Flower, confusing to query in the result backend, and requires anyone debugging bronze to understand the landing zone chain structure first.

The separate dispatch makes this explicit:

- Landing zone's job is done when `store_parquet` completes. `LandingUpload.status = COMPLETED` is the correct terminal state at that point regardless of what happens downstream.
- Bronze ingestion is a consumer of the landing zone output, not a step in the landing zone pipeline. Putting it in the chain implies it's part of the same unit of work. It isn't.
- Two independent Celery task graphs. Two independent result entries. Landing zone failure never affects bronze visibility. Bronze failure never taints the landing zone result.

### Why `dispatch_bronze_ingestion(upload)` not `(upload_id)`

`store_parquet` already has the `upload` object in scope after `LandingUpload.objects.select_related("save_master").get(id=upload_id)`.
Passing the full object avoids a second DB query inside the dispatcher, which needs `upload.simulation_source`, `upload.snapshot_type`, `upload.source_file_hash`, and other denormalised fields.

### Why the dispatcher creates the `BronzeIngestion` row, not the task

The `BronzeIngestion` row is the event record, not the task record. It represents "a bronze ingestion was initiated for this upload" —
that event happens at dispatch time, not when the worker picks up the task. This is identical to how `LandingUpload` is created in the view before `dispatch_pipeline()` is called.

The pattern is:

```
event record created synchronously → task dispatched → task operates on existing record
```

If the row were created inside the task, there would be a window between `dispatch_bronze_ingestion()` returning and the task starting where no `BronzeIngestion` row exists. During that window, querying "what bronze ingestions are pending for this upload?" gives the wrong answer. For a warehouse where lineage is load-bearing, that gap matters.

Creating the row in the dispatcher also allows passing `ingestion.id` directly to the task — the task receives the ID of the record it owns, rather than having to search for it by `landing_upload_id` with a fragile PENDING filter.

### Why `ingest_to_bronze(self, ingestion_id)` not `(self, upload_id)`

The task operates on the `BronzeIngestion` record — it should receive the ID of that record. It reaches the upload via `ingestion.landing_upload`. Passing `upload_id` and having the task find the ingestion by `BronzeIngestion.objects.get(landing_upload_id=upload_id, status=PENDING)` is fragile — if two ingestions race (e.g., re-dispatch), the PENDING filter could match multiple rows.

### Why DuckDB and not Django ORM for bronze INSERT

The parquet file contains 50-200 columns per row, all of which must collapse into a single JSONB `raw_data` column. DuckDB's `to_json(t)::JSONB` handles this in a single SQL expression at the database level — no row-by-row Python processing, no manual JSON construction. Django ORM would require reading the parquet into Python objects, iterating each row, constructing JSON manually, and issuing individual INSERTs or expensive bulk_create calls.

DuckDB also has a first-class PostgreSQL scanner via `ATTACH ... (TYPE postgres)`, allowing direct INSERT from parquet into PostgreSQL in a single query. This is the fastest path from parquet file to PostgreSQL table.

### Why DuckDB never touches DDL

`CREATE TABLE IF NOT EXISTS` inside a task creates two sources of truth — one in migrations, one in code. Silent drift occurs when the code creates a slightly different schema than what migrations defined. No rollback is possible. Migrations are the single source of schema truth. DuckDB executes INSERT only.

---

## Bronze data tables

### Table names and schema

All three tables live in the `bronze` PostgreSQL schema:

- `bronze.squad_snapshot`
- `bronze.scouting_snapshot`
- `bronze.squad_matchstats_snapshot`

### Shared columns (all three tables)

```
ingestion_id        BIGINT       NOT NULL  -- FK → bronze_ingestion.id
simulation_source   VARCHAR(255) NOT NULL  -- FM version
save_name           VARCHAR(255) NOT NULL  -- from save_master.slug
ingame_date         DATE         NOT NULL  -- when extracted from simulation
upload_timestamp    TIMESTAMPTZ  NOT NULL  -- when warehouse received the file
snapshot_type       VARCHAR(50)  NOT NULL  -- squad_snapshot | scouting_snapshot | ...
source_file_hash    VARCHAR(64)  NOT NULL  -- SHA-256, idempotency guard
raw_data            JSONB        NOT NULL  -- entire player row, keys=FM column names,
                                           -- values=raw strings. never cast.
```

### Additional columns for `squad_matchstats_snapshot`

```
opponent           VARCHAR(255) NOT NULL  -- opponent team name
ingame_matchdate   DATE         NOT NULL  -- match date in simulation
```

### Why JSONB for player data

FM24 and FM26 have different column names. `raw_data` absorbs this drift — the schema never changes, only the keys inside the JSONB differ by FM version. The bronze constraint is: never cast, never clean. A value of `£45.5M` is stored as the string `"£45.5M"` inside `raw_data`. Silver unpacks and types it.

`column_count` on `BronzeIngestion` tracks key count per ingestion batch — drift signals an FM version change before Silver breaks.

### Why metadata columns are structured, not JSONB

Metadata columns (`simulation_source`, `save_name`, `ingame_date`, `upload_timestamp`, `snapshot_type`, `source_file_hash`) are structured columns because they must be queryable and indexable for auditing and partitioning. Putting them inside `raw_data` would require JSONB extraction for every query that filters by simulation source or ingame date, which is slow at scale and impossible to index effectively.

### Why `squad_matchstats_snapshot` gets extra structured columns

The matchstats table represents player performance after a specific match. `opponent` and `ingame_matchdate` are fundamental query dimensions for match-level analysis (e.g., "show me all players who scored against Real Madrid in 2028"). These are not FM version-specific — every FM version has match data with opponents and dates. They belong as structured columns, not buried in JSONB.

---

## Status machine

### Shared enums in `core/choices.py`

Defined once, used across all pipeline layers:

```python
class PipelineStatus(models.TextChoices):
    """LandingUpload, BronzeIngestion, future Silver/Gold ingestion models."""
    PENDING    = "pending",    "Pending"
    PROCESSING = "processing", "Processing"
    RETRYING   = "retrying",   "Retrying"
    COMPLETED  = "completed",  "Completed"
    FAILED     = "failed",     "Failed"

class TaskStatus(models.TextChoices):
    """LandingZoneTask, BronzeIngestionTask, future Silver/Gold task ledgers."""
    PENDING   = "pending",   "Pending"
    RUNNING   = "running",   "Running"
    RETRYING  = "retrying",  "Retrying"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED    = "failed",    "Failed"
```

### Why shared enums

Silver and Gold will need the same status machine. Defining it once in `core` now is cheaper than retrofitting across four layers later. The migration impact is minimal — the DB stores the string values (`"pending"`, `"retrying"` etc.), which don't change. The migration Django generates for switching from local `TextChoices` to `core.choices.PipelineStatus` is an `AlterField` that changes Python-side choices metadata only. No data is touched.

### Why `core/choices.py` not `core/models.py`

Separate file avoids circular imports — `landing/models.py` imports from `core/choices.py` cleanly. If the choices lived in `core/models.py`, import cycles would arise when core models reference landing zone models or vice versa.

### BronzeIngestion.status

Uses `PipelineStatus` from `core/choices.py`. Coarse-grained — enough to answer "is this ingestion done, failed, or still running?"

### BronzeIngestionTask.status

Uses `TaskStatus` from `core/choices.py`. Tracks the Celery attempt lifecycle.

### BronzeIngestionTask.phase

Layer-specific `TextChoices` defined in `bronze/models.py`:

```python
class BronzeIngestionPhase(models.TextChoices):
    ATTACHING         = "attaching",          "Attaching to PostgreSQL"
    COUNTING_SOURCE   = "counting_source",    "Counting source parquet rows"
    INSERTING         = "inserting",          "Inserting into bronze table"
    COUNTING_INGESTED = "counting_ingested",  "Verifying ingested row count"
    COMPLETED         = "completed",          "Ingestion completed successfully"
```

### Why two fields (status + phase) instead of one

The two-field approach keeps the status machine simple (matching the `TaskStatus` contract that all layers share) while adding a second field for granular debugging. A single-field approach would require extending `TaskStatus` with bronze-specific values (attaching, counting_source, etc.) that don't apply to landing zone or future layers — breaking the shared contract.

With two fields, a failed task at the `inserting` phase is:

```
status: failed
phase:  inserting
```

This is immediately debuggable. Without the phase field, you'd know the task failed but not where in the lifecycle.

### Status transitions

```
BronzeIngestion:
  PENDING → PROCESSING ← set by dispatcher before task dispatch
                ↓
           COMPLETED   ← set by task on success
           RETRYING    ← set by record_failure when retries remain
           FAILED      ← set by record_failure when retries exhausted

BronzeIngestionTask.status:
  pending → running → succeeded
                    → retrying → (next row, attempt+1)
                    → failed

BronzeIngestionTask.phase:
  (empty) → attaching → counting_source → inserting → counting_ingested → completed
```

---

## Task: `ingest_to_bronze`

### Signature

```python
@shared_task(
    bind=True,
    base=PipelineTask,
    autoretry_for=(
        duckdb.IOException,
        duckdb.OperationalError,
        duckdb.ConnectionException,
    ),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def ingest_to_bronze(self, ingestion_id: int) -> None: ...
```

### Why `PipelineTask` in `core/tasks.py`

The base class provides `record_failure` shared across landing zone and bronze tasks. It was originally defined in `landing/tasks.py` but extracting it to `core/` prevents a cross-app dependency where bronze would import from landing for a base class.

`record_failure` is model-agnostic — it operates on the passed object, using shared `PipelineStatus` and `TaskStatus` enums for the status value. It takes three parameters:

- `obj` — the pipeline event record (LandingUpload or BronzeIngestion)
- `task_row` — the task ledger record (LandingZoneTask or BronzeIngestionTask)
- `exc` — the exception

Even though both models have the same field names (`status`), the method should not import either model — it uses the shared enums' string values.

### DuckDB connection string

Derived from `settings.DATABASES["default"]` via `core/db.py`:

```python
def get_pg_conn_string(alias="default"):
    db = settings.DATABASES[alias]
    return (
        f"host={db['HOST']} port={db['PORT']} "
        f"dbname={db['NAME']} user={db['USER']} "
        f"password={db['PASSWORD']}"
    )
```

### Why derived from DATABASES, not a separate setting

Avoids duplicating credentials. If the database configuration changes (host, port, credentials), there's one place to update. A separate `BRONZE_DATABASE_URL` setting would need to be kept in sync. If bronze moves to a separate database in the future, this function can be extended with a `BRONZE_DATABASE_URL` env var fallback — the abstraction point is already established.

### DuckDB ATTACH

```python
pg_conn = get_pg_conn_string()
conn.execute(f"ATTACH '{pg_conn}' AS bronze_schema (TYPE postgres)")
table = "bronze_schema.bronze.{}".format(upload.snapshot_type)
```

`bronze_schema` is the DuckDB alias for the attached PostgreSQL database. `bronze` is the PostgreSQL schema within that database where the data tables live. The fully qualified path `bronze_schema.bronze.squad_snapshot` is unambiguous — no relying on `public` schema shortcuts.

### DuckDB context manager

```python
from contextlib import contextmanager
import duckdb

@contextmanager
def duckdb_pg_connect():
    conn = duckdb.connect()
    try:
        yield conn
    finally:
        conn.close()
```

### Why a context manager

If the task crashes between `ATTACH` and a clean shutdown, the Python garbage collector will eventually close the DuckDB connection, but the PostgreSQL side may keep the connection open. A `try/finally conn.close()` guarantees cleanup regardless of exceptions. This prevents PostgreSQL connection leaks.

Inside the context manager: ATTACH, COUNT, column_count sample, INSERT, COUNT_INGESTED. Outside (Django ORM only): idempotency checks, BronzeIngestionTask row creation, all `.save()` calls.

### DuckDB INSERT pattern

```python
conn.execute(f"""
    INSERT INTO {table}
        (ingestion_id, simulation_source, save_name, ingame_date,
         upload_timestamp, snapshot_type, source_file_hash, raw_data)
    SELECT
        $1, $2, $3, $4::DATE, $5::TIMESTAMPTZ, $6, $7,
        to_json(t)::JSONB
    FROM read_parquet($8) t
""", [
    ingestion.id,
    upload.simulation_source,
    upload.save_master.slug,
    upload.ingame_date.isoformat(),
    upload.upload_timestamp.isoformat(),
    upload.snapshot_type,
    upload.source_file_hash,
    upload.parquet_path,
])
```

All metadata values use DuckDB positional placeholders (`$1`, `$2`, ...). The table name uses `.format()` — acceptable because `snapshot_type` comes from `LandingUpload.SnapshotType` choices, a controlled enum, not user input. Never use f-strings or `.format()` for values passed to DuckDB `execute()`.

---

## Exception handling

### Exception taxonomy

| Category                        | Exceptions                                                                                                                | Behavior                                                           |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `autoretry_for`                 | `duckdb.IOException`, `duckdb.OperationalError`, `duckdb.ConnectionException`                                             | Transient → retry up to 3x with backoff + jitter                   |
| Non-retryable (manual catch)    | `duckdb.CatalogException`, `duckdb.ConstraintException`, `duckdb.InvalidInputException`, `duckdb.NotImplementedException` | `record_failure` → re-raise (FAILED, no retry)                     |
| Idempotency guard (same upload) | N/A — early return, not exception                                                                                         | `return` with FAILED status. Never raise.                          |
| Idempotency guard (same hash)   | N/A — log warning, proceed                                                                                                | Log warning, continue with ingestion                               |
| Bare `except Exception`         | Anything not caught above                                                                                                 | `record_failure` → re-raise; `autoretry_for` decides based on type |

### Why each exception goes where it does

**`duckdb.IOException`**: Parquet read I/O errors (filesystem glitches, NFS blips, network filesystem timeouts). These resolve within seconds — retry makes sense.

**`duckdb.OperationalError`**: PostgreSQL timeouts, deadlocks, transient network blips during queries. Identical in nature to a database being temporarily unreachable — retry.

**`duckdb.ConnectionException`**: PostgreSQL unreachable at ATTACH time. Could not connect. Same semantics as `OperationalError` — the DB is temporarily down, wait and retry.

**`duckdb.CatalogException`**: Table doesn't exist. This means the migration hasn't run or has failed. Retrying will fail identically every time. Needs human investigation.

**`duckdb.ConstraintException`**: Primary key, foreign key, or unique constraint violation. Data integrity issue. Retrying won't fix corrupt data. Needs human investigation.

**`duckdb.InvalidInputException`**: Parquet schema doesn't match the INSERT target (e.g., column count mismatch, type mismatch). This can happen on FM version drift between what was written to the landing zone and what the bronze task expects. Not transient.

**`duckdb.NotImplementedException`**: DuckDB feature gap that was hit at runtime. Retrying a DuckDB bug will burn retry budget pointlessly.

### Idempotency guard behavior

The idempotency check uses `return`, not `raise`. If a completed ingestion exists for the same `landing_upload`, the task exits cleanly with FAILED status. Raising here would trigger a retry that hits the same guard and returns again, burning three retry slots doing nothing.

If a completed ingestion exists for the same `source_file_hash` but from a _different_ upload, the task logs a warning and proceeds. This permits metadata corrections — the same parquet content can be re-ingested under a different upload with corrected `simulation_source`. The duplicate rows are detectable via `ingestion_id` and `source_file_hash` columns.

---

## Full lifecycle

### Dispatcher (`dispatch_bronze_ingestion`)

```
1. BronzeIngestion.objects.create(
       landing_upload=upload,
       status=PipelineStatus.PROCESSING,
       simulation_source=upload.simulation_source,
       snapshot_type=upload.snapshot_type,
       source_file_hash=upload.source_file_hash,
       started_at=timezone.now(),
   )

2. result = ingest_to_bronze.delay(ingestion.id)

3. BronzeIngestion.objects.filter(id=ingestion.id).update(
       celery_task_id=result.id,
   )
```

### Task (`ingest_to_bronze`)

```
STEP 1: Idempotency check (same upload)
        BronzeIngestion.objects.filter(
            landing_upload=upload, status=PipelineStatus.COMPLETED
        ).exclude(id=ingestion.id).exists()
        → Yes: log warning, set FAILED, return
        → No: proceed

STEP 2: Idempotency check (same hash, different upload)
        BronzeIngestion.objects.filter(
            source_file_hash=upload.source_file_hash,
            status=PipelineStatus.COMPLETED,
        ).exclude(id=ingestion.id).exists()
        → Yes: log warning, proceed (metadata correction path)
        → No: proceed

STEP 3: BronzeIngestionTask.objects.create(
            ingestion=ingestion,
            step="ingest",
            attempt=self.request.retries + 1,
            status=TaskStatus.PENDING,
            celery_task_id=self.request.id or "",
            worker_hostname=self.request.hostname or "",
        )

STEP 4: task_row.status = TaskStatus.RUNNING
        task_row.started_at = timezone.now()
        task_row.save(update_fields=["status", "started_at"])

STEP 5: DuckDB context manager enters

STEP 6: task_row.phase = ATTACHING → save()
        conn.execute("ATTACH '...' AS bronze_schema (TYPE postgres)")

STEP 7: task_row.phase = COUNTING_SOURCE → save()
        source_count = conn.execute(
            "SELECT COUNT(*) FROM read_parquet($1)", [upload.parquet_path]
        ).fetchone()[0]

STEP 8: task_row.phase = INSERTING → save()
        column_count = conn.execute("""
            SELECT len(json_keys(to_json(t)))
            FROM read_parquet($1) t LIMIT 1
        """, [upload.parquet_path]).fetchone()[0]
        conn.execute("""
            INSERT INTO bronze_schema.bronze.{table}
                (ingestion_id, simulation_source, save_name, ingame_date,
                 upload_timestamp, snapshot_type, source_file_hash, raw_data)
            SELECT $1, $2, $3, $4::DATE, $5::TIMESTAMPTZ, $6, $7,
                   to_json(t)::JSONB
            FROM read_parquet($8) t
        """.format(table=upload.snapshot_type), [
            ingestion.id, upload.simulation_source,
            upload.save_master.slug, upload.ingame_date.isoformat(),
            upload.upload_timestamp.isoformat(), upload.snapshot_type,
            upload.source_file_hash, upload.parquet_path,
        ])

STEP 9: task_row.phase = COUNTING_INGESTED → save()
        ingested_count = conn.execute("""
            SELECT COUNT(*) FROM bronze_schema.bronze.{table}
            WHERE ingestion_id = $1
        """.format(table=upload.snapshot_type), [ingestion.id]).fetchone()[0]

STEP 10: DuckDB context manager exits (conn.close() guaranteed by finally)

STEP 11: ingestion.ingested_row_count = ingested_count
         ingestion.source_row_count = source_count
         ingestion.column_count = column_count
         ingestion.status = PipelineStatus.COMPLETED
         ingestion.completed_at = timezone.now()
         ingestion.save(update_fields=[
             "ingested_row_count", "source_row_count", "column_count",
             "status", "completed_at",
         ])

STEP 12: task_row.status = TaskStatus.SUCCEEDED
         task_row.phase = BronzeIngestionPhase.COMPLETED
         task_row.finished_at = timezone.now()
         task_row.save(update_fields=["status", "phase", "finished_at"])
```

### Error path

```
ANY STEP → non-retryable exception (CatalogException etc.) catches:
    self.record_failure(ingestion, task_row, exc)
    raise  # surfaces as FAILED, no retry

ANY STEP → transient exception (OperationalError etc.) passes through:
    autoretry_for catches it → retry with backoff
    record_failure already ran before re-raise → status is RETRYING

ALL STEPS → bare except Exception catches unexpected:
    self.record_failure(ingestion, task_row, exc)
    raise  # autoretry_for decides based on type
```

---

## Admin

### BronzeIngestionAdmin

```python
@admin.register(BronzeIngestion)
class BronzeIngestionAdmin(admin.ModelAdmin):
    list_display = [
        "id", "landing_upload_link", "snapshot_type", "simulation_source",
        "status", "source_row_count", "ingested_row_count",
        "column_count", "duration_seconds", "started_at",
    ]
    list_filter = ["status", "snapshot_type", "simulation_source"]
    search_fields = ["landing_upload__id", "source_file_hash", "celery_task_id"]
    readonly_fields = [f.name for f in BronzeIngestion._meta.fields] + ["duration_seconds"]

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
```

`landing_upload_link` is a custom column showing the source upload ID as a clickable link to the `LandingUploadAdmin` change page. This provides navigability in both directions — upload ↦ bronze ingestion (via a link column on `LandingUploadAdmin.list_display`) and bronze ingestion ↦ upload (via this column).

### BronzeIngestionTaskAdmin

```python
@admin.register(BronzeIngestionTask)
class BronzeIngestionTaskAdmin(admin.ModelAdmin):
    list_display = ["ingestion", "step", "attempt", "status", "phase",
                    "error_type", "started_at", "duration_seconds"]
    list_filter = ["step", "status", "phase", "worker_hostname"]
    search_fields = ["ingestion__id", "celery_task_id", "error_type"]
    readonly_fields = [...]

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
```

### Why link column on `LandingUploadAdmin`, not inline

A cross-app inline (`BronzeIngestionInline` imported in `landing/admin.py`) creates a coupling where landing admin code needs to import bronze models. A link column in `list_display` that generates an admin URL to `BronzeIngestionAdmin` with a pre-filtered query parameter avoids this while still providing one-click pipeline navigation.

---

## Logging

Same pattern as landing zone. Every log call inside the task includes `extra={"upload_id": ..., "step": "ingest"}`.

| Point                   | Level   | Message                                                         |
| ----------------------- | ------- | --------------------------------------------------------------- |
| Task starts             | INFO    | "Starting bronze ingestion"                                     |
| Idempotency skip        | WARNING | "Duplicate ingestion detected, returning early"                 |
| Hash match, proceeding  | WARNING | "Same source_file_hash found for different upload — proceeding" |
| Source rows counted     | INFO    | "Source rows: %d"                                               |
| INSERT complete         | INFO    | "Inserted into bronze.%s"                                       |
| Ingested rows verified  | INFO    | "Ingested rows: %d"                                             |
| DuckDB timing           | INFO    | "DuckDB INSERT (%.3fms)" / "Parquet COUNT (%.3fms)"             |
| Retrying                | WARNING | "Retrying (attempt %d): %s"                                     |
| Retries exhausted       | ERROR   | "Failed permanently: %s"                                        |
| Non-retryable exception | ERROR   | "Non-retryable: %s — investigate"                               |

`after_setup_task_logger` signal is already wired in `celery.py` — no changes needed.

---

## Migration strategy

### Bronze audit models

Standard Django migration via `makemigrations`. Creates `bronze_ingestion` and `bronze_ingestiontask` tables in the `public` schema (Django's default).

### Bronze data tables

Custom `RunSQL` migration in the bronze app. Key constraints:

1. **`atomic = False`** on the migration class. `CREATE INDEX CONCURRENTLY` cannot run inside a transaction.

2. **Separate `RunSQL` operations** for CREATE TABLE and CREATE INDEX within the same migration. CONCURRENTLY requires no active transaction.

3. **`reverse_sql` on every `RunSQL` operation** for rollback capability. `RunSQL.noop` is acceptable only where rollback is genuinely impossible — not as a shortcut.

4. **`CREATE SCHEMA IF NOT EXISTS bronze`** as the first operation.

5. **`CREATE INDEX CONCURRENTLY`** on `ingestion_id`, `ingame_date`, `simulation_source` for all three tables. `squad_matchstats_snapshot` additionally indexes `opponent` and `ingame_matchdate`.

6. **Never edit the original CREATE TABLE migration.** Schema changes go in new `RunSQL` migrations.

### Why RunSQL, not managed Django models

These tables are append-only, have no surrogate PK (the ingestion composite is the identity), hold JSONB raw data, and are written by DuckDB, not Django ORM. Managed models with `Meta.managed = False` and matching `db_table` would give Django migration awareness but still require RunSQL for actual creation (unmanaged models don't create tables). Pure RunSQL is simpler — one SQL statement per migration, no pretending the ORM owns these tables.

---

## Testing

### Infrastructure

Tests run against a real PostgreSQL database via Docker Compose (`fm-warehouse`). The root `conftest.py` sets `CELERY_TASK_ALWAYS_EAGER = True` so tasks run synchronously without a Celery worker.

DuckDB ATTACH connects to the same test database that pytest-django creates. `get_pg_conn_string()` resolves from `settings.DATABASES["default"]`, which pytest-django overrides to point to the test database.

### Test patterns

- `@pytest.mark.django_db` for DB-backed tests
- Real DuckDB operations (read parquet, write to PG) — never mocked
- Task functions called directly (e.g., `ingest_to_bronze(ingestion_id=...)`)
- `self.request` mocked with `unittest.mock.patch` for retry exhaustion tests
- DuckDB timing wrapped with `time.monotonic()` (same as production)
- Bronze data tables truncated between tests via DuckDB or raw SQL

### Test matrix

| Test                                      | What it checks                                                                  |
| ----------------------------------------- | ------------------------------------------------------------------------------- |
| Idempotency (same upload)                 | COMPLETED ingestion exists → task returns, no new rows                          |
| Idempotency (same hash, different upload) | COMPLETED ingestion exists → logs warning, proceeds                             |
| Success                                   | BronzeIngestion COMPLETED, source == ingested, task succeeded, phases populated |
| Retry                                     | `duckdb.IOException` → RETRYING, task row retrying, error fields set            |
| Exhausted                                 | max_retries hit → BronzeIngestion FAILED, task row failed                       |
| `duckdb.CatalogException`                 | Not retried — surfaces immediately as FAILED                                    |
| `duckdb.ConstraintException`              | Not retried — surfaces immediately as FAILED                                    |
| `column_count` populated                  | JSONB key count stored on BronzeIngestion after success                         |
| Phase tracking                            | task_row.phase transitions through all phases on success                        |
| Phase tracking (failure)                  | task_row.phase frozen at the failure point                                      |
| Admin read-only                           | has_add/has_change return False on both admin classes                           |
| Pipeline end-to-end                       | CSV upload → parquet → bronze rows in DB, full lineage chain intact             |

---

## `column_count` — drift detection

Computed by sampling the first parquet row's JSON keys:

```python
column_count = conn.execute(
    "SELECT len(json_keys(to_json(t))) FROM read_parquet($1) t LIMIT 1",
    [upload.parquet_path]
).fetchone()[0]
```

### Why first row only

There will be no FM version drift mid-batch. The user selects the FM version before uploading, so all rows in a single upload have the same column structure. Sampling the first row is sufficient and avoids scanning the entire parquet file.

If FM version drift occurs between uploads (today FM24, tomorrow FM26), the two uploads will have different `column_count` values on their respective `BronzeIngestion` records. A query comparing `column_count` across `BronzeIngestion` rows detects the change before Silver breaks.

---

## `rejected_row_count` — forward-looking

Currently always 0. Reserved for a future public release where FM views may not have the columns or tables that bronze expects. At that point, rows that fail to insert (e.g., missing required columns) will be counted here rather than causing a complete task failure.

No `INSERT OR IGNORE` or `ON CONFLICT DO NOTHING` is ever used. Idempotency must be explicit and auditable — rejected rows are counted and logged, not silently swallowed.

---

## Implementation order

### 1. `core/choices.py` — shared status enums

Create `PipelineStatus` and `TaskStatus`. These are the foundation — every other step depends on them. Must be first.

### 2. `core/tasks.py` — shared PipelineTask

Extract `PipelineTask` from `landing/tasks.py` into `core/tasks.py`. Update `record_failure` to use shared enums. Landing zone tasks import from the new location.

### 3. `core/db.py` — PostgreSQL connection string utility

`get_pg_conn_string()` constructs the connection URL from `settings.DATABASES`. Needed by the bronze task's DuckDB ATTACH.

### 4. Migrate `landing/models.py` to shared enums

Swap local `TextChoices` for `PipelineStatus` and `TaskStatus` imports. No data change. Generate and run migration. This must happen before bronze models are created because bronze models reference `BronzeIngestion.landing_upload = ForeignKey("landing.LandingUpload")` — the landing model needs its shared enum in place.

### 5. `bronze/models.py` — BronzeIngestion, BronzeIngestionTask, BronzeIngestionPhase

Define the audit models and the layer-specific phase enum. FK to `LandingUpload` uses `on_delete=PROTECT` (never cascade audit trail). No `unique=True` on `landing_upload` — re-ingestion from the same upload must be possible.

### 6. Add `"apps.bronze"` to `INSTALLED_APPS`

Required before Django will discover bronze migrations or models.

### 7. Generate migration for bronze audit models

Standard `makemigrations`. Creates `bronze_ingestion` and `bronze_ingestiontask` tables in the `public` schema.

### 8. Write RunSQL migration for bronze data tables

Creates `bronze.squad_snapshot`, `bronze.scouting_snapshot`, `bronze.squad_matchstats_snapshot` via `CREATE TABLE`. Then `CREATE INDEX CONCURRENTLY` for each table. `atomic = False`. Every operation has `reverse_sql`.

### 9. `bronze/tasks.py` — task and dispatcher

`dispatch_bronze_ingestion(upload)` creates the `BronzeIngestion` row, dispatches the task, sets `celery_task_id`.

`ingest_to_bronze(self, ingestion_id)` implements the full lifecycle with phase tracking, DuckDB context manager, exception handling, and logging.

`run_bronze_append(upload, ingestion, conn, log_ctx)` encapsulates the DuckDB operations (ATTACH, COUNT, column_count sample, INSERT, COUNT_INGESTED). Named as an action (append, not insert or update) to reflect the append-only semantics.

`duckdb_pg_connect()` context manager ensures `conn.close()` even on exception.

### 10. Update `landing/tasks.py` — call dispatch_bronze_ingestion

At the end of `store_parquet`'s success path, after `upload.status = COMPLETED` is saved, call:

```python
from apps.bronze.tasks import dispatch_bronze_ingestion
dispatch_bronze_ingestion(upload)
```

The import is inside the function body (or at module level — acceptable coupling since bronze ingestion is a downstream consequence of a successful landing zone write). This is the intentional coupling point between the two apps.

### 11. `bronze/admin.py` — admin classes

`BronzeIngestionAdmin` with `landing_upload_link` column, read-only, no add/change permissions.

`BronzeIngestionTaskAdmin` with phase in list, standalone listing for cross-ingestion debugging.

Link column on `LandingUploadAdmin.list_display` pointing to `BronzeIngestionAdmin` changelist filtered by `landing_upload__id`.

### 12. `AGENTS.md` — bronze conventions

Add bronze section covering: status enums, DuckDB exception taxonomy, phase tracking, RunSQL migration rules, DuckDB context manager pattern, and testing patterns.

### 13. `bronze/tests.py` — test suite

Comprehensive tests covering all paths in the test matrix above. Uses real DuckDB and real PostgreSQL via Docker. Follows existing landing zone test patterns.

### 14. Run validation

```
uv run pytest apps/bronze/tests.py apps/landing/tests.py
uv run ruff check apps/
uv run python manage.py makemigrations --check
```

---

## What NOT to do

- Do not cast, clean, or normalize any value inside `ingest_to_bronze`. `£45.5M` enters bronze as `"£45.5M"`. Silver handles typing.
- Do not use `CREATE TABLE IF NOT EXISTS` in any task. Migrations own DDL.
- Do not use Redis pub/sub or Redis Streams to trigger bronze from landing zone. Direct Celery `.delay()` call only.
- Do not set `unique=True` on `BronzeIngestion.landing_upload` — re-ingestion from the same upload must be possible.
- Do not add new steps to `BronzeIngestionTask.Step` without explicit instruction.
- Do not put metadata columns (`simulation_source`, `ingame_date`, etc.) inside `raw_data`. They are structured columns, queryable and indexable.
- Do not `INSERT OR IGNORE`. Idempotency is explicit and counted, not silent.
- Do not use f-strings or `.format()` for values passed to DuckDB `execute()`. Only the table name (a controlled enum) may use `.format()`.
- Do not use `CREATE INDEX` without `CONCURRENTLY` on bronze data tables — they grow over time and table locks are costly.
- Do not put DuckDB operations outside the context manager. The database connection must be closed cleanly on every code path.
- Do not raise from the idempotency guard. Return early with FAILED status — raising burns retry budget pointlessly.
