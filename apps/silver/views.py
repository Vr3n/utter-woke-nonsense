from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.bronze.models import BronzeIngestion
from apps.landing.pipeline import get_pipeline_status

from .models import SilverIngestion
from .tasks import dispatch_silver_ingestion


def _pipeline_detail_response(request, upload_id):
    ctx = get_pipeline_status(upload_id)
    ctx["save"] = ctx["upload"].save_master

    parse_attempts = sorted(
        [t for t in ctx["upload"].task_rows.all() if t.step == "parse"],
        key=lambda t: t.attempt,
    )
    store_attempts = sorted(
        [t for t in ctx["upload"].task_rows.all() if t.step == "store"],
        key=lambda t: t.attempt,
    )
    bronze_attempts = []
    if ctx["bronze"] is not None:
        bronze_attempts = sorted(
            list(ctx["bronze"].task_rows.all()),
            key=lambda t: t.attempt,
        )
    silver_attempts = []
    if ctx["silver"] is not None:
        silver_attempts = sorted(
            list(ctx["silver"].task_rows.all()),
            key=lambda t: t.attempt,
        )

    ctx.update({
        "parse_attempts": parse_attempts,
        "store_attempts": store_attempts,
        "bronze_attempts": bronze_attempts,
        "silver_attempts": silver_attempts,
    })
    return render(request, "datasets/partials/pipeline_detail.html", ctx)


@require_POST
def dispatch_silver(request, upload_id):
    bronze = (
        BronzeIngestion.objects
        .filter(landing_upload_id=upload_id)
        .order_by("-started_at")
        .first()
    )

    if bronze is None:
        ctx = get_pipeline_status(upload_id)
        ctx["save"] = ctx["upload"].save_master
        ctx["dispatch_message"] = "No bronze ingestion found for this upload."
        return render(request, "datasets/partials/pipeline_detail.html", ctx, status=404)

    if bronze.status != "completed":
        ctx = get_pipeline_status(upload_id)
        ctx["save"] = ctx["upload"].save_master
        ctx["dispatch_message"] = (
            f"Bronze is currently {bronze.get_status_display()}. "
            f"Silver will process automatically once bronze completes."
        )
        return render(request, "datasets/partials/pipeline_detail.html", ctx)

    existing_silver = (
        SilverIngestion.objects
        .filter(bronze_ingestion=bronze)
        .order_by("-started_at")
        .first()
    )

    if existing_silver is not None and existing_silver.status in (
        "processing", "completed",
    ):
        return _pipeline_detail_response(request, upload_id)

    try:
        dispatch_silver_ingestion(bronze)
    except Exception:
        return _pipeline_detail_response(request, upload_id)

    return _pipeline_detail_response(request, upload_id)
