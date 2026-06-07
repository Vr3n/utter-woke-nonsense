import re
import time

import duckdb
from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from pydantic import ValidationError

from apps.core.db import duckdb_pg_connect
from apps.core.choices import PipelineStatus, TaskStatus
from apps.core.db import get_pg_conn_string
from apps.core.metrics import (
    pipeline_null_warnings,
    pipeline_rows_dropped,
    pipeline_rows_processed,
    pipeline_task_duration,
    pipeline_task_failures,
)
from apps.core.tasks import PipelineTask
from .contracts import SCHEMA_CONTRACTS
from .exceptions import SchemaDriftError
from .models import SilverIngestion, SilverIngestionTask, SilverIngestionPhase

logger = get_task_logger(__name__)


def dispatch_silver_ingestion(bronze_ingestion):
    ingestion = SilverIngestion.objects.create(
        bronze_ingestion=bronze_ingestion,
        status=PipelineStatus.PROCESSING,
        simulation_source=bronze_ingestion.simulation_source,
        snapshot_type=bronze_ingestion.snapshot_type,
        source_file_hash=bronze_ingestion.source_file_hash,
        started_at=timezone.now(),
    )
    result = transform_to_silver.delay(ingestion.id)
    SilverIngestion.objects.filter(id=ingestion.id).update(
        celery_task_id=result.id,
    )


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


def _strip_suffix(val):
    return re.sub(r'\s*p/w$', '', val)


def parse_currency_min(val):
    if not val:
        return None
    val = val.strip()
    if val in ("Unknown", "N/A", "-", ""):
        return None
    val = _strip_suffix(val)
    parts = val.split(" - ")
    first = parts[0]
    cleaned = re.sub(r"^[^0-9.]+", "", first)
    if not cleaned:
        return None
    return _apply_multiplier(cleaned)


def parse_currency_max(val):
    if not val:
        return None
    val = val.strip()
    if val in ("Unknown", "N/A", "-", ""):
        return None
    val = _strip_suffix(val)
    parts = val.split(" - ")
    second = parts[1] if len(parts) > 1 else parts[0]
    cleaned = re.sub(r"^[^0-9.]+", "", second)
    if not cleaned:
        return None
    return _apply_multiplier(cleaned)


def parse_starts(val):
    if not val:
        return None
    val = val.strip()
    if val in ("Unknown", "N/A", "-", ""):
        return None
    starts_str = val.split(" (")[0]
    try:
        return int(starts_str)
    except (ValueError, TypeError):
        return None


def parse_subs(val):
    if not val:
        return None
    val = val.strip()
    if val in ("Unknown", "N/A", "-", ""):
        return None
    parts = val.split(" (")
    if len(parts) > 1:
        subs_str = parts[1].rstrip(")")
        try:
            return int(subs_str)
        except (ValueError, TypeError):
            return None
    return 0


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


