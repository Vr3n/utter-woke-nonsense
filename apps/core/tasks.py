import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def test_task():
    logger.info("Test task executed successfully")
    return "Test task completed"