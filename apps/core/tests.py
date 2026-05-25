from unittest.mock import Mock, patch

import pytest

from apps.core.choices import PipelineStatus, TaskStatus
from .db import get_pg_conn_string
from .tasks import PipelineTask


class TestGetPgConnString:
    def test_returns_formatted_connection_string(self, settings):
        result = get_pg_conn_string()
        assert result.startswith("host=")
        assert "port=" in result
        assert "dbname=" in result
        assert "user=" in result
        assert "password=" in result

    def test_respects_database_settings(self, settings):
        settings.DATABASES["default"]["HOST"] = "pg.example.com"
        settings.DATABASES["default"]["PORT"] = "6432"
        result = get_pg_conn_string()
        assert "host=pg.example.com" in result
        assert "port=6432" in result

    def test_accepts_custom_database_alias(self, settings):
        settings.DATABASES["replica"] = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "replica_db",
            "USER": "replica_user",
            "PASSWORD": "replica_pass",
            "HOST": "replica.example.com",
            "PORT": "5432",
        }
        result = get_pg_conn_string("replica")
        assert "dbname=replica_db" in result
        assert "user=replica_user" in result
        assert "password=replica_pass" in result
        assert "host=replica.example.com" in result


class TestPipelineTaskRecordFailure:
    @pytest.fixture
    def task_instance(self):
        return PipelineTask()

    @pytest.fixture
    def mock_obj(self):
        obj = Mock()
        obj.status = "processing"
        return obj

    @pytest.fixture
    def mock_task_row(self):
        row = Mock()
        row.status = "running"
        row.error_type = ""
        row.error_message = ""
        row.finished_at = None
        return row

    def test_sets_retrying_when_retries_remain(
        self, task_instance, mock_obj, mock_task_row
    ):
        with patch(
            "celery.app.task.Task.request"
        ) as mock_request:
            mock_request.retries = 0
            mock_request.max_retries = 3

            exc = ValueError("connection timeout")
            task_instance.record_failure(mock_obj, mock_task_row, exc)

        assert mock_obj.status == PipelineStatus.RETRYING
        mock_obj.save.assert_called_once_with(update_fields=["status"])

        assert mock_task_row.status == TaskStatus.RETRYING
        assert mock_task_row.error_type == "ValueError"
        assert mock_task_row.error_message == "connection timeout"
        assert mock_task_row.finished_at is not None
        mock_task_row.save.assert_called_once_with(
            update_fields=["status", "error_type", "error_message", "finished_at"]
        )

    def test_sets_failed_when_retries_exhausted(
        self, task_instance, mock_obj, mock_task_row
    ):
        with patch(
            "celery.app.task.Task.request"
        ) as mock_request:
            mock_request.retries = 3
            mock_request.max_retries = 3

            exc = RuntimeError("disk full")
            task_instance.record_failure(mock_obj, mock_task_row, exc)

        assert mock_obj.status == PipelineStatus.FAILED
        mock_obj.save.assert_called_once_with(update_fields=["status"])
        assert mock_task_row.status == TaskStatus.FAILED
        assert mock_task_row.error_type == "RuntimeError"
        assert mock_task_row.error_message == "disk full"

