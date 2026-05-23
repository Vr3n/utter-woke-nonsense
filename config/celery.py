import os
from celery import Celery
from celery.app.log import TaskFormatter
from celery.signals import after_setup_task_logger

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings"
)

app = Celery("fm_analytics")

app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()


@after_setup_task_logger.connect
def setup_task_logger(logger, **kwargs):
    for handler in logger.handlers:
        handler.setFormatter(TaskFormatter(
            "%(asctime)s [%(levelname)s] %(task_name)s[%(task_id)s] "
            "upload=%(upload_id)s step=%(step)s — %(message)s"
        ))
