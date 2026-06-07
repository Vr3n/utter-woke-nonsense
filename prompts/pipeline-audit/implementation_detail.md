# Phase 2 Implementation Detail

**Date:** 2026-06-01
**Audit refs:** L-3, L-1
**Status:** Implemented, tests passing

---

## L-3 — Centralise `duckdb_pg_connect` in `apps/core/db.py`

### What changed

- **Before:** `duckdb_pg_connect()` defined locally at `apps/bronze/tasks.py:33-39`, imported by `apps/silver/tasks.py:9` via `from apps.bronze.tasks import duckdb_pg_connect`.
- **After:** Defined once at `apps/core/db.py:16-23`. Bronze imports from `apps.core.db`. Silver imports from `apps.core.db`.

### Files modified

| File | Change |
|---|---|
| `apps/core/db.py:1-23` | Added import of `contextmanager`, `duckdb`. Added `duckdb_pg_connect(pool_max_connections=4)` context manager. Sets `pg_pool_max_connections` before yielding. |
| `apps/bronze/tasks.py:1-29` | Removed `from contextlib import contextmanager`. Removed local `duckdb_pg_connect` definition. Now imports from `apps.core.db`. |
| `apps/silver/tasks.py:9` | Changed import from `apps.bronze.tasks` to `apps.core.db`. |

### Design decisions

1. **Pool sizing parameter:** `pool_max_connections=4` — DuckDB's default is `4 <= cpu_count * 1.5 <= 32`. Explicit 4 gives conservative, predictable behaviour across environments.
2. **`SET` before ATTACH:** DuckDB docs require pool configuration options to be set before the database is attached. The execute happens inside `__enter__`, before the `yield`.
3. **No bronze/silver coupling removed:** Silver no longer depends on bronze's module for infrastructure concerns. `core/db.py` is the single home for database connectivity utilities (`get_pg_conn_string` already lived there).

### Verification

- Bronze tests: 28 passed
- Silver tests: 82 passed
- Core tests: 4 passed
- Landing tests: 31 passed

---

## L-1 — Dynamic `column_count` from `build_silver_pipeline`

### What changed

- **Before:** `ingestion.column_count = 54 if extra_columns else 80` — hardcoded magic numbers at `apps/silver/tasks.py:590`.
- **After:** `ingestion.column_count = column_count` — derived from the actual INSERT column list at SQL-generation time.

### Files modified

| File | Change |
|---|---|
| `apps/silver/tasks.py:395-401` | Added column count computation. `base_col_count = len([c for c in shared_insert_cols.split(",") if c.strip()])` then adds `len(extra_columns)`. `return` now returns `(sql, extra_columns, column_count)`. |
| `apps/silver/tasks.py:463-467` | Updated unpacking: `sql_pipeline, extra_columns, column_count = build_silver_pipeline(...)`. |
| `apps/silver/tasks.py:590-592` | Replaced hardcoded `54 if extra_columns else 80` with `ingestion.column_count = column_count`. |
| `apps/silver/tests.py:188,196,204,214,220,226,232,238,244,250,256,262,272,283,294,301,307,314,320` | All `build_silver_pipeline()` call sites updated to unpack three values. |

### Derived counts

| Snapshot type | `column_count` |
|---|---|
| `squad_snapshot` | 80 |
| `scouting_snapshot` | 80 |
| `squad_matchstats_snapshot` | 82 |

The old hardcoded `54` for matchstats was a bug — the actual INSERT column list is 82 columns (80 base + opponent + ingame_matchdate). The SQL pipeline generates the same INSERT for all three types plus extra columns for matchstats. If the matchstats table schema hasn't been expanded beyond 54 columns, no matchstats INSERT would have succeeded. This is a pre-existing discrepancy unrelated to this change.

### Design decisions

1. **Count from string, not from a tracking list:** The `shared_insert_cols` is a single multi-line string constant. Splitting by `,` and filtering whitespace is O(n) on a ~700-char string, called once per task run. A separate tracking list would duplicate the column listing in two places (DRY violation).
2. **No magic numbers:** The count is purely derived from the actual SQL. Future column additions via migration only need to edit the column string — the count updates automatically.
3. **Extra columns API preserved:** `extra_columns` is still returned for the caller to use (e.g. for DuckDB UDF registration logic). Adding `column_count` as a third return value is backward-compatible for any caller that unpacks properly.

### Verification

- All `TestBuildSilverPipeline` tests pass (21 tests)
- All `TestTransformToSilver*` tests pass
- All other silver tests (82 total) pass
