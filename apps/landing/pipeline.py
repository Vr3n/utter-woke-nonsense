from django.shortcuts import get_object_or_404

from apps.bronze.models import BronzeIngestion
from apps.core.choices import PipelineStatus
from apps.silver.models import SilverIngestion

from .models import LandingUpload, LandingZoneTask


def get_pipeline_status(upload_id):
    upload = get_object_or_404(
        LandingUpload.objects.prefetch_related("task_rows"),
        id=upload_id,
    )
    bronze = (
        BronzeIngestion.objects
        .filter(landing_upload=upload)
        .order_by("-started_at")
        .first()
    )
    silver = None
    if bronze is not None:
        silver = (
            SilverIngestion.objects
            .filter(bronze_ingestion=bronze)
            .order_by("-started_at")
            .first()
        )

    last_error = (
        upload.task_rows
        .filter(status=LandingZoneTask.Status.FAILED)
        .order_by("-attempt")
        .first()
    )

    terminal_statuses = {PipelineStatus.COMPLETED, PipelineStatus.FAILED, PipelineStatus.SKIPPED}
    is_terminal = (
        upload.status in terminal_statuses
        and (bronze is None or bronze.status in terminal_statuses)
        and (silver is None or silver.status in terminal_statuses)
    )

    return {
        "upload": upload,
        "bronze": bronze,
        "silver": silver,
        "last_error": last_error,
        "is_terminal": is_terminal,
    }
