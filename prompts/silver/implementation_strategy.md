# Silver Layer — DuckDB Implementation Strategy

## Architecture

```
bronze ingestion COMPLETED (ingest_to_bronze success path)
    │
    ├── dispatch_silver_ingestion(bronze_ingestion_id)
    │   ├── SilverIngestion.objects.create(status=PROCESSING)
    │   └── transform_to_silver.delay(silver_ingestion.id)
    │
    └── Celery task: transform_to_silver(silver_ingestion_id)
```

One bronze batch → one silver batch. Cross-batch dedup is gold's concern.

## Data flow

DuckDB reads from bronze PostgreSQL, transforms, writes to silver PostgreSQL.
All through a single ATTACH — one connection, one transaction.

```
pg_db.bronze.{snapshot_type}   → SELECT source data
pg_db.silver.{snapshot_type}    → INSERT cleaned data
```

DuckDB reads JSONB `raw_data` from bronze, unpacks all 70+ columns via
`json_extract_string()`, applies UDFs and SQL transforms, deduplicates
exact duplicates via `SELECT DISTINCT`, filters out NULL `unique_id`,
and writes to silver.

## Rationale for key decisions

All design decisions are documented with reasoning at the end of this document
in the "Decisions & Rationale" section. Each entry explains the decision,
the alternatives considered, why this approach was chosen, and what
consequences or tradeoffs were accepted.

## UDF specifications

Five Python functions registered on the DuckDB connection via
`conn.create_function()`. Each is a pure function — no side effects,
no I/O, no Django ORM access.

### `parse_currency_min(val: str | None) -> int | None`

```python
def parse_currency_min(val):
    if not val:
        return None
    val = val.strip()
    if val in ("Unknown", "N/A", "-", ""):
        return None
    # Split range on " - " and take the first part
    parts = val.split(" - ")
    first = parts[0]
    # Strip all leading non-numeric characters (currency symbols, spaces, etc.)
    cleaned = re.sub(r"^[^0-9.]+", "", first)
    if not cleaned:
        return None
    return _apply_multiplier(cleaned)
```

Helper `_apply_multiplier(cleaned: str) -> int`:

```python
def _apply_multiplier(cleaned):
    multiplier = 1
    if cleaned.upper().endswith("K"):
        multiplier = 1000
        cleaned = cleaned[:-1]
    elif cleaned.upper().endswith("M"):
        multiplier = 1000000
        cleaned = cleaned[:-1]
    try:
        return int(float(cleaned) * multiplier)
    except (ValueError, TypeError):
        return None
```

### `parse_currency_max(val: str | None) -> int | None`

```python
def parse_currency_max(val):
    if not val:
        return None
    val = val.strip()
    if val in ("Unknown", "N/A", "-", ""):
        return None
    parts = val.split(" - ")
    # If it's a range, take the second part. Otherwise same as min.
    second = parts[1] if len(parts) > 1 else parts[0]
    cleaned = re.sub(r"^[^0-9.]+", "", second)
    if not cleaned:
        return None
    return _apply_multiplier(cleaned)
```

### `parse_starts(val: str | None) -> int | None`

```python
def parse_starts(val):
    if not val:
        return None
    val = val.strip()
    if val in ("Unknown", "N/A", "-", ""):
        return None
    # Format: "6 (1)" or "4" or "0"
    # Split on " (" and take first part
    starts_str = val.split(" (")[0]
    try:
        return int(starts_str)
    except (ValueError, TypeError):
        return None
```

### `parse_subs(val: str | None) -> int | None`

```python
def parse_subs(val):
    if not val:
        return None
    val = val.strip()
    if val in ("Unknown", "N/A", "-", ""):
        return None
    parts = val.split(" (")
    if len(parts) > 1:
        # Has subs component: "6 (1)" → extract "1)"
        subs_str = parts[1].rstrip(")")
        try:
            return int(subs_str)
        except (ValueError, TypeError):
            return None
    # No subs component means subs were omitted → 0 (known value)
    return 0
```

### `parse_height(val: str | None) -> int | None`

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

Strips the `" cm"` suffix from FM height values like `"185 cm"`.
Sentinels (`N/A`, `-`, `Unknown`, empty, None) return NULL.
Case-sensitive — only exact uppercase sentinel matches.

### Registration

```python
from duckdb import DuckDBPyConnection

conn: DuckDBPyConnection  # from duckdb_pg_connect()
conn.create_function("parse_currency_min", parse_currency_min, [VARCHAR], INTEGER)
conn.create_function("parse_currency_max", parse_currency_max, [VARCHAR], INTEGER)
conn.create_function("parse_starts", parse_starts, [VARCHAR], INTEGER)
conn.create_function("parse_subs", parse_subs, [VARCHAR], INTEGER)
conn.create_function("parse_height", parse_height, [VARCHAR], INTEGER)
```

## Full SQL pipeline

Single DuckDB query with 4 CTEs (reduced from 5 — dedup is now `SELECT DISTINCT`
instead of `ROW_NUMBER()` window function).

### CTE 1: `source`

Filter bronze rows to current ingestion only.

```sql
SELECT * FROM pg_db.bronze.{snapshot_type}
WHERE ingestion_id = $1
```

### CTE 2: `unpacked`

Extract all 71 fields from `raw_data` JSONB once. Each field extracted as
VARCHAR then reused downstream. No repeated `json_extract_string` calls.

For `squad_matchstats_snapshot`, two extra columns are extracted:
`opponent_str` and `ingame_matchdate_str` (see "matchstats additional columns"
section below). The SQL is generated conditionally — snapshot type determines
which columns to include.

