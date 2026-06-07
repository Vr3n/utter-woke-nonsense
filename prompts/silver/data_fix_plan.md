# Silver Layer — Data Transformation Bug Fix Plan

## Executive Summary

An audit of the silver `transform_to_silver` pipeline against actual bronze database
contents (FM24 export data) revealed **3 critical data-corruption bugs**, **1 high-severity
data-loss bug**, and **2 observability gaps**. All three snapshot types (`squad_snapshot`,
`scouting_snapshot`) are affected; `squad_matchstats_snapshot` has zero rows in production
but the same code paths apply.

The root cause is a single pattern: **`build_silver_pipeline()` treats all snapshot types
identically**, but the actual FM export has different schemas — different date formats,
different JSON key names, different value suffixes per snapshot type.

---

## Bug Inventory

| ID  | Severity | Component                        | Symptom                                                            | Root Cause                                                                        |
| --- | -------- | -------------------------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| B1  | CRITICAL | `expires` date parsing           | All squad_snapshot expiry dates become NULL                        | Squads use ISO dates (`2027-06-30`), code assumes DD/MM/YYYY (`str_split(…,'/')`) |
| B2  | CRITICAL | `wage_min`/`wage_max`            | ALL wage values across both snapshot types become NULL             | `" p/w"` suffix not stripped before currency parsing                              |
| B3  | HIGH     | `nation_of_birth`                | Squad nation_of_birth always NULL                                  | Squad uses JSON key `"Nation"`, code extracts `"Nation of Birth"`                 |
| B4  | MEDIUM   | Phantom columns                  | `height`, `best_pos`, footedness, `offsides` always NULL in silver | These 6 JSON keys don't exist in any source data                                  |
| B5  | MEDIUM   | `personality` not passed through | Squad `Personality` field not extracted at all                     | No `json_extract_string` for `$."Personality"` in unpacked CTE                    |
| B6  | LOW      | Missing Prometheus counter       | Non-retryable exceptions don't increment `pipeline_task_failures`  | Non-retryable except block sets FAILED but skips the counter                      |

---

## B1 — Expires Date Format (CRITICAL)

### Evidence

```
bronze.squad_snapshot:      28 rows, 28 ISO dates   → "2027-06-30"  (YYYY-MM-DD)
bronze.scouting_snapshot:  1601 rows, 1539 DD/MM/YYYY → "30/6/2026"  (DD/MM/YYYY)
                            57 rows NULL, 5 rows 'N/A'
```

### Current code (`apps/silver/tasks.py:256-265`)

```sql
CASE
    WHEN expires_str IS NULL THEN NULL
    WHEN expires_str IN ('N/A', '-', 'Out of contract', 'Retired') THEN NULL
    ELSE make_date(
        CAST(str_split(expires_str, '/')[3] AS INT),
        CAST(str_split(expires_str, '/')[2] AS INT),
        CAST(str_split(expires_str, '/')[1] AS INT)
    )
END AS expires
```

For `"2027-06-30"`: `str_split('2027-06-30', '/')` → `['2027-06-30']` (single element,
no `/` delimiter). DuckDB's `list_extract` with out-of-bounds index returns NULL, so
`make_date(NULL, NULL, NULL)` → NULL. **All 28 squad expiry dates silently lost.**

### Fix

Replace the single date-parsing expression with a snapshot-type-aware branch:

```sql
CASE
    WHEN expires_str IS NULL                                  THEN NULL
    WHEN expires_str IN ('N/A', '-', 'Out of contract', 'Retired') THEN NULL
    WHEN expires_str ~ '^\d{4}-\d{2}-\d{2}$'                  THEN CAST(expires_str AS DATE)
    ELSE make_date(
        CAST(str_split(expires_str, '/')[3] AS INT),   -- year
        CAST(str_split(expires_str, '/')[2] AS INT),   -- month
        CAST(str_split(expires_str, '/')[1] AS INT)    -- day
    )
END AS expires
```

Alternative: Use DuckDB's `try_strptime` with multiple format strings:

