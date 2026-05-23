import pytest


@pytest.fixture(autouse=True)
def celery_task_eager(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
