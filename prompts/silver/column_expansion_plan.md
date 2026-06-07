# Silver layer — column expansion plan

## Context

The FM export template now includes ~29 new columns and has removed the
`Recommendation` column. This plan covers retrofitting `squad_snapshot` and
`scouting_snapshot`. `squad_matchstats_snapshot` is deferred.

## What changed

### Removed (1)

| CSV header     | Silver name     | Reason                   |
| -------------- | --------------- | ------------------------ |
| `Recommendation` | `recommendation` | No longer in FM export |

### Added (29)

| #  | CSV header                                   | Silver name                | Type     | Notes                              |
| -- | -------------------------------------------- | -------------------------- | -------- | ---------------------------------- |
| 1  | `Best Pos`                                   | `best_pos`                 | VARCHAR  | e.g. `"M (C)"`, `"GK"`            |
| 2  | `Position`                                   | `position`                 | VARCHAR  | e.g. `"DM, M (C)"`                |
| 3  | `Rating`                                     | `rating`                   | DOUBLE   | `-` → NULL                        |
| 4  | `Height`                                     | `height`                   | INT      | Strip `" cm"` suffix, parse via UDF |
| 5  | `Preferred Foot`                             | `preferred_foot`           | VARCHAR  | e.g. `"Right-Footed"`             |
| 6  | `Left Foot`                                  | `left_foot`                | VARCHAR  | e.g. `"Very Strong"`              |
| 7  | `Right Foot`                                 | `right_foot`               | VARCHAR  | e.g. `"Very Weak"`                |
| 8  | `Tcon/90`                                    | `tcon_per90`               | DOUBLE   | Turnovers conceded per 90         |
| 9  | `xSv %`                                      | `xsv_pct`                  | INT      | Expected save % (0–100)           |
| 10 | `Sv %`                                       | `sv_pct`                   | INT      | Actual save % (0–100)             |
| 11 | `CCC`                                        | `ccc`                      | INT      | Clear cut chances                 |
| 12 | `Aer A/90`                                   | `aer_a_per90`              | DOUBLE   | Aerial attempts per 90            |
| 13 | `K Hdrs/90`                                  | `k_hdrs_per90`             | DOUBLE   | Key headers per 90                |
| 14 | `Crs A/90`                                   | `crs_a_per90`              | DOUBLE   | Crosses attempted per 90          |
| 15 | `Cr C/90`                                    | `cr_c_per90`               | DOUBLE   | Crosses completed per 90          |
| 16 | `Con/90`                                     | `con_per90`                | DOUBLE   | Concentration per 90              |
| 17 | `Goals per 90 minutes`                       | `goals_per90`              | DOUBLE   | Goals per 90                      |
| 18 | `Saves/90`                                   | `saves_per90`              | DOUBLE   | Saves per 90                      |
| 19 | `Tgls/90`                                    | `tgls_per90`               | DOUBLE   | Tackles lost per 90               |
| 20 | `MLG`                                        | `mlg`                      | INT      | Minutes per league goal           |
| 21 | `Off`                                        | `offsides`                 | INT      | Offsides (`off` is SQL reserved)   |
| 22 | `Asts/90`                                    | `asts_per90`               | DOUBLE   | Assists per 90                    |
| 23 | `KP/90`                                      | `kp_per90`                 | DOUBLE   | Key passes per 90                 |
| 24 | `xGP/90`                                     | `xgp_per90`                | DOUBLE   | Expected goals prevented per 90   |
| 25 | `Shots From Outside The Box Per 90 minutes`  | `shots_outside_box_per90`  | DOUBLE   | Long shots per 90                 |
| 26 | `ShT/90`                                     | `sht_per90`                | DOUBLE   | Shots on target per 90            |
| 27 | `Shot/90`                                    | `shot_per90`               | DOUBLE   | Total shots per 90                |
| 28 | `K Tck/90`                                   | `k_tck_per90`              | DOUBLE   | Key tackles per 90                |
| 29 | `Shts Blckd/90`                              | `shts_blckd_per90`         | DOUBLE   | Shots blocked per 90              |

## Column count change

| Table                        | Before | After |
| ---------------------------- | ------ | ----- |
| `squad_snapshot`             | 52     | 80    |
| `scouting_snapshot`          | 52     | 80    |
| `squad_matchstats_snapshot`  | 54     | 54    |