```sql
COALESCE(
    TRY_STRPTIME(expires_str, '%Y-%m-%d')::DATE,
    TRY_STRPTIME(expires_str, '%d/%m/%Y')::DATE,
    TRY_STRPTIME(expires_str, '%-d/%-m/%Y')::DATE
)
```

The `regex` approach is preferred because:

- DuckDB's `TRY_STRPTIME` with `%-d` / `%-m` may not be available in all versions
- The regex + `make_date` pattern is already established in the codebase
- No new function registration needed (regex exists in DuckDB SQL)

The same fix applies to `ingame_matchdate_str` in the matchstats branch
(`apps/silver/tasks.py:137-142`), which uses identical `str_split` logic.

### Verification

1. Run unit test: `build_silver_pipeline` output contains the `~'^\d{4}-\d{2}-\d{2}$'` branch
2. Run integration test: insert squad data with ISO dates, verify `expires` is correct date
3. Query: `SELECT expires FROM silver.squad_snapshot WHERE expires IS NOT NULL LIMIT 1`

---

## B2 — Wage `p/w` Suffix Not Stripped (CRITICAL)

### Evidence

```
squad_snapshot wage samples:   "£4.6K p/w", "£1.3K p/w", "£825 p/w"
scouting_snapshot wage samples: "£230 - £320 p/w", "£110 - £140 p/w"
```

### Root cause

`_apply_multiplier()` checks `endswith("K")` / `endswith("M")`, but the actual value
after regex-cleaning is `"4.6K p/w"`, which does NOT end with `"K"` — it ends with `"W"`.
The stripped value `"320 p/w"` then fails `int(float(...))` → ValueError → returns None.

The regex `re.sub(r"^[^0-9.]+", "", first)` only strips the _leading_ currency symbol.
There is no suffix stripping at all.

### Impact

**Every single wage value across both snapshot types becomes NULL.** This is the most
widespread data-loss bug — 1629 rows affected.

### Fix

Strip the `p/w` suffix before multiplier parsing. There are two approaches:

**Approach A: In `parse_currency_min`/`parse_currency_max` (minimal change)**

```python
def parse_currency_min(val):
    if not val:
        return None
    val = val.strip()
    if val in ("Unknown", "N/A", "-", ""):
        return None
    parts = val.split(" - ")
    first = parts[0]
    cleaned = re.sub(r"^[^0-9.]+", "", first)
    # NEW: strip trailing p/w, K, M after stripping prefix
    cleaned = cleaned.rstrip("p/w ")  # or more robustly:
    cleaned = re.sub(r'\s*p/w$', '', cleaned)
    if not cleaned:
        return None
    return _apply_multiplier(cleaned)
```

**Approach B: Create a dedicated `parse_wage_min`/`parse_wage_max` UDF** that strips
the `p/w` suffix before delegating to `_apply_multiplier`. This keeps the currency
UDFs pure and avoids scope creep.

**Approach C (recommended):** Strip `p/w` in the SQL pipeline itself, before passing
to the UDF, plus create dedicated parse_wage UDFs:

```sql
parse_currency_min(NULLIF(REGEXP_REPLACE(wage_str, '\s*p/w$', ''), '')) AS wage_min,
```

**Recommendation: Approach A** — minimal change, single responsibility. The UDF is
already called `parse_currency_min/max` and is applied to both `transfer_value_str`
and `wage_str`. A dedicated suffix-strip at the top of the UDF is the simplest fix.
The `parse_wage` approach adds API surface without benefit since the suffix is the
only difference.

Add after the sentinel checks:

```python
    # Strip FM's " p/w" suffix from wages
    val = re.sub(r'\s*p/w$', '', val)
```