```sql
unpacked AS (
    SELECT
        -- structured metadata (pass-through, already typed)
        ingestion_id,
        simulation_source,
        save_name,
        ingame_date,
        upload_timestamp,
        snapshot_type,
        source_file_hash,
        -- identifiers
        json_extract_string(raw_data, '$."Unique ID"')         AS unique_id_str,
        json_extract_string(raw_data, '$."Player"')             AS player_str,
        json_extract_string(raw_data, '$."Age"')                AS age_str,
        json_extract_string(raw_data, '$."Nation of Birth"')    AS nation_str,
        json_extract_string(raw_data, '$."Club"')               AS club_str,
        json_extract_string(raw_data, '$."Division"')           AS division_str,
        -- playing time
        json_extract_string(raw_data, '$."Appearances"')        AS appearances_str,
        json_extract_string(raw_data, '$."Minutes"')            AS minutes_str,
        json_extract_string(raw_data, '$."Playing Time"')       AS playing_time_str,
        -- financials
        json_extract_string(raw_data, '$."Transfer Value"')     AS transfer_value_str,
        json_extract_string(raw_data, '$."Wage"')               AS wage_str,
        json_extract_string(raw_data, '$."Expires"')            AS expires_str,
        -- per-90 performance
        json_extract_string(raw_data, '$."Poss Lost/90"')       AS poss_lost_str,
        json_extract_string(raw_data, '$."Poss Won/90"')        AS poss_won_str,
        json_extract_string(raw_data, '$."Ps C/90"')            AS ps_c_str,
        json_extract_string(raw_data, '$."Ps A/90"')            AS ps_a_str,
        json_extract_string(raw_data, '$."Pr passes/90"')       AS pr_passes_str,
        json_extract_string(raw_data, '$."OP-KP/90"')           AS op_kp_str,
        json_extract_string(raw_data, '$."Ch C/90"')            AS ch_c_str,
        json_extract_string(raw_data, '$."Pres A/90"')          AS pres_a_str,
        json_extract_string(raw_data, '$."Pres C/90"')          AS pres_c_str,
        json_extract_string(raw_data, '$."Blk/90"')             AS blk_str,
        json_extract_string(raw_data, '$."Clr/90"')             AS clr_str,
        json_extract_string(raw_data, '$."Int/90"')             AS int_str,
        json_extract_string(raw_data, '$."Tck/90"')             AS tck_str,
        json_extract_string(raw_data, '$."Dist/90"')            AS dist_str,
        json_extract_string(raw_data, '$."Drb/90"')             AS drb_str,
        json_extract_string(raw_data, '$."Sprints/90"')         AS sprints_str,
        json_extract_string(raw_data, '$."OP-Crs A/90"')        AS op_crs_a_str,
        json_extract_string(raw_data, '$."OP-Crs C/90"')        AS op_crs_c_str,
        json_extract_string(raw_data, '$."Hdrs L/90"')          AS hdrs_l_str,
        json_extract_string(raw_data, '$."Hdrs W/90"')          AS hdrs_w_str,
        json_extract_string(raw_data, '$."Cln/90"')             AS cln_str,
        json_extract_string(raw_data, '$."xA/90"')              AS xa_str,
        json_extract_string(raw_data, '$."xG/90"')              AS xg_str,
        json_extract_string(raw_data, '$."NP-xG/90"')           AS np_xg_str,
        -- totals
        json_extract_string(raw_data, '$."xG-OP"')              AS xg_op_str,
        json_extract_string(raw_data, '$."Fouls Made"')         AS fouls_made_str,
        json_extract_string(raw_data, '$."Fouls Against"')      AS fouls_against_str,
        json_extract_string(raw_data, '$."Yel"')                AS yel_str,
        json_extract_string(raw_data, '$."Red cards"')          AS red_cards_str,
        -- new columns (29)
        json_extract_string(raw_data, '$."Best Pos"')           AS best_pos_str,
        json_extract_string(raw_data, '$."Position"')           AS position_str,
        json_extract_string(raw_data, '$."Rating"')             AS rating_str,
        json_extract_string(raw_data, '$."Height"')             AS height_str,
        json_extract_string(raw_data, '$."Preferred Foot"')     AS preferred_foot_str,
        json_extract_string(raw_data, '$."Left Foot"')          AS left_foot_str,
        json_extract_string(raw_data, '$."Right Foot"')         AS right_foot_str,
        json_extract_string(raw_data, '$."Tcon/90"')            AS tcon_per90_str,
        json_extract_string(raw_data, '$."xSv %"')              AS xsv_pct_str,
        json_extract_string(raw_data, '$."Sv %"')               AS sv_pct_str,
        json_extract_string(raw_data, '$."CCC"')                AS ccc_str,
        json_extract_string(raw_data, '$."Aer A/90"')           AS aer_a_per90_str,
        json_extract_string(raw_data, '$."K Hdrs/90"')          AS k_hdrs_per90_str,
        json_extract_string(raw_data, '$."Crs A/90"')           AS crs_a_per90_str,
        json_extract_string(raw_data, '$."Cr C/90"')            AS cr_c_per90_str,
        json_extract_string(raw_data, '$."Con/90"')             AS con_per90_str,
        json_extract_string(raw_data, '$."Goals per 90 minutes"') AS goals_per90_str,
        json_extract_string(raw_data, '$."Saves/90"')           AS saves_per90_str,
        json_extract_string(raw_data, '$."Tgls/90"')            AS tgls_per90_str,
        json_extract_string(raw_data, '$."MLG"')                AS mlg_str,
        json_extract_string(raw_data, '$."Off"')                AS offsides_str,
        json_extract_string(raw_data, '$."Asts/90"')            AS asts_per90_str,
        json_extract_string(raw_data, '$."KP/90"')              AS kp_per90_str,
        json_extract_string(raw_data, '$."xGP/90"')             AS xgp_per90_str,
        json_extract_string(raw_data, '$."Shots From Outside The Box Per 90 minutes"') AS shots_outside_box_per90_str,
        json_extract_string(raw_data, '$."ShT/90"')             AS sht_per90_str,
        json_extract_string(raw_data, '$."Shot/90"')            AS shot_per90_str,
        json_extract_string(raw_data, '$."K Tck/90"')           AS k_tck_per90_str,
        json_extract_string(raw_data, '$."Shts Blckd/90"')      AS shts_blckd_per90_str
    FROM source
)
```

### CTE 3: `cleaned`

Apply all transforms: type casting, NULL handling, UDFs, date parsing.

Uses `TRY_CAST` for `unique_id` to gracefully handle non-numeric values —
bad data becomes NULL and is filtered by the final CTE rather than
crashing the entire batch with `InvalidInputException`.

