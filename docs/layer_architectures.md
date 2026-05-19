# Defining the Layers

## [18/05/2026] Core philosophy.

Why do we need this?

- I am playing football manager and I want to do it the moneyball way.
- Collecting data, and analyzing players is the task, and we need a good system for analysis.
- This data warehouse will help store the data, so that we can create Models, or Do simple analysis.

The System Priorities:

- `replayability`
- `lineage`
- `immutability`
- `append-only ingestion`
- `temporal history preservation.`
- `idempotenncy`

## Landing & Bronze Zones.

I am starting with Landing Zone to Bronze Layer.

- The landing zone consists of parsing the csv / html and saving them as parquet files.
- User will upload these files with the required metadata.
- That meta-data will be useful for rollbacks, lineage, replayability.
- The system is designed to be idempotent.
- Duplicate uploads are rejected using content hashing.

bronze will consist of:

- `squad_snapshot`
- `scouting_snapshot`
- `squad_matchstats_snapshot`

### The Landing Zone: The raw file Archive

- html / csv will be converted into parquet file.
- Stored in `source/type/save_name/ingame_date/file_name_<ingestion_timestamp>.parquet`.
- This way we will have source replayability.

- The type here is `squad_snapshot`, `scouting_snapshot` or `squad_matchstats_snapshot`.
- Easy segregation of different type of snapshots.

#### Landing Zone Responsibilities

- Accepts CSV or HTML uploads.
- converts them into parquet format.
- stores immutable raw snapshots.
- adds ingestion metadata.
- does NOT clean or normalize data

Minimal Transformations Only.

Landing Zone Storage Structure:

```
- source/
  - snapshot_type/
    - save_name/
      - ingame_data/
        - file*name*[ingestion_timestamp].parquet
```

### Metadata Requirements

Uploads require metadata such as:

- `simulation_source`: The version of football manager this data was extracted from.
- `save_name`: Universe from which the data was extracted.
- `ingame_date`: The date when data was extracted from the simulation.
- `upload_timestamp`: when warehouse received the data.
- `snapshot_type`: is it `squad_snapshot`, `scouting_snapshot`, or `squad_matchstats_snapshot`.
- `source_file_hash`: For content idempotency.

### The Bronze Layer: Raw ingestion layer in db.

- Store the parquet data into the bronze db with the metadata.
- Also store a transaction with a FK so that it is easier to navigate through, and also not introduce drift.
- The date related metadata will also be stored with the structured bronze data so that it is easy to query and partition.
- Existing Bronze records are never mutated.
- Corrections require new ingestion batches.

The bronze layer does not clean values, or make changes to raw data.

#### Bronze tables are

- `bronze.squad_snapshot`
- `bronze.scouting_snapshot`
- `bronze.squad_matchstats_snapshot`

Bronze row represent An observation about a player at a specific simulation point in time.

#### Bronze Metadata strategy

structured metadata columns are:

- `simulation_source`
- `save_name`
- `ingame_date`
- `upload_timestamp`
- `snapshot_type`
- `source_file_hash`

unstructured json columns are:

- `source_file_name`
- `source_file_hash`

#### Bronze Layer Constraints

Bronze layer Does NOT:

- `clean values`
- `normalize currencies`
- `split columns`
- `remove units`
- `calculate analytics`
- `infer business logic`
- `dervice trends`

Bronze only preserves raw historical observations with lineage metadata.

## Snapshot Types

The bronze layer currently supports three snapshot domains:

### 1: squad_snapshot

It represents:

- Periodic squad state snapshots.
- transfer values
- wages
- cumulative squad statistics

Grain:

One row represents one player's observed state within a single squad snapshot at a specific simulation date.

### 2: scouting_snapshot

It represents:

- Periodic squad state snapshots.
- recruitment evaluation snapshots
- scouting intelligence observations.

Grain:

One row represents one scouted player's observed state within a single scouting snapshot at a specific simulation date.

### 3: squad_matchstats_snapshot

Represents:

- player match-level performance observations.
- tactical & performance analysis after matces.

Grain:

One row represents one player's observed stats after a single match after a specific match.

## Temporal Modelling

The platform models:

`Player observations over time`

This allows:

- Historical analysis
- progression tracking
- scouting evolution
- transfer value evolution
- replayability
- time-series analysis
