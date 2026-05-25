from django.db import models


class DatasetEvent(models.Model):
    """
    Append-only event log decoupling the Celery pipeline from the SSE-driven UI.

    Tasks write one row per pipeline milestone (parse_completed, store_failed,
    bronze_completed, etc.). The SSE view reads rows newer than the client's
    Last-Event-ID using a single indexed query on (upload_id, created_at).

    This indirection avoids coupling the SSE reader to the mutable status
    fields of LandingZoneTask / BronzeIngestionTask, and avoids polling
    multiple tables with different lifecycle semantics.

    Events are never mutated or deleted. Old rows can be pruned by a
    future management command if the table grows large.
    """

    class EventType(models.TextChoices):
        PARSE_COMPLETED = "parse_completed", "Parse Completed"
        PARSE_FAILED = "parse_failed", "Parse Failed"
        STORE_COMPLETED = "store_completed", "Store Completed"
        STORE_FAILED = "store_failed", "Store Failed"
        BRONZE_COMPLETED = "bronze_completed", "Bronze Completed"
        BRONZE_FAILED = "bronze_failed", "Bronze Failed"

    upload = models.ForeignKey(
        "landing.LandingUpload",
        on_delete=models.CASCADE,
        related_name="events",
    )
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    payload = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["upload", "created_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} for upload {self.upload_id} @ {self.created_at}"
