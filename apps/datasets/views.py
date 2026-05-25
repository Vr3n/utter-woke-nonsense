import json
import time

from django.db import connection
from django.db.models import OuterRef, Subquery
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404, render

from apps.bronze.models import BronzeIngestion, BronzeIngestionTask
from apps.core.models import SaveMaster
from apps.landing.models import LandingUpload, LandingZoneTask

from .models import DatasetEvent
from .perf import CaptureQueries


def _compute_p95_latency(save):
    """Seconds at the 95th percentile of completed bronze pipeline duration."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXTRACT(EPOCH FROM (bi.completed_at - lu.upload_timestamp))
            FROM bronze_bronzeingestion bi
            JOIN landing_landingupload lu ON lu.id = bi.landing_upload_id
            WHERE lu.save_master_id = %s
              AND bi.status = 'completed'
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


def dataset_list(request, save_slug):
    save = get_object_or_404(
        SaveMaster.objects.select_related("game_version"), slug=save_slug
    )
    saves = SaveMaster.objects.select_related("game_version").order_by("-updated_at")
    active_team = (
        save.managed_teams.filter(is_active=True).select_related("team").first()
    )

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
            )
        )

    active_failed = []
    completed_landing = []
    completed_bronze = []

    for u in uploads:
        if u.status == "completed":
            completed_landing.append(u)
            if u.bronze_status == "completed":
                completed_bronze.append(u)
        else:
            active_failed.append(u)

    total_uploads = len(uploads)
    completed_count = sum(
        1 for u in uploads if u.status == "completed"
    )
    health = round((completed_count / total_uploads * 100)) if total_uploads else 0
    backlog = sum(
        1
        for u in uploads
        if u.status in ("pending", "processing", "retrying")
    )
    data_volume = sum(u.file_size_bytes or 0 for u in uploads)
    p95 = _compute_p95_latency(save)

    context = {
        "save": save,
        "saves": saves,
        "active_team": active_team,
        "active_failed": active_failed,
        "completed_landing": completed_landing,
        "completed_bronze": completed_bronze,
        "perf": perf,
        "health": health,
        "backlog": backlog,
        "data_volume": _human_size(data_volume),
        "p95_latency": f"{p95}s" if p95 is not None else "—",
        "total_uploads": total_uploads,
        "completed_count": completed_count,
    }
    return render(request, "pages/datasets/list.html", context)


def dataset_detail(request, save_slug, upload_id):
    save = get_object_or_404(SaveMaster, slug=save_slug)
    saves = SaveMaster.objects.select_related("game_version").order_by("-updated_at")

    upload = get_object_or_404(
        LandingUpload.objects.prefetch_related(
            "task_rows",
            "bronze_ingestions__task_rows",
        ).select_related("save_master"),
        id=upload_id,
        save_master=save,
    )

    bronze_ingestion = upload.bronze_ingestions.first()

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
    if bronze_ingestion and bronze_ingestion.completed_at:
        delta = bronze_ingestion.completed_at - upload.upload_timestamp
        pipeline_duration = round(delta.total_seconds(), 1)

    context = {
        "save": save,
        "saves": saves,
        "upload": upload,
        "bronze_ingestion": bronze_ingestion,
        "parse_attempts": parse_attempts,
        "store_attempts": store_attempts,
        "bronze_attempts": bronze_attempts,
        "pipeline_duration": pipeline_duration,
        "file_size": _human_size(upload.file_size_bytes),
    }
    return render(request, "pages/datasets/detail.html", context)


def _event_generator(upload_id=None, save_slug=None):
    """Yield SSE-formatted DatasetEvent rows.

    Call with either upload_id (detail page) or save_slug (listing page).
    """
    last_id = 0

    filters = {}
    if upload_id is not None:
        filters["upload_id"] = upload_id
    if save_slug is not None:
        filters["upload__save_master__slug"] = save_slug

    while True:
        events = (
            DatasetEvent.objects.filter(**filters, id__gt=last_id)
            .select_related("upload")
            .order_by("id")
        )

        for event in events:
            data = {
                "id": event.id,
                "upload_id": event.upload_id,
                "type": event.event_type,
                "payload": event.payload,
            }
            yield f"event: dataset-event\ndata: {json.dumps(data)}\n\n"
            last_id = event.id

        if not events:
            time.sleep(0.5)


def event_stream(request, upload_id):
    return StreamingHttpResponse(
        _event_generator(upload_id=upload_id),
        content_type="text/event-stream",
    )


def listing_event_stream(request, save_slug):
    return StreamingHttpResponse(
        _event_generator(save_slug=save_slug),
        content_type="text/event-stream",
    )


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

    upload = get_object_or_404(
        LandingUpload.objects.select_related("save_master").annotate(
            parse_status=Subquery(parse_task.values("status")[:1]),
            store_status=Subquery(store_task.values("status")[:1]),
            bronze_status=Subquery(bronze.values("status")[:1]),
        ),
        id=upload_id,
    )
    dots = _phase_dots(upload)
    return render(
        request,
        "datasets/partials/listing_row.html",
        {
            "upload": upload,
            "save": upload.save_master,
            "dots": dots,
            "file_size": _human_size(upload.file_size_bytes),
        },
    )
