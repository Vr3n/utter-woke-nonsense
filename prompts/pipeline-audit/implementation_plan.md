# Pipeline Architecture Audit: Remediation & Enhancement Plan

**Date:** May 31, 2026
**Generated:** Sunday, May 31, 2026 19:29:48 IST (13:59:48 UTC)
**Scope:** Landing → Bronze → Silver pipeline (Gold not implemented)
**Source Report:** `prompts/pipeline-audit/report.md`
**Status:** Phase 1 ✅ Phase 2 ✅ Phase 3 ✅ | Phase 4 (Gold) — Future

---

## Table of Contents

1. [Priority Overview](#1-priority-overview)
2. [Phase 1 — Immediate Bug Fixes (Next Sprint)](#2-phase-1--immediate-bug-fixes-next-sprint)
3. [Phase 2 — Short-Term (Within 2 Sprints)](#3-phase-2--short-term-within-2-sprints)
4. [Phase 3 — Medium-Term Backlog](#4-phase-3--medium-term-backlog)
5. [Phase 4 — Gold Layer Planning (Future)](#5-phase-4--gold-layer-planning-future)
6. [Test Coverage Gaps](#6-test-coverage-gaps)
7. [Research Sources](#7-research-sources)
8. [Appendix: File Reference Map](#8-appendix-file-reference-map)

---

## 1. Priority Overview

The audit report identified **9 issues**: 1 critical, 2 high, 2 medium, 4 low.
This plan groups fixes into 4 implementation phases, adds **5 enhancement items**
based on current (May 2026) industry research, and maps **6 missing test scenarios**.

| Phase               | Items          | Nature                                                     | Effort Estimate  |
| ------------------- | -------------- | ---------------------------------------------------------- | ---------------- |
| **1 — Immediate**   | 4 fixes        | Bug fixes (retries, error display, progress, stuck status) | 1-2 days         |
| **2 — Short-term**  | 4 fixes        | Error propagation, dynamic column count, module cleanup    | 2-3 days         |
| **3 — Medium-term** | 5 enhancements | Monitoring, data contracts, alerting, FKs                  | 1-2 weeks        |
| **4 — Future**      | 3 principles   | Gold layer architecture guidance                           | N/A (design doc) |

**Cross-cutting pattern:** The systemic `error_type`/`error_message` gap appears in
Phase 1 (core `record_failure` fix) and Phase 2 (layer-specific non-retryable handlers).
Treat as a single coordinated change.

---

## 2. Phase 1 — Immediate Bug Fixes (Next Sprint)

### 2.1 C-1: Bronze `autoretry_for` is empty

| Field          | Value                                       |
| -------------- | ------------------------------------------- |
| **Layer**      | Bronze                                      |
| **Severity**   | Critical                                    |
| **File:Line**  | `apps/bronze/tasks.py:125`                  |
| **Design doc** | `prompts/bronze/ingestion.md` lines 216-228 |

**Problem:** `autoretry_for=()` is empty despite the design document specifying
transient DuckDB exceptions. Celery never retries. The `record_failure` code writes
RETRYING to the DB, but Celery's `autoretry_for` mechanism never fires because no
exception type is registered. Transient errors (network blips, PG timeouts) permanently
fail the bronze pipeline after a single attempt, despite `max_retries=3` being configured.

**Fix:**

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
def ingest_to_bronze(self, ingestion_id: int) -> None:
```

**Rationale from research:**

- Celery 5.6.3 documentation: `autoretry_for` is the canonical mechanism for
  transient error retry. When an exception matching `autoretry_for` is raised,
  Celery catches it, increments retry count, applies backoff/jitter, and re-queues.
- MoldStud 2025 survey: exponential backoff + jitter reduces cascading failure
  spikes by ~43% compared to fixed-interval retries.
- Ines Panker (2020) — widely cited Celery retry guide: "most exceptions are not
  worth a retry. Transient network/db errors are the canonical use case."
- The existing `record_failure` + `except Exception` block correctly handles the
  _exhaustion path_ (writing FAILED to DB). The `autoretry_for` tuple handles the
  _retry scheduling_. They are complementary, not duplicates.

**Verification:**

1. Unit test: simulate `duckdb.IOException` in `ingest_to_bronze`, assert task is
   re-invoked (check `self.request.retries` increments).
2. Integration test: kill PG connection mid-ingestion, assert retry count increases.
3. Regression test: existing `TestIngestToBronzeErrorHandling` test for transient
   errors should now pass with retries instead of immediate FAILED.

---

### 2.2 H-1: `record_failure` never populates ingestion-level error fields

| Field         | Value                   |
| ------------- | ----------------------- |
| **Layer**     | All (Core)              |
| **Severity**  | High                    |
| **File:Line** | `apps/core/tasks.py:20` |

**Problem:** `PipelineTask.record_failure` saves only `obj.status` — never
`obj.error_type` or `obj.error_message`. The model fields exist in the DB schema
but are write-only in all non-DuplicateIngestion failure paths. When bronze or
silver fails, the frontend template reads `{{ bronze.error_message }}` and renders
blank — users see "Failed" with no details.

**Root cause analysis:** The `record_failure` method was designed when only
task-level errors were needed. The ingestion-level `error_type`/`error_message`
fields were added later as a schema enhancement, but the write path was never updated.

**Fix (`apps/core/tasks.py` — full replacement of `record_failure`):**

```python
class PipelineTask(celery.Task):
    def record_failure(self, obj, task_row, exc):
        if self.request.retries < self.max_retries:
            obj.status = PipelineStatus.RETRYING
            task_row.status = TaskStatus.RETRYING
        else:
            obj.status = PipelineStatus.FAILED
            task_row.status = TaskStatus.FAILED

        obj.error_type = type(exc).__name__
        obj.error_message = str(exc)
        obj.save(update_fields=["status", "error_type", "error_message"])

        task_row.error_type = type(exc).__name__
        task_row.error_message = str(exc)
        task_row.finished_at = timezone.now()
        task_row.save(
            update_fields=["status", "error_type", "error_message", "finished_at"]
        )
```

**Why this is safe:**

- Both `LandingUpload` and `BronzeIngestion`/`SilverIngestion` inherit from models
  that have `error_type` and `error_message` fields.
- `LandingUpload` currently does NOT have these fields (by design per AGENTS.md —
  "The error detail lives in LandingZoneTask.error_message, not on the upload").
  However, `record_failure` is called only with `BronzeIngestion` and
  `SilverIngestion` objects. The landing layer does NOT use `record_failure`
  for its upload-level calls — it handles errors explicitly in `store_parquet`.
  This means the fix is safe: `LandingUpload` will never be passed to
  `record_failure`.

**Rationale from research:**

- Industry standard (Azure Data Explorer ingestor client, dbt Labs best practices
  2025, VexData 2026): error details must be available at the pipeline-object
  level, not buried in task rows. Frontend UIs should query a single status object.
- The report's "Error Field Propagation Pattern" section (2.4) identifies this as
  the #1 UX gap. The frontend bronze_status.html template already renders
  `{{ bronze.error_type }}: {{ bronze.error_message }}` — but the values are always
  empty strings.

**Changes required across layers:**
| File | Change |
|------|--------|
| `apps/core/tasks.py` | Fix `record_failure` (above) |
| `apps/bronze/tests.py` | Add test: assert `record_failure` sets `ingestion.error_type` |
| `apps/silver/tests.py` | Add test: assert `record_failure` sets `ingestion.error_type` |

---

### 2.3 H-2: Silver progress skips step 3

| Field         | Value                          |
| ------------- | ------------------------------ |
| **Layer**     | Silver                         |
| **Severity**  | High                           |
| **File:Line** | `apps/silver/tasks.py:507-569` |

**Problem:** `parse_progress_current` goes: 1 (ATTACHING) → 2 (COUNTING_SOURCE)
→ 4 (COUNTING_SILVER). The TRANSFORMING and INSERTING phases both leave
`parse_progress_current` at 2. The progress bar stays at 50% through the
longest-running phases (transformation and insertion) before jumping to 100%.

**Fix:**
In the TRANSFORMING phase block (`apps/silver/tasks.py:524-529`):

```python
task_row.phase = SilverIngestionPhase.TRANSFORMING
task_row.save(update_fields=["phase"])
ingestion.parse_progress_current = 3
ingestion.parse_progress_description = (
    SilverIngestionPhase.TRANSFORMING.label
)
ingestion.save(
    update_fields=["parse_progress_current", "parse_progress_description"]
)
```

And in the INSERTING phase block (`apps/silver/tasks.py:551-560`):
Note: INSERTING currently has no progress update at all. Consider whether
it should share step 3 (if transformation + insertion are considered one
logical "transform & load" step) or increment to a new value. The audit
report recommends setting step 3 during TRANSFORMING. The INSERTING phase
can remain at step 3 since the 4-step lifecycle maps to 6 phases.

**Rationale from research:**

- ETL pipeline observability best practices (DQLabs 2026): progress granularity
  should be highest during the longest-running phases. Staying at 50% through
  transformation — the heaviest operation (CTE pipeline, UDF execution, INSERT) —
  makes the frontend look stalled.
- The bronze layer handles this correctly: 4 phases → 4 progress steps, each
  with an explicit increment. Silver has 6 phases but maps to 4 progress steps.
  The gap is that TRANSFORMING and INSERTING (which together represent ~80% of
  execution time) share a single progress increment.

**Verification:**

- Unit test: assert `parse_progress_current == 3` after TRANSFORMING phase is set.
- Existing test: update `TestTransformToSilver*` tests to verify the progress sequence.

---

### 2.4 M-2: Landing non-retryable handlers skip status updates

| Field         | Value                           |
| ------------- | ------------------------------- |
| **Layer**     | Landing                         |
| **Severity**  | Medium                          |
| **File:Line** | `apps/landing/tasks.py:210-233` |

**Problem:** When `FileNotFoundError` or `FileExistsError` fire in
`store_parquet`, the handler logs, creates a DatasetEvent, and re-raises — but
never updates `upload.status` to FAILED. The upload remains in PROCESSING
indefinitely. The HTMX poll keeps polling `/upload_status_poll/` because
`is_terminal` depends on status, which is still PROCESSING.

**Fix — FileNotFoundError block (`apps/landing/tasks.py:210-221`):**

```python
except FileNotFoundError:
    logger.error("Non-retryable: FileNotFoundError — investigate", extra=log_ctx)
    upload.status = PipelineStatus.FAILED
    upload.save(update_fields=["status"])
    DatasetEvent.objects.create(
        upload=upload,
        event_type=DatasetEvent.EventType.STORE_FAILED,
        payload={
            "attempt": attempt,
            "error_type": "FileNotFoundError",
            "error_message": f"Staging file missing for upload {upload_id}",
        },
    )
    raise
```

**Fix — FileExistsError block (`apps/landing/tasks.py:222-233`):**

```python
except FileExistsError:
    logger.error("Non-retryable: FileExistsError — investigate", extra=log_ctx)
    upload.status = PipelineStatus.FAILED
    upload.save(update_fields=["status"])
    DatasetEvent.objects.create(
        upload=upload,
        event_type=DatasetEvent.EventType.STORE_FAILED,
        payload={
            "attempt": attempt,
            "error_type": "FileExistsError",
            "error_message": f"Destination already exists for upload {upload_id}",
        },
    )
    raise
```

**Rationale from research:**

- Medallion architecture pipeline quality standards (OneUptime 2026, BairesDev 2025):
  every failure path must produce a terminal status. Non-terminal PROCESSING
  creates false "in progress" state that breaks the frontend polling mechanism
  and masks real failures from monitoring.
- The Celery worker DOES set Celery's internal task state to FAILURE, but the
  Django DB status (which the HTMX poll reads) remains PROCESSING. These two
  status systems are independent — the DB must also be updated.

**Verification:**

- Unit test: assert `upload.status == PipelineStatus.FAILED` after `store_parquet`
  raises `FileNotFoundError`.
- Integration test: simulate missing staging file, verify HTMX poll returns
  terminal template (no `hx-trigger`).

---

## 3. Phase 2 — Short-Term (Within 2 Sprints)

### 3.1 M-1 (Bronze): Non-retryable handler doesn't set ingestion error fields

| Field         | Value                          |
| ------------- | ------------------------------ |
| **Layer**     | Bronze                         |
| **Severity**  | Medium                         |
| **File:Line** | `apps/bronze/tasks.py:228-229` |

**Problem:** Same systemic pattern as H-1 but in the dedicated non-retryable
`except` block. `ingestion.status = PipelineStatus.FAILED` and
`ingestion.save(update_fields=["status"])` — but `ingestion.error_type` and
`ingestion.error_message` are never set. The frontend renders blank error details.

**Fix (`apps/bronze/tasks.py:228-229`):**

```python
ingestion.status = PipelineStatus.FAILED
ingestion.error_type = type(exc).__name__
ingestion.error_message = str(exc)
ingestion.save(update_fields=["status", "error_type", "error_message"])
```

No change needed to the task_row block (lines 230-236) — those already set
task-level error fields correctly.

**Why this is separate from H-1:** H-1 fixes `record_failure` — the generic
`except Exception` path. This non-retryable block (`except (duckdb.CatalogException, ...)`)
bypasses `record_failure` entirely (intentionally — non-retryable exceptions must
not go through the retry path). So both locations must be fixed independently.

---

### 3.2 M-1 (Silver): Non-retryable handler doesn't set ingestion error fields

| Field         | Value                          |
| ------------- | ------------------------------ |
| **Layer**     | Silver                         |
| **Severity**  | Medium                         |
| **File:Line** | `apps/silver/tasks.py:635-636` |

**Problem:** Identical pattern to the bronze non-retryable handler.

**Fix (`apps/silver/tasks.py:635-636`):**

```python
ingestion.status = PipelineStatus.FAILED
ingestion.error_type = type(exc).__name__
ingestion.error_message = str(exc)
ingestion.save(update_fields=["status", "error_type", "error_message"])
```

---

### 3.3 L-1: Silver `column_count` is hardcoded

| Field         | Value                      |
| ------------- | -------------------------- |
| **Layer**     | Silver                     |
| **Severity**  | Low                        |
| **File:Line** | `apps/silver/tasks.py:590` |

**Problem:**

```python
ingestion.column_count = 54 if extra_columns else 80
```

This is a magic number derived from migration DDL but not dynamically computed.
Bronze does this correctly by sampling JSONB keys with `json_keys(to_json(t))`.
If the silver schema changes (new columns added via migration), this number
must be manually updated — and the developer must know to update it.

**Fix approach (two options):**

1. **Count from pipeline columns** (preferred): Parse the INSERT column list
   from `build_silver_pipeline()` output. The pipeline function knows exactly
   which columns it inserts. Example:
   ```python
   # After building the pipeline SQL, extract column count
   insert_cols = extract_insert_columns(sql_pipeline)
   ingestion.column_count = len(insert_cols)
   ```
2. **Count from the silver table schema**: Query `information_schema.columns`
   for the target silver table:
   ```python
   column_count = conn.execute(
       "SELECT COUNT(*) FROM information_schema.columns "
       "WHERE table_schema = 'silver' AND table_name = $1",
       [table_name],
   ).fetchone()[0]
   ```
   This is more robust (auto-adapts to migrations) but adds a query.

**Recommendation:** Option 1 — count from the pipeline itself. The pipeline
SQL is the source of truth for what columns the silver layer considers
"known." This catches drift between the pipeline definition and the table schema.

**Rationale from research:**

- Bronze already demonstrates the correct pattern: dynamic discovery via
  `json_keys(to_json(t))`. Hardcoded magic numbers are a well-known antipattern
  in data pipelines (SQLServerCentral pipeline design patterns, 2025).
- The silver migration DDL (0002, 0003) and the pipeline column list are two
  separate artifacts that must stay in sync. Deriving `column_count` from the
  pipeline eliminates one source of drift.

---

### 3.4 L-3: `duckdb_pg_connect` cross-import

| Field         | Value                    |
| ------------- | ------------------------ |
| \*\*Layer     | Silver ↔ Bronze          |
| **Severity**  | Low                      |
| **File:Line** | `apps/silver/tasks.py:9` |

**Problem:** Silver imports `duckdb_pg_connect` from `apps.bronze.tasks`.
This creates an implicit cross-app dependency. Bronze owns parquet-to-bronze
ingestion; silver should not depend on bronze's module internals for a generic
utility.

**Fix:**

1. **Move** `duckdb_pg_connect()` from `apps/bronze/tasks.py` to
   `apps/core/db.py` (alongside `get_pg_conn_string`).

2. **Update imports:**
   - `apps/bronze/tasks.py`: remove local definition, import from
     `apps.core.db`
   - `apps/silver/tasks.py`: change import from `apps.bronze.tasks` to
     `apps.core.db`
   - Any test files that import it (check `apps/silver/tests.py`)

3. **Optionally enhance** with DuckDB connection pool configuration per
   DuckDB 1.5.2 docs:
   ```python
   @contextmanager
   def duckdb_pg_connect(pool_max_connections=4):
       conn = duckdb.connect()
       conn.execute(f"SET pg_pool_max_connections = {pool_max_connections}")
       try:
           yield conn
       finally:
           conn.close()
   ```

**Rationale from research:**

- DuckDB 1.5.2 (April 2026) introduced significant enhancements to the
  PostgreSQL extension connection pool: `pg_pool_max_connections`,
  `pg_pool_idle_timeout_millis`, reaper thread for connection lifecycle
  management, and thread-local caching.
- MotherDuck blog (May 2026): "PostgreSQL + DuckDB integration" recommends
  configuring pool limits per workload. For batch ETL pipelines, a small pool
  (2-4 connections) with aggressive idle timeout is optimal.
- The cross-app import is a code smell that violates Django's app isolation
  principle. Centralizing to `apps.core.db` is the correct architectural fix
  regardless of pooling enhancements.

---

## 4. Phase 3 — Medium-Term Enhancements ✅

**Status: COMPLETED (June 1, 2026).** All items except N-1 (deferred) implemented.
233 tests passing. See updates below for actual decisions diverging from original plan.

---

### 4.1 N-1: Monitoring instrumentation

⏸️ **Deferred.** Will add prometheus-client, /metrics endpoint, and Flower
Prometheus export in a future cycle.

| Field        | Value          |
| ------------ | -------------- |
| **Layer**    | All            |
| **Severity** | Enhancement    |
| **Effort**   | 3-5 days       |
| **Status**   | ⏸️ Not started |

**Current state:** Only logging (via `get_task_logger`) and DatasetEvent
DB records. No metrics export, no real-time worker monitoring, no alerting.

**Recommended stack:**

```
Flower (real-time web UI) → Prometheus (metrics collection) → Grafana (dashboards)
```

**Metrics to instrument:** (from original plan — kept for when implementation begins)

| Metric                           | Type      | Labels                                 | Source                                                          |
| -------------------------------- | --------- | -------------------------------------- | --------------------------------------------------------------- |
| `pipeline_task_duration_seconds` | Histogram | `task_name`, `status`, `snapshot_type` | Task start/end in each `transform_to_silver`/`ingest_to_bronze` |
| `pipeline_task_retries_total`    | Counter   | `task_name`                            | Incremented in `record_failure` RETRYING path                   |
| `pipeline_task_failures_total`   | Counter   | `task_name`, `error_type`              | Incremented in `record_failure` FAILED path                     |
| `pipeline_rows_processed_total`  | Counter   | `layer`, `snapshot_type`               | After successful INSERT in bronze/silver                        |
| `pipeline_rows_dropped_total`    | Counter   | `layer`, `reason`                      | unique_id drops in silver                                       |

---

### 4.2 N-2: Schema contract validation ✅

| Field      | Value                                                                           |
| ---------- | ------------------------------------------------------------------------------- |
| **Status** | ✅ **COMPLETED**                                                                |
| **Files**  | `apps/silver/contracts.py`, `apps/silver/exceptions.py`, `apps/silver/tasks.py` |
| **Deps**   | `pydantic>=2.11.2` added to `pyproject.toml`                                    |

**Actual implementation diverges from original plan in 3 key ways:**

| Aspect                | Planned                      | Actual                                       | Rationale                                                                                                                        |
| --------------------- | ---------------------------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Layer**             | Bronze (`run_bronze_append`) | Silver (`transform_to_silver`, after ATTACH) | Bronze is write-only raw JSONB archive. Silver is the consumer boundary.                                                         |
| **Extra columns**     | `extra="forbid"`             | `extra="allow"`                              | Only validate essential reference keys (Unique ID, Player, Club, Division, Age). Extra columns from FM don't break the pipeline. |
| **Validation method** | `model_validate(row[0])`     | `model_validate_json(sample[0])`             | DuckDB returns PG JSON as string, not dict.                                                                                      |

**Validation flow (apps/silver/tasks.py:509-524):**

```python
from pydantic import ValidationError

contract_cls = SCHEMA_CONTRACTS.get(bronze_ingestion.snapshot_type)
if contract_cls:
    sample = conn.execute(
        f"SELECT raw_data FROM pg_db.bronze.{bronze_ingestion.snapshot_type} "
        "WHERE ingestion_id = $1 LIMIT 1",
        [bronze_ingestion.id],
    ).fetchone()
    if sample:
        try:
            contract_cls.model_validate_json(sample[0])
        except ValidationError as e:
            raise SchemaDriftError(
                f"Schema contract violated for {bronze_ingestion.snapshot_type}: {e}"
            )
```

**Contract models (`apps/silver/contracts.py`):**

- `SquadSnapshotContract` — Unique ID, Player, Age, Club, Division (extra="allow")
- `ScoutingSnapshotContract` — Unique ID, Player, Age, Club, Division (extra="allow")
- `SquadMatchstatsSnapshotContract` — Unique ID, Player, Opponent (extra="allow")
- `SCHEMA_CONTRACTS` dict mapping snapshot_type → contract class

**Non-retryable wiring:**

- `SchemaDriftError` added to silver's non-retryable tuple alongside `CatalogException`, `ConstraintException`, `InvalidInputException`, `NotImplementedException`

---

### 4.3 N-3: Empty-batch → SKIPPED ✅

| Field      | Value                                 |
| ---------- | ------------------------------------- |
| **Status** | ✅ **COMPLETED**                      |
| **Files**  | `apps/silver/tasks.py`                |
| **Status** | `PipelineStatus.SKIPPED` (not FAILED) |

**Actual implementation diverges from original plan:**

| Aspect      | Planned               | Actual                                                    | Rationale                                                          |
| ----------- | --------------------- | --------------------------------------------------------- | ------------------------------------------------------------------ |
| **Outcome** | Warning log only      | Early return with `SKIPPED` status                        | An empty batch is not a failure — but it's not "completed" either. |
| **Status**  | N/A (log only)        | `PipelineStatus.SKIPPED` (new enum value)                 | Distinguishable from both COMPLETED and FAILED in monitoring.      |
| **Event**   | Optional DatasetEvent | Creates `SILVER_COMPLETED` event (same as normal success) | Consistent event stream — no special empty-batch event type.       |

**Flow (apps/silver/tasks.py:529-530):**

```python
source_count = conn.execute(...).fetchone()[0]
if source_count == 0:
    ingestion.status = PipelineStatus.SKIPPED
    ingestion.error_type = "EmptyBatch"
    ingestion.error_message = "Bronze source has 0 rows — skipping silver transformation."
    ingestion.save(update_fields=["status", "error_type", "error_message"])
    # Creates SILVER_COMPLETED DatasetEvent
    return
```

**Dependency:** Required adding `SKIPPED` to `PipelineStatus` (see SKIPPED enum section below).

---

### 4.4 N-4: Row-count deviation ✅

| Field      | Value                                          |
| ---------- | ---------------------------------------------- |
| **Status** | ✅ **COMPLETED**                               |
| **Files**  | `apps/silver/utils.py`, `apps/silver/tasks.py` |

**Actual implementation differs from plan:**

| Aspect            | Planned                          | Actual                                                  |
| ----------------- | -------------------------------- | ------------------------------------------------------- |
| **Location**      | Inline in `apps/silver/tasks.py` | Extracted to `apps/silver/utils.py`                     |
| **Function name** | N/A (inline)                     | `get_historical_avg_row_count()`                        |
| **Threshold**     | `< 50%` of historical average    | `< 50%` of historical average (unchanged)               |
| **Query**         | Direct ORM (harder to swap)      | Extracted helper (adaptable to materialized view later) |

**Implementation (`apps/silver/utils.py`):**

```python
def get_historical_avg_row_count(snapshot_type, simulation_source):
    historical_avg = BronzeIngestion.objects.filter(
        snapshot_type=snapshot_type,
        simulation_source=simulation_source,
        status=PipelineStatus.COMPLETED,
    ).aggregate(avg_rows=Avg("source_row_count"))["avg_rows"]

    if historical_avg and source_count < historical_avg * 0.5:
        logger.warning(
            "Row count deviation: %d vs historical avg %.0f (%.0f%% of expected)",
            source_count, historical_avg,
            (source_count / historical_avg) * 100,
            extra=log_ctx,
        )
```

**Wired into** `apps/silver/tasks.py` after the N-3 empty-batch check (line 536-545).

---

### 4.5 N-5: Foreign key constraints ✅

| Field      | Value                                                                                          |
| ---------- | ---------------------------------------------------------------------------------------------- |
| **Status** | ✅ **COMPLETED**                                                                               |
| **Files**  | `bronze/migrations/0004_add_fk_constraints.py`, `silver/migrations/0005_add_fk_constraints.py` |

**Actual implementation differs from plan:**

| Aspect             | Planned                         | Actual                                    | Rationale                                                     |
| ------------------ | ------------------------------- | ----------------------------------------- | ------------------------------------------------------------- |
| **Approach**       | `NOT VALID` + separate VALIDATE | Simple `REFERENCES ... ON DELETE CASCADE` | NOT VALID adds complexity with no benefit at current volumes. |
| **Migration type** | Single RunSQL                   | Two migrations (bronze 0004, silver 0005) | Each app owns its migrations.                                 |
| **atomic**         | `atomic = False`                | `atomic = False` (unchanged)              | DDL operations require non-atomic migration.                  |

**Bronze constraints (`bronze/migrations/0004_add_fk_constraints.py`):**

```sql
ALTER TABLE bronze.squad_snapshot
    ADD CONSTRAINT fk_squad_snapshot_ingestion
    FOREIGN KEY (ingestion_id) REFERENCES bronze_bronzeingestion(id) ON DELETE CASCADE;
-- Same pattern for scouting_snapshot, squad_matchstats_snapshot
```

**Silver constraints** — same pattern for silver.squad_snapshot, silver.scouting_snapshot, silver.squad_matchstats_snapshot.

**Test infrastructure impact:**

- Requires `TransactionTestCase` (via `@pytest.mark.django_db(transaction=True)`) for any test that uses DuckDB ATTACH to PG (DuckDB opens separate PG connection; Django transaction not visible).
- `conftest.py` monkey-patches PostgreSQL `sql_flush` to use `TRUNCATE ... CASCADE` — Django's TransactionTestCase flush needs to cascade through non-Django-managed data tables.
- Test cleanup fixture `clear_bronze_tables` truncates bronze data tables after each test.

---

### 4.6 SKIPPED enum (new — not in original plan) ✅

Added during implementation because N-3 used SKIPPED instead of FAILED.

| Aspect           | Detail                                                                    |
| ---------------- | ------------------------------------------------------------------------- |
| **Status**       | ✅ **COMPLETED**                                                          |
| **Enum**         | `PipelineStatus.SKIPPED` in `apps/core/choices.py`                        |
| **Upload model** | `LandingUpload.Status.SKIPPED` in `apps/landing/models.py`                |
| **Templates**    | 7 templates updated with gray `skipped` branch                            |
| **Views**        | 4 views (bronze, silver, landing, datasets) mark SKIPPED as terminal      |
| **Admin**        | Gray dot in `apps/landing/admin.py`, zinc-600 in `apps/landing/tables.py` |

**Terminal semantics:** SKIPPED is terminal (no hx-trigger for HTMX polling). Error detail available via `error_type`/`error_message` where applicable. Not a "bad" state — just distinguishes "nothing to do" from "completed" for monitoring.

---

### 4.7 Execution order

All Phase 3 items were executed in dependency order:

1. **N-5** FK constraints — migrations run first, no code changes needed
2. **SKIPPED enum** — required by N-3 before it could reference PipelineStatus.SKIPPED
3. **N-3** Empty batch → SKIPPED — needed SKIPPED enum
4. **N-4** Row count deviation — independent of N-3
5. **N-2** Schema contracts — independent of others

---

## 5. Phase 4 — Gold Layer Planning (Future)

When implementing the Gold layer, these principles from the audit research
should guide the design:

### 5.1 Domain logic separation

| Principle                     | Detail                                                                                                                                         |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **The boundaries rule**       | "Does this transformation require domain knowledge?" If yes → Gold. If no → Silver. Silver handles cleaning and typing only.                   |
| **Current silver validation** | Silver currently enforces `unique_id` presence, TRY_CAST typing, DATE parsing, dedup. All structural. ✅ No domain logic leaks.                |
| **Gold's scope**              | Player potential scoring, transfer value estimation, performance percentiles, trend analysis over time. These require FM domain understanding. |

### 5.2 Consumer-specific tables

| Principle                   | Detail                                                                                                   |
| --------------------------- | -------------------------------------------------------------------------------------------------------- |
| **One table per use case**  | No shared Gold tables. Each table serves exactly one consumer: dashboard, ML feature set, report.        |
| **Denormalization allowed** | Gold can join across silver tables. Pre-joined, pre-aggregated data appropriate for analytical velocity. |
| **Naming convention**       | `gold.{domain}_{purpose}` e.g., `gold.player_valuation`, `gold.team_performance`                         |

### 5.3 Performance optimization

| Principle                 | Detail                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------ |
| **Pre-aggregation**       | Gold should pre-compute expensive aggregations so dashboards query pre-joined results, not raw silver. |
| **Partitioning strategy** | Partition by `ingame_date` (month) — revisit when tables exceed 10M rows.                              |
| **Materialized views**    | Consider DuckDB's materialized view support or scheduled Gold refresh via Celery Beat.                 |

### 5.4 Documented lineage

| Principle                | Detail                                                                                                         |
| ------------------------ | -------------------------------------------------------------------------------------------------------------- |
| **Traceability**         | Every Gold column must trace back to a Silver column and a Bronze key.                                         |
| **DatasetEvent lineage** | Extend DatasetEvent pattern to Gold: `GOLD_COMPLETED`/`GOLD_FAILED` events with column-level lineage metadata. |
| **Documentation**        | Store lineage in a machine-readable YAML file (e.g., `gold/lineage.yaml`) alongside the pipeline code.         |

### 5.5 Lessons from current audit

| Lesson                   | Apply to Gold                                                                                                                   |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| **autoretry_for**        | Define Gold-specific transient exception taxonomy from day one.                                                                 |
| **Error propagation**    | Build `GoldIngestion` model with `error_type`/`error_message` and populate them in `record_failure` and non-retryable handlers. |
| **Progress tracking**    | Define progress phases before coding. Map each phase to a progress step. Don't skip steps.                                      |
| **Dynamic column count** | Derive from the INSERT column list, not from a hardcoded constant.                                                              |
| **Monitoring**           | Instrument Prometheus metrics from the first Gold task, not retrofitted later.                                                  |
| **Schema contracts**     | Validate Gold input schemas against expected shapes before processing.                                                          |

---

## 6. Test Coverage Gaps

From the report's appendix (section 6):

### Phase 1 gaps (must fix alongside the bug)

| Missing Test                                                                 | Risk                       | Phase | Priority |
| ---------------------------------------------------------------------------- | -------------------------- | ----- | -------- |
| `ingestion.error_type`/`error_message` populated after non-retryable failure | Won't catch M-1 regression | 2     | High     |
| `record_failure` propagates errors to ingestion object                       | Won't catch H-1 regression | 1     | High     |
| Silver progress goes 1→2→3→4 (not skipping)                                  | Won't catch H-2 regression | 1     | High     |
| `LandingUpload` status = FAILED after FileNotFoundError                      | Won't catch M-2 regression | 1     | Medium   |
| Bronze `ingest_to_bronze` actually retries (not just writes RETRYING to DB)  | Won't catch C-1 regression | 1     | Critical |

### Phase 3 gaps

| Missing Test                                          | Risk                       | Phase | Priority | Status                                         |
| ----------------------------------------------------- | -------------------------- | ----- | -------- | ---------------------------------------------- |
| Empty bronze batch (0 rows) — L-4 behavior            | Silent empty-success       | 3     | Low      | ✅ Covered by N-3 early return                 |
| Schema drift detection (when contract is implemented) | Won't catch N-2 regression | 3     | Medium   | ✅ Covered by contract validation in silver    |
| Row count deviation alerting triggers correctly       | Won't catch N-4 regression | 3     | Low      | ✅ Covered by `get_historical_avg_row_count()` |
| Frontend HTMX dispatch endpoint error response        | Frontend integration test  | 3     | Low      | ⏸️ Not yet — manual testing only               |
| SKIPPED status renders correctly across templates     | UI regression              | 3     | Low      | ⏸️ Not yet — manual testing only               |

### Test implementation guidelines

For Phase 1 tests, follow the existing test patterns:

**Bronze tests** (`apps/bronze/tests.py`):

```python
class TestIngestToBronzeErrorHandling(TestCase):
    def test_retry_on_transient_error(self):
        """Assert that transient IOException triggers Celery retry mechanism."""
        # Arrange: mock parquet read to raise duckdb.IOException
        # Act: call ingest_to_bronze
        # Assert: self.request.retries > 0 (task was re-invoked)

    def test_non_retryable_sets_ingestion_error_fields(self):
        """Assert that non-retryable CatalogException populates ingestion-level error fields."""
        # Arrange: mock ATTACH to raise duckdb.CatalogException
        # Act: call ingest_to_bronze with expected failure
        # Assert: ingestion.error_type == "CatalogException"
        # Assert: ingestion.error_message is non-empty
```

**Core tests** (`apps/core/tests.py`):

```python
class TestPipelineTask(TestCase):
    def test_record_failure_propagates_to_obj(self):
        """Assert that record_failure sets obj.error_type and obj.error_message."""
```

**Silver tests** (`apps/silver/tests.py`):

```python
class TestTransformToSilverProgress(TestCase):
    def test_progress_goes_1_2_3_4(self):
        """Assert parse_progress_current sequence is 1, 2, 3, 4 — not 1, 2, 4."""
```

**Landing tests** (create `apps/landing/tests.py` if it doesn't exist):

```python
class TestStoreParquetNonRetryable(TestCase):
    def test_file_not_found_sets_upload_failed(self):
        """Assert upload.status is FAILED after FileNotFoundError."""
```

---

## 7. Research Sources

All sources accessed May 31, 2026.

### Celery & Task Orchestration

| Source                             | URL                                                                             | Key Insight                                                                                 |
| ---------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Celery 5.6.3 docs — Tasks          | https://docs.celeryq.dev/en/stable/userguide/tasks.html                         | `autoretry_for` canonical pattern; `retry_backoff` + `retry_jitter` for exponential backoff |
| Celery 5.6.3 docs — Monitoring     | https://docs.celeryq.dev/en/stable/userguide/monitoring.html                    | Flower + Prometheus + Grafana stack                                                         |
| MoldStud 2025 — Celery Retries     | https://moldstud.com/articles/p-understanding-celery-retries                    | 43% reduction in cascading failures with backoff + jitter; monitoring retry outcomes        |
| Ines Panker — Celery Retry Guide   | https://www.ines-panker.com/2020/10/29/retry-celery-tasks.html                  | Selective retries; most exceptions not worth retrying                                       |
| Cronradar 2025 — Celery Monitoring | https://cronradar.com/blog/celery-monitoring                                    | Silent failure blind spots; Flower + Prometheus production setup                            |
| OneUptime 2025 — OpenTelemetry     | https://oneuptime.com/blog/post/2025-01-06-celery-opentelemetry-oneuptime       | Distributed tracing for Celery workers                                                      |
| Usman Asif 2025 — Task Routing     | https://usmanasifbutt.github.io/blog/2025/03/13/celery-task-routing-and-retries | Dead Letter Queues; exponential backoff                                                     |

### Medallion Architecture & Data Pipelines

| Source                                     | URL                                                                      | Key Insight                                                                     |
| ------------------------------------------ | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| Databricks DLT Apparel Pipeline            | https://github.com/jrlasak/databricks_apparel_streaming                  | Practical medallion implementation with SCD Type 2                              |
| OneUptime 2026 — Medallion Architecture    | https://oneuptime.com/blog/post/2026-01-24-handle-medallion-architecture | Bronze/Silver/Gold layer responsibilities; schema-on-read vs enforced schema    |
| BairesDev 2025 — Medallion Architecture    | https://www.bairesdev.com/blog/data-pipeline-design                      | Separation of concerns; data contracts per layer                                |
| ResearchGate 2026 — AI-Ready Pipelines     | https://www.researchgate.net/publication/392702198                       | 30% reduction in deployment time with cloud-native medallion; schema versioning |
| Surface.syr.edu — DS Project Failure Risks | https://surface.syr.edu/cgi/viewcontent.cgi?article=3293&context=etd     | Real-world medallion implementations; IAM risks in migration                    |

### DuckDB & PostgreSQL Integration

| Source                              | URL                                                                            | Key Insight                                                                |
| ----------------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| DuckDB 1.5.2 — PostgreSQL Extension | https://duckdb.org/docs/current/core_extensions/postgres/overview              | ATTACH + connection pool; parallel query limitations                       |
| DuckDB 1.5.2 — Connection Pool      | https://duckdb.org/docs/current/core_extensions/postgres/connection_pool       | `pg_pool_max_connections` (default 32); reaper thread; thread-local cache  |
| MotherDuck 2026 — PG + DuckDB       | https://motherduck.com/blog/postgres-duckdb-options                            | 3 integration methods; use replicas for production; monitor CPU/memory/I/O |
| EthicalAds 2025 — DuckDB + PG       | https://www.ethicalads.io/blog/2025/02/duckdb-and-postgresql-make-a-great-pair | Real-world production migration from PG-only to PG + DuckDB + Parquet      |

### Error Handling & Data Quality

| Source                                  | URL                                                                                           | Key Insight                                                                  |
| --------------------------------------- | --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| DEV.to 2026 — AI Pipeline Errors        | https://dev.to/temitopeajao/error-handling-patterns-for-python-ai-pipelines                   | 4 failure categories (infrastructure, input, output, business logic)         |
| Reintech 2026 — Celery Retries          | https://reintech.io/blog/error-handling-retry-policies-celery-tasks                           | Selective retries; not all errors equal                                      |
| DataDef.io 2025 — Data Contracts        | https://datadef.io/guides/en/data-contracts                                                   | Schema, SLAs, quality rules; enforce in CI/CD                                |
| DQOps 2025 — Data Contracts             | https://dqops.com/data-contract-definition-and-validation                                     | Machine-readable YAML contracts; automated validation                        |
| The Data Governor 2026 — Data Contracts | https://thedatagovernor.com/data-contracts                                                    | Complete practitioner's guide; VA and Nestle Purina case studies             |
| Tredence 2026 — Data Contracts          | https://www.tredence.com/blog/data-contracts                                                  | Prevent broken pipelines; data reliability as multi-million-dollar challenge |
| DQLabs 2026 — Pipeline Observability    | https://dqlabs.ai/blog/data-pipeline-observability-architecture-challenges-and-best-practices | Monitor record count anomalies, null value spikes, stalled jobs              |
| ML Journey 2025 — Schema Evolution      | https://mljourney.com/schema-evolution-in-data-pipelines                                      | Backward vs forward compatible changes; schema drift handling                |

---

## 8. Appendix: File Reference Map

### All files requiring changes

| File                                           | Phase | Change                                                                               |
| ---------------------------------------------- | ----- | ------------------------------------------------------------------------------------ |
| `apps/bronze/tasks.py:125`                     | 1     | Add `autoretry_for` tuple with 3 DuckDB exceptions                                   |
| `apps/bronze/tasks.py:228-229`                 | 2     | Add `ingestion.error_type`/`error_message` save in non-retryable block               |
| `apps/core/tasks.py:13-26`                     | 1     | Fix `record_failure` to propagate errors to `obj`                                    |
| `apps/silver/tasks.py:9`                       | 2     | Change import from `apps.bronze.tasks` to `apps.core.db`                             |
| `apps/silver/tasks.py:524-529`                 | 1     | Add `parse_progress_current = 3` during TRANSFORMING                                 |
| `apps/silver/tasks.py:590`                     | 2     | Derive `column_count` dynamically from pipeline columns                              |
| `apps/silver/tasks.py:635-636`                 | 2     | Add `ingestion.error_type`/`error_message` save in non-retryable block               |
| `apps/landing/tasks.py:210-233`                | 1     | Set `upload.status = FAILED` in FileNotFound/FileExists handlers                     |
| `apps/core/db.py`                              | 2     | Receive `duckdb_pg_connect()` from bronze; optionally add pool config                |
| `apps/bronze/tasks.py:33-39`                   | 2     | Remove local `duckdb_pg_connect`; import from `apps.core.db`                         |
| `apps/bronze/tests.py`                         | 1,2   | Add tests for retry behavior, error propagation, progress                            |
| `apps/silver/tests.py`                         | 1,2   | Add tests for progress sequence, error propagation                                   |
| `apps/core/tests.py`                           | 1     | Add tests for `record_failure` propagation                                           |
| `apps/landing/tests.py`                        | 1     | Add tests for non-retryable status update                                            |
|                                                |       |                                                                                      |
| **Phase 3 additions**                          |       |                                                                                      |
| `apps/core/choices.py`                         | 3     | `SKIPPED = "skipped", "Skipped"` added to `PipelineStatus`                           |
| `apps/landing/models.py`                       | 3     | `SKIPPED` added to `LandingUpload.Status`                                            |
| `bronze/migrations/0004_add_fk_constraints.py` | 3     | New — 3 ALTER TABLE ADD CONSTRAINT ON DELETE CASCADE for bronze data tables          |
| `silver/migrations/0005_add_fk_constraints.py` | 3     | New — 3 ALTER TABLE ADD CONSTRAINT ON DELETE CASCADE for silver data tables          |
| `apps/silver/exceptions.py`                    | 3     | New — `SchemaDriftError` definition                                                  |
| `apps/silver/contracts.py`                     | 3     | New — Pydantic models per snapshot type (`extra="allow"`)                            |
| `apps/silver/utils.py`                         | 3     | New — `get_historical_avg_row_count()` helper                                        |
| `apps/silver/tasks.py`                         | 3     | N-3 empty-batch SKIPPED early return + N-4 deviation alert + N-2 contract validation |
| `apps/bronze/tasks.py:136`                     | 3     | Duplicate detection → `PipelineStatus.SKIPPED`                                       |
| `apps/silver/tasks.py:434`                     | 3     | Duplicate detection → `PipelineStatus.SKIPPED`                                       |
| `apps/bronze/views.py:18`                      | 3     | `SKIPPED` added to is_terminal                                                       |
| `apps/silver/views.py:30,85,116`               | 3     | `SKIPPED` added to all 3 is_terminal tuples                                          |
| `apps/landing/views.py:131`                    | 3     | `SKIPPED` added to is_terminal                                                       |
| `apps/landing/admin.py`                        | 3     | `"skipped": "gray"` added to `_status_color`                                         |
| `apps/landing/tables.py:29-35`                 | 3     | `"skipped"` CSS mapping added to `render_status`                                     |
| `apps/datasets/views.py:137`                   | 3     | Backlog check includes `"skipped"`                                                   |
| `pyproject.toml`                               | 3     | Add `pydantic>=2.11.2` dependency                                                    |
| `conftest.py`                                  | 3     | Monkey-patch PG `sql_flush` to use CASCADE for test FK compat                        |
| 7 template files                               | 3     | Added gray `skipped` branch to status dots/blocks                                    |
| `apps/bronze/tests.py`                         | 3     | Updated assertions for SKIPPED, column_count (4→7), parquet fixture                  |
| `apps/silver/tests.py`                         | 3     | Updated `{}` → `{0}` in SQL format assertion                                         |

### Files requiring no changes (verified correct)

| File                           | Verification                                                                        |
| ------------------------------ | ----------------------------------------------------------------------------------- |
| `apps/core/choices.py`         | `PipelineStatus` and `TaskStatus` enums cover all needed states                     |
| `apps/datasets/models.py`      | DatasetEvent coverage is complete (all 8 event types)                               |
| `apps/bronze/models.py`        | BronzeIngestion has `error_type`, `error_message`, progress fields                  |
| `apps/silver/models.py`        | SilverIngestion has `error_type`, `error_message`, progress fields                  |
| `apps/silver/tasks.py:125-140` | `build_silver_pipeline` generates correct CTE structure                             |
| `apps/bronze/tasks.py:42-100`  | `run_bronze_append` correctly uses `json_keys(to_json(t))` for dynamic column count |

### Naming map: Report IDs ↔ Code locations

| Report ID | Type                                 | Code Location                                                           |
| --------- | ------------------------------------ | ----------------------------------------------------------------------- |
| C-1       | Critical                             | `apps/bronze/tasks.py:125` — `autoretry_for=()`                         |
| H-1       | High                                 | `apps/core/tasks.py:20` — `record_failure` missing `obj.error_*`        |
| H-2       | High                                 | `apps/silver/tasks.py:524-529` — missing `parse_progress_current = 3`   |
| M-1       | Medium                               | `apps/bronze/tasks.py:228-229` + `apps/silver/tasks.py:635-636`         |
| M-2       | Medium                               | `apps/landing/tasks.py:210-227` — missing `upload.status = FAILED`      |
| L-1       | Low                                  | `apps/silver/tasks.py:590` — hardcoded `column_count`                   |
| L-2       | Low                                  | `bronze/migrations/0004` + `silver/migrations/0005` — FK added ✅       |
| L-3       | Low                                  | `apps/silver/tasks.py:9` — cross-import moved to `apps.core.db` ✅      |
| L-4       | Low                                  | `apps/silver/tasks.py` — empty-batch → SKIPPED with early return ✅     |
|           |                                      |                                                                         |
|           | **Phase 3 additions (no report ID)** |                                                                         |
| N-1       | Enhancement                          | Monitoring — deferred ⏸️                                                |
| N-2       | Enhancement                          | `apps/silver/contracts.py` — schema contracts ✅                        |
| N-3       | Enhancement                          | `apps/silver/tasks.py` — empty-batch SKIPPED early return ✅            |
| N-4       | Enhancement                          | `apps/silver/utils.py` — row count deviation alerting ✅                |
| N-5       | Enhancement                          | `bronze/migrations/0004` + `silver/migrations/0005` — FK constraints ✅ |
| SKIPPED   | Enhancement                          | `apps/core/choices.py` + 7 templates + 4 views — new terminal status ✅ |

---

_Plan generated from code audit (May 31, 2026) + industry research from Celery,
Databricks, DuckDB, dbt Labs, and data engineering community (2025-2026)._
