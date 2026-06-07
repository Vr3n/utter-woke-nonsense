# ADR 0003: Use a Single DuckDB ATTACH for Both Bronze and Silver PostgreSQL Schemas

**Status:** Accepted

**Date:** 2026-05-28

## Context

The silver transformation pipeline reads data from `bronze` schema tables
and writes cleaned data to `silver` schema tables. Both schemas reside in
the same PostgreSQL database. DuckDB connects to PostgreSQL via the
`ATTACH ... (TYPE postgres)` statement, which maps the entire PG database
to a local alias.

The question: should the silver task use one ATTACH (covering both schemas)
or two separate ATTACHes (one aliased for bronze reads, one for silver writes)?

## Decision

Use a single ATTACH with alias `pg_db`:

```python
conn.execute(f"ATTACH '{pg_conn}' AS pg_db (TYPE postgres)")
```

References become:
- Reading bronze data: `pg_db.bronze.{snapshot_type}`
- Writing to silver:  `pg_db.silver.{snapshot_type}`

Both the source (bronze) and destination (silver) tables are in the same
PostgreSQL database. A single ATTACH provides access to all schemas within
that database under one alias.

## Alternatives Considered

### Two ATTACHes with separate aliases

```python
conn.execute(f"ATTACH '{pg_conn}' AS bronze_layer (TYPE postgres)")
conn.execute(f"ATTACH '{pg_conn}' AS silver_layer (TYPE postgres)")
```

- **Pros:** SQL reads more explicitly — `bronze_layer.bronze.{type}` vs
  `silver_layer.silver.{type}`.
- **Cons:** Opens two connections to the same PG database. DuckDB opens
  separate transactions per attached database and does not support
  cross-database atomicity. The two connections provide no isolation or
  performance benefit since they connect to the same PG server.
  Additional connection overhead (~0.5ms per ATTACH).

### Reuse bronze's `bronze_schema` alias

- **Pros:** Consistent with existing bronze code.
- **Cons:** The alias `bronze_schema` is misleading for silver writes —
  it suggests the connection only accesses the bronze schema, but it's
  used to write to silver.

## Rationale

- **One ATTACH is sufficient:** DuckDB's `ATTACH` maps to a PostgreSQL
  **database** (not a schema). Since both `bronze` and `silver` are
  schemas within the same PG database, one ATTACH provides access to both.
- **Lower overhead:** One PG connection instead of two.
- **Self-documenting:** The PG schema name (`bronze` vs `silver`) in the
  fully-qualified table reference already tells you where the data lives.
  The ATTACH alias adds no information — it's just a local handle.
- **Future cross-schema queries:** If a silver CTE ever needs to JOIN
  bronze and silver data (e.g., for validation), both are under one alias.
  Two ATTACHes would require cross-alias JOIN syntax that works but is
  unnecessary complexity.

## Consequences

- All SQL in the silver pipeline uses `pg_db.{schema}.{table}` references.
- If bronze and silver ever move to separate PostgreSQL databases, this
  code will need two ATTACHes — but that's a future concern and unlikely
  in this architecture.
- The ATTACH alias `pg_db` is neutral and not tied to any specific layer.
