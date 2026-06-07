Here's everything consolidated from the last two exchanges.

---

## Silver Layer — Decisions Consolidated

### Core drop rule

```
unique_id IS NULL  →  DROP before INSERT, log in dropped_row_count
unique_id present  →  INSERT into silver, fully typed and cleaned
```

`unique_id` is the single gate. No `is_usable` flag. No `unusable_reason` vocabulary. No quality tiers. A row either has an identity or it doesn't exist in silver.

---

### Schema — what's removed, what stays

**Removed entirely:**

- `is_usable BOOLEAN`
- `unusable_reason VARCHAR`
- Gold base view (`WHERE is_usable = TRUE`)
- Quality audit view (`WHERE is_usable = FALSE`)

**Silver row contract:** if it's in silver, it's a valid typed observation. Gold filters by what it needs analytically — it doesn't filter by quality flags.

---

### Categorical columns — VARCHAR, no constraints

| Column            | Type         | Rule                                        |
| ----------------- | ------------ | ------------------------------------------- |
| `playing_time`    | VARCHAR NULL | 15 known FM values, open set, no constraint |
| `recommendation`  | VARCHAR NULL | A+ through F, `-` cleaned to NULL           |
| `division`        | VARCHAR NULL | open set, save-dependent                    |
| `club`            | VARCHAR NULL | open set                                    |
| `nation_of_birth` | VARCHAR NULL | open set, country names                     |

No `CHECK` constraints. No FK dimension tables. FM changes label vocabularies between versions — constraints break ingestion. A `playing_time_dim` dimension table becomes worth building only when the label→tier mapping is duplicated across multiple gold models.

---

### Cleaning functions — confirmed behaviour

#### `parse_currency(value) → (min INT, max INT)`

Strip any leading non-numeric prefix (any currency symbol — `£`, `€`, `$`), then parse K/M multipliers:

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
'£500'          → (500, 500)     ← no K/M suffix, raw number
```

After stripping the currency symbol, if the remaining string is empty or non-numeric → `(NULL, NULL)`, never an error.

#### `parse_wage(value) → (min INT, max INT)`

Same as `parse_currency` after stripping ` p/w` suffix. `'N/A'` → `(NULL, NULL)`.

#### `parse_appearances(value) → (starts INT, subs INT)`

```
NULL      → (NULL, NULL)
'6 (1)'   → (6, 1)
'4'       → (4, 0)      ← no subs component means 0, not NULL
'0'       → (0, 0)
```

The `0` default for subs applies only when the format omits the subs component. If the whole field is `NULL`, both return `NULL`. The distinction: `"4"` means "4 starts, 0 sub appearances" — that's a known value. `NULL` means the field wasn't exported.

#### `parse_expires(value) → DATE`

Two-step: `str_split` + `make_date`. Handles single-digit months and days without regex padding:

```sql
CASE
    WHEN expires IS NULL THEN NULL
    WHEN expires IN ('N/A', '-', 'Out of contract', 'Retired') THEN NULL
    ELSE make_date(
        CAST(str_split(expires, '/')[3] AS INT),  -- year
        CAST(str_split(expires, '/')[2] AS INT),  -- month (handles single digit)
        CAST(str_split(expires, '/')[1] AS INT)   -- day
    )
