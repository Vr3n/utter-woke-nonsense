from pathlib import Path

from django.contrib import admin

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
        "data_label",
        "ingame_date",
        "status",
        "upload_timestamp",
        "file_size_bytes",
    )
    list_filter = ("snapshot_type", "status", "simulation_source")
    search_fields = ("data_label", "source_file_name", "save_master__slug")
    readonly_fields = (
        "source_file_hash",
        "upload_timestamp",
        "file_size_bytes",
        "entry_task_id",
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