def build_silver_pipeline(snapshot_type):
    extra_unpack_cols = ""
    extra_clean_cols = ""
    extra_select_cols = ""
    extra_insert_cols = ""

    if snapshot_type == "squad_matchstats_snapshot":
        extra_unpack_cols = """
            json_extract_string(raw_data, '$."Opponent"')    AS opponent_str,
            json_extract_string(raw_data, '$."Match Date"')  AS ingame_matchdate_str,
        """
        extra_clean_cols = """
            NULLIF(opponent_str, '')           AS opponent,
            CASE
                WHEN ingame_matchdate_str IS NULL THEN NULL
                WHEN ingame_matchdate_str IN ('N/A', '-') THEN NULL
                WHEN ingame_matchdate_str ~ '^\\d{4}-\\d{2}-\\d{2}$'
                    THEN ingame_matchdate_str::DATE
                ELSE make_date(
                    CAST(str_split(ingame_matchdate_str, '/')[3] AS INT),
                    CAST(str_split(ingame_matchdate_str, '/')[2] AS INT),
                    CAST(str_split(ingame_matchdate_str, '/')[1] AS INT)
                )
            END                                AS ingame_matchdate,
        """
        extra_select_cols = ", opponent, ingame_matchdate"
        extra_insert_cols = ", opponent, ingame_matchdate"

    unpack_sql = f"""
    unpacked AS (
        SELECT
            ingestion_id,
            simulation_source,
            save_name,
            ingame_date,
            upload_timestamp,
            snapshot_type,
            source_file_hash,
            season,
            json_extract_string(raw_data, '$."Unique ID"')     AS unique_id_str,
            json_extract_string(raw_data, '$."Player"')         AS player_str,
            json_extract_string(raw_data, '$."Age"')            AS age_str,
            COALESCE(
                NULLIF(json_extract_string(raw_data, '$."Nation of Birth"'), ''),
                NULLIF(json_extract_string(raw_data, '$."Nation"'), '')
            ) AS nation_str,
            json_extract_string(raw_data, '$."Club"')           AS club_str,
            json_extract_string(raw_data, '$."Division"')       AS division_str,
            json_extract_string(raw_data, '$."Appearances"')    AS appearances_str,
            json_extract_string(raw_data, '$."Minutes"')        AS minutes_str,
            json_extract_string(raw_data, '$."Playing Time"')   AS playing_time_str,
            json_extract_string(raw_data, '$."Transfer Value"') AS transfer_value_str,
            json_extract_string(raw_data, '$."Wage"')           AS wage_str,
            json_extract_string(raw_data, '$."Expires"')        AS expires_str,
            json_extract_string(raw_data, '$."Poss Lost/90"')   AS poss_lost_str,
            json_extract_string(raw_data, '$."Poss Won/90"')    AS poss_won_str,
            json_extract_string(raw_data, '$."Ps C/90"')        AS ps_c_str,
            json_extract_string(raw_data, '$."Ps A/90"')        AS ps_a_str,
            json_extract_string(raw_data, '$."Pr passes/90"')   AS pr_passes_str,
            json_extract_string(raw_data, '$."OP-KP/90"')       AS op_kp_str,
            json_extract_string(raw_data, '$."Ch C/90"')        AS ch_c_str,
            json_extract_string(raw_data, '$."Pres A/90"')      AS pres_a_str,
            json_extract_string(raw_data, '$."Pres C/90"')      AS pres_c_str,
            json_extract_string(raw_data, '$."Blk/90"')         AS blk_str,
            json_extract_string(raw_data, '$."Clr/90"')         AS clr_str,
            json_extract_string(raw_data, '$."Int/90"')         AS int_str,
            json_extract_string(raw_data, '$."Tck/90"')         AS tck_str,
            json_extract_string(raw_data, '$."Dist/90"')        AS dist_str,
            json_extract_string(raw_data, '$."Drb/90"')         AS drb_str,
            json_extract_string(raw_data, '$."Sprints/90"')     AS sprints_str,
            json_extract_string(raw_data, '$."OP-Crs A/90"')    AS op_crs_a_str,
            json_extract_string(raw_data, '$."OP-Crs C/90"')    AS op_crs_c_str,
            json_extract_string(raw_data, '$."Hdrs L/90"')      AS hdrs_l_str,
            json_extract_string(raw_data, '$."Hdrs W/90"')      AS hdrs_w_str,
            json_extract_string(raw_data, '$."Cln/90"')         AS cln_str,
            json_extract_string(raw_data, '$."xA/90"')          AS xa_str,
            json_extract_string(raw_data, '$."xG/90"')          AS xg_str,
            json_extract_string(raw_data, '$."NP-xG/90"')       AS np_xg_str,
            json_extract_string(raw_data, '$."xG-OP"')          AS xg_op_str,
            json_extract_string(raw_data, '$."Fouls Made"')     AS fouls_made_str,
            json_extract_string(raw_data, '$."Fouls Against"')  AS fouls_against_str,
            json_extract_string(raw_data, '$."Yel"')            AS yel_str,
            json_extract_string(raw_data, '$."Red cards"')      AS red_cards_str,
            json_extract_string(raw_data, '$."Best Pos"')       AS best_pos_str,
            json_extract_string(raw_data, '$."Position"')       AS position_str,
            json_extract_string(raw_data, '$."Rating"')         AS rating_str,
            json_extract_string(raw_data, '$."Height"')         AS height_str,
            json_extract_string(raw_data, '$."Preferred Foot"') AS preferred_foot_str,
            json_extract_string(raw_data, '$."Left Foot"')      AS left_foot_str,
            json_extract_string(raw_data, '$."Right Foot"')     AS right_foot_str,
            json_extract_string(raw_data, '$."Tcon/90"')        AS tcon_per90_str,
            json_extract_string(raw_data, '$."xSv %"')          AS xsv_pct_str,
            json_extract_string(raw_data, '$."Sv %"')           AS sv_pct_str,
            json_extract_string(raw_data, '$."CCC"')            AS ccc_str,
            json_extract_string(raw_data, '$."Aer A/90"')       AS aer_a_per90_str,
            json_extract_string(raw_data, '$."K Hdrs/90"')      AS k_hdrs_per90_str,
            json_extract_string(raw_data, '$."Crs A/90"')       AS crs_a_per90_str,
            json_extract_string(raw_data, '$."Cr C/90"')        AS cr_c_per90_str,
            json_extract_string(raw_data, '$."Con/90"')         AS con_per90_str,
            json_extract_string(raw_data, '$."Goals per 90 minutes"') AS goals_per90_str,
            json_extract_string(raw_data, '$."Saves/90"')       AS saves_per90_str,
            json_extract_string(raw_data, '$."Tgls/90"')        AS tgls_per90_str,
            json_extract_string(raw_data, '$."MLG"')            AS mlg_str,
            json_extract_string(raw_data, '$."Off"')            AS offsides_str,
            json_extract_string(raw_data, '$."Asts/90"')        AS asts_per90_str,
            json_extract_string(raw_data, '$."KP/90"')          AS kp_per90_str,
            json_extract_string(raw_data, '$."xGP/90"')         AS xgp_per90_str,
            json_extract_string(raw_data, '$."Shots From Outside The Box Per 90 minutes"') AS shots_outside_box_per90_str,
            json_extract_string(raw_data, '$."ShT/90"')         AS sht_per90_str,
            json_extract_string(raw_data, '$."Shot/90"')        AS shot_per90_str,
            json_extract_string(raw_data, '$."K Tck/90"')       AS k_tck_per90_str,
            json_extract_string(raw_data, '$."Shts Blckd/90"')  AS shts_blckd_per90_str,
            {extra_unpack_cols}
        FROM source
    )
    """

    iso_date_re = r"\d{{4}}-\d{{2}}-\d{{2}}"
    cleaned_sql = f"""
    cleaned AS (
        SELECT
            ingestion_id,
            simulation_source,
            save_name,
            ingame_date,
            upload_timestamp,
            snapshot_type,
            source_file_hash,
            season,
            TRY_CAST(NULLIF(unique_id_str, '') AS BIGINT) AS unique_id,
            NULLIF(player_str, '')                    AS player,
            NULLIF(age_str, '')::INT                  AS age,
            NULLIF(nation_str, '')                    AS nation_of_birth,
            NULLIF(club_str, '')                      AS club,
            NULLIF(division_str, '')                  AS division,
            parse_starts(appearances_str)             AS starting_appearances,
            parse_subs(appearances_str)               AS substitute_appearances,
            NULLIF(minutes_str, '')::INT              AS minutes,
            NULLIF(playing_time_str, '')              AS playing_time,
            parse_currency_min(transfer_value_str)    AS transfer_value_min,
            parse_currency_max(transfer_value_str)    AS transfer_value_max,
            parse_currency_min(wage_str)              AS wage_min,
            parse_currency_max(wage_str)              AS wage_max,
            CASE
                WHEN expires_str IS NULL THEN NULL
                WHEN expires_str IN ('N/A', '-',
                     'Out of contract', 'Retired') THEN NULL
                WHEN expires_str ~ '^{iso_date_re}$'
                    THEN expires_str::DATE
                ELSE make_date(
                    CAST(str_split(expires_str, '/')[3] AS INT),
                    CAST(str_split(expires_str, '/')[2] AS INT),
                    CAST(str_split(expires_str, '/')[1] AS INT)
                )
            END                                       AS expires,
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
            NULLIF(NULLIF(shts_blckd_per90_str, ''), '-')::DOUBLE AS shts_blckd_per90,
            {extra_clean_cols}
        FROM unpacked
    )
    """

    final_sql = f"""
    final AS (
        SELECT DISTINCT
            ingestion_id, simulation_source, save_name,
            ingame_date, upload_timestamp, snapshot_type, source_file_hash,
            season,
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
            {extra_select_cols}
        FROM cleaned
        WHERE unique_id IS NOT NULL
    )
    """

    shared_insert_cols = """
        ingestion_id, simulation_source, save_name, ingame_date,
        upload_timestamp, snapshot_type, source_file_hash, season,
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
    """

    insert_sql = f"""
    INSERT INTO pg_db.silver.{{0}}
        ({shared_insert_cols}{extra_insert_cols})
    SELECT * FROM final
    """

    sql = f"""
    WITH source AS (
        SELECT * FROM pg_db.bronze.{{0}}
        WHERE ingestion_id = $1
    ),
    {unpack_sql},
    {cleaned_sql},
    {final_sql}
    {insert_sql}
    """

    extra_columns = []
    if snapshot_type == "squad_matchstats_snapshot":
        extra_columns = ["opponent", "ingame_matchdate"]

    base_col_count = len([c for c in shared_insert_cols.split(",") if c.strip()])
    column_count = base_col_count + len(extra_columns)
    return sql, extra_columns, column_count


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

    logger.info("Starting silver transformation", extra=log_ctx)

    if (
        SilverIngestion.objects.filter(
            bronze_ingestion=bronze_ingestion,
            status=PipelineStatus.COMPLETED,
        )
        .exclude(id=ingestion.id)
        .exists()
    ):
        logger.warning(
            "Duplicate silver ingestion detected, returning early",
            extra=log_ctx,
        )
        ingestion.status = PipelineStatus.SKIPPED
        ingestion.error_type = "DuplicateIngestion"
        ingestion.error_message = (
            "A completed silver ingestion already exists for this bronze ingestion."
        )
        ingestion.save(update_fields=["status", "error_type", "error_message"])
        return

    attempt = self.request.retries + 1
    task_row = SilverIngestionTask.objects.create(
        ingestion=ingestion,
        step="transform",
        attempt=attempt,
        celery_task_id=self.request.id or "",
        status=TaskStatus.PENDING,
        worker_hostname=self.request.hostname or "",
    )
    task_row.status = TaskStatus.RUNNING
    task_row.started_at = timezone.now()
    task_row.save(update_fields=["status", "started_at"])

    try:
        task_row.phase = SilverIngestionPhase.ATTACHING
        task_row.save(update_fields=["phase"])
        ingestion.parse_progress_current = 1
        ingestion.parse_progress_description = SilverIngestionPhase.ATTACHING.label
        ingestion.save(
            update_fields=["parse_progress_current", "parse_progress_description"]
        )

        sql_pipeline, extra_columns, column_count = build_silver_pipeline(
            bronze_ingestion.snapshot_type
        )

        with duckdb_pg_connect() as conn:
            pg_conn = get_pg_conn_string()
            conn.execute(f"ATTACH '{pg_conn}' AS pg_db (TYPE postgres)")

            conn.create_function(
                "parse_currency_min",
                parse_currency_min,
                [str],
                int,
                null_handling="SPECIAL",
            )
            conn.create_function(
                "parse_currency_max",
                parse_currency_max,
                [str],
                int,
                null_handling="SPECIAL",
            )
            conn.create_function(
                "parse_starts",
                parse_starts,
                [str],
                int,
                null_handling="SPECIAL",
            )
            conn.create_function(
                "parse_subs",
                parse_subs,
                [str],
                int,
                null_handling="SPECIAL",
            )
            conn.create_function(
                "parse_height",
                parse_height,
                [str],
                int,
                null_handling="SPECIAL",
            )
            logger.info("Registered 5 DuckDB UDFs", extra=log_ctx)

            contract_cls = SCHEMA_CONTRACTS.get(bronze_ingestion.snapshot_type)
            if contract_cls:
                sample = conn.execute(
                    f"SELECT raw_data FROM pg_db.bronze.{bronze_ingestion.snapshot_type} "
                    "WHERE ingestion_id = $1 LIMIT 1",
                    [bronze_ingestion.id],
                ).fetchone()
                if sample:
                    try:
                        contract_cls.model_validate_json(sample[0])
                    except ValidationError as e:
                        raise SchemaDriftError(
                            f"Schema contract violated for {bronze_ingestion.snapshot_type}: {e}"
                        )

            task_row.phase = SilverIngestionPhase.COUNTING_SOURCE
            task_row.save(update_fields=["phase"])
            ingestion.parse_progress_current = 2
            ingestion.parse_progress_description = (
                SilverIngestionPhase.COUNTING_SOURCE.label
            )
            ingestion.save(
                update_fields=["parse_progress_current", "parse_progress_description"]
            )

            source_count = conn.execute(
                f"SELECT COUNT(*) FROM pg_db.bronze.{bronze_ingestion.snapshot_type} "
                "WHERE ingestion_id = $1",
                [bronze_ingestion.id],
            ).fetchone()[0]
            logger.info("Bronze source rows: %d", source_count, extra=log_ctx)

            if source_count == 0:
                logger.warning(
                    "Bronze source has 0 rows for ingestion %d — pipeline produced empty result",
                    bronze_ingestion.id,
                    extra=log_ctx,
                )
                ingestion.status = PipelineStatus.SKIPPED
                ingestion.error_message = (
                    "Bronze source had 0 rows — pipeline produced empty result"
                )
                ingestion.completed_at = timezone.now()
                ingestion.parse_progress_current = 5
                ingestion.parse_progress_description = "Skipped"
                ingestion.save(
                    update_fields=[
                        "status",
                        "error_message",
                        "completed_at",
                        "parse_progress_current",
                        "parse_progress_description",
                    ]
                )
                task_row.phase = SilverIngestionPhase.COMPLETED
                task_row.status = TaskStatus.SUCCEEDED
                task_row.finished_at = timezone.now()
                task_row.save(update_fields=["phase", "status", "finished_at"])
                return

            from .utils import get_historical_avg_row_count

            historical_avg = get_historical_avg_row_count(
                bronze_ingestion.snapshot_type,
                bronze_ingestion.simulation_source,
            )
            if historical_avg and source_count < historical_avg * 0.5:
                logger.warning(
                    "Row count deviation: %d vs historical avg %.0f (%.0f%% of expected)",
                    source_count,
                    historical_avg,
                    (source_count / historical_avg) * 100,
                    extra=log_ctx,
                )

            task_row.phase = SilverIngestionPhase.TRANSFORMING
            task_row.save(update_fields=["phase"])
            ingestion.parse_progress_current = 3
            ingestion.parse_progress_description = (
                SilverIngestionPhase.TRANSFORMING.label
            )
            ingestion.save(
                update_fields=["parse_progress_current", "parse_progress_description"]
            )

            t0 = time.monotonic()

            unique_id_count = conn.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT
                        TRY_CAST(NULLIF(
                            json_extract_string(raw_data, '$."Unique ID"'), ''
                        ) AS BIGINT) AS unique_id
                    FROM pg_db.bronze.{bronze_ingestion.snapshot_type}
                    WHERE ingestion_id = $1
                ) sub WHERE unique_id IS NOT NULL
                """,
                [bronze_ingestion.id],
            ).fetchone()[0]

            logger.info("Rows with unique_id: %d", unique_id_count, extra=log_ctx)

            if source_count > 0:
                for col_name in [
                    "Height", "Best Pos", "Preferred Foot",
                    "Left Foot", "Right Foot", "Off",
                ]:
                    null_count = conn.execute(
                        f"SELECT COUNT(*) FROM "
                        f"pg_db.bronze.{bronze_ingestion.snapshot_type} "
                        f"WHERE ingestion_id = $1 AND "
                        f"json_extract_string(raw_data, '$.\"{col_name}\"') "
                        f"IS NULL",
                        [bronze_ingestion.id],
                    ).fetchone()[0]
                    if null_count == source_count:
                        pipeline_null_warnings.labels(
                            layer="silver",
                            snapshot_type=bronze_ingestion.snapshot_type,
                            column_name=col_name,
                        ).inc()
                        logger.warning(
                            "Column %s is NULL for all %d source rows "
                            "— possible schema drift",
                            col_name, source_count, extra=log_ctx,
                        )

            task_row.phase = SilverIngestionPhase.INSERTING
            task_row.save(update_fields=["phase"])
            ingestion.parse_progress_current = 4
            ingestion.parse_progress_description = SilverIngestionPhase.INSERTING.label
            ingestion.save(
                update_fields=["parse_progress_current", "parse_progress_description"]
            )

            table_name = bronze_ingestion.snapshot_type
            full_sql = sql_pipeline.format(table_name)

            conn.execute(full_sql, [bronze_ingestion.id])
            logger.info("Inserted into silver.%s", table_name, extra=log_ctx)

            task_row.phase = SilverIngestionPhase.VERIFYING_INSERTION
            task_row.save(update_fields=["phase"])
            ingestion.parse_progress_current = 5
            ingestion.parse_progress_description = (
                SilverIngestionPhase.VERIFYING_INSERTION.label
            )
            ingestion.save(
                update_fields=["parse_progress_current", "parse_progress_description"]
            )

            inserted_count = conn.execute(
                f"SELECT COUNT(*) FROM pg_db.silver.{table_name} "
                "WHERE ingestion_id = $1",
                [bronze_ingestion.id],
            ).fetchone()[0]

            transform_time = (time.monotonic() - t0) * 1000
            dropped_count = source_count - unique_id_count

            logger.info(
                "Silver rows: %d, dropped: %d (%.3fms)",
                inserted_count,
                dropped_count,
                transform_time,
                extra=log_ctx,
            )

        ingestion.source_row_count = source_count
        ingestion.inserted_row_count = inserted_count
        ingestion.dropped_row_count = dropped_count
        ingestion.column_count = column_count
        ingestion.status = PipelineStatus.COMPLETED
        ingestion.completed_at = timezone.now()
        ingestion.parse_progress_current = 5
        ingestion.parse_progress_description = "Done"
        ingestion.save(
            update_fields=[
                "source_row_count",
                "inserted_row_count",
                "dropped_row_count",
                "column_count",
                "status",
                "completed_at",
                "parse_progress_current",
                "parse_progress_description",
            ]
        )

        task_row.phase = SilverIngestionPhase.COMPLETED
        task_row.status = TaskStatus.SUCCEEDED
        task_row.finished_at = timezone.now()
        task_row.save(update_fields=["phase", "status", "finished_at"])


        pipeline_task_duration.labels(
            task_name="transform_to_silver",
            snapshot_type=bronze_ingestion.snapshot_type,
            status="completed",
        ).observe(task_row.duration_seconds)
        pipeline_rows_processed.labels(
            layer="silver",
            snapshot_type=bronze_ingestion.snapshot_type,
        ).inc(inserted_count)
        pipeline_rows_dropped.labels(
            layer="silver",
            reason="no_unique_id",
        ).inc(dropped_count)

    except (
        duckdb.CatalogException,
        duckdb.ConstraintException,
        duckdb.InvalidInputException,
        duckdb.NotImplementedException,
        duckdb.ConversionException,
        SchemaDriftError,
    ) as exc:
        logger.error(
            "Non-retryable: %s — investigate",
            type(exc).__name__,
            extra={**log_ctx, "error_type": type(exc).__name__},
        )
        pipeline_task_failures.labels(
            task_name="transform_to_silver",
            error_type=type(exc).__name__,
        ).inc()
        ingestion.status = PipelineStatus.FAILED
        ingestion.error_type = type(exc).__name__
        ingestion.error_message = str(exc)
        ingestion.parse_progress_description = "Failed"
        ingestion.save(
            update_fields=[
                "status",
                "error_type",
                "error_message",
                "parse_progress_description",
            ]
        )
        task_row.status = TaskStatus.FAILED
        task_row.error_type = type(exc).__name__
        task_row.error_message = str(exc)
        task_row.finished_at = timezone.now()
        task_row.save(
            update_fields=["status", "error_type", "error_message", "finished_at"]
        )
        pipeline_task_duration.labels(
            task_name="transform_to_silver",
            snapshot_type=bronze_ingestion.snapshot_type,
            status="failed",
        ).observe(task_row.duration_seconds)

        raise
    except Exception as exc:
        retryable = isinstance(exc, tuple(self.autoretry_for))
        if not retryable:
            logger.error(
                "Non-retryable: %s — investigate",
                type(exc).__name__,
                extra=log_ctx,
            )
        elif self.request.retries < self.max_retries:
            logger.warning("Retrying (attempt %d): %s", attempt, exc, extra=log_ctx)
        else:
            logger.error("Failed permanently: %s", exc, extra=log_ctx)
        self.record_failure(ingestion, task_row, exc, retryable=retryable)

        pipeline_task_duration.labels(
            task_name="transform_to_silver",
            snapshot_type=bronze_ingestion.snapshot_type,
            status="failed",
        ).observe(task_row.duration_seconds)

        raise
