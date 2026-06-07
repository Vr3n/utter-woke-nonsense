# Silver Layer

After Bronze is ingested, DuckDB cleans and transforms the dirty data into typed,
null-safe columns. Silver is the first semantically queryable layer — per-90 rates
are typed as FLOAT, currencies are parsed to INT, appearances are split, and all
FM column abbreviations are expanded to readable snake_case names.

Silver reads from the bronze PostgreSQL table (not from parquet). One bronze batch
at a time — triggered per bronze completion via direct Celery call from
`ingest_to_bronze`'s success path. Cross-batch deduplication is gold's concern.

## Principles

- **No sentinel values.** All unknowns → SQL NULL. No `-1`, no `'UNKNOWN'`.
- **`unique_id` is mandatory.** Rows without a Unique ID are dropped before INSERT
  and counted in `SilverIngestion.dropped_row_count`. No row reaches silver
  without a `unique_id`.
- **There is no `is_usable` flag.** No `unusable_reason` column. No quality tiers.
  If a row is in silver, it is a valid typed observation.
- **Deduplicate within a single silver batch.** Rows matching on
  `(unique_id, club, division)` collapsed to first occurrence in source parquet
  file. A transferred player differs in `club`, so those rows are preserved.
  Cross-batch deduplication is gold's concern.
- **All `/90` columns** are per-90-minute performance rates. Renamed with `_per90`.
- **All currency fields** (`transfer_value`, `wage`) split into `_min` and `_max`
  columns to represent FM's value ranges.
- **No CHECK constraints. No FK dimension tables** on any categorical column.
  FM changes vocabularies between versions — constraints break ingestion.

## Database & Tables

Silver tables live in the `silver` PostgreSQL schema.

- `silver.squad_snapshot`
- `silver.scouting_snapshot`
- `silver.squad_matchstats_snapshot`

### Structured metadata columns (all tables)

```
ingestion_id        BIGINT        NOT NULL  → FK to silver_ingestion.id
simulation_source   VARCHAR(255)  NOT NULL  → FM version
save_name           VARCHAR(255)  NOT NULL  → from save_master.slug
ingame_date         DATE          NOT NULL  → when extracted from simulation
upload_timestamp    TIMESTAMPTZ   NOT NULL  → when warehouse received the file
snapshot_type       VARCHAR(50)   NOT NULL  → squad_snapshot | scouting_snapshot | squad_matchstats_snapshot
source_file_hash    VARCHAR(64)   NOT NULL  → SHA-256, lineage link to bronze
```

### Additional columns for `squad_matchstats_snapshot`

```
opponent           VARCHAR(255)  NULL  → opponent team name
ingame_matchdate   DATE          NULL  → match date in simulation
```

Both are NULL-able. A row with no match date or opponent is suspicious but
should not crash the ingestion batch.

## Column mapping — scouting & squad snapshots

All three snapshot types share these columns. `squad_matchstats_snapshot` adds
`opponent` and `ingame_matchdate` on top.

### Identifiers & demographics

| # | Source | Silver name | Type | Notes |
|---|---|---|---|---|
| 1 | `Unique ID` | `unique_id` | BIGINT | FM internal player ID |
| 2 | `Player` | `player` | VARCHAR | nullable |
| 3 | `Age` | `age` | INT | nullable |
| 4 | `Nation of Birth` | `nation_of_birth` | VARCHAR | nullable |
| 5 | `Club` | `club` | VARCHAR | nullable |
| 6 | `Division` | `division` | VARCHAR | nullable |

### Playing time & appearances

| # | Source | Silver name | Type | Notes |
|---|---|---|---|---|
| 7 | `Appearances` | `starting_appearances` | INT | parsed from `starts(subs)` |
| 8 | (same) | `substitute_appearances` | INT | 0 when subs omitted in format, NULL if source null |
| 9 | `Minutes` | `minutes` | INT | nullable |
| 10 | `Playing Time` | `playing_time` | VARCHAR | categorical, open set, no constraint |

### Financials

Parse currency strings with K (×1000) and M (×1,000,000) multipliers after
stripping any leading non-numeric prefix (any currency symbol). Ranges become
min/max columns. Single values have min = max.

| # | Source | Silver name | Type | Notes |
|---|---|---|---|---|
| 11 | `Transfer Value` | `transfer_value_min` | INT | nullable, parsed numeric |
| 12 | (same) | `transfer_value_max` | INT | nullable, parsed numeric |
| 13 | `Wage` | `wage_min` | INT | nullable, stripped of currency + ` p/w` |
| 14 | (same) | `wage_max` | INT | nullable, same |
| 15 | `Expires` | `expires` | DATE | `str_split` + `make_date`; `N/A`, `-`, `Out of contract`, `Retired` → NULL |

### Performance rates (per 90 minutes)

All source columns with `/90` suffix are renamed to `_per90`.

