import logging

import celery
from celery import shared_task
from django.utils import timezone

from apps.core.choices import PipelineStatus, TaskStatus

logger = logging.getLogger(__name__)


class PipelineTask(celery.Task):
    def record_failure(self, obj, task_row, exc):
        if self.request.retries < self.max_retries:
            obj.status = PipelineStatus.RETRYING
            task_row.status = TaskStatus.RETRYING
        else:
            obj.status = PipelineStatus.FAILED
            task_row.status = TaskStatus.FAILED
        obj.save(update_fields=["status"])
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