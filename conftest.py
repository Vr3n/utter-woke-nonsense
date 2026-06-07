import pytest
from django.db.backends.postgresql.operations import DatabaseOperations


@pytest.fixture(autouse=True)
def celery_task_eager(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True


original_sql_flush = DatabaseOperations.sql_flush


def _cascade_sql_flush(self, style, tables, *, reset_sequences=False, allow_cascade=False):
    return original_sql_flush(self, style, tables, reset_sequences=reset_sequences, allow_cascade=True)


DatabaseOperations.sql_flush = _cascade_sql_flush
