from pathlib import Path

import celery
import pandas as pd
from celery import chain, shared_task
from celery.utils.log import get_task_logger
from celery_progress.backend import ProgressRecorder
from django.conf import settings
from django.utils import timezone

from .converter import ConversionContext, CsvConversionStrategy, HtmlConversionStrategy
from .models import LandingUpload, LandingZoneTask

logger = get_task_logger(__name__)


class PipelineTask(celery.Task):
    def record_failure(self, upload, task_row, exc):
        if self.request.retries < self.max_retries:
            upload.status = LandingUpload.Status.RETRYING
            task_row.status = LandingZoneTask.Status.RETRYING
        else:
            upload.status = LandingUpload.Status.FAILED
            task_row.status = LandingZoneTask.Status.FAILED
        upload.save(update_fields=["status"])
        task_row.error_type = type(exc).__name__
        task_row.error_message = str(exc)
        task_row.finished_at = timezone.now()
        task_row.save(
            update_fields=["status", "error_type", "error_message", "finished_at"]
        )


@shared_task(
    bind=True,
    base=PipelineTask,
    autoretry_for=(ValueError, pd.errors.ParserError, pd.errors.EmptyDataError),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
)
def parse_file(self, upload_id: int) -> int:
    upload = LandingUpload.objects.select_related("save_master").get(id=upload_id)
    log_ctx = {"upload_id": upload_id, "step": "parse"}
    recorder = ProgressRecorder(self) if self.request.id else None

    logger.info("Starting parse", extra=log_ctx)

    attempt = self.request.retries + 1
    task_row = LandingZoneTask.objects.create(
        upload=upload,
        step=LandingZoneTask.Step.PARSE,
        attempt=attempt,
        celery_task_id=self.request.id or "",
        status=LandingZoneTask.Status.PENDING,
        worker_hostname=self.request.hostname or "",
    )
    task_row.status = LandingZoneTask.Status.RUNNING
    task_row.started_at = timezone.now()
    task_row.save(update_fields=["status", "started_at"])

    upload.parse_progress_current = 0
    upload.parse_progress_description = "Uploading"
    upload.save(update_fields=["parse_progress_current", "parse_progress_description"])
    if recorder:
        recorder.set_progress(0, 3, description="Uploading")

    try:
        source_path = Path(upload.incoming_path())
        ext = Path(upload.source_file_name).suffix.lower()

        if ext == ".csv":
            strategy = CsvConversionStrategy()
        elif ext == ".html":
            strategy = HtmlConversionStrategy()
        else:
            raise ValueError(f"Unsupported file extension: {ext}")

        logger.info("Strategy: %s", strategy.__class__.__name__, extra=log_ctx)

        staging_path = (
            Path(settings.DATASOURCE_ROOT) / "staging" / f"{upload_id}.parquet"
        )

        upload.parse_progress_current = 1
        upload.parse_progress_description = "Parsing the file"
        upload.save(update_fields=["parse_progress_current", "parse_progress_description"])
        if recorder:
            recorder.set_progress(1, 3, description="Parsing the file")

        ctx = ConversionContext(strategy)

        upload.parse_progress_current = 2
        upload.parse_progress_description = "Converting to Parquet"
        upload.save(update_fields=["parse_progress_current", "parse_progress_description"])
        if recorder:
            recorder.set_progress(2, 3, description="Converting to Parquet")

        ctx.execute(str(source_path), str(staging_path))

        upload.parse_progress_current = 3
        upload.parse_progress_description = "Done"
        upload.save(update_fields=["parse_progress_current", "parse_progress_description"])
        if recorder:
            recorder.set_progress(3, 3, description="Done")

        logger.info("Staging written", extra=log_ctx)

        task_row.status = LandingZoneTask.Status.SUCCEEDED
        task_row.finished_at = timezone.now()
        task_row.save(update_fields=["status", "finished_at"])
    except Exception as exc:
        if self.request.retries < self.max_retries:
            logger.warning("Retrying (attempt %d): %s", attempt, exc, extra=log_ctx)
        else:
            logger.error("Failed permanently: %s", exc, extra=log_ctx)
        self.record_failure(upload, task_row, exc)
        raise

    return upload_id


@shared_task(
    bind=True,
    base=PipelineTask,
    autoretry_for=(OSError, IOError),
    max_retries=3,
    retry_backoff=True,
    retry_jitter=True,
    acks_late=True,
)
def store_parquet(self, upload_id: int) -> None:
    upload = LandingUpload.objects.select_related("save_master").get(id=upload_id)
    log_ctx = {"upload_id": upload_id, "step": "store"}

    logger.info("Starting store", extra=log_ctx)

    attempt = self.request.retries + 1
    task_row = LandingZoneTask.objects.create(
        upload=upload,
        step=LandingZoneTask.Step.STORE,
        attempt=attempt,
        celery_task_id=self.request.id or "",
        status=LandingZoneTask.Status.PENDING,
        worker_hostname=self.request.hostname or "",
    )
    task_row.status = LandingZoneTask.Status.RUNNING
    task_row.started_at = timezone.now()
    task_row.save(update_fields=["status", "started_at"])

    try:
        staging_path = (
            Path(settings.DATASOURCE_ROOT) / "staging" / f"{upload_id}.parquet"
        )
        if not staging_path.exists():
            raise FileNotFoundError(
                f"Staging file missing for upload {upload_id}. "
                f"Re-dispatch the full pipeline from parse_file."
            )

        dest_dir = (
            Path(settings.DATASOURCE_ROOT)
            / upload.snapshot_type
            / upload.save_master.slug
            / upload.ingame_date.isoformat()
        )
        dest_name = (
            f"{upload.data_label}_"
            f"{upload.upload_timestamp.strftime('%Y%m%d%H%M%S')}.parquet"
        )
        dest_path = dest_dir / dest_name

        if dest_path.exists():
            raise FileExistsError(
                f"Destination already exists: {dest_path}. "
                f"Possible duplicate dispatch for upload {upload_id}."
            )

        dest_dir.mkdir(parents=True, exist_ok=True)
        staging_path.rename(dest_path)

        logger.info("Moved to final path", extra=log_ctx)

        upload.parquet_path = str(dest_path)
        upload.status = LandingUpload.Status.COMPLETED
        upload.save(update_fields=["parquet_path", "status"])

        task_row.status = LandingZoneTask.Status.SUCCEEDED
        task_row.finished_at = timezone.now()
        task_row.save(update_fields=["status", "finished_at"])
    except FileNotFoundError:
        logger.error("Non-retryable: FileNotFoundError — investigate", extra=log_ctx)
        raise
    except FileExistsError:
        logger.error("Non-retryable: FileExistsError — investigate", extra=log_ctx)
        raise
    except Exception as exc:
        if self.request.retries < self.max_retries:
            logger.warning("Retrying (attempt %d): %s", attempt, exc, extra=log_ctx)
        else:
            logger.error("Failed permanently: %s", exc, extra=log_ctx)
        self.record_failure(upload, task_row, exc)
        raise


def dispatch_pipeline(upload_id: int):
    LandingUpload.objects.filter(id=upload_id).update(
        status=LandingUpload.Status.PROCESSING
    )
    result = chain(parse_file.s(upload_id), store_parquet.s()).delay()
    LandingUpload.objects.filter(id=upload_id).update(
        entry_task_id=result.id,
    )
