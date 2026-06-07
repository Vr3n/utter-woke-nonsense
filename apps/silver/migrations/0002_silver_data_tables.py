from django.db import migrations


CREATE_SILVER_SCHEMA = "CREATE SCHEMA IF NOT EXISTS silver;"
DROP_SILVER_SCHEMA = "DROP SCHEMA IF EXISTS silver CASCADE;"

CREATE_SQUAD_TABLE = """
CREATE TABLE IF NOT EXISTS silver.squad_snapshot (
    -- structured metadata
    ingestion_id        BIGINT          NOT NULL,
    simulation_source   VARCHAR(255)    NOT NULL,
    save_name           VARCHAR(255)    NOT NULL,
    ingame_date         DATE            NOT NULL,
    upload_timestamp    TIMESTAMPTZ     NOT NULL,
    snapshot_type       VARCHAR(50)     NOT NULL,
    source_file_hash    VARCHAR(64)     NOT NULL,
    -- identifiers
    unique_id           BIGINT          NOT NULL,
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
    red_cards           INT,
    recommendation      VARCHAR(50)
);
"""

DROP_SQUAD_TABLE = "DROP TABLE IF EXISTS silver.squad_snapshot;"

CREATE_SCOUTING_TABLE = """
CREATE TABLE IF NOT EXISTS silver.scouting_snapshot (
    -- structured metadata
    ingestion_id        BIGINT          NOT NULL,
    simulation_source   VARCHAR(255)    NOT NULL,
    save_name           VARCHAR(255)    NOT NULL,
    ingame_date         DATE            NOT NULL,
    upload_timestamp    TIMESTAMPTZ     NOT NULL,
    snapshot_type       VARCHAR(50)     NOT NULL,
    source_file_hash    VARCHAR(64)     NOT NULL,
    -- identifiers
    unique_id           BIGINT          NOT NULL,
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
    red_cards           INT,
    recommendation      VARCHAR(50)
);
"""

DROP_SCOUTING_TABLE = "DROP TABLE IF EXISTS silver.scouting_snapshot;"

CREATE_MATCHSTATS_TABLE = """
CREATE TABLE IF NOT EXISTS silver.squad_matchstats_snapshot (
    -- structured metadata
    ingestion_id        BIGINT          NOT NULL,
    simulation_source   VARCHAR(255)    NOT NULL,
    save_name           VARCHAR(255)    NOT NULL,
    ingame_date         DATE            NOT NULL,
    upload_timestamp    TIMESTAMPTZ     NOT NULL,
    snapshot_type       VARCHAR(50)     NOT NULL,
    source_file_hash    VARCHAR(64)     NOT NULL,
    -- identifiers
    unique_id           BIGINT          NOT NULL,
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
    red_cards           INT,
    recommendation      VARCHAR(50),
    -- matchstats extras
    opponent            VARCHAR(255),
    ingame_matchdate    DATE
);
"""

DROP_MATCHSTATS_TABLE = "DROP TABLE IF EXISTS silver.squad_matchstats_snapshot;"

# Squad indexes
IDX_SQUAD_INGESTION = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_squad_ingestion "
    "ON silver.squad_snapshot (ingestion_id);"
)
IDX_SQUAD_INGESTION_DROP = "DROP INDEX IF EXISTS idx_silver_squad_ingestion;"

IDX_SQUAD_INGAME_DATE = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_squad_ingame_date "
    "ON silver.squad_snapshot (ingame_date);"
)
IDX_SQUAD_INGAME_DATE_DROP = "DROP INDEX IF EXISTS idx_silver_squad_ingame_date;"

IDX_SQUAD_UNIQUE_ID = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_squad_unique_id "
    "ON silver.squad_snapshot (unique_id);"
)
IDX_SQUAD_UNIQUE_ID_DROP = "DROP INDEX IF EXISTS idx_silver_squad_unique_id;"

IDX_SQUAD_DEDUP = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_squad_dedup "
    "ON silver.squad_snapshot (unique_id, club, division);"
)
IDX_SQUAD_DEDUP_DROP = "DROP INDEX IF EXISTS idx_silver_squad_dedup;"

# Scouting indexes
IDX_SCOUTING_INGESTION = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_scouting_ingestion "
    "ON silver.scouting_snapshot (ingestion_id);"
)
IDX_SCOUTING_INGESTION_DROP = "DROP INDEX IF EXISTS idx_silver_scouting_ingestion;"

IDX_SCOUTING_INGAME_DATE = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_scouting_ingame_date "
    "ON silver.scouting_snapshot (ingame_date);"
)
IDX_SCOUTING_INGAME_DATE_DROP = "DROP INDEX IF EXISTS idx_silver_scouting_ingame_date;"

IDX_SCOUTING_UNIQUE_ID = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_scouting_unique_id "
    "ON silver.scouting_snapshot (unique_id);"
)
IDX_SCOUTING_UNIQUE_ID_DROP = "DROP INDEX IF EXISTS idx_silver_scouting_unique_id;"

