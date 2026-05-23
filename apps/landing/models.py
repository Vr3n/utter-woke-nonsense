from pathlib import Path

from django.db import models
from django.conf import settings


class LandingUpload(models.Model):
    class SnapshotType(models.TextChoices):
        SQUAD = "squad_snapshot", "Squad Snapshot"
        SCOUTING = "scouting_snapshot", "Scouting Snapshot"
        MATCHSTATS = "squad_matchstats_snapshot", "Squad Match Stats"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        RETRYING = "retrying", "Retrying"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    save_master = models.ForeignKey(
        "core.SaveMaster",
        on_delete=models.CASCADE,
        related_name="uploads",
    )
    snapshot_type = models.CharField(max_length=50, choices=SnapshotType.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    ingame_date = models.DateField()
    data_label = models.CharField(
        max_length=255,
        db_index=True,
        help_text="User-defined label e.g. winter_scouting, under_23_scouting",
    )
    simulation_source = models.CharField(max_length=255)
    source_file_hash = models.CharField(max_length=64, unique=True)
    source_file_name = models.CharField(max_length=500)
    parquet_path = models.CharField(max_length=1000, blank=True, default="")
    file_size_bytes = models.BigIntegerField(null=True, blank=True)
    upload_timestamp = models.DateTimeField(auto_now_add=True)

    entry_task_id = models.CharField(max_length=255, blank=True, default="")
    parse_progress_current = models.PositiveSmallIntegerField(default=0)
    parse_progress_total = models.PositiveSmallIntegerField(default=3)
    parse_progress_description = models.CharField(max_length=100, default="Pending")

    @property
    def parse_progress_percent(self):
        if not self.parse_progress_total:
            return 0
        return int((self.parse_progress_current / self.parse_progress_total) * 100)

    def incoming_path(self):
        return str(Path(settings.DATASOURCE_ROOT) / "incoming" / str(self.id) / self.source_file_name)

    @property
    def staging_path(self):
        return str(Path(settings.DATASOURCE_ROOT) / "staging" / f"{self.id}.parquet")

    class Meta:
        ordering = ["-upload_timestamp"]
        indexes = [
            models.Index(fields=["save_master", "snapshot_type"]),
        ]


class LandingZoneTask(models.Model):
    class Step(models.TextChoices):
        PARSE = "parse", "Parse"
        STORE = "store", "Store"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        RETRYING = "retrying", "Retrying"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    upload = models.ForeignKey(
        LandingUpload,
        on_delete=models.CASCADE,
        related_name="task_rows",
    )
    celery_task_id = models.CharField(max_length=255, db_index=True, blank=True, default="")
    step = models.CharField(max_length=20, choices=Step.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempt = models.PositiveIntegerField()
    worker_hostname = models.CharField(max_length=255, blank=True, default="")
    error_type = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    @property
    def duration_seconds(self):
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["upload", "step", "attempt"],
                name="unique_upload_step_attempt",
            ),
        ]
