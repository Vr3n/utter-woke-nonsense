from pathlib import Path

from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse

from .models import LandingUpload, LandingZoneTask


class LandingZoneTaskInline(admin.TabularInline):
    model = LandingZoneTask
    extra = 0
    can_delete = False
    readonly_fields = [
        "celery_task_id", "step", "attempt", "status",
        "worker_hostname", "error_type", "error_message",
        "started_at", "finished_at", "duration_seconds_display",
    ]
    fields = [
        "step", "attempt", "status", "celery_task_id",
        "worker_hostname", "error_type", "error_message",
        "started_at", "finished_at", "duration_seconds_display",
    ]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Duration (s)")
    def duration_seconds_display(self, obj):
        return obj.duration_seconds


@admin.register(LandingUpload)
class LandingUploadAdmin(admin.ModelAdmin):
    list_display = (
        "save_master",
        "snapshot_type",
        "season",
        "data_label",
        "ingame_date",
        "status",
        "bronze_status_display",
        "upload_timestamp",
        "file_size_bytes",
    )

    @admin.display(description="Bronze")
    def bronze_status_display(self, obj):
        ingestion = obj.bronze_ingestions.order_by("-id").first()
        if ingestion is None:
            return ""
        url = reverse(
            "admin:bronze_bronzeingestion_change",
            args=[ingestion.id],
        )
        return format_html(
            '<a href="{}" style="color:{}">{}</a>',
            url,
            self._status_color(ingestion.status),
            ingestion.status,
        )

    def _status_color(self, status):
        colors = {
        "completed": "green",
        "failed": "red",
        "skipped": "gray",
        "processing": "orange",
        "retrying": "orange",
        "pending": "gray",
        }
        return colors.get(status, "gray")
    list_filter = ("snapshot_type", "status", "simulation_source")
    search_fields = ("data_label", "source_file_name", "save_master__slug")
    readonly_fields = (
        "source_file_hash",
        "upload_timestamp",
        "file_size_bytes",
        "entry_task_id",
        "error_type",
        "error_message",
        "parse_progress_current",
        "parse_progress_total",
        "parse_progress_description",
    )
    inlines = [LandingZoneTaskInline]
    ordering = ("-upload_timestamp",)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self._delete_files(obj)
        super().delete_queryset(request, queryset)

    def delete_model(self, request, obj):
        self._delete_files(obj)
        super().delete_model(request, obj)

    def _delete_files(self, obj):
        incoming = Path(obj.incoming_path())
        if incoming.exists():
            incoming.unlink()

        staging = Path(obj.staging_path)
        if staging.exists():
            staging.unlink()

        if obj.parquet_path:
            parquet = Path(obj.parquet_path)
            if parquet.exists():
                parquet.unlink()


@admin.register(LandingZoneTask)
class LandingZoneTaskAdmin(admin.ModelAdmin):
    list_display = [
        "upload", "step", "attempt", "status", "error_type",
        "started_at", "duration_seconds_display",
    ]
    list_filter = ["step", "status", "worker_hostname"]
    search_fields = ["upload__id", "celery_task_id", "error_type"]
    readonly_fields = [
        "upload", "celery_task_id", "step", "attempt", "status",
        "worker_hostname", "error_type", "error_message",
        "started_at", "finished_at", "duration_seconds_display",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Duration (s)")
    def duration_seconds_display(self, obj):
        return obj.duration_seconds
