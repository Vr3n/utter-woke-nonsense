from pathlib import Path

from django.shortcuts import get_object_or_404, render

from apps.core.models import SaveMaster

from .forms import SnapshotUploadForm
from .models import LandingUpload
from .pipeline import get_pipeline_status
from .tables import LandingUploadTable
from .tasks import dispatch_pipeline


def save_snapshot_page(request, save_slug, snapshot_type):
    save = get_object_or_404(
        SaveMaster.objects.select_related("game_version"), slug=save_slug
    )
    saves = SaveMaster.objects.select_related("game_version").order_by("-updated_at")
    active_team = (
        save.managed_teams.filter(is_active=True).select_related("team").first()
    )
    form = SnapshotUploadForm(initial={"snapshot_type": snapshot_type})

    context = {
        "save": save,
        "saves": saves,
        "active_team": active_team,
        "snapshot_type": snapshot_type,
        "form": form,
    }
    return render(request, "pages/save_snapshot.html", context)


def upload_snapshot(request, save_slug):
    save = get_object_or_404(
        SaveMaster.objects.select_related("game_version"), slug=save_slug
    )
    form = SnapshotUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(
            request,
            "landing/partials/upload_form.html",
            {
                "form": form,
                "save": save,
                "snapshot_type": request.POST.get("snapshot_type", ""),
            },
            status=400,
        )

    source_file = form.cleaned_data["source_file"]
    snapshot_type = form.cleaned_data["snapshot_type"]

    upload = LandingUpload.objects.create(
        save_master=save,
        snapshot_type=snapshot_type,
        season=form.cleaned_data["season"],
        ingame_date=form.cleaned_data["ingame_date"],
        data_label=form.cleaned_data["data_label"],
        simulation_source=save.game_version.game_version,
        source_file_hash=form.file_hash,
        source_file_name=source_file.name,
        file_size_bytes=source_file.size,
    )

    incoming = Path(upload.incoming_path())
    incoming.parent.mkdir(parents=True, exist_ok=True)
    with open(incoming, "wb") as f:
        for chunk in source_file.chunks():
            f.write(chunk)

    dispatch_pipeline(upload.id)

    return render(
        request,
        "landing/partials/pipeline_status.html",
        {
            "save": save,
            "snapshot_type": snapshot_type,
            "upload": upload,
            "bronze": None,
            "silver": None,
            "last_error": None,
            "is_terminal": False,
        },
        status=202,
    )


def pipeline_status_poll(request, save_slug, snapshot_type, upload_id):
    ctx = get_pipeline_status(upload_id)
    ctx["save"] = ctx["upload"].save_master
    ctx["snapshot_type"] = snapshot_type

    response = render(request, "landing/partials/pipeline_status.html", ctx)
    if ctx["upload"].status == LandingUpload.Status.COMPLETED:
        response["HX-Trigger"] = "datasource-uploaded"
    return response


def upload_table_partial(request, save_slug, snapshot_type):
    from django_tables2 import RequestConfig

    save = get_object_or_404(SaveMaster, slug=save_slug)
    qs = LandingUpload.objects.filter(
        save_master=save, snapshot_type=snapshot_type
    ).select_related("save_master")
    table = LandingUploadTable(qs, request=request)
    RequestConfig(
        request, paginate={"per_page": 10}
    ).configure(table)

    return render(
        request,
        "landing/partials/upload_table.html",
        {"table": table, "save": save, "snapshot_type": snapshot_type},
    )


def upload_retry(request, save_slug, upload_id):
    save = get_object_or_404(SaveMaster, slug=save_slug)
    upload = get_object_or_404(
        LandingUpload.objects.select_related("save_master"),
        id=upload_id,
        save_master=save,
        status=LandingUpload.Status.FAILED,
    )
    dispatch_pipeline(upload.id)
    upload.refresh_from_db()

    return render(
        request,
        "landing/partials/upload_table_row.html",
        {"upload": upload, "save": save},
    )