END AS expires
```

Missing from original doc, now added: `'Out of contract'`, `'Retired'`, `'-'` all → `NULL`.

---

### Deduplication

**Scope:** within a single silver batch only. Cross-batch player tracking is gold's concern.

**Rule:** rows matching on `(unique_id, club, division)` collapsed to first occurrence in source parquet file.

```sql
ROW_NUMBER() OVER (
    PARTITION BY unique_id, club, division
    ORDER BY (SELECT NULL)  -- stable within DuckDB's single parquet read
) = 1
```

`ORDER BY (SELECT NULL)` is deterministic for a given parquet file — documented as "first occurrence in source file." A transferred player differs in `club`, so those rows are preserved as distinct observations.

---

### Silver trigger

One bronze batch at a time. Triggered per bronze completion via direct Celery call from `ingest_to_bronze`'s success path:

```python
dispatch_silver_ingestion(bronze_ingestion_id)
```

Same pattern as landing→bronze. No Redis pub/sub. No Redis Streams.

---

### `SilverIngestion` audit model

Mirrors `BronzeIngestion` exactly. Same shared enums from `core.choices`. Key fields:

```python
class SilverIngestion(models.Model):
    bronze_ingestion    = models.ForeignKey(BronzeIngestion, on_delete=models.PROTECT,
                                            related_name="silver_ingestions")
    status              = models.CharField(choices=PipelineStatus.choices, ...)
    simulation_source   = models.CharField(max_length=255)   # denormalised
    snapshot_type       = models.CharField(max_length=50)    # denormalised
    source_file_hash    = models.CharField(max_length=64)    # lineage to bronze
    source_row_count    = models.IntegerField(null=True)     # rows from bronze
    inserted_row_count  = models.IntegerField(null=True)     # written to silver
    dropped_row_count   = models.IntegerField(default=0)     # missing unique_id
    column_count        = models.IntegerField(null=True)     # schema drift signal
    started_at          = models.DateTimeField(null=True)
    completed_at        = models.DateTimeField(null=True)
    error_type          = models.CharField(blank=True, default="")
    error_message       = models.TextField(blank=True, default="")

    @property
    def duration_seconds(self): ...
```

`dropped_row_count` should be `0` on clean FM exports. Non-zero signals a bad export or parser issue worth investigating. The lineage story: bronze has 450 rows, silver inserted 447, dropped 3 (missing `unique_id`). Complete without storing the dropped rows anywhere.

`SilverIngestionTask` mirrors `BronzeIngestionTask` exactly — one row per attempt, same status machine, same fields.

---

### matchstats-specific columns

```sql
opponent          VARCHAR(255)  NULL   -- NULL-able, not NOT NULL
ingame_matchdate  DATE          NULL   -- NULL-able, not NOT NULL
```

Changed from the original doc. A row with no match date or opponent is suspicious but shouldn't crash the ingestion batch. Both feed into gold filtering by analytical need.

---

### `AGENTS.md` — silver additions

```markdown
## Silver layer — core rules

The single drop gate is unique_id. Rows without unique_id are dropped
before INSERT and counted in SilverIngestion.dropped_row_count.
No row reaches silver without a unique_id.

There is no is_usable flag. No unusable_reason column. No quality tiers.
If a row is in silver, it is a valid typed observation. Remove any
reference to is_usable from silver schema or queries.

Silver processes one bronze batch at a time. One BronzeIngestion →
one SilverIngestion. Cross-batch deduplication is gold's concern.

Deduplication within a batch: (unique_id, club, division).
Tiebreak: first occurrence in source parquet file.
ROW_NUMBER() OVER (PARTITION BY unique_id, club, division ORDER BY (SELECT NULL)) = 1

## Silver cleaning rules

parse_currency: strip any leading non-numeric prefix (any currency symbol),
then parse K/M. Empty or non-numeric remainder → (NULL, NULL), never error.
'-', 'Unknown', NULL all → (NULL, NULL).

parse_appearances: NULL source → (NULL, NULL).
Format without subs component e.g. "4" → (4, 0). 0 is known, not NULL.

parse_expires: str_split + make_date. Full null guard covers:
NULL, 'N/A', '-', 'Out of contract', 'Retired' → NULL.
Never use strptime for FM dates — single-digit months break it.

## Silver categorical columns

No CHECK constraints. No FK dimension tables on any categorical column.
FM changes vocabularies between versions.
recommendation: '-' → NULL. All other values stored as-is.
playing_time: stored as-is, 15 known values, open set.

## Silver audit

SilverIngestion.dropped_row_count is the only record of dropped rows.
Dropped rows are not stored anywhere in silver.
dropped_row_count = 0 is expected on clean exports.
Non-zero warrants investigation of the bronze parquet file.
```
