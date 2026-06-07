# Pipeline Architecture Audit: Bronze & Silver Layers

**Date:** May 2026
**Scope:** Landing → Bronze → Silver pipeline (Gold not implemented)
**Methodology:** Code review + comparison against medallion architecture best practices from
Databricks, Microsoft, and the data engineering community.

---

## 1. Executive Summary

The bronze and silver layers implement a structurally sound medallion architecture. The
separation of concerns is clear: bronze preserves raw parquet data with minimal
transformation, silver applies structured cleaning and typing. Both layers share a
consistent exception taxonomy, idempotency guards, and audit-trail patterns.

**One critical defect and several moderate gaps were found.**

The critical defect is in `bronze/tasks.py:125`: `autoretry_for=()` is empty despite
the design document specifying transient DuckDB exceptions. All exceptions are caught
manually, `record_failure` writes `RETRYING` to the database, but Celery never retries
because no exception type is registered for auto-retry. Transient errors immediately
go to FAILED after a single attempt.

The moderate gaps affect user-visible error display and progress tracking.

| Severity | Count | Key examples                                                                                                                                      |
| -------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Critical | 1     | `ingest_to_bronze` never retries — `autoretry_for=()` is empty                                                                                    |
| High     | 1     | `record_failure` never populates ingestion-level `error_type`/`error_message`                                                                     |
| Medium   | 3     | Silver progress skips step 3, landing non-retryable handlers don't set status, `FileNotFoundError`/`FileExistsError` handlers skip status updates |
| Low      | 4     | Hardcoded `column_count`, no FK on data tables, `duckdb_pg_connect` cross-import, no empty-batch guard                                            |

---

## 2. Research Context: Industry Standards

### 2.1 Medallion Architecture (Databricks / Microsoft)

The medallion architecture defines three layers with progressively improving data quality:

| Layer      | Characteristic                                                            | Our implementation                                                                  |
| ---------- | ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| **Bronze** | Raw, append-only, minimal transformation, schema-on-read, source of truth | ✅ Parquet stored as-is. JSONB preserves raw FM export. Append-only by design.      |
| **Silver** | Cleaned, typed, deduplicated, validated, non-aggregated record per entity | ✅ SELECT DISTINCT dedup, TRY_CAST typing, NULL unique_id dropped. No aggregations. |
| **Gold**   | Aggregated, business-domain-specific, consumer-optimized                  | ❌ Not implemented yet                                                              |

**Industry recommendation:** "Silver should handle cleaning, conforming, and typing —
operations any engineer can understand without domain knowledge. Gold handles joins,
aggregations, and business logic." (Cosmosthrace, 2026)

**Our alignment:** The silver layer correctly limits itself to structural cleaning
(type casting, null handling, dedup). No domain-level business logic (which would
belong in a future Gold layer).

### 2.2 Data Engineering Pipeline Quality Standards

From synthesis of multiple sources (dbt Labs, Celigo, SQLServerCentral, VexData):

| Standard                                                                  | Our status                                                                                                                   |
| ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **Idempotency** — same input produces same output regardless of run count | ✅ Bronze and silver both have idempotency guards (DuplicateIngestion) and use deterministic INSERT (not append-only random) |
| **Exception taxonomy** — separate non-retryable from transient errors     | ✅ Both layers define the 4 non-retryable DuckDB exceptions                                                                  |
| **Audit trail** — every run recorded with status, counts, errors          | ✅ Task rows per attempt, DatasetEvent on every terminal state                                                               |
| **Progress observability** — real-time visibility into pipeline state     | ✅ DB-backed progress fields with HTMX polling                                                                               |
| **Connection hygiene** — connections closed in all paths                  | ✅ Context manager with `finally: conn.close()`                                                                              |
| **Atomic file operations** — no partial writes                            | ✅ `Path.rename()` not `shutil.copy()`                                                                                       |
| **Schema evolution** — handle source changes without breaking             | ✅ Raw data in JSONB, silver uses TRY_CAST for safe coercion                                                                 |
| **Backward compatibility** — reprocess from source                        | ✅ Bronze data fully preserved, silver can be rebuilt                                                                        |
| **Monitoring hooks** — metrics, alerts, lineage                           | ⚠️ No formal monitoring instrumentation. Only logging + DatasetEvents.                                                       |
| **Data quality gates** — row-level validation with quarantine             | ⚠️ `unique_id` is the only quality gate. No schema validation, range checks, or quarantine tables.                           |

### 2.3 Shift-Left Validation Principle

Industry best practice (VexData, 2026): "Catch data quality issues at the earliest
possible point — ideally at ingestion, before any transformation occurs."

