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
The error detail lives on both the upload (LandingUpload.error_message) and LandingZoneTask.error_message.
record_failure writes obj.error_type/obj.error_message uniformly on ingestion-level objects.

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

## Shared status enums

core/choices.py defines two shared enums used across all pipeline layers:

  PipelineStatus   used by: LandingUpload, BronzeIngestion, future Silver/Gold
  TaskStatus       used by: LandingZoneTask, BronzeIngestionTask, future Silver/Gold

Import from core.choices, never redefine locally. If you add a new pipeline
model, use these enums for its status field — do not create a local Status
class. PipelineTask.record_failure in core/tasks.py is typed to these enums.

Never add a new status value to either enum without updating all models,
admin list_filter definitions, and the status machine documentation.

## Bronze app conventions

### BronzeIngestion Task lifecycle

BronzeIngestion.status uses PipelineStatus. Coarse-grained (COMPLETED/FAILED).
BronzeIngestionTask.status uses TaskStatus. Tracks the attempt lifecycle.
BronzeIngestionTask.phase uses BronzeIngestionPhase (in bronze/models.py)
for granular DuckDB operation tracking:

  attaching → counting_source → inserting → counting_ingested → completed

Phase is updated via save(update_fields=["phase"]) at each checkpoint.

### DuckDB connection management

Always use the duckdb_pg_connect() context manager from bronze/tasks.py.
Conn.close() must run in finally block — PostgreSQL connections leak otherwise.
All DuckDB operations (ATTACH, COUNT, INSERT, SELECT) happen inside the context
manager. All Django ORM operations happen outside.

### DuckDB INSERT pattern

  - Table name uses .format() — acceptable because snapshot_type is a
    controlled TextChoices enum, not user input.
  - All value parameters MUST use DuckDB positional placeholders ($1, $2, ...).
  - Never use f-strings or .format() for values passed to DuckDB execute().
  - DuckDB uses ::JSON, not ::JSONB. The PostgreSQL ATTACH layer handles
    DuckDB JSON → PG JSONB mapping.

### DuckDB exception taxonomy for ingest_to_bronze

Non-retryable (catch in dedicated except block, set FAILED immediately):
  duckdb.CatalogException       table missing → migration failure
  duckdb.ConstraintException    PK/FK/unique violation → data integrity
  duckdb.InvalidInputException  parquet schema mismatch → FM version drift
  duckdb.NotImplementedException DuckDB feature gap → not transient

Transient (fall through to generic except Exception + record_failure):
  duckdb.IOException           parquet read glitches, filesystem blips
  duckdb.OperationalError      PG timeouts, deadlocks, transient network
  duckdb.ConnectionException   PG unreachable at ATTACH time

Idempotency guard: early return with FAILED status, never raise.
record_failure + raise for all other exceptions. Do not swallow.

### Bronze data table creation

RunSQL migration (0002_bronze_data_tables.py) creates bronze schema + tables.
atomic = False — needed for CREATE INDEX CONCURRENTLY.
Every RunSQL must have reverse_sql for rollback.
CREATE TABLE and CREATE INDEX must be separate RunSQL operations.
Schema changes go in new RunSQL migrations — never edit 0002.

### Bronze progress fields

BronzeIngestion has parse_progress_current (default 0), parse_progress_total (default 4),
and parse_progress_description for frontend progress display — matching the LandingUpload
pattern. The @property parse_progress_percent computes percentage.

The 4-step phase-to-progress mapping:

  | Step | Phase | Description |
  |---|---|---|
  | 1 | attaching | "Attaching to PostgreSQL" |
  | 2 | counting_source | "Counting source parquet rows" |
  | 3 | inserting | "Inserting into bronze table" |
  | 4 | counting_ingested | "Verifying ingested row count" |
  | final | completed | "Done" |

Both phase (on BronzeIngestionTask) and progress fields (on BronzeIngestion) are updated
at each checkpoint via save(update_fields=[...]). ProgressRecorder is NOT used for bronze
— only DB fields are updated.

### Bronze frontend visibility

The bronze status poll view (bronze_status_poll in bronze/views.py) serves a partial
template (bronze/partials/bronze_status.html) that HTMX polls every 2s. It appears in
upload_status.html after the landing zone COMPLETED block. States:

  - No record yet — "Bronze: Pending" with zinc dot
  - Processing — amber pulsing dot, phase description with fade-slide-up animation,
    progress bar via c-ui.progress
  - Completed — green dot, row count
  - Failed — red dot, error type + message

The bronze_status.html partial omits hx-trigger when is_terminal, stopping the poll.

### Bronze URL config

apps/bronze/urls.py defines the bronze_status endpoint, included in config/urls.py.

### Testing bronze

Use real DuckDB and real PostgreSQL (Docker fm-warehouse). Never mock DuckDB.
CELERY_TASK_ALWAYS_EAGER = True (set in root conftest.py) — tasks run inline.
Test fixtures create parquet files with pandas, truncate bronze tables after
each test via DuckDB ATTACH. Always use duckdb_pg_connect() context manager
in fixtures to prevent connection leaks.

## Silver layer — core rules

The single drop gate is unique_id. Rows without unique_id are dropped before
INSERT and counted in SilverIngestion.dropped_row_count. No row reaches silver
without a unique_id.