## Decisions (resolved via design review)

| # | Issue | Decision |
|---|---|---|
| 1 | Rating `-` sentinel | `NULLIF(NULLIF(rating_str, ''), '-')::DOUBLE` |
| 2 | Height parsing | New `parse_height(UDF` (5th UDF, registered alongside existing 4) |
| 3 | All new numeric cols | Defensive double `NULLIF(NULLIF(..., ''), '-')` |
| 4 | Matchstats `column_count` | Drive-by fix to 54 |
| 5 | Migration structure | Single `ALTER TABLE` per table, `atomic=True` |
| 6 | Old data compatibility | No backfill — NULL for new columns on old bronze rows |
| 7 | Migration `reverse_sql` | Full reverse with `IF EXISTS` guards |
| 8 | Testing scope | Unit tests only: `TestParseHeight` + `TestBuildSilverPipeline` assert updates |
| 9 | `parse_height` contract | Strips `" cm"`; sentinels `N/A`, `-`, `Unknown`, empty, None → NULL |

### `parse_height` UDF

```python
def parse_height(val):
    if not val:
        return None
    val = val.strip()
    if val in ("Unknown", "N/A", "-", ""):
        return None
    cleaned = val.replace(" cm", "")
    try:
        return int(cleaned)
    except (ValueError, TypeError):
        return None
```

Registered as: `conn.create_function("parse_height", parse_height, [str], int)`

## Files to change

### 1. New migration — `apps/silver/migrations/0003_add_new_columns.py`

Two `RunSQL` operations (single `ALTER TABLE` per table). `atomic = True` (default).

**Forward SQL — squad_snapshot:**
```sql
ALTER TABLE silver.squad_snapshot
    DROP COLUMN IF EXISTS recommendation,
    ADD COLUMN best_pos VARCHAR(50),
    ADD COLUMN position VARCHAR(255),
    ADD COLUMN rating DOUBLE PRECISION,
    ADD COLUMN height INT,
    ADD COLUMN preferred_foot VARCHAR(50),
    ADD COLUMN left_foot VARCHAR(50),
    ADD COLUMN right_foot VARCHAR(50),
    ADD COLUMN tcon_per90 DOUBLE PRECISION,
    ADD COLUMN xsv_pct INT,
    ADD COLUMN sv_pct INT,
    ADD COLUMN ccc INT,
    ADD COLUMN aer_a_per90 DOUBLE PRECISION,
    ADD COLUMN k_hdrs_per90 DOUBLE PRECISION,
    ADD COLUMN crs_a_per90 DOUBLE PRECISION,
    ADD COLUMN cr_c_per90 DOUBLE PRECISION,
    ADD COLUMN con_per90 DOUBLE PRECISION,
    ADD COLUMN goals_per90 DOUBLE PRECISION,
    ADD COLUMN saves_per90 DOUBLE PRECISION,
    ADD COLUMN tgls_per90 DOUBLE PRECISION,
    ADD COLUMN mlg INT,
    ADD COLUMN offsides INT,
    ADD COLUMN asts_per90 DOUBLE PRECISION,
    ADD COLUMN kp_per90 DOUBLE PRECISION,
    ADD COLUMN xgp_per90 DOUBLE PRECISION,
    ADD COLUMN shots_outside_box_per90 DOUBLE PRECISION,
    ADD COLUMN sht_per90 DOUBLE PRECISION,
    ADD COLUMN shot_per90 DOUBLE PRECISION,
    ADD COLUMN k_tck_per90 DOUBLE PRECISION,
    ADD COLUMN shts_blckd_per90 DOUBLE PRECISION;
```

