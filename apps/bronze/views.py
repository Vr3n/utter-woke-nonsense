from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.landing.models import LandingUpload
from apps.landing.pipeline import get_pipeline_status

from .models import BronzeIngestion
from .tasks import dispatch_bronze_ingestion


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
def dispatch_bronze(request, upload_id):
    upload = get_object_or_404(LandingUpload, id=upload_id)

    existing = (
        BronzeIngestion.objects
        .filter(landing_upload=upload)
        .order_by("-started_at")
        .first()
    )

    if existing is not None and existing.status in ("processing", "completed"):
        return _pipeline_detail_response(request, upload.id)

    try:
        dispatch_bronze_ingestion(upload)
    except Exception:
        return _pipeline_detail_response(request, upload.id)

    return _pipeline_detail_response(request, upload.id)