This affects both `parse_currency_min` and `parse_currency_max`. It's safe for
transfer values too since they never have a `p/w` suffix (the regex won't match).

### Verification

1. Unit test: `parse_currency_min("£4.6K p/w") == 4600`
2. Unit test: `parse_currency_max("£230 - £320 p/w") == 320`
3. Integration test: run pipeline against scouting data, verify `wage_min`/`wage_max`
   are populated
4. Grafana: `pipeline_rows_dropped{layer="silver", reason="parse_failure"}` should
   drop sharply after deploy

---

## B3 — `Nation` vs `Nation of Birth` Key Mismatch (HIGH)

### Evidence

```
bronze.squad_snapshot:    "Nation": "ESP"   (key: "Nation")
bronze.scouting_snapshot: "Nation of Birth": "Colombia" (key: "Nation of Birth")
```

### Current code (`apps/silver/tasks.py:160`)

```python
json_extract_string(raw_data, '$."Nation of Birth"') AS nation_str,
```

This extracts NULL for all squad rows since the key is `"Nation"`, not
`"Nation of Birth"`.

### Fix

Use `COALESCE` to try both keys, in order (nation-specific before generic):

```sql
COALESCE(
    NULLIF(json_extract_string(raw_data, '$."Nation of Birth"'), ''),
    NULLIF(json_extract_string(raw_data, '$."Nation"'), '')
) AS nation_str,
```

This handles both snapshot types in a single expression. For scouting rows,
`$."Nation of Birth"` matches (returns the value), `COALESCE` short-circuits.
For squad rows, `$."Nation of Birth"` returns NULL, `$."Nation"` matches.

### Verification

1. Unit test: Query pipeline-generated SQL contains `COALESCE(...'$."Nation of Birth"'..., ...'$."Nation"'...)`
2. Integration test: Insert squad rows, verify `nation_of_birth = 'ESP'`
3. Existing scouting rows unaffected

---

## B4 — Phantom Columns (MEDIUM)

### Evidence

Six JSON keys that the silver pipeline extracts don't exist in ANY bronze source data:

| JSON key             | Extracted in   | Exists in Bronze |
| -------------------- | -------------- | ---------------- |
| `$."Height"`         | `unpacked` CTE | ✗ (0 rows)       |
| `$."Best Pos"`       | `unpacked` CTE | ✗ (0 rows)       |
| `$."Preferred Foot"` | `unpacked` CTE | ✗ (0 rows)       |
| `$."Left Foot"`      | `unpacked` CTE | ✗ (0 rows)       |
| `$."Right Foot"`     | `unpacked` CTE | ✗ (0 rows)       |
| `$."Off"`            | `unpacked` CTE | ✗ (0 rows)       |

### Assessment

These columns produce NULLs silently — no crash, no data corruption. They were added
in the `column_expansion_plan.md` (migration 0003) based on the assumption they'd be
in the FM export. For this particular save/simulation they are absent. They may appear
in other FM versions or export templates.

### Decision

**Do NOT remove the columns from the pipeline.** The cost of keeping them is zero
(json_extract_string returns NULL for missing keys, the pipeline still runs). The
cost of removing and re-adding is a migration cycle per FM version.

**Instead, add a schema-drift log** (see Observability section below) that warns when
all rows in a batch have NULL for a given extracted column. This surfaces the issue
without breaking the pipeline.

---

## B5 — `Personality` Not Extracted (MEDIUM)

### Evidence

Squad squad data has `"Personality": "Model Citizen"`, `"Personality": "Spirited"`, etc.
The silver pipeline doesn't extract this field at all — no `$."Personality"` in the
`unpacked` CTE, no column in the silver schema.

### Decision

This is a **feature gap, not a bug fix**. The `Personality` field is FM24-specific
and wasn't in the original column expansion plan. It should be added in a future
iteration alongside a dashboard that uses it. Do not include in this fix cycle.

---

## B6 — Observability Gaps (MEDIUM)

### Evidence

Both `bronze/tasks.py` and `silver/tasks.py` have non-retryable exception handlers
that set `status=FAILED` + save to DB but **do not increment Prometheus counters**:

**`apps/silver/tasks.py:721-765`** (non-retryable):

```python
except (duckdb.CatalogException, duckdb.ConstraintException, ...) as exc:
    ingestion.status = PipelineStatus.FAILED
    ingestion.save(...)
    task_row.save(...)
    ingestion.parse_progress_description = "Failed"
    # MISSING: pipeline_task_failures.labels(...).inc()
    raise
```

Compare with the generic handler at `tasks.py:766-796` which calls
`self.record_failure()` → increments `pipeline_task_failures` or
`pipeline_task_retries`.

**Same gap in `apps/bronze/tasks.py:219-262`.**

### Impact

- Non-retryable failures (CatalogException, SchemaDriftError, etc.) don't appear in
  the `pipeline_task_failures_total` Prometheus counter
- Grafana alerts that fire on failure rate won't trigger for these
- Operations team has no signal that a migration is missing or schema has drifted

### Fix

Add `pipeline_task_failures` increment in the non-retryable except block. Since both
bronze and silver have the identical pattern, fix both at once.

**In `apps/silver/tasks.py`** (after line 733, before `save`):

```python
        pipeline_task_failures.labels(
            task_name="transform_to_silver",
            error_type=type(exc).__name__,
        ).inc()
```

**In `apps/bronze/tasks.py`** (matching location):

```python
        pipeline_task_failures.labels(
            task_name="ingest_to_bronze",
            error_type=type(exc).__name__,
        ).inc()
```

Don't forget to add the import:

```python
from apps.core.metrics import pipeline_task_failures  # (if not already imported)
```

Check: `apps/silver/tasks.py:12` already imports `pipeline_rows_dropped`,
`pipeline_rows_processed`, `pipeline_task_duration` — but **not**
`pipeline_task_failures`. Add it.

`apps/bronze/tasks.py:10` imports only `pipeline_rows_processed`,
`pipeline_task_duration`. Add `pipeline_task_failures` there too.

---

## Observable Metrics — New Additions

### New counter: `pipeline_null_warnings_total`

Track the phantom-column warning from B4:

```python
pipeline_null_warnings = Counter(
    "pipeline_null_warnings_total",
    "Number of uploads with suspicious column patterns",
    ["layer", "snapshot_type", "column_name"],
)
```

Incremented in the silver pipeline when every row in a batch has NULL for a column
that was expected to have data. Threshold: 100% null rate for a JSON-extracted column.

Implementation sketch (after source count):

```python
# Sample: check for fully-null extracted columns
if source_count > 0:
    for col_name in ["Height", "Best Pos", "Preferred Foot", "Left Foot", "Right Foot", "Off"]:
        null_count = conn.execute(
            f"SELECT COUNT(*) FROM pg_db.bronze.{bronze_ingestion.snapshot_type} "
            f"WHERE ingestion_id = $1 AND json_extract_string(raw_data, '$.\"{col_name}\"') IS NULL",
            [bronze_ingestion.id],
        ).fetchone()[0]
        if null_count == source_count:
            pipeline_null_warnings.labels(
                layer="silver",
                snapshot_type=bronze_ingestion.snapshot_type,
                column_name=col_name,
            ).inc()
            logger.warning(
                "Column %s is NULL for all %d source rows — possible schema drift",
                col_name, source_count, extra=log_ctx,
            )
```

This runs only during the `TRANSFORMING` phase and doesn't affect the pipeline outcome.
It surfaces schema drift in Grafana before someone manually queries the DB.

### Log points — no changes needed

The current log points (per AGENTS.md) are sufficient. Every phase transition is logged.
What's missing is **structured context** for error logs — add `error_type` to the
`extra` dict:

```python
logger.error(
    "Non-retryable: %s — investigate", type(exc).__name__,
    extra={**log_ctx, "error_type": type(exc).__name__},
)
```

This allows Loki-based alerting to regex on `{error_type="CatalogException"}`.

---

## Implementation Order

| #   | File                                            | Change                                                                        | Risk                                              | Test Coverage Needed                                                             |
| --- | ----------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------- |
| 1   | `apps/silver/tasks.py`                          | Fix B1: date format regex + COALESCE for `expires` + `ingame_matchdate`       | Low — additive, doesn't break existing DD/MM/YYYY | Unit: SQL contains both branches. Integration: ISO input → correct DATE          |
| 2   | `apps/silver/tasks.py`                          | Fix B2: strip `p/w` suffix in `_apply_multiplier` or `parse_currency_min/max` | Low — additive regex, no existing path changes    | Unit: `parse_currency_min("£4.6K p/w")`, `parse_currency_max("£230 - £320 p/w")` |
| 3   | `apps/silver/tasks.py`                          | Fix B3: COALESCE `Nation of Birth` + `Nation`                                 | Low — additive, scouting path unchanged           | Unit: JSON extraction pattern. Integration: squad rows → correct nation          |
| 4   | `apps/silver/tasks.py` + `apps/bronze/tasks.py` | Fix B6: add `pipeline_task_failures` import + increment                       | Low — import + one line                           | Unit: mock counter, verify `.inc()` called                                       |
| 5   | `apps/silver/tasks.py`                          | Add B4: pipeline_null_warnings logic                                          | Low — log + counter only, non-blocking            | Unit: mock counter, verify `.inc()` called for fully-null columns                |
| 6   | `apps/core/metrics.py`                          | Add `pipeline_null_warnings` Counter                                          | None                                              | N/A — standard Prometheus client                                                 |
| 7   | `apps/silver/tests.py`                          | Tests for all fixes                                                           | None                                              | See below                                                                        |

---

## Testing Strategy

### Unit tests (`apps/silver/tests.py`)

Add to `TestBuildSilverPipeline`:

```
- test_expires_handles_iso_format()   — SQL contains ~'^\d{4}-\d{2}-\d{2}$'
- test_expires_handles_slash_format() — SQL still contains str_split(..., '/')
- test_nation_fallsback_to_nation()   — SQL contains COALESCE with both keys
```

Add to `TestDocstringUDFs`:

```
- test_parse_currency_min_strips_pw_suffix()
- test_parse_currency_max_strips_pw_suffix()
- test_parse_currency_min_pw_suffix_no_range()
```

### Integration tests (new file: `apps/silver/tests_integration.py`)

These tests require a real PostgreSQL + DuckDB connection. Use the existing
`CELERY_TASK_ALWAYS_EAGER = True` pattern from the root conftest.

```
- test_squad_iso_expires_parsed_correctly()
- test_wage_values_parsed_correctly()
- test_nation_of_birth_squad_vs_scouting()
- test_phantom_column_null_warning_counter()
```

_Fixture setup:_ Create bronze rows with known raw_data JSON, run
`transform_to_silver`, assert silver table contents.

### Query-based verification (run against staging)

```sql
-- Before deploy: confirm the NULL rate
SELECT snapshot_type,
       COUNT(*) FILTER (WHERE raw_data->>'Expires' ~ '^\d{4}-\d{2}-\d{2}$') AS iso,
       COUNT(*) FILTER (WHERE raw_data->>'Expires' ~ '^\d{1,2}/\d{1,2}/\d{4}$') AS dmy
FROM bronze.squad_snapshot GROUP BY snapshot_type;

SELECT 'wage_null_rate' AS metric,
       COUNT(*) FILTER (WHERE (raw_data->>'Wage') IS NOT NULL) AS total_non_null,
       COUNT(*) FILTER (WHERE (raw_data->>'Wage') IS NOT NULL AND wage_min IS NULL) AS wage_parsed_null
FROM bronze.squad_snapshot b
LEFT JOIN silver.squad_snapshot s ON s.ingestion_id = b.ingestion_id
WHERE b.ingestion_id = <latest>;
```

---

## Rollout Plan

### Phase 1 — Code changes (est. 1 day)

1. Apply fix B1 (date format) + B2 (wage suffix) + B3 (nation key)
2. Apply fix B6 (observability counters)
3. Apply B4 (null warning metrics)
4. Write unit tests for all changes
5. Run existing test suite: `pytest apps/silver/tests.py -v`

### Phase 2 — Integration test + staging deploy (est. 1 day)

1. Run integration tests against staging DB
2. Deploy to staging
3. Run manual ingestion: upload a squad CSV, verify silver table
4. Check Grafana: verify `pipeline_task_failures` appears, `pipeline_null_warnings` fires

### Phase 3 — Production rollout (est. 2 hours)

1. Merge to main
2. Run migration (if any) — no new migration needed for these fixes
3. Re-deploy celery workers
4. Re-process existing bronze batches that have failed silver:
   - Manual trigger: click "Retry Silver" in the upload detail page
   - Or: `python manage.py shell` → re-dispatch for specific bronze_ingestion IDs

### Phase 4 — Monitoring (ongoing)

1. After all batches processed, verify `pipeline_null_warnings` for phantom columns
2. Check `pipeline_rows_dropped{reason="no_unique_id"}` — should be roughly unchanged
3. Check `wage_min` population rate in silver tables

---

## Rollback Plan

Each fix is independent and can be reverted individually:

| Fix | Revert method                        | Side effects                                  |
| --- | ------------------------------------ | --------------------------------------------- |
| B1  | Revert to old `str_split` expression | Squad ISO dates become NULL again             |
| B2  | Revert `p/w` strip line              | ALL wage values become NULL again             |
| B3  | Revert to `$."Nation of Birth"` only | Squad nation becomes NULL again               |
| B6  | Remove counter increment lines       | Non-retryable failures disappear from metrics |

All reverted `build_silver_pipeline` changes require a worker restart. No DB migration.
Old data in silver tables is NOT affected — fixes only affect future ingestions.

---

## Appendix A: Full Bronze Key Differences

| JSON Key          | Squad (28 rows)      | Scouting (1601 rows) | Extracted by Silver  |
| ----------------- | -------------------- | -------------------- | -------------------- |
| `Nation`          | ✅ `"ESP"`           | ❌                   | ❌ (bug B3)          |
| `Nation of Birth` | ❌                   | ✅ `"Colombia"`      | ✅                   |
| `Personality`     | ✅ `"Model Citizen"` | ❌                   | ❌ (bug B5)          |
| `Position`        | ✅ `"D/WB/M/AM (L)"` | ❌                   | ✅                   |
| `Rating`          | ✅ `"6.87"`          | ❌                   | ✅                   |
| `Clr/90`          | ❌                   | ✅ `"2.2"`           | ✅                   |
| `Hdrs L/90`       | ❌                   | ✅ `"1.9"`           | ✅                   |
| `Hdrs W/90`       | ❌                   | ✅ `"2.4"`           | ✅                   |
| `Recommendation`  | ❌                   | ✅ `"C+"`            | ❌ (dropped in 0003) |
| `Height`          | ❌                   | ❌                   | ✅ (phantom B4)      |
| `Best Pos`        | ❌                   | ❌                   | ✅ (phantom B4)      |
| `Preferred Foot`  | ❌                   | ❌                   | ✅ (phantom B4)      |
| `Left Foot`       | ❌                   | ❌                   | ✅ (phantom B4)      |
| `Right Foot`      | ❌                   | ❌                   | ✅ (phantom B4)      |
| `Off` (offsides)  | ❌                   | ❌                   | ✅ (phantom B4)      |

---

## Appendix B: DuckDB `str_split` Out-of-Bounds Behavior

Verified against DuckDB: `str_split('2027-06-30', '/')[3]` does NOT throw an error.
DuckDB's `list_extract` (the bracket operator) returns NULL for out-of-bounds access
on a list. This means B1 is a **silent data corruption** bug — no exception, no crash,
just NULL dates in the silver table.

This is the most insidious class of bug because:

- No alert fires (no error, no retry)
- The pipeline reports COMPLETED successfully
- Silver `inserted_row_count` matches bronze `source_row_count`
- The data loss is invisible until someone queries `expires` values
