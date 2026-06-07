import logging

import celery
from celery import shared_task
from django.utils import timezone

from apps.core.choices import PipelineStatus, TaskStatus
from apps.core.metrics import pipeline_task_failures, pipeline_task_retries

logger = logging.getLogger(__name__)


class PipelineTask(celery.Task):
    def record_failure(self, obj, task_row, exc, retryable=True):
        if retryable and self.request.retries < self.max_retries:
            obj.status = PipelineStatus.RETRYING
            task_row.status = TaskStatus.RETRYING
            pipeline_task_retries.labels(task_name=self.name).inc()
        else:
            obj.status = PipelineStatus.FAILED
            task_row.status = TaskStatus.FAILED
            pipeline_task_failures.labels(
                task_name=self.name,
                error_type=type(exc).__name__,
            ).inc()
        obj.error_type = type(exc).__name__
        obj.error_message = str(exc)
        obj.save(update_fields=["status", "error_type", "error_message"])
        task_row.error_type = type(exc).__name__
        task_row.error_message = str(exc)
        task_row.finished_at = timezone.now()
        task_row.save(
            update_fields=["status", "error_type", "error_message", "finished_at"]
        )


@shared_task
def test_task():
    logger.info("Test task executed successfully")
    return "Test task completed"