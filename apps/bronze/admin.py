from django.contrib import admin

from .models import BronzeIngestion, BronzeIngestionTask


class BronzeIngestionTaskInline(admin.TabularInline):
    model = BronzeIngestionTask
    extra = 0
    can_delete = False
    readonly_fields = [
        "celery_task_id", "step", "attempt", "status", "phase",
        "worker_hostname", "error_type", "error_message",
        "started_at", "finished_at", "duration_seconds_display",
    ]
    fields = [
        "step", "attempt", "status", "phase", "celery_task_id",
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


@admin.register(BronzeIngestion)
class BronzeIngestionAdmin(admin.ModelAdmin):
    list_display = [
        "id", "landing_upload_link", "snapshot_type", "simulation_source",
        "status", "parse_progress_current", "parse_progress_total",
        "source_row_count", "ingested_row_count",
        "column_count", "duration_seconds_display", "started_at",
    ]
    list_filter = ["status", "snapshot_type", "simulation_source"]
    search_fields = ["landing_upload__id", "source_file_hash", "celery_task_id"]
    readonly_fields = [
        f.name for f in BronzeIngestion._meta.fields
    ] + ["duration_seconds_display"]
    inlines = [BronzeIngestionTaskInline]
    ordering = ["-started_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Upload")
    def landing_upload_link(self, obj):
        from django.utils.html import format_html
        from django.urls import reverse

        url = reverse(
            "admin:landing_landingupload_change",
            args=[obj.landing_upload_id],
        )
        return format_html(
            '<a href="{}">#{}</a>', url, obj.landing_upload_id
        )

    @admin.display(description="Duration (s)")
    def duration_seconds_display(self, obj):
        return obj.duration_seconds


@admin.register(BronzeIngestionTask)
class BronzeIngestionTaskAdmin(admin.ModelAdmin):
    list_display = [
        "ingestion", "step", "attempt", "status", "phase",
        "error_type", "started_at", "duration_seconds_display",
    ]
    list_filter = ["step", "status", "phase", "worker_hostname"]
    search_fields = ["ingestion__id", "celery_task_id", "error_type"]
    readonly_fields = [
        "ingestion", "celery_task_id", "step", "attempt", "status",
        "phase", "worker_hostname", "error_type", "error_message",
        "started_at", "finished_at", "duration_seconds_display",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Duration (s)")
    def duration_seconds_display(self, obj):
        return obj.duration_seconds
