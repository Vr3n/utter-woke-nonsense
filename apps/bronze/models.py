from django.db import models

from apps.core.choices import PipelineStatus, TaskStatus


class BronzeIngestionPhase(models.TextChoices):
    ATTACHING = "attaching", "Attaching to PostgreSQL"
    COUNTING_SOURCE = "counting_source", "Counting source parquet rows"
    INSERTING = "inserting", "Inserting into bronze table"
    COUNTING_INGESTED = "counting_ingested", "Verifying ingested row count"
    COMPLETED = "completed", "Ingestion completed successfully"


class BronzeIngestion(models.Model):
    landing_upload = models.ForeignKey(
        "landing.LandingUpload",
        on_delete=models.PROTECT,
        related_name="bronze_ingestions",
    )
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=PipelineStatus.choices,
        default=PipelineStatus.PENDING,
    )
    simulation_source = models.CharField(max_length=255)
    snapshot_type = models.CharField(max_length=50)
    source_file_hash = models.CharField(max_length=64, db_index=True)
    source_row_count = models.IntegerField(null=True, blank=True)
    ingested_row_count = models.IntegerField(null=True, blank=True)
    rejected_row_count = models.IntegerField(default=0)
    column_count = models.IntegerField(null=True, blank=True)
    error_type = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    parse_progress_current = models.PositiveSmallIntegerField(default=0)
    parse_progress_total = models.PositiveSmallIntegerField(default=4)
    parse_progress_description = models.CharField(max_length=100, default="Pending")

    class Meta:
        ordering = ["-started_at"]

    @property
    def duration_seconds(self):
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    @property
    def parse_progress_percent(self):
        if not self.parse_progress_total:
            return 0
        return int((self.parse_progress_current / self.parse_progress_total) * 100)

    def __str__(self):
        return f"BronzeIngestion #{self.id} [{self.snapshot_type}]"


class BronzeIngestionTask(models.Model):
    ingestion = models.ForeignKey(
        BronzeIngestion,
        on_delete=models.CASCADE,
        related_name="task_rows",
    )
    celery_task_id = models.CharField(max_length=255, db_index=True, blank=True, default="")
    step = models.CharField(max_length=20, default="ingest")
    attempt = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.PENDING,
    )
    phase = models.CharField(
        max_length=30,
        choices=BronzeIngestionPhase.choices,
        blank=True,
        default="",
    )
    worker_hostname = models.CharField(max_length=255, blank=True, default="")
    error_type = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ingestion", "step", "attempt"],
                name="unique_bronze_ingestion_step_attempt",
            ),
        ]

    @property
    def duration_seconds(self):
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None