```sql
cleaned AS (
    SELECT
        -- structured metadata (pass-through)
        ingestion_id,
        simulation_source,
        save_name,
        ingame_date,
        upload_timestamp,
        snapshot_type,
        source_file_hash,
        -- identifiers: TRY_CAST protects against non-numeric unique_id
        TRY_CAST(NULLIF(unique_id_str, '') AS BIGINT) AS unique_id,
        NULLIF(player_str, '')                    AS player,
        NULLIF(age_str, '')::INT                  AS age,
        NULLIF(nation_str, '')                    AS nation_of_birth,
        NULLIF(club_str, '')                      AS club,
        NULLIF(division_str, '')                  AS division,
        -- playing time
        parse_starts(appearances_str)             AS starting_appearances,
        parse_subs(appearances_str)               AS substitute_appearances,
        NULLIF(minutes_str, '')::INT              AS minutes,
        NULLIF(playing_time_str, '')              AS playing_time,
        -- financials
        parse_currency_min(transfer_value_str)    AS transfer_value_min,
        parse_currency_max(transfer_value_str)    AS transfer_value_max,
        parse_currency_min(wage_str)              AS wage_min,
        parse_currency_max(wage_str)              AS wage_max,
        -- expires: str_split + make_date, never strptime
        CASE
            WHEN expires_str IS NULL THEN NULL
            WHEN expires_str IN ('N/A', '-',
                 'Out of contract', 'Retired') THEN NULL
            ELSE make_date(
                CAST(str_split(expires_str, '/')[3] AS INT),
                CAST(str_split(expires_str, '/')[2] AS INT),
                CAST(str_split(expires_str, '/')[1] AS INT)
            )
        END                                       AS expires,
        -- per-90 rates
        NULLIF(poss_lost_str, '')::DOUBLE         AS poss_lost_per90,
        NULLIF(poss_won_str, '')::DOUBLE          AS poss_won_per90,
        NULLIF(ps_c_str, '')::DOUBLE              AS ps_c_per90,
        NULLIF(ps_a_str, '')::DOUBLE              AS ps_a_per90,
        NULLIF(pr_passes_str, '')::DOUBLE         AS pr_passes_per90,
        NULLIF(op_kp_str, '')::DOUBLE             AS op_kp_per90,
        NULLIF(ch_c_str, '')::DOUBLE              AS ch_c_per90,
        NULLIF(pres_a_str, '')::DOUBLE            AS pres_a_per90,
        NULLIF(pres_c_str, '')::DOUBLE            AS pres_c_per90,
        NULLIF(blk_str, '')::DOUBLE               AS blk_per90,
        NULLIF(clr_str, '')::DOUBLE               AS clr_per90,
        NULLIF(int_str, '')::DOUBLE               AS int_per90,
        NULLIF(tck_str, '')::DOUBLE               AS tck_per90,
        NULLIF(dist_str, '')::DOUBLE              AS dist_per90,
        NULLIF(drb_str, '')::DOUBLE               AS drb_per90,
        NULLIF(sprints_str, '')::DOUBLE           AS sprints_per90,
        NULLIF(op_crs_a_str, '')::DOUBLE          AS op_crs_a_per90,
        NULLIF(op_crs_c_str, '')::DOUBLE          AS op_crs_c_per90,
        NULLIF(hdrs_l_str, '')::DOUBLE            AS hdrs_l_per90,
        NULLIF(hdrs_w_str, '')::DOUBLE            AS hdrs_w_per90,
        NULLIF(cln_str, '')::DOUBLE               AS cln_per90,
        NULLIF(xa_str, '')::DOUBLE                AS xa_per90,
        NULLIF(xg_str, '')::DOUBLE                AS xg_per90,
        NULLIF(np_xg_str, '')::DOUBLE             AS np_xg_per90,
        -- totals and aggregates
        NULLIF(xg_op_str, '')::DOUBLE             AS xg_op,
        NULLIF(fouls_made_str, '')::INT           AS fouls_made,
        NULLIF(fouls_against_str, '')::INT        AS fouls_against,
        NULLIF(yel_str, '')::INT                  AS yellow_cards,
        NULLIF(red_cards_str, '')::INT                       AS red_cards,
        NULLIF(best_pos_str, '')                            AS best_pos,
        NULLIF(position_str, '')                            AS position,
        NULLIF(NULLIF(rating_str, ''), '-')::DOUBLE         AS rating,
        parse_height(height_str)                            AS height,
        NULLIF(preferred_foot_str, '')                      AS preferred_foot,
        NULLIF(left_foot_str, '')                           AS left_foot,
        NULLIF(right_foot_str, '')                          AS right_foot,
        NULLIF(NULLIF(tcon_per90_str, ''), '-')::DOUBLE     AS tcon_per90,
        NULLIF(NULLIF(xsv_pct_str, ''), '-')::INT           AS xsv_pct,
        NULLIF(NULLIF(sv_pct_str, ''), '-')::INT            AS sv_pct,
        NULLIF(NULLIF(ccc_str, ''), '-')::INT               AS ccc,
        NULLIF(NULLIF(aer_a_per90_str, ''), '-')::DOUBLE    AS aer_a_per90,
        NULLIF(NULLIF(k_hdrs_per90_str, ''), '-')::DOUBLE   AS k_hdrs_per90,
        NULLIF(NULLIF(crs_a_per90_str, ''), '-')::DOUBLE    AS crs_a_per90,
        NULLIF(NULLIF(cr_c_per90_str, ''), '-')::DOUBLE     AS cr_c_per90,
        NULLIF(NULLIF(con_per90_str, ''), '-')::DOUBLE      AS con_per90,
        NULLIF(NULLIF(goals_per90_str, ''), '-')::DOUBLE    AS goals_per90,
        NULLIF(NULLIF(saves_per90_str, ''), '-')::DOUBLE    AS saves_per90,
        NULLIF(NULLIF(tgls_per90_str, ''), '-')::DOUBLE     AS tgls_per90,
        NULLIF(NULLIF(mlg_str, ''), '-')::INT               AS mlg,
        NULLIF(NULLIF(offsides_str, ''), '-')::INT          AS offsides,
        NULLIF(NULLIF(asts_per90_str, ''), '-')::DOUBLE     AS asts_per90,
        NULLIF(NULLIF(kp_per90_str, ''), '-')::DOUBLE       AS kp_per90,
        NULLIF(NULLIF(xgp_per90_str, ''), '-')::DOUBLE      AS xgp_per90,
        NULLIF(NULLIF(shots_outside_box_per90_str, ''), '-')::DOUBLE AS shots_outside_box_per90,
        NULLIF(NULLIF(sht_per90_str, ''), '-')::DOUBLE      AS sht_per90,
        NULLIF(NULLIF(shot_per90_str, ''), '-')::DOUBLE     AS shot_per90,
        NULLIF(NULLIF(k_tck_per90_str, ''), '-')::DOUBLE    AS k_tck_per90,
        NULLIF(NULLIF(shts_blckd_per90_str, ''), '-')::DOUBLE AS shts_blckd_per90
    FROM unpacked
)
```

### CTE 4: `final`

`SELECT DISTINCT` removes exact duplicate rows (all columns identical).
Rows without a `unique_id` are dropped before insertion.
No `is_usable` flag or quality tiers — if a row is in silver, it is a
valid typed observation.

Replaces the earlier `ROW_NUMBER() OVER (PARTITION BY ...)` approach
because within a single FM batch, two rows with the same `(unique_id,
club, division)` but different stats are legitimate distinct observations,
not duplicates. Only rows with identical values in ALL columns are removed.

