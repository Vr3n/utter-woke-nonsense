# Football Manager Moneyball Data Platform — Compact Context

## Core Philosophy

Purpose:

- Build a realistic football analytics / Moneyball-style data platform.
- Store historical Football Manager exports for:
  - analytics
  - scouting
  - ML models
  - player progression analysis
  - tactical analysis

The project is intentionally designed like a real-world data engineering platform instead of a toy project.

Core priorities:

- replayability
- lineage
- immutability
- append-only ingestion
- temporal history preservation
- historical analytical truth

Key mental model:

```text id="ctx1"
The warehouse models observations about players over time,
NOT current player state.
```

---

# Architecture Philosophy

Uses Medallion Architecture:

```text id="ctx2"
Landing Zone → Bronze → Silver → Gold
```

Current implementation scope:

- Landing Zone
- Bronze Layer

Silver and Gold intentionally deferred until Bronze semantics stabilize.

Medallion references:

- [Datadef Medallion Architecture Guide](https://datadef.io/guides/en/medallion-architecture?utm_source=chatgpt.com)
- [ClickHouse Lakehouse Overview](https://clickhouse.com/resources/engineering/data-lakehouse?utm_source=chatgpt.com)
- [Databricks-style Medallion Concepts](https://www.erathos.com/en/blog/medallion-architechture-completeguide?utm_source=chatgpt.com)

Background references on Bronze/Silver/Gold semantics: ([Datadef][1])

---

# Tech Stack Philosophy

Django acts as:

- operational control plane
- ingestion interface
- metadata management
- orchestration UI

Celery acts as:

- async ingestion orchestrator
- retry system
- background workflow runner

DuckDB acts as:

- analytical transformation engine
- parquet transformation/query engine
- Silver-layer processing engine

PostgreSQL acts as:

- Bronze warehouse
- Gold serving warehouse
- metadata + lineage store

Parquet acts as:

- immutable raw storage
- replayable source archive

Architecture resembles:

- lightweight lakehouse
- ELT workflow
- analytics engineering platform

---

# Important DE Concepts Already Established

The system models:

- historical observations
- periodic snapshots
- temporal analytics

NOT:

- mutable current-state CRUD tables

The system follows:

- append-only ingestion
- immutable raw storage
- replayability-first design
- ELT instead of ETL

Key DE concepts already discussed:

- grain definition
- temporal modeling
- event time vs processing time
- lineage
- replayability
- idempotent ingestion
- periodic snapshot facts
- Bronze vs Silver responsibility separation
- star schema planned for Gold

---

# Snapshot Domains

## 1. squad_snapshot

Represents:

- periodic squad snapshots
- transfer values
- wages
- cumulative squad statistics

Examples:

- after 10 matches
- after transfer window
- end of season

Grain:

```text id="ctx3"
One row represents one player's observed state
within a single squad snapshot
at a specific simulation date.
```

---

## 2. scouting_snapshot

Represents:

- scouting observations
- recruitment evaluation snapshots
- external player analysis

Grain:

```text id="ctx4"
One row represents one scouted player's observed state
within a scouting snapshot
at a specific simulation date.
```

---

## 3. squad_matchstats_snapshot

Represents:

- player match-level observations
- tactical/performance analysis after matches

Grain:

```text id="ctx5"
One row represents one player's observed performance
for a specific match.
```

---

# Time Semantics

## ingame_date

Represents:

- Football Manager simulation-world date

Business/Event timestamp.

---

## upload_timestamp

Represents:

- when the warehouse received the upload

Operational/Processing timestamp.

These timestamps are intentionally different concepts.

---

# Landing Zone

Purpose:

- immutable raw archive
- replayability
- rollback capability
- raw truth preservation

Responsibilities:

- accept CSV/HTML uploads
- convert to parquet
- preserve raw structure
- attach ingestion metadata

Landing Zone performs:

- minimal transformations only

Landing Zone does NOT:

- clean values
- normalize units
- apply business logic
- derive analytics

---

# Landing Zone Storage Structure

```text id="ctx6"
source/
    snapshot_type/
        save_name/
            ingame_date/
                file_name_<ingestion_timestamp>.parquet
```

Example:

```text id="ctx7"
source/
    squad_snapshot/
        malaga_rtg/
            2026-05-18/
                squad_snapshot_20260518_221030.parquet
```

---

# Metadata Requirements

Uploads contain:

- save_name
- ingame_date
- upload_timestamp
- snapshot_type
- source_file_hash

Purpose:

- lineage
- replayability
- idempotency
- rollback support
- historical querying

---

# Duplicate Prevention

The ingestion system is idempotent.

Duplicate uploads are prevented using:

- content hashing (`source_file_hash`)

If file hash already exists:

- upload rejected as duplicate

---

# Bronze Layer

Purpose:

- queryable raw ingestion layer
- temporal historical storage
- replay source for Silver
- lineage-aware warehouse layer

Bronze stores:

- parquet data
- ingestion metadata
- structured temporal metadata

Bronze is:

- append-only
- immutable
- minimally transformed

---

# Bronze Tables

```text id="ctx8"
bronze.squad_snapshot
bronze.scouting_snapshot
bronze.squad_matchstats_snapshot
```

---

# Bronze Responsibilities

Bronze:

- stores raw-ish source observations
- preserves historical truth
- stores ingestion metadata
- supports replayability
- supports lineage tracking

Bronze does NOT:

- normalize currencies
- split columns
- clean units
- derive analytics
- infer trends
- calculate business logic

Bronze preserves raw source semantics.

---

# Bronze Metadata Strategy

Bronze contains structured metadata columns:

- save_name
- ingame_date
- upload_timestamp
- snapshot_type
- source_file_hash

Additional ingestion transaction tables with FK relationships are acceptable for:

- lineage
- navigation
- operational tracking

Important insight:

- transactions solve orphan-row problems better than fully denormalized JSONB metadata.

---

# Planned Silver Layer (Future)

Silver responsibilities planned:

- cleaning
- standardization
- normalization
- semantic consistency

Examples already planned:

- split appearances into starts/subs
- parse transfer value ranges
- normalize currencies
- strip units
- type casting
- deduplication

Silver is:

- semantically clean
- NOT business-aggregated

---

# Planned Gold Layer (Future)

Gold will contain:

- star schema
- fact tables
- dimensions
- business analytics
- scouting analytics
- ML-ready analytical datasets

Gold intended for:

- dashboards
- OLAP queries
- tactical analysis
- Moneyball-style analytics

---

# Pipeline Mental Model

System workflow intuition:

```text id="ctx9"
User Upload
    ↓
Django validates metadata
    ↓
Celery ingestion task
    ↓
CSV/HTML → Parquet conversion
    ↓
Landing Zone storage
    ↓
Bronze ingestion
    ↓
Future Silver transformations
    ↓
Future Gold warehouse
```

System resembles:

- lightweight lakehouse
- analytics engineering platform
- ingestion orchestration platform

---

# Important Conceptual Insights Already Established

## Bronze stores observations, not conclusions

GOOD:

```text id="ctx10"
Observed player snapshot at specific time
```

BAD:

```text id="ctx11"
Player improved after interval
```

Trend analysis belongs later in Silver/Gold.

---

## Warehouse Thinking

The platform stores:

```text id="ctx12"
Historical truth at specific moments in time
```

NOT:

- latest mutable player state

---

## Replayability Principle

If:

- Bronze corrupted
- parsing logic changes
- Silver needs rebuilding

The entire warehouse should be reconstructable from:

- Landing Zone parquet files
  OR
- Bronze tables

---

# Current Scope Boundary

Current implementation scope:

- Landing Zone
- Bronze Layer only

Avoid:

- overengineering
- premature Gold design
- premature ML pipelines

Focus currently:

- ingestion semantics
- lineage
- replayability
- temporal modeling
- Bronze correctness

Relevant medallion + DE references used during design discussions: ([Datadef][1])

[1]: https://datadef.io/guides/en/medallion-architecture?utm_source=chatgpt.com "Medallion Architecture Guide: Bronze, Silver, Gold Data Platform (2025)"
