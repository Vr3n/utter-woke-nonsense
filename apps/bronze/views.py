from django.shortcuts import render

from apps.core.choices import PipelineStatus

from .models import BronzeIngestion


def bronze_status_poll(request, upload_id):
    bronze = (
        BronzeIngestion.objects
        .filter(landing_upload_id=upload_id)
        .order_by("-started_at")
        .first()
    )

    is_terminal = (
        bronze is not None
        and bronze.status in (PipelineStatus.COMPLETED, PipelineStatus.FAILED)
    )

    return render(request, "bronze/partials/bronze_status.html", {
        "bronze": bronze,
        "upload_id": upload_id,
        "is_terminal": is_terminal,
    })