```sql
final AS (
    SELECT DISTINCT
        ingestion_id, simulation_source, save_name,
        ingame_date, upload_timestamp, snapshot_type, source_file_hash,
        unique_id, player, age, nation_of_birth, club, division,
        starting_appearances, substitute_appearances, minutes, playing_time,
        transfer_value_min, transfer_value_max, wage_min, wage_max, expires,
        poss_lost_per90, poss_won_per90, ps_c_per90, ps_a_per90,
        pr_passes_per90, op_kp_per90, ch_c_per90,
        pres_a_per90, pres_c_per90, blk_per90, clr_per90,
        int_per90, tck_per90, dist_per90, drb_per90, sprints_per90,
        op_crs_a_per90, op_crs_c_per90, hdrs_l_per90, hdrs_w_per90, cln_per90,
        xa_per90, xg_per90, np_xg_per90,
        xg_op, fouls_made, fouls_against, yellow_cards, red_cards,
        best_pos, position, rating, height,
        preferred_foot, left_foot, right_foot,
        tcon_per90, xsv_pct, sv_pct, ccc,
        aer_a_per90, k_hdrs_per90, crs_a_per90, cr_c_per90,
        con_per90, goals_per90, saves_per90, tgls_per90,
        mlg, offsides, asts_per90, kp_per90, xgp_per90,
        shots_outside_box_per90, sht_per90, shot_per90,
        k_tck_per90, shts_blckd_per90
    FROM cleaned
    WHERE unique_id IS NOT NULL
)
```

### INSERT

```sql
INSERT INTO pg_db.silver.{snapshot_type}
    (ingestion_id, simulation_source, save_name, ingame_date,
     upload_timestamp, snapshot_type, source_file_hash,
     unique_id, player, age, nation_of_birth, club, division,
     starting_appearances, substitute_appearances, minutes, playing_time,
     transfer_value_min, transfer_value_max, wage_min, wage_max, expires,
     poss_lost_per90, poss_won_per90, ps_c_per90, ps_a_per90,
     pr_passes_per90, op_kp_per90, ch_c_per90,
     pres_a_per90, pres_c_per90, blk_per90, clr_per90,
     int_per90, tck_per90, dist_per90, drb_per90, sprints_per90,
     op_crs_a_per90, op_crs_c_per90, hdrs_l_per90, hdrs_w_per90, cln_per90,
     xa_per90, xg_per90, np_xg_per90,
     xg_op, fouls_made, fouls_against, yellow_cards, red_cards,
     best_pos, position, rating, height,
     preferred_foot, left_foot, right_foot,
     tcon_per90, xsv_pct, sv_pct, ccc,
     aer_a_per90, k_hdrs_per90, crs_a_per90, cr_c_per90,
     con_per90, goals_per90, saves_per90, tgls_per90,
     mlg, offsides, asts_per90, kp_per90, xgp_per90,
     shots_outside_box_per90, sht_per90, shot_per90,
     k_tck_per90, shts_blckd_per90)
SELECT * FROM final
```

## Row counts

All computed inside DuckDB, inside the same ATTACH:

```python
source_count = conn.execute(
    "SELECT COUNT(*) FROM source", [ingestion.id]
).fetchone()[0]

unique_id_count = conn.execute("""
    SELECT COUNT(*) FROM cleaned WHERE unique_id IS NOT NULL
""").fetchone()[0]

inserted_count = conn.execute("""
    SELECT COUNT(*) FROM final
""").fetchone()[0]

dropped_count = source_count - unique_id_count
```

`dropped_count` tracks only rows dropped due to NULL `unique_id` — the
single drop gate. `SELECT DISTINCT` may remove additional exact-duplicate
rows, but these are not counted as "dropped" because they carry no
distinct information.

## Phase tracking

`SilverIngestionPhase` mirrors bronze's pattern:

```python
class SilverIngestionPhase(models.TextChoices):
    ATTACHING        = "attaching",        "Attaching to PostgreSQL"
    COUNTING_SOURCE  = "counting_source",  "Counting source bronze rows"
    TRANSFORMING     = "transforming",     "Transforming and cleaning data"
    INSERTING        = "inserting",        "Inserting into silver table"
    COUNTING_SILVER  = "counting_silver",  "Verifying silver row count"
    COMPLETED        = "completed",        "Ingestion completed successfully"
```

Each phase is saved via `task_row.save(update_fields=["phase"])` and
`ingestion.save(update_fields=["parse_progress_current", ...])`
at each checkpoint.

### Phase-to-progress mapping

| Step  | Phase           | parse_progress_current | Description                      |
| ----- | --------------- | ---------------------- | -------------------------------- |
| 1     | attaching       | 1                      | "Attaching to PostgreSQL"        |
| 2     | counting_source | 2                      | "Counting source bronze rows"    |
| 3     | transforming    | 3                      | "Transforming and cleaning data" |
| 4     | inserting       | 3                      | "Inserting into silver table"    |
| 5     | counting_silver | 4                      | "Verifying silver row count"     |
| final | completed       | 4                      | "Done"                           |

`parse_progress_total` defaults to 4.
Both "transforming" and "inserting" map to step 3 — the description text
changes even though the bar doesn't advance.

## Exception taxonomy

Same pattern as bronze, adapted for silver operations:

### Non-retryable (catch in dedicated except block)

| Exception                        | Signal                                   | Action               |
| -------------------------------- | ---------------------------------------- | -------------------- |
| `duckdb.CatalogException`        | Silver table missing → migration failure | Set FAILED, re-raise |
| `duckdb.ConstraintException`     | PK/FK/unique violation → data integrity  | Set FAILED, re-raise |
| `duckdb.InvalidInputException`   | Schema mismatch → FM version drift       | Set FAILED, re-raise |
| `duckdb.NotImplementedException` | DuckDB feature gap                       | Set FAILED, re-raise |

### Transient (autoretry_for)

| Exception                    | Why transient                         |
| ---------------------------- | ------------------------------------- |
| `duckdb.IOException`         | PostgreSQL read/write glitches        |
| `duckdb.OperationalError`    | PG timeouts, deadlocks, network blips |
| `duckdb.ConnectionException` | PG unreachable at ATTACH time         |

### Idempotency guard

If a COMPLETED `SilverIngestion` exists for the same `bronze_ingestion`,
return early with FAILED status. Never raise.

```python
if SilverIngestion.objects.filter(
    bronze_ingestion=bronze_ingestion,
    status=PipelineStatus.COMPLETED,
).exclude(id=ingestion.id).exists():
    ingestion.status = PipelineStatus.FAILED
    ingestion.error_type = "DuplicateIngestion"
    ingestion.error_message = (
        "A completed silver ingestion already exists for this bronze ingestion."
    )
    ingestion.save(update_fields=["status", "error_type", "error_message"])
    return
```

## Task structure

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
def transform_to_silver(self, ingestion_id: int) -> None:
    ingestion = SilverIngestion.objects.select_related(
        "bronze_ingestion__landing_upload__save_master"
    ).get(id=ingestion_id)
    bronze_ingestion = ingestion.bronze_ingestion
    upload = bronze_ingestion.landing_upload
    log_ctx = {"upload_id": upload.id, "step": "transform"}
    ...
