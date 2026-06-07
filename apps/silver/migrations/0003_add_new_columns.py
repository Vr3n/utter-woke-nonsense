from django.db import migrations

ALTER_SQUAD_FORWARD = """
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
"""

ALTER_SQUAD_REVERSE = """
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
"""

ALTER_SCOUTING_FORWARD = """
ALTER TABLE silver.scouting_snapshot
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
"""

ALTER_SCOUTING_REVERSE = """
ALTER TABLE silver.scouting_snapshot
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
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ("silver", "0002_silver_data_tables"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ALTER_SQUAD_FORWARD,
            reverse_sql=ALTER_SQUAD_REVERSE,
        ),
        migrations.RunSQL(
            sql=ALTER_SCOUTING_FORWARD,
            reverse_sql=ALTER_SCOUTING_REVERSE,
        ),
    ]
