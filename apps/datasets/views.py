from django.db import connection
from django.db.models import OuterRef, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.bronze.models import BronzeIngestion, BronzeIngestionTask
from apps.core.models import SaveMaster
from apps.landing.models import LandingUpload, LandingZoneTask
from apps.landing.pipeline import get_pipeline_status
from apps.landing.tasks import dispatch_pipeline
from apps.silver.models import SilverIngestion

from .perf import CaptureQueries


def _compute_p95_latency(save):
    """Seconds at the 95th percentile of end-to-end pipeline duration (through silver when available)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXTRACT(EPOCH FROM (
              COALESCE(si.completed_at, bi.completed_at) - lu.upload_timestamp
            ))
            FROM bronze_bronzeingestion bi
            JOIN landing_landingupload lu ON lu.id = bi.landing_upload_id
            LEFT JOIN silver_silveringestion si ON si.bronze_ingestion_id = bi.id
            WHERE lu.save_master_id = %s
              AND bi.completed_at IS NOT NULL
              AND lu.upload_timestamp IS NOT NULL
            ORDER BY 1
            """,
            [save.id],
        )
        rows = cursor.fetchall()
    if not rows:
        return None
    latencies = [r[0] for r in rows]
    idx = int(len(latencies) * 0.95)
    return round(latencies[idx], 1)


def _human_size(bytes_):
    if bytes_ is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024:
            return f"{bytes_:.1f} {unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f} TB"


def _phase_dots(upload):
    """Return 4-tuple of (phase_label, status) for each pipeline stage."""
    phases = [
        ("Source", "completed" if upload.status else "pending"),
        ("Parse", upload.parse_status or "pending"),
        ("Store", upload.store_status or "pending"),
    ]
    bronze_status = upload.bronze_status or "pending"
    phases.append(("Bronze", bronze_status))
    return phases


def _get_listing_sections(save):
    parse_task = LandingZoneTask.objects.filter(
        upload=OuterRef("pk"), step="parse"
    ).order_by("-attempt")
    store_task = LandingZoneTask.objects.filter(
        upload=OuterRef("pk"), step="store"
    ).order_by("-attempt")
    bronze = BronzeIngestion.objects.filter(
        landing_upload=OuterRef("pk")
    ).order_by("-started_at")
    bronze_task = BronzeIngestionTask.objects.filter(
        ingestion__landing_upload=OuterRef("pk")
    ).order_by("-ingestion__started_at", "-attempt")
    silver = SilverIngestion.objects.filter(
        bronze_ingestion__landing_upload=OuterRef("pk")
    ).order_by("-started_at")

    with CaptureQueries() as perf:
        uploads = list(
            LandingUpload.objects.filter(save_master=save)
            .select_related("save_master")
            .annotate(
                parse_status=Subquery(parse_task.values("status")[:1]),
                parse_error_type=Subquery(parse_task.values("error_type")[:1]),
                parse_error_message=Subquery(parse_task.values("error_message")[:1]),
                store_status=Subquery(store_task.values("status")[:1]),
                store_error_type=Subquery(store_task.values("error_type")[:1]),
                store_error_message=Subquery(store_task.values("error_message")[:1]),
                bronze_status=Subquery(bronze.values("status")[:1]),
                bronze_id=Subquery(bronze.values("id")[:1]),
                bronze_source_rows=Subquery(
                    bronze.values("source_row_count")[:1]
                ),
                bronze_ingested_rows=Subquery(
                    bronze.values("ingested_row_count")[:1]
                ),
                bronze_error_type=Subquery(bronze.values("error_type")[:1]),
                bronze_error_message=Subquery(bronze.values("error_message")[:1]),
                bronze_task_status=Subquery(bronze_task.values("status")[:1]),
                bronze_task_error_type=Subquery(
                    bronze_task.values("error_type")[:1]
                ),
                bronze_task_error_message=Subquery(
                    bronze_task.values("error_message")[:1]
                ),
                silver_status=Subquery(silver.values("status")[:1]),
            )
        )

    active_failed = []
    awaiting_ingestion = []
    awaiting_transformation = []
    fully_processed = []

    for u in uploads:
        if u.status != "completed":
            active_failed.append(u)
        elif u.silver_status == "completed":
            fully_processed.append(u)
        elif u.bronze_status == "completed":
            awaiting_transformation.append(u)
        else:
            awaiting_ingestion.append(u)

    total_uploads = len(uploads)
    fully_processed_count = len(fully_processed)
    health = round((fully_processed_count / total_uploads * 100)) if total_uploads else 0
    backlog = sum(
        1
        for u in uploads
        if u.status in ("pending", "processing", "retrying", "skipped")
    )
    data_volume = sum(u.file_size_bytes or 0 for u in uploads)

    return {
        "active_failed": active_failed,
        "awaiting_ingestion": awaiting_ingestion,
        "awaiting_transformation": awaiting_transformation,
        "fully_processed": fully_processed,
        "perf": perf,
        "health": health,
        "backlog": backlog,
        "data_volume": _human_size(data_volume),
        "total_uploads": total_uploads,
    }


def dataset_list(request, save_slug):
    save = get_object_or_404(
        SaveMaster.objects.select_related("game_version"), slug=save_slug
    )
    saves = SaveMaster.objects.select_related("game_version").order_by("-updated_at")
    active_team = (
        save.managed_teams.filter(is_active=True).select_related("team").first()
    )
    p95 = _compute_p95_latency(save)
    sections = _get_listing_sections(save)

    context = {
        "save": save,
        "saves": saves,
        "active_team": active_team,
        "p95_latency": f"{p95}s" if p95 is not None else "—",
        **sections,
    }
    return render(request, "pages/datasets/list.html", context)