| # | Source | Silver name | Type | Expansion |
|---|---|---|---|---|
| 16 | `Poss Lost/90` | `poss_lost_per90` | FLOAT | Possession lost |
| 17 | `Poss Won/90` | `poss_won_per90` | FLOAT | Possession won |
| 18 | `Ps C/90` | `ps_c_per90` | FLOAT | Passes completed |
| 19 | `Ps A/90` | `ps_a_per90` | FLOAT | Passes attempted |
| 20 | `Pr passes/90` | `pr_passes_per90` | FLOAT | Progressive passes |
| 21 | `OP-KP/90` | `op_kp_per90` | FLOAT | Open-play key passes |
| 22 | `Ch C/90` | `ch_c_per90` | FLOAT | Chances created |
| 23 | `Pres A/90` | `pres_a_per90` | FLOAT | Presses attempted |
| 24 | `Pres C/90` | `pres_c_per90` | FLOAT | Presses completed |
| 25 | `Blk/90` | `blk_per90` | FLOAT | Blocks |
| 26 | `Clr/90` | `clr_per90` | FLOAT | Clearances |
| 27 | `Int/90` | `int_per90` | FLOAT | Interceptions |
| 28 | `Tck/90` | `tck_per90` | FLOAT | Tackles |
| 29 | `Dist/90` | `dist_per90` | FLOAT | Distance (km) |
| 30 | `Drb/90` | `drb_per90` | FLOAT | Dribbled past |
| 31 | `Sprints/90` | `sprints_per90` | FLOAT | Sprints |
| 32 | `OP-Crs A/90` | `op_crs_a_per90` | FLOAT | Open-play crosses attempted |
| 33 | `OP-Crs C/90` | `op_crs_c_per90` | FLOAT | Open-play crosses completed |
| 34 | `Hdrs L/90` | `hdrs_l_per90` | FLOAT | Headers lost |
| 35 | `Hdrs W/90` | `hdrs_w_per90` | FLOAT | Headers won |
| 36 | `Cln/90` | `cln_per90` | FLOAT | Clean sheets |
| 37 | `xA/90` | `xa_per90` | FLOAT | Expected assists |
| 38 | `xG/90` | `xg_per90` | FLOAT | Expected goals |
| 39 | `NP-xG/90` | `np_xg_per90` | FLOAT | Non-penalty expected goals |

### Totals & aggregates

| # | Source | Silver name | Type | Notes |
|---|---|---|---|---|
| 40 | `xG-OP` | `xg_op` | FLOAT | Total xG overperformance (not per-90), nullable |
| 41 | `Fouls Made` | `fouls_made` | INT | nullable |
| 42 | `Fouls Against` | `fouls_against` | INT | nullable |
| 43 | `Yel` | `yellow_cards` | INT | nullable |
| 44 | `Red cards` | `red_cards` | INT | nullable |
| 45 | `Best Pos` | `best_pos` | VARCHAR | e.g. `"M (C)"`, `"GK"` |
| 46 | `Position` | `position` | VARCHAR | e.g. `"DM, M (C)"` |
| 47 | `Rating` | `rating` | DOUBLE | `-` → NULL |
| 48 | `Height` | `height` | INT | Strip `" cm"` via `parse_height` UDF |
| 49 | `Preferred Foot` | `preferred_foot` | VARCHAR | e.g. `"Right-Footed"` |
| 50 | `Left Foot` | `left_foot` | VARCHAR | e.g. `"Very Strong"` |
| 51 | `Right Foot` | `right_foot` | VARCHAR | e.g. `"Very Weak"` |
| 52 | `Tcon/90` | `tcon_per90` | DOUBLE | Turnovers conceded per 90 |
| 53 | `xSv %` | `xsv_pct` | INT | Expected save % (0–100) |
| 54 | `Sv %` | `sv_pct` | INT | Actual save % (0–100) |
| 55 | `CCC` | `ccc` | INT | Clear cut chances |
| 56 | `Aer A/90` | `aer_a_per90` | DOUBLE | Aerial attempts per 90 |
| 57 | `K Hdrs/90` | `k_hdrs_per90` | DOUBLE | Key headers per 90 |
| 58 | `Crs A/90` | `crs_a_per90` | DOUBLE | Crosses attempted per 90 |
| 59 | `Cr C/90` | `cr_c_per90` | DOUBLE | Crosses completed per 90 |
| 60 | `Con/90` | `con_per90` | DOUBLE | Concentration per 90 |
| 61 | `Goals per 90 minutes` | `goals_per90` | DOUBLE | Goals per 90 |
| 62 | `Saves/90` | `saves_per90` | DOUBLE | Saves per 90 |
| 63 | `Tgls/90` | `tgls_per90` | DOUBLE | Tackles lost per 90 |
| 64 | `MLG` | `mlg` | INT | Minutes per league goal |
| 65 | `Off` | `offsides` | INT | Offsides (`off` is SQL reserved) |
| 66 | `Asts/90` | `asts_per90` | DOUBLE | Assists per 90 |
| 67 | `KP/90` | `kp_per90` | DOUBLE | Key passes per 90 |
| 68 | `xGP/90` | `xgp_per90` | DOUBLE | Expected goals prevented per 90 |
| 69 | `Shots From Outside The Box Per 90 minutes` | `shots_outside_box_per90` | DOUBLE | Long shots per 90 |
| 70 | `ShT/90` | `sht_per90` | DOUBLE | Shots on target per 90 |
| 71 | `Shot/90` | `shot_per90` | DOUBLE | Total shots per 90 |
| 72 | `K Tck/90` | `k_tck_per90` | DOUBLE | Key tackles per 90 |
| 73 | `Shts Blckd/90` | `shts_blckd_per90` | DOUBLE | Shots blocked per 90 |