**Reverse SQL — squad_snapshot:**
```sql
ALTER TABLE silver.squad_snapshot
    DROP COLUMN IF EXISTS shts_blckd_per90,
    DROP COLUMN IF EXISTS k_tck_per90,
    DROP COLUMN IF EXISTS shot_per90,
    DROP COLUMN IF EXISTS sht_per90,
    DROP COLUMN IF EXISTS shots_outside_box_per90,
    DROP COLUMN IF EXISTS xgp_per90,
    DROP COLUMN IF EXISTS kp_per90,
    DROP COLUMN IF EXISTS asts_per90,
    DROP COLUMN IF EXISTS offsides,
    DROP COLUMN IF EXISTS mlg,
    DROP COLUMN IF EXISTS tgls_per90,
    DROP COLUMN IF EXISTS saves_per90,
    DROP COLUMN IF EXISTS goals_per90,
    DROP COLUMN IF EXISTS con_per90,
    DROP COLUMN IF EXISTS cr_c_per90,
    DROP COLUMN IF EXISTS crs_a_per90,
    DROP COLUMN IF EXISTS k_hdrs_per90,
    DROP COLUMN IF EXISTS aer_a_per90,
    DROP COLUMN IF EXISTS ccc,
    DROP COLUMN IF EXISTS sv_pct,
    DROP COLUMN IF EXISTS xsv_pct,
    DROP COLUMN IF EXISTS tcon_per90,
    DROP COLUMN IF EXISTS right_foot,
    DROP COLUMN IF EXISTS left_foot,
    DROP COLUMN IF EXISTS preferred_foot,
    DROP COLUMN IF EXISTS height,
    DROP COLUMN IF EXISTS rating,
    DROP COLUMN IF EXISTS position,
    DROP COLUMN IF EXISTS best_pos,
    ADD COLUMN recommendation VARCHAR(50);
```

Same pattern for `scouting_snapshot`.

### 2. `apps/silver/tasks.py` — `build_silver_pipeline()`

#### Add `parse_height` function

Add the new function before `build_silver_pipeline`. Register it alongside the other 4 UDFs.

#### `unpacked` CTE

Remove:
```sql
json_extract_string(raw_data, '$."Recommendation"') AS recommendation_str,
```

Add 29 new `json_extract_string` lines (see full list in implementation).

#### `cleaned` CTE

Remove:
```sql
NULLIF(recommendation_str, '-')           AS recommendation,
```

Add 29 new transform lines with the following patterns:

- **VARCHAR** (simple `NULLIF`): `best_pos`, `position`, `preferred_foot`, `left_foot`, `right_foot`
- **DOUBLE** (double `NULLIF`): `rating`, all per-90 stats
- **INT** (double `NULLIF`): `xsv_pct`, `sv_pct`, `ccc`, `mlg`, `offsides`
- **UDF**: `height` via `parse_height(height_str)`

#### `final` CTE

Add all 29 new columns to `SELECT DISTINCT`, remove `recommendation`.

#### `shared_insert_cols`

Add all 29 new columns, remove `recommendation`.

#### `column_count`

```python
column_count = 54 if extra_columns else 80
```

### 3. `apps/silver/tests.py`

#### Add `TestParseHeight` class

Test the new UDF with: `"185 cm"`, `"N/A"`, `"-"`, `"Unknown"`, empty string, `None`.

#### Update `TestBuildSilverPipeline`

Assertions for all 3 snapshot types must:
- Verify new columns appear in `unpacked`, `cleaned`, `final` CTEs and `shared_insert_cols`
- Verify `recommendation` is absent
- Verify `parse_height` is called in the cleaned CTE
- Column assertions updated: squad/scouting snapshot type assertions reflect 29 new cols

### 4. Documentation

- `AGENTS.md` — update silver column mapping table; remove `recommendation`, add 29 new columns; note `column_count` values
- `prompts/silver/context.md` — update column mapping tables
- `prompts/silver/implementation_strategy.md` — update SQL CTE listings, UDF count 4→5, note `parse_height` UDF

## Edge cases

| Column | Value example | Handling |
|--------|--------------|----------|
| `Rating` | `"-"` | `NULLIF(NULLIF(rating_str, ''), '-')::DOUBLE` |
| `Height` | `"185 cm"` | `parse_height()` UDF — strips `" cm"`, sentinel → None |
| All numeric | `"-"` | `NULLIF(NULLIF(str, ''), '-')::INT/DOUBLE` |
| `Preferred Foot` | `"Unknown Footedness"` | Stored as-is (VARCHAR), no sentinel mapping |
| `Off` | `"10"` | Renamed to `offsides` to avoid SQL keyword |
| `MLG` | `"0"` | INT, `0` is a valid value (no games played) |
| Old bronze rows | missing keys | `json_extract_string` returns NULL → new columns NULL |

## Non-goals

- No new indexes on the new columns
- No changes to `squad_matchstats_snapshot`
- No changes to `SilverIngestion` / `SilverIngestionTask` models
- No changes to bronze layer
- No changes to frontend templates or progress polling
- No data backfill for old rows