def dataset_list_sections(request, save_slug):
    save = get_object_or_404(SaveMaster, slug=save_slug)
    sections = _get_listing_sections(save)
    return render(request, "datasets/partials/listing_sections.html", {
        "save": save,
        **sections,
    })


def dataset_detail(request, save_slug, upload_id):
    save = get_object_or_404(SaveMaster, slug=save_slug)
    saves = SaveMaster.objects.select_related("game_version").order_by("-updated_at")

    upload = get_object_or_404(
        LandingUpload.objects.prefetch_related(
            "task_rows",
            "bronze_ingestions__task_rows",
            "bronze_ingestions__silver_ingestions__task_rows",
        ).select_related("save_master"),
        id=upload_id,
        save_master=save,
    )

    bronze_ingestion = upload.bronze_ingestions.first()

    silver_ingestion = None
    silver_attempts = []
    if bronze_ingestion is not None:
        silver_ingestion = (
            bronze_ingestion.silver_ingestions
            .order_by("-started_at")
            .first()
        )
        if silver_ingestion is not None:
            silver_attempts = sorted(
                list(silver_ingestion.task_rows.all()),
                key=lambda t: t.attempt,
            )

    parse_attempts = sorted(
        [t for t in upload.task_rows.all() if t.step == "parse"],
        key=lambda t: t.attempt,
    )
    store_attempts = sorted(
        [t for t in upload.task_rows.all() if t.step == "store"],
        key=lambda t: t.attempt,
    )
    bronze_attempts = (
        sorted(
            list(bronze_ingestion.task_rows.all()),
            key=lambda t: t.attempt,
        )
        if bronze_ingestion
        else []
    )

    pipeline_duration = None
    pipeline_end = None
    if silver_ingestion and silver_ingestion.completed_at:
        pipeline_end = silver_ingestion.completed_at
    elif bronze_ingestion and bronze_ingestion.completed_at:
        pipeline_end = bronze_ingestion.completed_at
    if pipeline_end:
        delta = pipeline_end - upload.upload_timestamp
        pipeline_duration = round(delta.total_seconds(), 1)

    context = {
        "save": save,
        "saves": saves,
        "upload": upload,
        "bronze_ingestion": bronze_ingestion,
        "silver_ingestion": silver_ingestion,
        "parse_attempts": parse_attempts,
        "store_attempts": store_attempts,
        "bronze_attempts": bronze_attempts,
        "silver_attempts": silver_attempts,
        "pipeline_duration": pipeline_duration,
        "file_size": _human_size(upload.file_size_bytes),
    }
    return render(request, "pages/datasets/detail.html", context)


def dataset_pipeline_poll(request, save_slug, upload_id):
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


def node_detail_partial(request, upload_id, step):
    upload = get_object_or_404(
        LandingUpload.objects.prefetch_related("task_rows"), id=upload_id
    )

    if step == "bronze":
        bronze = get_object_or_404(
            BronzeIngestion.objects.prefetch_related("task_rows"),
            landing_upload=upload,
        )
        attempts = sorted(
            list(bronze.task_rows.all()), key=lambda t: t.attempt
        )
    elif step == "silver":
        silver = (
            SilverIngestion.objects
            .filter(bronze_ingestion__landing_upload=upload)
            .prefetch_related("task_rows")
            .order_by("-started_at")
            .first()
        )
        if silver is None:
            raise Http404("No silver ingestion for this upload")
        attempts = sorted(
            list(silver.task_rows.all()), key=lambda t: t.attempt
        )
    else:
        attempts = sorted(
            [t for t in upload.task_rows.all() if t.step == step],
            key=lambda t: t.attempt,
        )

    return render(
        request,
        "datasets/partials/node_detail.html",
        {"step": step, "attempts": attempts},
    )


def listing_row_partial(request, upload_id):
    parse_task = LandingZoneTask.objects.filter(
        upload=OuterRef("pk"), step="parse"
    ).order_by("-attempt")
    store_task = LandingZoneTask.objects.filter(
        upload=OuterRef("pk"), step="store"
    ).order_by("-attempt")
    bronze = BronzeIngestion.objects.filter(
        landing_upload=OuterRef("pk")
    ).order_by("-started_at")
    silver = SilverIngestion.objects.filter(
        bronze_ingestion__landing_upload=OuterRef("pk")
    ).order_by("-started_at")

    upload = get_object_or_404(
        LandingUpload.objects.select_related("save_master").annotate(
            parse_status=Subquery(parse_task.values("status")[:1]),
            store_status=Subquery(store_task.values("status")[:1]),
            bronze_status=Subquery(bronze.values("status")[:1]),
            silver_status=Subquery(silver.values("status")[:1]),
        ),
        id=upload_id,
    )

    is_terminal = (
        upload.status in ("failed", "skipped")
        or upload.silver_status == "completed"
    )

    response = render(
        request,
        "datasets/partials/listing_row.html",
        {
            "upload": upload,
            "save": upload.save_master,
            "file_size": _human_size(upload.file_size_bytes),
            "is_terminal": is_terminal,
        },
    )
    if is_terminal:
        response["HX-Trigger"] = "datasets-changed"
    return response


@require_POST
def retry_landing(request, save_slug, upload_id):
    upload = get_object_or_404(
        LandingUpload.objects.select_related("save_master"),
        id=upload_id,
        save_master__slug=save_slug,
        status=LandingUpload.Status.FAILED,
    )
    dispatch_pipeline(upload.id)
    return redirect("dataset_detail", save_slug=save_slug, upload_id=upload_id)