There is no is_usable flag. No unusable_reason column. No quality tiers. If a
row is in silver, it is a valid typed observation.

Silver processes one bronze batch at a time. One BronzeIngestion → one
SilverIngestion. Cross-batch deduplication is gold's concern.

Deduplication within a batch: SELECT DISTINCT on all columns (exact row dedup).
Within a single FM export, two rows with the same (unique_id, club, division)
but different stats are legitimate distinct observations — keep both.
Only rows with identical values in ALL columns are removed.
No ROW_NUMBER() tiebreaker. No information loss.

### SilverIngestion Task lifecycle

dispatch_silver_ingestion(bronze_ingestion) is called from ingest_to_bronze
success path (inside BronzeIngestion COMPLETED block). It creates a PROCESSING
SilverIngestion record and chains transform_to_silver.delay().

SilverIngestion.status uses PipelineStatus. SilverIngestionTask.status uses TaskStatus.
SilverIngestionTask.phase uses SilverIngestionPhase (in silver/models.py):

  attaching → counting_source → transforming → inserting → verifying_insertion → completed

### Silver progress fields

Same pattern as bronze. parse_progress_total defaults to 5. The 6-phase
lifecycle maps to 5 progress steps:

| Step | Phase               | Description                      |
| ---- | ------------------- | -------------------------------- |
| 1    | attaching           | "Attaching to PostgreSQL"        |
| 2    | counting_source     | "Counting source bronze rows"    |
| 3    | transforming        | "Transforming and cleaning data" |
| 4    | inserting           | "Inserting into silver table"    |
| 5    | verifying_insertion | "Verifying ingested row count"   |
| final| completed           | "Done"                           |

### DuckDB connection — transform_to_silver

Import duckdb_pg_connect from bronze/tasks.py. Single ATTACH with alias pg_db.
Reads from pg_db.bronze.{type}, writes to pg_db.silver.{type}.
Both schemas live in the same PostgreSQL database — one ATTACH suffices.

Register 4 UDFs on connection before executing the SQL pipeline:
  parse_currency_min(VARCHAR) → INTEGER
  parse_currency_max(VARCHAR) → INTEGER
  parse_starts(VARCHAR) → INTEGER
  parse_subs(VARCHAR) → INTEGER

### SQL pipeline structure

build_silver_pipeline() generates a single SQL string with 4 CTEs:
  source → unpacked → cleaned → final → INSERT

Conditionally includes opponent/ingame_matchdate columns for
squad_matchstats_snapshot. The INSERT column list is also conditional.

Try_CAST for unique_id — non-numeric values become NULL and are filtered
by WHERE unique_id IS NOT NULL in the final CTE. Never crashes a batch.

Date parsing uses str_split + make_date (DD/MM/YYYY), never strptime.

### DuckDB exception taxonomy for transform_to_silver

Non-retryable (catch in dedicated except block, set FAILED immediately):
  duckdb.CatalogException       silver table missing → migration failure
  duckdb.ConstraintException    PK/FK/unique violation → data integrity
  duckdb.InvalidInputException  JSONB key missing → FM version drift
  duckdb.NotImplementedException DuckDB feature gap → not transient

Transient (fall through to generic except Exception + record_failure):
  duckdb.IOException           PG read/write glitches
  duckdb.OperationalError      PG timeouts, deadlocks, transient network
  duckdb.ConnectionException   PG unreachable at ATTACH time

Idempotency guard: early return with FAILED if a COMPLETED SilverIngestion
exists for the same bronze_ingestion. Never raise.

### Silver data table migration

0002_silver_data_tables.py creates silver schema + 3 tables:
  silver.squad_snapshot (52 columns)
  silver.scouting_snapshot (52 columns)
  silver.squad_matchstats_snapshot (54 columns — adds opponent, ingame_matchdate)

0003_add_new_columns.py ALTERs squad and scouting tables to add 29 new
columns (excluding recommendation → 80 columns each). matchstats is
unchanged (stays at 54). See AGENTS.md silver column mapping below
for the full list of new columns.

New RunSQL operations in new migrations — never edit 0001 or 0002.

Each table has 4 indexes:
  idx_silver_{table}_ingestion    on (ingestion_id)
  idx_silver_{table}_ingame_date  on (ingame_date)
  idx_silver_{table}_unique_id    on (unique_id)
  idx_silver_{table}_dedup        on (unique_id, club, division) — composite for gold

Matchstats gets 2 extra indexes:
  idx_silver_matchstats_opponent   on (opponent)
  idx_silver_matchstats_matchdate  on (ingame_matchdate)

### Dataset events

SILVER_COMPLETED and SILVER_FAILED are added to DatasetEvent.EventType in
apps/datasets/models.py. Created on task success/failure matching bronze pattern.

### dispatch_silver_ingestion

Called from bronze/tasks.py ingest_to_bronze COMPLETED block. Creates
SilverIngestion with status=PROCESSING, chains transform_to_silver.delay().

### dispatch pipeline wiring

dispatch_pipeline (landing/tasks.py) → ingest_to_bronze → on COMPLETED →
dispatch_silver_ingestion → transform_to_silver

### Silver audit

SilverIngestion.dropped_row_count tracks only NULL unique_id drops.
SELECT DISTINCT removals are NOT counted as "dropped" — they carry no
distinct information.
dropped_row_count = 0 is expected on clean exports. Non-zero warrants investigation.