IDX_SCOUTING_DEDUP = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_scouting_dedup "
    "ON silver.scouting_snapshot (unique_id, club, division);"
)
IDX_SCOUTING_DEDUP_DROP = "DROP INDEX IF EXISTS idx_silver_scouting_dedup;"

# Matchstats indexes
IDX_MATCHSTATS_INGESTION = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_matchstats_ingestion "
    "ON silver.squad_matchstats_snapshot (ingestion_id);"
)
IDX_MATCHSTATS_INGESTION_DROP = (
    "DROP INDEX IF EXISTS idx_silver_matchstats_ingestion;"
)

IDX_MATCHSTATS_INGAME_DATE = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_matchstats_ingame_date "
    "ON silver.squad_matchstats_snapshot (ingame_date);"
)
IDX_MATCHSTATS_INGAME_DATE_DROP = (
    "DROP INDEX IF EXISTS idx_silver_matchstats_ingame_date;"
)

IDX_MATCHSTATS_UNIQUE_ID = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_matchstats_unique_id "
    "ON silver.squad_matchstats_snapshot (unique_id);"
)
IDX_MATCHSTATS_UNIQUE_ID_DROP = (
    "DROP INDEX IF EXISTS idx_silver_matchstats_unique_id;"
)

IDX_MATCHSTATS_DEDUP = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_matchstats_dedup "
    "ON silver.squad_matchstats_snapshot (unique_id, club, division);"
)
IDX_MATCHSTATS_DEDUP_DROP = (
    "DROP INDEX IF EXISTS idx_silver_matchstats_dedup;"
)

IDX_MATCHSTATS_OPPONENT = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_matchstats_opponent "
    "ON silver.squad_matchstats_snapshot (opponent);"
)
IDX_MATCHSTATS_OPPONENT_DROP = (
    "DROP INDEX IF EXISTS idx_silver_matchstats_opponent;"
)

IDX_MATCHSTATS_MATCHDATE = (
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_matchstats_matchdate "
    "ON silver.squad_matchstats_snapshot (ingame_matchdate);"
)
IDX_MATCHSTATS_MATCHDATE_DROP = (
    "DROP INDEX IF EXISTS idx_silver_matchstats_matchdate;"
)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("silver", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_SILVER_SCHEMA,
            reverse_sql=DROP_SILVER_SCHEMA,
        ),
        # Squad table + indexes
        migrations.RunSQL(
            sql=CREATE_SQUAD_TABLE,
            reverse_sql=DROP_SQUAD_TABLE,
        ),
        migrations.RunSQL(
            sql=IDX_SQUAD_INGESTION,
            reverse_sql=IDX_SQUAD_INGESTION_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_SQUAD_INGAME_DATE,
            reverse_sql=IDX_SQUAD_INGAME_DATE_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_SQUAD_UNIQUE_ID,
            reverse_sql=IDX_SQUAD_UNIQUE_ID_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_SQUAD_DEDUP,
            reverse_sql=IDX_SQUAD_DEDUP_DROP,
        ),
        # Scouting table + indexes
        migrations.RunSQL(
            sql=CREATE_SCOUTING_TABLE,
            reverse_sql=DROP_SCOUTING_TABLE,
        ),
        migrations.RunSQL(
            sql=IDX_SCOUTING_INGESTION,
            reverse_sql=IDX_SCOUTING_INGESTION_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_SCOUTING_INGAME_DATE,
            reverse_sql=IDX_SCOUTING_INGAME_DATE_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_SCOUTING_UNIQUE_ID,
            reverse_sql=IDX_SCOUTING_UNIQUE_ID_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_SCOUTING_DEDUP,
            reverse_sql=IDX_SCOUTING_DEDUP_DROP,
        ),
        # Matchstats table + indexes
        migrations.RunSQL(
            sql=CREATE_MATCHSTATS_TABLE,
            reverse_sql=DROP_MATCHSTATS_TABLE,
        ),
        migrations.RunSQL(
            sql=IDX_MATCHSTATS_INGESTION,
            reverse_sql=IDX_MATCHSTATS_INGESTION_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_MATCHSTATS_INGAME_DATE,
            reverse_sql=IDX_MATCHSTATS_INGAME_DATE_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_MATCHSTATS_UNIQUE_ID,
            reverse_sql=IDX_MATCHSTATS_UNIQUE_ID_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_MATCHSTATS_DEDUP,
            reverse_sql=IDX_MATCHSTATS_DEDUP_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_MATCHSTATS_OPPONENT,
            reverse_sql=IDX_MATCHSTATS_OPPONENT_DROP,
        ),
        migrations.RunSQL(
            sql=IDX_MATCHSTATS_MATCHDATE,
            reverse_sql=IDX_MATCHSTATS_MATCHDATE_DROP,
        ),
    ]