Our pipeline catches one quality issue (missing `unique_id`) at the silver layer.
Bronze stores JSONB without any validation. A schema change in the FM export that
renames a key column would not be detected until silver fails with a DuckDB
`InvalidInputException`. There is no schema-contract check at the bronze entry point.

### 2.4 Error Field Propagation Pattern

Industry standard (multiple sources): error details should be available at the
pipeline-object level, not buried in task rows. Frontend UIs and monitoring systems
should be able to query a single status object per pipeline run.

Our implementation stores error details **only** on task rows for non-DuplicateIngestion
failures. The ingestion-level `error_type`/`error_message` fields exist in the model
schema but are never populated on failure paths. Only the landing layer handles this
correctly by querying the task row explicitly in the view.

---

## 3. Layer-by-Layer Review

### 3.1 Landing Layer

#### Strengths

1. **Clean file-flow semantics.** Staging → rename is atomic at the OS level.
2. **Non-retryable exceptions** (`FileNotFoundError`, `FileExistsError`) are caught
   separately and correctly signal for investigation.
3. **Progress Recorder** and DB fields are updated in tandem at every checkpoint.
4. **HTMX polling partial** correctly uses DB status to stop polling on terminal states.

#### Issues

1. **Non-retryable handlers skip status updates.** When `FileNotFoundError` or
   `FileExistsError` fire in `store_parquet`, the handler logs, creates a DatasetEvent,
   and re-raises — but never calls `record_failure` and never updates
   `LandingZoneTask.status` to FAILED. The upload remains in PROCESSING indefinitely.
   (See `landing/tasks.py:210-227`)