## Cleaning functions

All cleaning happens in DuckDB SQL via user-defined functions or CASE expressions.

### `parse_currency(value) -> (min INT, max INT)`

Strip any leading non-numeric prefix (any currency symbol — `£`, `€`, `$`, etc.),
then parse K (×1000) and M (×1,000,000) multipliers.

```
NULL            → (NULL, NULL)
''              → (NULL, NULL)
'-'             → (NULL, NULL)
'Unknown'       → (NULL, NULL)
'£130K'         → (130000, 130000)
'€2.8M'         → (2800000, 2800000)
'£30K - £300K'  → (30000, 300000)
'£0'            → (0, 0)
'£0 - £0'       → (0, 0)
'£500'          → (500, 500)        ← no K/M suffix, raw number
```

After stripping the currency symbol, if the remaining string is empty or
non-numeric → `(NULL, NULL)`, never an error.

### `parse_wage(value) -> (min INT, max INT)`

Same as `parse_currency` after stripping ` p/w` suffix.

```
'N/A'    → (NULL, NULL)
```

### `parse_appearances(value) -> (starts INT, subs INT)`

```
NULL      → (NULL, NULL)         ← whole field missing
'6 (1)'   → (6, 1)               ← starts and subs both present
'4'       → (4, 0)               ← subs omitted = 0, not NULL
'0'       → (0, 0)               ← known zero
```

The `0` default for subs applies only when the format omits the subs component.
If the whole field is `NULL`, both return `NULL`. The distinction: `"4"` means
"4 starts, 0 sub appearances" — that's a known value. `NULL` means the field
wasn't exported by FM.

### `parse_expires(value) -> DATE`

Two-step: `str_split` + `make_date`. Handles single-digit months and days
without regex padding. Never use `strptime` for FM dates — single-digit months
break DuckDB's strict parser.

```sql
CASE
    WHEN expires IS NULL THEN NULL
    WHEN expires IN ('N/A', '-', 'Out of contract', 'Retired') THEN NULL
    ELSE make_date(
        CAST(str_split(expires, '/')[3] AS INT),   -- year
        CAST(str_split(expires, '/')[2] AS INT),   -- month (handles 1 digit)
        CAST(str_split(expires, '/')[1] AS INT)    -- day
    )
END AS expires
```

### `rename_per90(column_name) -> str`

Replace `/90` with `_per90`, then map remaining source abbreviations to
lower_snake_case according to the mapping table above. Hyphens become underscores.
Spaces become underscores.

## Column naming conventions

- `lower_snake_case`
- `/90` → `_per90` (e.g. `ps_c_per90`)
- Hyphens → underscores (`OP-KP/90` → `op_kp_per90`)
- Spaces → underscores (`Nation of Birth` → `nation_of_birth`)
- Abbreviations preserved but documented in this file

## Silver audit model

Mirrors bronze exactly. Same shared enums from `core/choices.py`.

### `SilverIngestion`

```python
class SilverIngestion(models.Model):
    bronze_ingestion    = models.ForeignKey(BronzeIngestion, on_delete=models.PROTECT,
                                            related_name="silver_ingestions")
    status              = models.CharField(choices=PipelineStatus.choices, ...)
    simulation_source   = models.CharField(max_length=255)    # denormalised
    snapshot_type       = models.CharField(max_length=50)     # denormalised
    source_file_hash    = models.CharField(max_length=64)     # lineage to bronze
    source_row_count    = models.IntegerField(null=True)      # rows from bronze
    inserted_row_count  = models.IntegerField(null=True)      # written to silver
    dropped_row_count   = models.IntegerField(default=0)      # missing unique_id
    column_count        = models.IntegerField(null=True)      # schema drift signal
    started_at          = models.DateTimeField(null=True)
    completed_at        = models.DateTimeField(null=True)
    error_type          = models.CharField(blank=True, default="")
    error_message       = models.TextField(blank=True, default="")
```

`dropped_row_count` should be `0` on clean FM exports. Non-zero signals a bad
export or parser issue worth investigating. Dropped rows are not stored anywhere
in silver — `dropped_row_count` is the only record.

### `SilverIngestionTask`

Mirrors `BronzeIngestionTask` exactly — one row per attempt, same status machine,
same fields.