```

### Lifecycle pseudocode

```
 1. Idempotency check → early return if duplicate
 2. SilverIngestionTask.objects.create(status=PENDING)
 3. task_row.status = RUNNING, .started_at = now
 4. with duckdb_pg_connect() as conn:
 5.     ATTACH to PostgreSQL (single ATTACH, alias "pg_db")
 6.     Register UDFs (parse_currency_min, parse_currency_max, parse_starts, parse_subs, parse_height)
 7.     Phase: COUNTING_SOURCE → count source rows
 8.     Phase: TRANSFORMING → execute CTE pipeline (4 CTEs)
 9.     Phase: INSERTING → INSERT into silver table
10.     Phase: COUNTING_SILVER → count inserted + dropped
11. conn.close() via finally
12. Update SilverIngestion (row counts, status=COMPLETED, completed_at)
13. DatasetEvent.objects.create(event_type=SILVER_COMPLETED)
14. task_row.status = SUCCEEDED, phase = COMPLETED
```

## DuckDB connection management

Reuse the same `duckdb_pg_connect()` context manager pattern from bronze.
It's already in `bronze/tasks.py`. Import rather than duplicate:

```python
from apps.bronze.tasks import duckdb_pg_connect
```

The ATTACH uses a single alias `pg_db` for both bronze and silver schemas.
Both schemas live in the same PostgreSQL database, so one ATTACH suffices:

```python
conn.execute(f"ATTACH '{pg_conn}' AS pg_db (TYPE postgres)")
```

References become:

- Read bronze: `pg_db.bronze.{snapshot_type}`
- Write silver: `pg_db.silver.{snapshot_type}`

See ADR `docs/adr/0003-single-attach-postgres.md` for the full reasoning
behind the single-ATTACH decision.

## matchstats additional columns

For `squad_matchstats_snapshot`, the silver table has two extra columns:

```sql
opponent          VARCHAR(255)  NULL
ingame_matchdate  DATE          NULL
```

These are extracted from bronze's raw_data alongside the 42 shared columns.
The SQL is **generated conditionally** in Python based on snapshot_type:

```python
def build_silver_pipeline(snapshot_type: str) -> tuple[str, list[str]]:
    """Generate SQL pipeline and column list for a given snapshot type."""
    extra_columns = []
    extra_unpack_sql = ""

    if snapshot_type == "squad_matchstats_snapshot":
        extra_unpack_sql = """
            json_extract_string(raw_data, '$."Opponent"')    AS opponent_str,
            json_extract_string(raw_data, '$."Match Date"')  AS ingame_matchdate_str,
        """
        extra_columns = ["opponent", "ingame_matchdate"]

    # Build SQL with conditional columns...
    return sql, extra_columns
```

Matchstats cleaning:

```sql
NULLIF(opponent_str, '')                     AS opponent,
CASE
    WHEN ingame_matchdate_str IS NULL THEN NULL
    WHEN ingame_matchdate_str IN ('N/A', '-') THEN NULL
    ELSE make_date(
        CAST(str_split(ingame_matchdate_str, '/')[3] AS INT),
        CAST(str_split(ingame_matchdate_str, '/')[2] AS INT),
        CAST(str_split(ingame_matchdate_str, '/')[1] AS INT)
    )
END                                          AS ingame_matchdate,
```

The silver data table DDL for matchstats includes these in its column list.
The DDL for squad and scouting does not.

## DuckDB exception taxonomy — silver-specific

The same categories as bronze apply, with one addition:

| Exception                 | When it fires in silver                                        |
| ------------------------- | -------------------------------------------------------------- |
| `CatalogException`        | `silver.squad_snapshot` table doesn't exist                    |
| `ConstraintException`     | Duplicate PK/FK across batches (shouldn't happen, signals bug) |
| `InvalidInputException`   | JSONB key missing — FM version changed column name             |
| `IOException`             | PG storage blip during ATTACH SELECT                           |
| `OperationalError`        | PG connection timeout during large INSERT                      |
| `ConnectionException`     | PG unreachable at ATTACH time                                  |
| `NotImplementedException` | DuckDB feature gap with JSONB or UDF                           |

The UDF registration itself can fail with `duckdb.InvalidInputException` if
the Python function signature doesn't match the DuckDB type declaration.
This is a non-retryable implementation error.

## Data table DDL

RunSQL migration creates the silver schema and tables. Example for
`scouting_snapshot`:

```sql
CREATE SCHEMA IF NOT EXISTS silver;

CREATE TABLE silver.scouting_snapshot (
    -- structured metadata
    ingestion_id        BIGINT        NOT NULL,
    simulation_source   VARCHAR(255)  NOT NULL,
    save_name           VARCHAR(255)  NOT NULL,
    ingame_date         DATE          NOT NULL,
    upload_timestamp    TIMESTAMPTZ   NOT NULL,
    snapshot_type       VARCHAR(50)   NOT NULL,
    source_file_hash    VARCHAR(64)   NOT NULL,
    -- identifiers
    unique_id           BIGINT        NOT NULL,
    player              VARCHAR(255),
    age                 INT,
    nation_of_birth     VARCHAR(255),
    club                VARCHAR(255),
    division            VARCHAR(255),
    -- playing time
    starting_appearances        INT,
    substitute_appearances      INT,
    minutes                     INT,
    playing_time                VARCHAR(100),
    -- financials
    transfer_value_min  INT,
    transfer_value_max  INT,
    wage_min            INT,
    wage_max            INT,
    expires             DATE,
    -- per-90 rates
    poss_lost_per90     DOUBLE PRECISION,
    poss_won_per90      DOUBLE PRECISION,
    ps_c_per90          DOUBLE PRECISION,
    ps_a_per90          DOUBLE PRECISION,
    pr_passes_per90     DOUBLE PRECISION,
    op_kp_per90         DOUBLE PRECISION,
    ch_c_per90          DOUBLE PRECISION,
    pres_a_per90        DOUBLE PRECISION,
    pres_c_per90        DOUBLE PRECISION,
    blk_per90           DOUBLE PRECISION,
    clr_per90           DOUBLE PRECISION,
    int_per90           DOUBLE PRECISION,
    tck_per90           DOUBLE PRECISION,
    dist_per90          DOUBLE PRECISION,
    drb_per90           DOUBLE PRECISION,
    sprints_per90       DOUBLE PRECISION,
    op_crs_a_per90      DOUBLE PRECISION,
    op_crs_c_per90      DOUBLE PRECISION,
    hdrs_l_per90        DOUBLE PRECISION,
    hdrs_w_per90        DOUBLE PRECISION,
    cln_per90           DOUBLE PRECISION,
    xa_per90            DOUBLE PRECISION,
    xg_per90            DOUBLE PRECISION,
    np_xg_per90         DOUBLE PRECISION,
    -- totals
    xg_op               DOUBLE PRECISION,
    fouls_made          INT,
    fouls_against       INT,
    yellow_cards        INT,
    red_cards           INT
    -- (recommendation removed in 0003; 29 new columns added via ALTER TABLE)
);

