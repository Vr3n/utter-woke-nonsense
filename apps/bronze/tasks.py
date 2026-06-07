import time

import duckdb
from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from apps.core.choices import PipelineStatus, TaskStatus
from apps.core.db import duckdb_pg_connect, get_pg_conn_string
from apps.core.metrics import pipeline_rows_processed, pipeline_task_duration, pipeline_task_failures
from apps.core.tasks import PipelineTask
from .models import BronzeIngestion, BronzeIngestionTask, BronzeIngestionPhase

logger = get_task_logger(__name__)


def dispatch_bronze_ingestion(upload):
    ingestion = BronzeIngestion.objects.create(
        landing_upload=upload,
        status=PipelineStatus.PROCESSING,
        simulation_source=upload.simulation_source,
        snapshot_type=upload.snapshot_type,
        source_file_hash=upload.source_file_hash,
        started_at=timezone.now(),
    )
    result = ingest_to_bronze.delay(ingestion.id)
    BronzeIngestion.objects.filter(id=ingestion.id).update(
        celery_task_id=result.id,
    )


def run_bronze_append(upload, ingestion, task_row, conn, log_ctx):
    pg_conn = get_pg_conn_string()
    conn.execute(f"ATTACH '{pg_conn}' AS bronze_schema (TYPE postgres)")
    table = "bronze_schema.bronze.{}".format(upload.snapshot_type)

    task_row.phase = BronzeIngestionPhase.COUNTING_SOURCE
    task_row.save(update_fields=["phase"])
    ingestion.parse_progress_current = 2
    ingestion.parse_progress_description = BronzeIngestionPhase.COUNTING_SOURCE.label
    ingestion.save(update_fields=["parse_progress_current", "parse_progress_description"])

    t0 = time.monotonic()
    source_count = conn.execute(
        "SELECT COUNT(*) FROM read_parquet($1)", [upload.parquet_path]
    ).fetchone()[0]
    logger.info(
        "Parquet COUNT (%.3fms) rows=%d",
        (time.monotonic() - t0) * 1000,
        source_count,
        extra=log_ctx,
    )

    column_count = conn.execute(
        "SELECT len(json_keys(to_json(t))) FROM read_parquet($1) t LIMIT 1",
        [upload.parquet_path],
    ).fetchone()[0]

    task_row.phase = BronzeIngestionPhase.INSERTING
    task_row.save(update_fields=["phase"])
    ingestion.parse_progress_current = 3
    ingestion.parse_progress_description = BronzeIngestionPhase.INSERTING.label
    ingestion.save(update_fields=["parse_progress_current", "parse_progress_description"])

    t1 = time.monotonic()
    conn.execute(
        f"""
        INSERT INTO {table}
            (ingestion_id, simulation_source, save_name, ingame_date,
             upload_timestamp, snapshot_type, source_file_hash,
             season, raw_data)
        SELECT
            $1, $2, $3, $4::DATE, $5::TIMESTAMPTZ, $6, $7, $8,
            to_json(t)::JSON
        FROM read_parquet($9) t
        """,
        [
            ingestion.id,
            upload.simulation_source,
            upload.save_master.slug,
            upload.ingame_date.isoformat(),
            upload.upload_timestamp.isoformat(),
            upload.snapshot_type,
            upload.source_file_hash,
            upload.season,
            upload.parquet_path,
        ],
    )
    logger.info(
        "DuckDB INSERT (%.3fms)", (time.monotonic() - t1) * 1000,
        extra=log_ctx,
    )

    task_row.phase = BronzeIngestionPhase.COUNTING_INGESTED
    task_row.save(update_fields=["phase"])
    ingestion.parse_progress_current = 4
    ingestion.parse_progress_description = BronzeIngestionPhase.COUNTING_INGESTED.label
    ingestion.save(update_fields=["parse_progress_current", "parse_progress_description"])

    ingested_count = conn.execute(
        f"""
        SELECT COUNT(*) FROM {table}
        WHERE ingestion_id = $1
        """,
        [ingestion.id],
    ).fetchone()[0]

    logger.info("Source rows: %d", source_count, extra=log_ctx)
    logger.info("Ingested rows: %d", ingested_count, extra=log_ctx)

    return source_count, ingested_count, column_count


