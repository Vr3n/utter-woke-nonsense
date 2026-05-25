from django.db import migrations


CREATE_BRONZE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS bronze;"

DROP_BRONZE_SCHEMA = "DROP SCHEMA IF EXISTS bronze CASCADE;"

CREATE_SQUAD_SNAPSHOT_TABLE = """
CREATE TABLE IF NOT EXISTS bronze.squad_snapshot (
    ingestion_id        BIGINT          NOT NULL,
    simulation_source   VARCHAR(255)    NOT NULL,
    save_name           VARCHAR(255)    NOT NULL,
    ingame_date         DATE            NOT NULL,
    upload_timestamp    TIMESTAMPTZ     NOT NULL,
    snapshot_type       VARCHAR(50)     NOT NULL,
    source_file_hash    VARCHAR(64)     NOT NULL,
    raw_data            JSONB           NOT NULL
);
"""

DROP_SQUAD_SNAPSHOT_TABLE = "DROP TABLE IF EXISTS bronze.squad_snapshot;"

CREATE_SCOUTING_SNAPSHOT_TABLE = """
CREATE TABLE IF NOT EXISTS bronze.scouting_snapshot (
    ingestion_id        BIGINT          NOT NULL,
    simulation_source   VARCHAR(255)    NOT NULL,
    save_name           VARCHAR(255)    NOT NULL,
    ingame_date         DATE            NOT NULL,
    upload_timestamp    TIMESTAMPTZ     NOT NULL,
    snapshot_type       VARCHAR(50)     NOT NULL,
    source_file_hash    VARCHAR(64)     NOT NULL,
    raw_data            JSONB           NOT NULL
);
"""

DROP_SCOUTING_SNAPSHOT_TABLE = "DROP TABLE IF EXISTS bronze.scouting_snapshot;"

CREATE_MATCHSTATS_SNAPSHOT_TABLE = """
CREATE TABLE IF NOT EXISTS bronze.squad_matchstats_snapshot (
    ingestion_id        BIGINT          NOT NULL,
    simulation_source   VARCHAR(255)    NOT NULL,
    save_name           VARCHAR(255)    NOT NULL,
    ingame_date         DATE            NOT NULL,
    upload_timestamp    TIMESTAMPTZ     NOT NULL,
    snapshot_type       VARCHAR(50)     NOT NULL,
    source_file_hash    VARCHAR(64)     NOT NULL,
    opponent            VARCHAR(255)    NOT NULL,
    ingame_matchdate    DATE            NOT NULL,
    raw_data            JSONB           NOT NULL
);
"""

DROP_MATCHSTATS_SNAPSHOT_TABLE = (
    "DROP TABLE IF EXISTS bronze.squad_matchstats_snapshot;"
)

# Indexes — one creation and drop pair per table to keep reverse_sql clean
IDX_SQUAD_INGESTION = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_squad_ingestion "
    "ON bronze.squad_snapshot(ingestion_id);"
)
IDX_SQUAD_INGESTION_DROP = (
    "DROP INDEX IF EXISTS idx_squad_ingestion;"
)

IDX_SCOUTING_INGESTION = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scouting_ingestion "
    "ON bronze.scouting_snapshot(ingestion_id);"
)
IDX_SCOUTING_INGESTION_DROP = (
    "DROP INDEX IF EXISTS idx_scouting_ingestion;"
)

IDX_MATCHSTATS_INGESTION = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_matchstats_ingestion "
    "ON bronze.squad_matchstats_snapshot(ingestion_id);"
)
IDX_MATCHSTATS_INGESTION_DROP = (
    "DROP INDEX IF EXISTS idx_matchstats_ingestion;"
)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("bronze", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_BRONZE_SCHEMA,
            reverse_sql=DROP_BRONZE_SCHEMA,
        ),
        migrations.RunSQL(
            sql=CREATE_SQUAD_SNAPSHOT_TABLE,
            reverse_sql=DROP_SQUAD_SNAPSHOT_TABLE,
        ),
        migrations.RunSQL(
            sql=CREATE_SCOUTING_SNAPSHOT_TABLE,
            reverse_sql=DROP_SCOUTING_SNAPSHOT_TABLE,
        ),
        migrations.RunSQL(
            sql=CREATE_MATCHSTATS_SNAPSHOT_TABLE,
            reverse_sql=DROP_MATCHSTATS_SNAPSHOT_TABLE,
        ),
        migrations.RunSQL(
            sql=IDX_SQUAD_INGESTION,
            reverse_sql=IDX_SQUAD_INGESTION_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_SCOUTING_INGESTION,
            reverse_sql=IDX_SCOUTING_INGESTION_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_MATCHSTATS_INGESTION,
            reverse_sql=IDX_MATCHSTATS_INGESTION_DROP,
        ),
    ]