-- New columns added by 0003 migration:
-- best_pos, position, rating, height, preferred_foot, left_foot, right_foot,
-- tcon_per90, xsv_pct, sv_pct, ccc, aer_a_per90, k_hdrs_per90,
-- crs_a_per90, cr_c_per90, con_per90, goals_per90, saves_per90,
-- tgls_per90, mlg, offsides, asts_per90, kp_per90, xgp_per90,
-- shots_outside_box_per90, sht_per90, shot_per90, k_tck_per90,
-- shts_blckd_per90

-- squad_matchstats_snapshot gets two extra columns:
-- opponent          VARCHAR(255),
-- ingame_matchdate  DATE

-- Single-column indexes for ingestion, date, and unique_id lookups
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_scouting_ingestion
    ON silver.scouting_snapshot (ingestion_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_scouting_ingame_date
    ON silver.scouting_snapshot (ingame_date);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_scouting_unique_id
    ON silver.scouting_snapshot (unique_id);

-- Composite index for gold-layer dedup queries: (unique_id, club, division)
-- Gold deduplicates across silver batches using this triple as the dedup key.
-- Without this index, gold would need a full sequential scan + sort for every
-- dedup query. The index trades small storage overhead (~32 bytes/row) for
-- dramatically faster dedup performance on large datasets.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_scouting_dedup
    ON silver.scouting_snapshot (unique_id, club, division);
```

### `squad_matchstats_snapshot` extras

```sql
CREATE TABLE silver.squad_matchstats_snapshot (
    ...
    -- all shared columns above +
    opponent          VARCHAR(255),
    ingame_matchdate  DATE,
    ...
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_matchstats_opponent
    ON silver.squad_matchstats_snapshot (opponent);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_matchstats_matchdate
    ON silver.squad_matchstats_snapshot (ingame_matchdate);
```

## Logging points

| Point                   | Level   | Message                                                |
| ----------------------- | ------- | ------------------------------------------------------ |
| Task starts             | INFO    | "Starting silver transformation"                       |
| Idempotency skip        | WARNING | "Duplicate silver ingestion detected, returning early" |
| UDFs registered         | INFO    | "Registered 5 DuckDB UDFs"                             |
| Source rows counted     | INFO    | "Bronze source rows: %d"                               |
| Transform complete      | INFO    | "Transformed rows (after filter): %d"                  |
| INSERT complete         | INFO    | "Inserted into silver.%s"                              |
| Silver rows verified    | INFO    | "Silver rows: %d, dropped: %d"                         |
| Retrying                | WARNING | "Retrying (attempt %d): %s"                            |
| Retries exhausted       | ERROR   | "Failed permanently: %s"                               |
| Non-retryable exception | ERROR   | "Non-retryable: %s — investigate"                      |

On success and failure, `DatasetEvent` entries are created (matching the
bronze pattern) for SSE-driven UI consumption:

```python
# On success:
DatasetEvent.objects.create(
    upload=upload,
    event_type=DatasetEvent.EventType.SILVER_COMPLETED,
    payload={"attempt": attempt, "source_rows": source_count, "inserted_rows": inserted_count},
)

# On failure:
DatasetEvent.objects.create(
    upload=upload,
    event_type=DatasetEvent.EventType.SILVER_FAILED,
    payload={"attempt": attempt, "error_type": ..., "error_message": ...},
)
```

## Silver audit models

### SilverIngestion

```python
class SilverIngestion(models.Model):
    bronze_ingestion = models.ForeignKey(
        "bronze.BronzeIngestion",
        on_delete=models.PROTECT,
        related_name="silver_ingestions",
    )
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=PipelineStatus.choices,
        default=PipelineStatus.PENDING,
    )
    simulation_source = models.CharField(max_length=255)
    snapshot_type = models.CharField(max_length=50)
    source_file_hash = models.CharField(max_length=64, db_index=True)
    source_row_count = models.IntegerField(null=True, blank=True)
    inserted_row_count = models.IntegerField(null=True, blank=True)
    dropped_row_count = models.IntegerField(default=0)
    column_count = models.IntegerField(null=True, blank=True)
    error_type = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    parse_progress_current = models.PositiveSmallIntegerField(default=0)
    parse_progress_total = models.PositiveSmallIntegerField(default=4)
    parse_progress_description = models.CharField(max_length=100, default="Pending")

    class Meta:
        ordering = ["-started_at"]

    @property
    def duration_seconds(self):
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def parse_progress_percent(self):
        if not self.parse_progress_total:
            return 0
        return int((self.parse_progress_current / self.parse_progress_total) * 100)
```

### SilverIngestionTask

```python
class SilverIngestionTask(models.Model):
    ingestion = models.ForeignKey(
        SilverIngestion,
        on_delete=models.CASCADE,
        related_name="task_rows",
    )
    celery_task_id = models.CharField(max_length=255, db_index=True, blank=True, default="")
    step = models.CharField(max_length=20, default="transform")
    attempt = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.PENDING,
    )
    phase = models.CharField(
        max_length=30,
        choices=SilverIngestionPhase.choices,
        blank=True,
        default="",
    )
    worker_hostname = models.CharField(max_length=255, blank=True, default="")
    error_type = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ingestion", "step", "attempt"],
                name="unique_silver_ingestion_step_attempt",
            ),
        ]

    @property
    def duration_seconds(self):
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None
```

## Implementation order

| #   | File                           | What                                                                                                                                  |
| --- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `apps/silver/models.py`        | SilverIngestion, SilverIngestionTask, SilverIngestionPhase                                                                            |
| 2   | `apps/datasets/models.py`      | Add SILVER_COMPLETED / SILVER_FAILED to DatasetEvent.EventType                                                                        |
| 3   | Migration: silver audit models | `makemigrations silver`                                                                                                               |
| 4   | Migration: silver data tables  | RunSQL with `CREATE SCHEMA IF NOT EXISTS silver`, 3 `CREATE TABLE`, indexes (single + composite), `reverse_sql` for each              |
| 5   | `apps/silver/tasks.py`         | `duckdb_pg_connect()` (import from bronze), 4 UDFs, `transform_to_silver()`, `dispatch_silver_ingestion()`, `build_silver_pipeline()` |
| 6   | `apps/bronze/tasks.py`         | Add `dispatch_silver_ingestion()` call in `ingest_to_bronze` success path                                                             |
| 7   | `apps/silver/admin.py`         | SilverIngestionAdmin + SilverIngestionTaskAdmin (read-only)                                                                           |
| 8   | `apps/silver/tests.py`         | Integration tests with real DuckDB + PostgreSQL                                                                                       |
| 9   | `AGENTS.md`                    | Append silver section                                                                                                                 |

---

## Decisions & Rationale

This section documents every significant design decision so that future
contributors (and future-you) can understand why something was done a
particular way, what alternatives were considered, and what tradeoffs
were accepted.

---

### Decision 1: Single ATTACH for both bronze and silver schemas

**Context:** DuckDB ATTACH connects to a PostgreSQL database. Both the
`bronze` and `silver` schemas live in the same PG database. The silver
layer needs to read from `bronze` tables and write to `silver` tables.

**Decision:** Use one ATTACH with alias `pg_db`. Access bronze as
`pg_db.bronze.{table}` and silver as `pg_db.silver.{table}`.

**Alternatives considered:**

- Two ATTACHes (`bronze_schema` + `silver_schema`): Opens two PG
  connections to the same database. DuckDB opens separate transactions
  per attached database and does not support cross-database transactions.
  Since both schemas are in the same PG database, two connections
  provide no benefit — only overhead.
- Reusing bronze's `bronze_schema` alias: Works but the alias name
  `bronze_schema` is misleading for silver writes.

**Rationale:** One connection is sufficient because a single ATTACH maps
to the entire PG database, not to an individual schema. The alias name
`pg_db` is neutral and descriptive. Reduces connection overhead and
simplifies cleanup.

**Consequences:**

- All SQL references use the same `pg_db.` prefix regardless of schema.
- If bronze and silver ever move to separate PG databases, the code will
  need two ATTACHes — but that's a future concern (and unlikely).

See ADR: `docs/adr/0003-single-attach-postgres.md`

---

### Decision 2: `SELECT DISTINCT` for dedup (exact row dedup)

**Context:** The silver layer needs to remove duplicate rows within a
single bronze batch. The original plan used `ROW_NUMBER() OVER (PARTITION
BY unique_id, club, division)`, which keeps only one row per
player/club/division combination.

**Decision:** Use `SELECT DISTINCT` on all columns. A row is a duplicate
only if every single column has the same value as another row.

**Alternatives considered:**

- `ROW_NUMBER() OVER (PARTITION BY unique_id, club, division)`: Drops
  legitimate distinct observations of the same player (e.g., same player
  with different clubs or different stats within the same batch). Too
  aggressive.
- No dedup in silver: All rows pass through. Bloat from exact FM export
  duplicates (~3% in the sample parquet). Gold must dedup everything.

**Rationale:** Within a single FM export, two rows with the same
`(unique_id, club, division)` but different `minutes` or `appearances`
are legitimate distinct observations (e.g., the FM export process
duplicated a row mid-save with partially updated data). We should keep
both. `SELECT DISTINCT` is the safest and most correct approach —
it removes only rows that carry zero additional information.

**Consequences:**

- `dropped_row_count` tracks only NULL unique_id drops, not DISTINCT removals.
- Gold layer will have slightly more rows to process but with no information loss.
- No tiebreaker needed — no decision about "which row wins."

---

### Decision 3: `recommendation VARCHAR(50)` (not 10)

**Context:** The original DDL had `recommendation VARCHAR(10)`. Sample
FM export data shows values like "Sign — Hot Prospect" (19 characters)
and "Transfer Listed" (15 characters).

**Decision:** Use `VARCHAR(50)`.

**Alternatives considered:**

- `VARCHAR` (unbounded): PostgreSQL stores as varlena. Zero truncation
  risk but no character limit at the type level.
- `VARCHAR(10)`: Silent truncation of valid FM recommendation values.

**Rationale:** `VARCHAR(50)` covers all observed and foreseeable FM
recommendation values without wasting storage. FM recommendation
vocabularies are short phrases, not paragraphs.

**Consequences:**

- Safe upper bound. No truncation for current data.
- If FM ever produces longer recommendations, the column can be
  widened in a migration.

---

### Decision 4: No transaction guard — bronze-style autoretum

**Context:** DuckDB's PostgreSQL ATTACH uses PG's autocommit mode.
Each SQL statement runs in its own implicit PG transaction. If a
multi-row INSERT via DuckDB fails mid-execution, PG rolls back the
entire statement.

**Decision:** No special transaction handling. Same pattern as bronze:
the `INSERT INTO pg_db.silver.{type} SELECT ...` is a single SQL
statement, atomic at the PG level. If it fails, PG rolls back entirely.
On retry, the INSERT runs fresh.

**Alternatives considered:**

- DuckDB `conn.begin()` / `conn.commit()`: DuckDB transactions do NOT
  propagate to ATTACH'd PostgreSQL connections. DuckDB opens separate
  transactions per attached database (per DuckDB docs: "transactions
  are only atomic within each database file"). Wrapping in a DuckDB
  transaction would have zero effect on PG.
- `INSERT ... WHERE NOT EXISTS`: Prevents re-insertion if rows exist.
  Dangerous — a partial failure could write some rows, and on retry
  the WHERE NOT EXISTS check would see those rows and skip, leaving
  the batch in a permanently partial state. Silent data loss.
- `DELETE + INSERT` inside DuckDB transaction: The DELETE and INSERT
  are still separate PG autocommit statements since DuckDB transactions
  don't wrap PG ATTACH operations. If DELETE succeeds and INSERT fails,
  you lose data.

**Rationale:** The PostgreSQL ATTACH layer uses DuckDB's COPY protocol,
which sends the entire result set as a single PG statement. PG wraps it
in an implicit transaction. If the connection drops mid-stream, PG
rolls back the entire COPY. The only failure mode is a DuckDB crash
before the INSERT starts — which is just a clean retry. No transaction
guard can improve on this without exposing PG transactions through
the ATTACH layer, which DuckDB doesn't support.

**Consequences:**

- If a PG `ConstraintException` occurs (e.g., duplicate PK), PG rejects
  the entire INSERT — zero rows committed. Not a concern since silver
  has no PK constraints.
- Same risk profile as bronze. The pattern is already accepted.

---

### Decision 5: `TRY_CAST` for unique_id instead of `::BIGINT`

**Context:** The bronze `raw_data` JSONB may contain non-numeric
values for the "Unique ID" field (e.g., if FM exports a corrupted
value). A bare `::BIGINT` cast throws `InvalidInputException` and
kills the entire batch.

**Decision:** Use `TRY_CAST(NULLIF(unique_id_str, '') AS BIGINT)`.
Non-numeric and empty values become SQL NULL, which is then naturally
filtered by the `WHERE unique_id IS NOT NULL` condition in the `final` CTE.

**Alternatives considered:**

- `NULLIF(unique_id_str, '')::BIGINT`: Throws on non-numeric. Risk of
  killing a batch for one bad row.
- CASE expression manually checking for known non-numeric patterns:
  Brittle — can't anticipate all future bad values.
- Filter non-numeric in Python before DuckDB: Adds round-trip overhead.

**Rationale:** `TRY_CAST` is DuckDB's built-in safe cast. It produces
NULL on any value that can't be cast to BIGINT. The single drop gate
(unique_id IS NOT NULL → dropped) naturally catches these. No batch
failures from bad data.

**Consequences:**

- Rows with unexpected unique_id values are silently dropped and
  counted in `dropped_row_count`.
- `dropped_row_count` may include legitimate data with corrupted
  unique_id — an investigation signal, not a bug.

---

### Decision 6: Date format `DD/MM/YYYY` confirmed

**Context:** The `expires` and `ingame_matchdate` fields use `str_split`
with hardcoded index positions `[3]` for year, `[2]` for month, `[1]`
for day. This assumes `DD/MM/YYYY`.

**Decision:** `DD/MM/YYYY` is the correct format for all FM date
fields. No locale-specific behavior — FM consistently exports dates
in day/month/year order.

**Alternatives considered:**

- `strptime`: DuckDB's `strptime` is strict and fails on single-digit
  months (e.g., `1/1/2027` fails because month "1" doesn't match
  `%m` format). This was the original reason `str_split` was chosen.
- `YYYY-MM-DD` assumption: Not how FM exports.

**Rationale:** The date format was verified against the sample parquet
export. All dates are DD/MM/YYYY. The `str_split + make_date` approach
is confirmed correct and handles single-digit months correctly.

**Consequences:**

- If a future FM version changes the date format, `str_split` indices
  would need to be re-verified and potentially parameterized per
  snapshot type.

---

### Decision 7: Conditional SQL for matchstats extra columns

**Context:** `squad_matchstats_snapshot` has two extra columns
(`opponent`, `ingame_matchdate`) that the other snapshot types don't.
The SQL pipeline must handle different column sets per type.

**Decision:** Generate SQL dynamically in Python. The shared 80
columns are always included for squad/scouting; the matchstats-only
columns are conditionally added on top of the 52 shared columns when
`snapshot_type == "squad_matchstats_snapshot"`. The column
list for INSERT is also conditional.

**Alternatives considered:**

- Always extract and add to all tables as nullable columns: Uniform SQL
  but forces all three silver tables to have opponent/matchdate columns
  even though they'll always be NULL for squad and scouting snapshots.
  Schema waste and conceptual confusion — "opponent" is meaningless
  on a scouting snapshot.

**Rationale:** Each silver table should contain only columns that are
semantically relevant to its snapshot type. Conditional SQL generation
adds a small amount of Python complexity but keeps the schema clean.

**Consequences:**

- `build_silver_pipeline()` function needed in `tasks.py`.
- Column counts differ per snapshot type: 80 for scouting/squad,
  54 for matchstats.
- The `column_count` field on SilverIngestion will vary.

---

### Decision 8: `DatasetEvent` for silver (SILVER_COMPLETED / SILVER_FAILED)

**Context:** The SSE-driven UI reads `DatasetEvent` entries for
real-time status updates. The bronze layer creates `BRONZE_COMPLETED`
and `BRONZE_FAILED` events. Silver should be visible in the same
event stream.

**Decision:** Add `SILVER_COMPLETED` and `SILVER_FAILED` to
`DatasetEvent.EventType`. Create events on silver task success and
failure, matching the bronze pattern exactly.

**Alternatives considered:**

- No DatasetEvent: SSE would need to poll `SilverIngestion.status`
  directly. Inconsistent with the bronze pattern and would require
  the SSE view to know about silver.

**Rationale:** The DatasetEvent abstraction decouples the SSE stream
from individual pipeline model schemas. Adding silver event types
maintains this decoupling and is consistent with the bronze pattern.

**Consequences:**

- Requires a migration on `apps/datasets` to add new enum values.
- SSE view doesn't need changes — it already reads any event type.

---

### Decision 9: Composite index `(unique_id, club, division)` for gold dedup

**Context:** Gold layer will deduplicate across silver batches using
`(unique_id, club, division)` as the dedup key. Without a composite
index, every gold dedup query requires a full sequential scan of the
silver table plus a sort.

**Decision:** Add a composite index `(unique_id, club, division)` on
each silver table, alongside the existing single-column indexes.

**Alternatives considered:**

- Only single-column indexes (`ingestion_id`, `ingame_date`,
  `unique_id`): Gold dedup queries would scan the entire table and sort
  in memory. Fast enough for small tables but doesn't scale.
- No additional index: Can be added later if needed. But adding an
  index on a large table with `CREATE INDEX CONCURRENTLY` is expensive.

**Rationale:** Silver tables are append-only (no UPDATEs, no DELETEs),
so index maintenance overhead is minimal. Batch INSERTs update the
index efficiently. The composite index directly supports gold's
primary query pattern. The storage cost (~32 bytes per row) is
negligible compared to a 45+ column table.

**Consequences:**

- Gold dedup queries benefit immediately without index rebuilds.
- Slightly slower batch INSERTs (index update per row).
- Index survives VACUUM and autovacuum without fragmentation (append-only).

---

### Decision 10: ATIACH alias `pg_db` (neutral naming)

**Context:** The DuckDB ATTACH alias needs a name. Bronze used
`bronze_schema` (descriptive of the layer, not the database content).

**Decision:** Use `pg_db` as the alias. It's short, neutral, and
describes what it connects to (PostgreSQL database) rather than what
it's used for (bronze vs silver).

**Alternatives considered:**

- `bronze_schema`: Reusing bronze's alias works but misleading for
  silver operations. Not wrong but lacks clarity.
- `silver_schema`: Would have been chosen if we used two ATTACHes.
  With one ATTACH, `silver_schema` implies it only accesses silver,
  which is wrong — we read bronze through it too.

**Rationale:** The alias is just a local handle for the DuckDB session.
It should be clear and not misleading. `pg_db` communicates "this is
our PostgreSQL database" without implying any particular schema.

**Consequences:**

- SQL reads as `pg_db.bronze.{type}` and `pg_db.silver.{type}` — both
  schemas are visible under the same prefix.
- If bronze's ATTACH is ever refactored to use `pg_db` too, the
  alias would be shared across layers (they'd need separate DuckDB
  connections for each Celery task anyway, so each session sets its
  own alias).

---

### Decision 11: `column_count` is set per snapshot type

**Context:** Silver's column count is fixed per snapshot type — 80 for
squad/scouting, 54 for matchstats. Unlike bronze (which discovers
columns from the parquet), silver's schema is set by migration DDL.
The column count is a schema drift signal — if the DDL and the code
disagree, a changed `column_count` value flags the mismatch.

**Decision:** `transform_to_silver` sets `column_count = 54 if extra_columns else 80`.

**Rationale:** A fixed count per snapshot type is a useful schema drift
detector. If a migration changes the schema without updating the count,
or if the code generates a different number of columns, the mismatch
appears in the admin.

**Consequences:**

- `column_count` is always populated (not NULL).
- If a new migration adds or removes columns without updating the
  pipeline code, the count will drift — which is the desired signal.