2. **`LandingUpload` has no `error_type`/`error_message` fields.** This is a deliberate
   design choice documented in AGENTS.md ("The error detail lives in
   `LandingZoneTask.error_message`, not on the upload"). The view handles this correctly
   by querying `task_rows.filter(status="failed")`. However, this is inconsistent with
   bronze and silver, which DO have these fields but never populate them.

3. **`record_failure` creates a DatasetEvent but the non-retryable handlers don't.** The
   `STORE_FAILED` event is correctly created in both the non-retryable handlers (lines 212, 224) and the generic handler (line 240). This is correct.

### 3.2 Bronze Layer

#### Strengths

1. **Exception taxonomy is well-designed** — 4 non-retryable types caught in dedicated
   blocks, generic `Exception` for transients.
2. **Progress tracking is correct** — 1→2→3→4 maps cleanly to the 4 phases.
3. **DuckDB column count sampling** — uses `json_keys(to_json(t))` to dynamically detect
   column count rather than hardcoding it.
4. **Idempotency guard** cleanly returns FAILED without raising.
5. **Test coverage is strong** — 7 model tests, 3 dispatch tests, 1 idempotency test,
   3 error handling tests (transient, exhaustion, non-retryable), 3 success tests, 1 E2E test.
6. **`column_count` is dynamic** — derived from actual JSONB keys, not hardcoded.

#### Issues

1. **CRITICAL: `autoretry_for=()` is empty** (`bronze/tasks.py:125`). The design
   document (`prompts/bronze/ingestion.md` lines 216-228) specifies that
   `duckdb.IOException`, `duckdb.OperationalError`, and `duckdb.ConnectionException`
   should be in `autoretry_for`. The actual code has `()`. When a transient error occurs:
   - The `except Exception` block catches it
   - `record_failure` sets `ingestion.status = RETRYING` and `task_row.status = RETRYING`
   - The exception is re-raised
   - But since no exception type is in `autoretry_for`, Celery marks the task as FAILED
   - The database shows RETRYING, but Celery never re-invokes

   **Impact:** Transient errors (network blips, PG timeouts) permanently fail the bronze
   pipeline after a single attempt, despite `max_retries=3` being configured. The
   `record_failure` code writes RETRYING to the DB, creating an illusion that retries
   are happening when they are not.

2. **HIGH: `record_failure` never populates ingestion-level error fields** (systemic).
   When a failure occurs (either non-retryable or transient):
   - `task_row.error_type` and `task_row.error_message` are correctly set
   - `ingestion.error_type` and `ingestion.error_message` remain `""` (empty string)
   - The only place `ingestion.error_type` is set is the `DuplicateIngestion` guard

   This affects the bronze frontend template (`bronze_status.html:30-34`):

   ```django
   {% if bronze.error_message %}
     <span class="text-zinc-500">{{ bronze.error_type }}</span>: {{ bronze.error_message }}
   {% endif %}
   ```

   When bronze fails, the user sees "Bronze: Failed" with a red dot but **no error
   details** because `bronze.error_message` is empty.

3. **Non-retryable handler doesn't set ingestion error fields** (bronze/tasks.py:228-231).
   Same pattern — task_row gets the error, ingestion doesn't.

### 3.3 Silver Layer

#### Strengths

1. **SQL pipeline is well-structured** — 4 CTEs (source → unpacked → cleaned → final)
   with clear separation of concerns. Each CTE handles exactly one transformation step.
2. **UDFs are properly registered** and handle sentinel values ("N/A", "-", "") correctly.
3. **Date parsing uses `str_split` + `make_date`** as required — no `strptime`.
4. **`SELECT DISTINCT` at final stage** — exact row dedup without information loss.
5. **Event-driven frontend polling** — no wasted requests when no silver record exists.
6. **Test coverage is thorough** — 78 tests across models (15), pipeline SQL (18), UDFs
   (32), admin (6), dispatch (3), idempotency (1), error handling (1).

#### Issues

1. **HIGH: Silver progress tracking skips step 3** (silver/tasks.py:507-569).
   `parse_progress_current` goes: 1 (ATTACHING) → 2 (COUNTING_SOURCE) → 4 (COUNTING_SILVER).
   The TRANSFORMING and INSERTING phases both leave `parse_progress_current` at 2.
   The progress bar stays at 50% through the longest-running phases (transformation
   and insertion) before jumping to 100%.

   **Comparison:** Bronze correctly progresses 1→2→3→4 through its 4 phases.

2. **HIGH: `record_failure` gap** — same systemic issue as bronze. Error details never
   populate `SilverIngestion.error_type`/`error_message` on failure. The frontend
   template reads from these fields.

3. **MEDIUM: `column_count` is hardcoded** (`silver/tasks.py:590`):

   ```python
   ingestion.column_count = 54 if extra_columns else 80
   ```

   This is a magic number derived from the migration DDL but not dynamically computed.
   Bronze does this correctly by sampling JSONB keys. If the silver schema changes (new
   columns added), this number must be manually updated.

4. **MEDIUM: Non-retryable handler doesn't set ingestion error fields**
   (silver/tasks.py:635-640). Same pattern as bronze — task_row gets errors, ingestion
   doesn't.

5. **LOW: No empty-batch guard.** If bronze has 0 rows, silver inserts 0 rows and
   reports success with `dropped_count=0`. This is correct behavior but unusual —
   a warning log would help distinguish "normal empty" from "something went wrong."

6. **LOW: `duckdb_pg_connect()` imported from bronze** (`apps.bronze.tasks`), creating
   an implicit cross-app coupling. Moving to `apps.core.db` would be cleaner.

### 3.4 Cross-Cutting Patterns

#### Error field propagation: summary of the systemic gap

| Model                 | Has `error_type` | Has `error_message` | Populated on failure?                             |
| --------------------- | ---------------- | ------------------- | ------------------------------------------------- |
| `LandingUpload`       | No               | No                  | N/A (task row queried in view)                    |
| `LandingZoneTask`     | Yes              | Yes                 | ✅ By `record_failure` and non-retryable handlers |
| `BronzeIngestion`     | Yes              | Yes                 | ❌ Only for `DuplicateIngestion`                  |
| `BronzeIngestionTask` | Yes              | Yes                 | ✅ By `record_failure` and non-retryable handlers |
| `SilverIngestion`     | Yes              | Yes                 | ❌ Only for `DuplicateIngestion`                  |
| `SilverIngestionTask` | Yes              | Yes                 | ✅ By `record_failure` and non-retryable handlers |

**Root cause:** `PipelineTask.record_failure` in `core/tasks.py:13-26` saves only
`obj.status` — never `obj.error_type` or `obj.error_message`. The non-retryable handlers
in both layers follow the same pattern. The model fields exist in the schema but are
write-only in non-failure paths and never read-back by any code except `DuplicateIngestion`.

#### Autoretry configuration

| Task                  | `autoretry_for`                                        | Design doc specifies                                                             |
| --------------------- | ------------------------------------------------------ | -------------------------------------------------------------------------------- |
| `parse_file`          | `(ValueError, ParserError, EmptyDataError)`            | ✅ Match                                                                         |
| `store_parquet`       | `(OSError, IOError)`                                   | ✅ Match                                                                         |
| `ingest_to_bronze`    | `()`                                                   | ❌ **Empty** — should contain IOException, OperationalError, ConnectionException |
| `transform_to_silver` | `(IOException, OperationalError, ConnectionException)` | ✅ Match                                                                         |

#### DuckDB connection management

All layers use the same `duckdb_pg_connect()` context manager pattern from
`bronze/tasks.py:33-39`. It guarantees `conn.close()` in `finally`. Silver imports it
from bronze — a cross-app dependency that should be centralized.

#### DatasetEvent coverage

Every terminal pipeline state produces a DatasetEvent. Coverage is complete:

- PARSE_COMPLETED / PARSE_FAILED
- STORE_COMPLETED / STORE_FAILED
- BRONZE_COMPLETED / BRONZE_FAILED
- SILVER_COMPLETED / SILVER_FAILED

Each event payload includes relevant metadata (attempt number, duration, row counts,
error details).

---

## 4. Issues Found

### Critical (1)

| ID      | Layer  | Issue                                                                                                                                                                                                                                        | File:Line             | Fix                                                                                                                             |
| ------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **C-1** | Bronze | `autoretry_for=()` prevents all retries. Transient DuckDB errors (IOException, OperationalError, ConnectionException) immediately go to FAILED after one attempt despite `max_retries=3`. The DB shows RETRYING but Celery never re-invokes. | `bronze/tasks.py:125` | Add `duckdb.IOException, duckdb.OperationalError, duckdb.ConnectionException` to `autoretry_for` tuple, matching the design doc |

### High (2)

| ID      | Layer  | Issue                                                                                                                                              | File:Line                 | Fix                                                                                                                                                            |
| ------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **H-1** | All    | `record_failure` never saves `error_type`/`error_message` on the pipeline object. Bronze and silver templates show "Failed" with no error details. | `core/tasks.py:20`        | Add `obj.error_type = type(exc).__name__; obj.error_message = str(exc); obj.save(update_fields=["status", "error_type", "error_message"])` to `record_failure` |
| **H-2** | Silver | Progress skips step 3. `parse_progress_current` goes 1→2→4. Stays at 50% through transformation (the longest phase).                               | `silver/tasks.py:507-569` | Set `parse_progress_current = 3` during TRANSFORMING phase, before calling `build_silver_pipeline`                                                             |

### Medium (2)

| ID      | Layer   | Issue                                                                                                                      | File:Line                                            | Fix                                                                                                                                                                                      |
| ------- | ------- | -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **M-1** | All     | Non-retryable handlers don't set ingestion-level error fields. Same symptom as H-1 — frontend shows blank error details.   | `bronze/tasks.py:228-231`, `silver/tasks.py:635-640` | Add `ingestion.error_type = type(exc).__name__; ingestion.error_message = str(exc); ingestion.save(update_fields=["status", "error_type", "error_message"])` in each non-retryable block |
| **M-2** | Landing | Non-retryable handlers for `FileNotFoundError`/`FileExistsError` skip status updates entirely. Upload stays in PROCESSING. | `landing/tasks.py:210-227`                           | Set `upload.status = FAILED` and save before re-raising (or call `record_failure`)                                                                                                       |

### Low (4)

| ID      | Layer  | Issue                                                                                                   | File:Line                               |
| ------- | ------ | ------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| **L-1** | Silver | `column_count` hardcoded (54/80). Not dynamically derived from pipeline columns.                        | `silver/tasks.py:590`                   |
| **L-2** | All    | Silver data tables have no FOREIGN KEY constraints on `ingestion_id`. Orphan rows possible at DB level. | `migrations/0002_silver_data_tables.py` |
| **L-3** | Silver | `duckdb_pg_connect()` imported from `bronze.tasks`, not a shared utility.                               | `silver/tasks.py:9`                     |
| **L-4** | Silver | No empty-batch guard or warning when bronze has 0 rows.                                                 | `silver/tasks.py`                       |

---

## 5. Recommendations

### Immediate fixes (next sprint)

1. **C-1**: Add transient DuckDB exceptions to `ingest_to_bronze`'s `autoretry_for`.
2. **H-1**: Fix `record_failure` in `core/tasks.py` to propagate errors to the pipeline object.
3. **H-2**: Add `parse_progress_current = 3` to silver's TRANSFORMING phase.
4. **M-1**: Fix non-retryable handlers to set ingestion-level error fields.

### Short-term (within 2 sprints)

5. **M-2**: Fix `store_parquet`'s non-retryable handlers to set `upload.status = FAILED`.
6. **L-1**: Derive `column_count` dynamically from the INSERT column list in `build_silver_pipeline`.

### Medium-term backlog

7. **Data contracts**: Add a schema contract validation step at the bronze entry point.
   Validate that incoming parquet JSONB keys match expected FM export column names
   before processing. This catches FM version drift before it reaches silver.
8. **Centralize `duckdb_pg_connect()`** in `apps/core/db.py`.
9. **Add row-count deviation alerting**: Compare actual vs expected row counts and
   log warnings when volume deviates significantly.
10. **No partitioning for silver tables** — acceptable at current scale but worth
    revisiting when table sizes grow.

### Gold layer planning (future)

When implementing the Gold layer, observe these principles from industry research:

- **The boundaries rule**: "Does this transformation require domain knowledge?" If yes,
  it belongs in Gold. Silver handles cleaning and typing only.
- **Consumer-specific**: Each Gold table serves one business use case.
- **Performance-optimized**: Pre-aggregated, potentially denormalized.
- **Documented lineage**: Every Gold column should trace back to Silver.

---

## 6. Appendix: Test Coverage Summary

### Bronze (662 lines, 30 tests)

| Test class                        | Tests | What it covers                                                                     |
| --------------------------------- | ----- | ---------------------------------------------------------------------------------- |
| `TestBronzeIngestionModel`        | 7     | Default status, FK, duration, str, defaults                                        |
| `TestBronzeIngestionTaskModel`    | 7     | Default status, phase, FK, unique constraint, duration, hostname                   |
| `TestDispatchBronzeIngestion`     | 3     | Creates PROCESSING, celery_task_id, correct ingestion_id                           |
| `TestIngestToBronzeIdempotency`   | 1     | Duplicate guard returns FAILED                                                     |
| `TestIngestToBronzeSuccess`       | 3     | Inserts rows, SUCCEEDED task, phase tracking                                       |
| `TestIngestToBronzeErrorHandling` | 3     | Transient (IOException), exhaustion (4 attempts), non-retryable (CatalogException) |
| `TestBronzeIngestionPhase`        | 1     | All 5 phase values                                                                 |
| `TestEndToEndPipeline`            | 1     | Full CSV→parquet→bronze                                                            |
| `TestBronzeIngestionAdmin`        | 3     | No add, change renders, list renders                                               |
| `TestBronzeIngestionTaskAdmin`    | 3     | No add, read-only change, list renders                                             |

### Silver (674 lines, 78 tests)

| Test class                           | Tests | What it covers                                                                                                                     |
| ------------------------------------ | ----- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `TestSilverIngestionModel`           | 8     | Default status, FK, duration, str, dropped_row_count default, progress percent                                                     |
| `TestSilverIngestionTaskModel`       | 7     | Default status, phase, FK, unique constraint, duration, hostname                                                                   |
| `TestSilverIngestionPhase`           | 1     | All 6 phase values                                                                                                                 |
| `TestBuildSilverPipeline`            | 18    | SQL structure, CTEs, TRY_CAST, SELECT DISTINCT, $1 placeholder, NULL filter, matchstats extras, new columns, recommendation absent |
| `TestDocstringUDFs`                  | 25    | parse_currency_min (11), parse_currency_max (4), parse_starts (6), parse_subs (7)                                                  |
| `TestParseHeight`                    | 7     | Height parsing with various inputs                                                                                                 |
| `TestSilverIngestionAdmin`           | 6     | No add, change renders, list renders (both models)                                                                                 |
| `TestDispatchSilverIngestion`        | 3     | Creates PROCESSING, celery_task_id, correct ingestion_id                                                                           |
| `TestTransformToSilverIdempotency`   | 1     | Duplicate guard returns FAILED                                                                                                     |
| `TestTransformToSilverErrorHandling` | 1     | Non-retryable (CatalogException) surfaces immediately                                                                              |

### Test coverage gaps

| Missing test                                                                 | Risk                             |
| ---------------------------------------------------------------------------- | -------------------------------- |
| `ingestion.error_type`/`error_message` populated after non-retryable failure | Won't catch M-1 regression       |
| `record_failure` propagates errors to ingestion object                       | Won't catch H-1 regression       |
| Silver progress goes 1→2→3→4 (not skipping)                                  | Won't catch H-2 regression       |
| Empty bronze batch (0 rows)                                                  | Won't catch L-4 behavior         |
| Frontend HTMX dispatch endpoint error response                               | Frontend integration test needed |
| `LandingUpload` status set to FAILED after FileNotFoundError                 | Won't catch M-2 regression       |

---

_Report generated from code review and industry research conducted May 31, 2026._
_Research sources: Databricks medallion architecture docs, Azure Databricks best practices,
Cosmosthrace architecture guide, dbt Labs ETL best practices, VexData pipeline testing
strategy, SQLServerCentral pipeline design patterns._