@shared_task(
    bind=True,
    base=PipelineTask,
    autoretry_for=(duckdb.IOException, duckdb.OperationalError, duckdb.ConnectionException),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def ingest_to_bronze(self, ingestion_id: int) -> None:
    ingestion = BronzeIngestion.objects.select_related(
        "landing_upload__save_master"
    ).get(id=ingestion_id)
    upload = ingestion.landing_upload
    log_ctx = {"upload_id": upload.id, "step": "ingest"}

    logger.info("Starting bronze ingestion", extra=log_ctx)

    if BronzeIngestion.objects.filter(
        landing_upload=upload,
        status=PipelineStatus.COMPLETED,
    ).exclude(id=ingestion.id).exists():
        logger.warning(
            "Duplicate ingestion detected, returning early", extra=log_ctx
        )
        ingestion.status = PipelineStatus.SKIPPED
        ingestion.error_type = "DuplicateIngestion"
        ingestion.error_message = (
            "A completed ingestion already exists for this upload."
        )
        ingestion.save(update_fields=["status", "error_type", "error_message"])
        return

    attempt = self.request.retries + 1
    task_row = BronzeIngestionTask.objects.create(
        ingestion=ingestion,
        step="ingest",
        attempt=attempt,
        celery_task_id=self.request.id or "",
        status=TaskStatus.PENDING,
        worker_hostname=self.request.hostname or "",
    )
    task_row.status = TaskStatus.RUNNING
    task_row.started_at = timezone.now()
    task_row.save(update_fields=["status", "started_at"])

    try:
        task_row.phase = BronzeIngestionPhase.ATTACHING
        task_row.save(update_fields=["phase"])
        ingestion.parse_progress_current = 1
        ingestion.parse_progress_description = BronzeIngestionPhase.ATTACHING.label
        ingestion.save(update_fields=["parse_progress_current", "parse_progress_description"])

        with duckdb_pg_connect() as conn:
            source_count, ingested_count, column_count = run_bronze_append(
                upload, ingestion, task_row, conn, log_ctx,
            )

        ingestion.source_row_count = source_count
        ingestion.ingested_row_count = ingested_count
        ingestion.column_count = column_count
        ingestion.status = PipelineStatus.COMPLETED
        ingestion.completed_at = timezone.now()
        ingestion.parse_progress_current = 4
        ingestion.parse_progress_description = "Done"
        ingestion.save(
            update_fields=[
                "source_row_count",
                "ingested_row_count",
                "column_count",
                "status",
                "completed_at",
                "parse_progress_current",
                "parse_progress_description",
            ]
        )

        task_row.phase = BronzeIngestionPhase.COMPLETED
        task_row.status = TaskStatus.SUCCEEDED
        task_row.finished_at = timezone.now()
        task_row.save(update_fields=["phase", "status", "finished_at"])



        pipeline_task_duration.labels(
            task_name="ingest_to_bronze",
            snapshot_type=upload.snapshot_type,
            status="completed",
        ).observe(task_row.duration_seconds)
        pipeline_rows_processed.labels(
            layer="bronze",
            snapshot_type=upload.snapshot_type,
        ).inc(ingested_count)

        from apps.silver.tasks import dispatch_silver_ingestion as _dispatch_silver
        _dispatch_silver(ingestion)

    except (
        duckdb.CatalogException,
        duckdb.ConstraintException,
        duckdb.InvalidInputException,
        duckdb.NotImplementedException,
        duckdb.ConversionException,
    ) as exc:
        logger.error(
            "Non-retryable: %s — investigate", type(exc).__name__,
            extra={**log_ctx, "error_type": type(exc).__name__},
        )
        pipeline_task_failures.labels(
            task_name="ingest_to_bronze",
            error_type=type(exc).__name__,
        ).inc()
        ingestion.status = PipelineStatus.FAILED
        ingestion.error_type = type(exc).__name__
        ingestion.error_message = str(exc)
        ingestion.parse_progress_description = "Failed"
        ingestion.save(
            update_fields=[
                "status", "error_type", "error_message", "parse_progress_description"
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
            task_name="ingest_to_bronze",
            snapshot_type=upload.snapshot_type,
            status="failed",
        ).observe(task_row.duration_seconds)

        raise
    except Exception as exc:
        retryable = isinstance(exc, tuple(self.autoretry_for))
        if not retryable:
            logger.error(
                "Non-retryable: %s — investigate", type(exc).__name__,
                extra=log_ctx,
            )
        elif self.request.retries < self.max_retries:
            logger.warning(
                "Retrying (attempt %d): %s", attempt, exc, extra=log_ctx
            )
        else:
            logger.error("Failed permanently: %s", exc, extra=log_ctx)
        self.record_failure(ingestion, task_row, exc, retryable=retryable)

        pipeline_task_duration.labels(
            task_name="ingest_to_bronze",
            snapshot_type=upload.snapshot_type,
            status="failed",
        ).observe(task_row.duration_seconds)

        raise
