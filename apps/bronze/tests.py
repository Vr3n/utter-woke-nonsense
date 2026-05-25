import datetime
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone

from apps.core.choices import PipelineStatus, TaskStatus
from apps.landing.models import LandingUpload


@pytest.fixture
def save(db):
    from apps.core.models import FootballManagerVersionMaster, SaveMaster

    fmv = FootballManagerVersionMaster.objects.create(game_version="FM24")
    return SaveMaster.objects.create(
        name="my-save",
        slug="my-save",
        start_date="2024-01-01",
        game_version=fmv,
    )


@pytest.fixture
def completed_upload(save, tmp_path, settings):
    """Create a completed landing upload with a real parquet file."""
    settings.DATASOURCE_ROOT = str(tmp_path)
    ingame_date = datetime.date(2028, 7, 15)
    upload = LandingUpload.objects.create(
        save_master=save,
        snapshot_type="squad_snapshot",
        ingame_date=ingame_date,
        data_label="summer_squad",
        simulation_source="FM24",
        source_file_hash="test_hash_for_bronze",
        source_file_name="squad.csv",
        file_size_bytes=42,
        status=PipelineStatus.COMPLETED,
    )
    dest_dir = (
        Path(settings.DATASOURCE_ROOT)
        / upload.snapshot_type
        / upload.save_master.slug
        / upload.ingame_date.isoformat()
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{upload.data_label}_20280715000000.parquet"
    upload.parquet_path = str(dest_path)
    upload.save(update_fields=["parquet_path"])
    return upload


@pytest.fixture
def bronze_ingestion(completed_upload, db):
    from .models import BronzeIngestion

    return BronzeIngestion.objects.create(
        landing_upload=completed_upload,
        simulation_source="FM24",
        snapshot_type="squad_snapshot",
        source_file_hash="test_hash_for_bronze",
    )


@pytest.fixture
def bronze_task(bronze_ingestion, db):
    from .models import BronzeIngestionTask

    return BronzeIngestionTask.objects.create(
        ingestion=bronze_ingestion,
        step="ingest",
        attempt=1,
        celery_task_id="test-task-id",
    )


@pytest.fixture
def admin_client(client, db):
    User = get_user_model()
    User.objects.create_superuser(
        username="admin", password="adminpass", email="admin@example.com"
    )
    client.login(username="admin", password="adminpass")
    return client


class TestBronzeIngestionModel:
    def test_creates_with_default_pending_status(self, bronze_ingestion):
        assert bronze_ingestion.status == PipelineStatus.PENDING

    def test_landing_upload_fk_is_not_unique(self, bronze_ingestion, completed_upload):
        from .models import BronzeIngestion

        second = BronzeIngestion.objects.create(
            landing_upload=completed_upload,
            simulation_source="FM24",
            snapshot_type="squad_snapshot",
            source_file_hash="second_hash",
        )
        assert second.landing_upload_id == bronze_ingestion.landing_upload_id

    def test_duration_seconds_requires_both_timestamps(self, bronze_ingestion):
        assert bronze_ingestion.duration_seconds is None

    def test_duration_seconds_returns_diff(self, bronze_ingestion):
        from django.utils import timezone
        import datetime

        bronze_ingestion.started_at = timezone.now()
        bronze_ingestion.completed_at = bronze_ingestion.started_at + datetime.timedelta(seconds=30)
        assert bronze_ingestion.duration_seconds == 30

    def test_str_representation(self, bronze_ingestion):
        text = str(bronze_ingestion)
        assert str(bronze_ingestion.id) in text
        assert bronze_ingestion.snapshot_type in text

    def test_column_count_defaults_to_none(self, bronze_ingestion):
        assert bronze_ingestion.column_count is None

    def test_rejected_row_count_defaults_to_zero(self, bronze_ingestion):
        assert bronze_ingestion.rejected_row_count == 0


class TestBronzeIngestionTaskModel:
    def test_creates_with_default_pending_status(self, bronze_task):
        assert bronze_task.status == TaskStatus.PENDING

    def test_creates_with_empty_phase(self, bronze_task):
        assert bronze_task.phase == ""

    def test_fk_to_bronze_ingestion(self, bronze_task, bronze_ingestion):
        assert bronze_task.ingestion == bronze_ingestion

    def test_unique_constraint_on_ingestion_step_attempt(self, bronze_ingestion):
        from .models import BronzeIngestionTask

        BronzeIngestionTask.objects.create(
            ingestion=bronze_ingestion,
            step="ingest",
            attempt=1,
        )
        with pytest.raises(IntegrityError):
            BronzeIngestionTask.objects.create(
                ingestion=bronze_ingestion,
                step="ingest",
                attempt=1,
            )

    def test_duration_seconds_requires_both_timestamps(self, bronze_task):
        assert bronze_task.duration_seconds is None

    def test_duration_seconds_returns_diff(self, bronze_task):
        from django.utils import timezone
        import datetime

        bronze_task.started_at = timezone.now()
        bronze_task.finished_at = bronze_task.started_at + datetime.timedelta(seconds=45)
        assert bronze_task.duration_seconds == 45

    def test_worker_hostname_defaults_to_empty(self, bronze_task):
        assert bronze_task.worker_hostname == ""


class TestDispatchBronzeIngestion:
    def test_creates_bronze_ingestion_with_processing_status(
        self, completed_upload
    ):
        from .models import BronzeIngestion
        from .tasks import dispatch_bronze_ingestion

        with patch("apps.bronze.tasks.ingest_to_bronze.delay") as mock_delay:
            mock_delay.return_value.id = "mock-task-id"
            dispatch_bronze_ingestion(completed_upload)

        ingestion = BronzeIngestion.objects.get(
            landing_upload=completed_upload
        )
        assert ingestion.status == PipelineStatus.PROCESSING
        assert ingestion.simulation_source == "FM24"
        assert ingestion.snapshot_type == "squad_snapshot"
        assert ingestion.source_file_hash == "test_hash_for_bronze"
        assert ingestion.started_at is not None

    def test_sets_celery_task_id_after_dispatch(
        self, completed_upload
    ):
        from .models import BronzeIngestion
        from .tasks import dispatch_bronze_ingestion

        with patch("apps.bronze.tasks.ingest_to_bronze.delay") as mock_delay:
            mock_delay.return_value.id = "celery-task-abc"
            dispatch_bronze_ingestion(completed_upload)

        ingestion = BronzeIngestion.objects.get(
            landing_upload=completed_upload
        )
        assert ingestion.celery_task_id == "celery-task-abc"

    def test_dispatches_ingest_task_with_ingestion_id(
        self, completed_upload
    ):
        from .models import BronzeIngestion
        from .tasks import dispatch_bronze_ingestion

        with patch("apps.bronze.tasks.ingest_to_bronze.delay") as mock_delay:
            mock_delay.return_value.id = "mock-task-id"
            dispatch_bronze_ingestion(completed_upload)

        ingestion = BronzeIngestion.objects.get(
            landing_upload=completed_upload
        )
        mock_delay.assert_called_once_with(ingestion.id)


class TestIngestToBronzeIdempotency:
    def test_completed_ingestion_for_same_upload_returns_early(
        self, completed_upload, bronze_ingestion
    ):
        from .models import BronzeIngestion
        from .tasks import ingest_to_bronze

        bronze_ingestion.status = PipelineStatus.COMPLETED
        bronze_ingestion.save(update_fields=["status"])

        new_ingestion = BronzeIngestion.objects.create(
            landing_upload=completed_upload,
            simulation_source="FM24",
            snapshot_type="squad_snapshot",
            source_file_hash="new_hash",
            status=PipelineStatus.PROCESSING,
            started_at=timezone.now(),
        )

        with patch(
            "celery.app.task.Task.request"
        ) as mock_request:
            mock_request.retries = 0
            mock_request.max_retries = 3
            mock_request.id = "test-id"
            mock_request.hostname = "test-host"

            ingest_to_bronze(new_ingestion.id)

        new_ingestion.refresh_from_db()
        assert new_ingestion.status == PipelineStatus.FAILED
        assert new_ingestion.error_type == "DuplicateIngestion"


@pytest.fixture
def squad_parquet(tmp_path):
    """Create a temporary parquet file simulating FM squad data."""
    import pandas as pd

    df = pd.DataFrame({
        "name": ["Player A", "Player B", "Player C"],
        "age": [25, 22, 30],
        "value": ["10M", "8M", "15M"],
        "position": ["GK", "DF", "FW"],
    })
    path = tmp_path / "squad.parquet"
    df.to_parquet(path, index=False)
    return str(path)


@pytest.fixture(autouse=True)
def bronze_data_tables(db):
    """Ensure bronze data tables exist before each test that needs them.
    This fixture takes 'db' to ensure migrations have run, then creates
    the bronze schema and tables if they don't exist yet."""
    import duckdb
    from apps.core.db import get_pg_conn_string

    pg_conn = get_pg_conn_string()
    conn = duckdb.connect()
    try:
        conn.execute(
            f"ATTACH '{pg_conn}' AS bronze_schema (TYPE postgres)"
        )
        conn.execute("CREATE SCHEMA IF NOT EXISTS bronze_schema.bronze;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bronze_schema.bronze.squad_snapshot (
                ingestion_id BIGINT,
                simulation_source VARCHAR,
                save_name VARCHAR,
                ingame_date DATE,
                upload_timestamp TIMESTAMPTZ,
                snapshot_type VARCHAR,
                source_file_hash VARCHAR,
                raw_data JSON
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bronze_schema.bronze.scouting_snapshot (
                ingestion_id BIGINT,
                simulation_source VARCHAR,
                save_name VARCHAR,
                ingame_date DATE,
                upload_timestamp TIMESTAMPTZ,
                snapshot_type VARCHAR,
                source_file_hash VARCHAR,
                raw_data JSON
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bronze_schema.bronze.squad_matchstats_snapshot (
                ingestion_id BIGINT,
                simulation_source VARCHAR,
                save_name VARCHAR,
                ingame_date DATE,
                upload_timestamp TIMESTAMPTZ,
                snapshot_type VARCHAR,
                source_file_hash VARCHAR,
                opponent VARCHAR,
                ingame_matchdate DATE,
                raw_data JSON
            )
        """)
    finally:
        conn.close()


@pytest.fixture
def bronze_ingestion_processing(completed_upload, squad_parquet, bronze_data_tables, settings):
    """Create a processing BronzeIngestion with a real parquet path on disk."""
    from .models import BronzeIngestion

    upload = completed_upload
    upload.parquet_path = squad_parquet
    upload.save(update_fields=["parquet_path"])

    return BronzeIngestion.objects.create(
        landing_upload=upload,
        simulation_source="FM24",
        snapshot_type="squad_snapshot",
        source_file_hash="processing_hash",
        status=PipelineStatus.PROCESSING,
        started_at=timezone.now(),
    )


@pytest.fixture(autouse=True)
def clear_bronze_tables(bronze_data_tables):
    """Truncate bronze data tables after each test to keep tests isolated."""
    import duckdb
    from apps.core.db import get_pg_conn_string

    yield

    pg_conn = get_pg_conn_string()
    conn = duckdb.connect()
    try:
        conn.execute(f"ATTACH '{pg_conn}' AS bronze_schema (TYPE postgres)")
        conn.execute("TRUNCATE bronze_schema.bronze.squad_snapshot;")
        conn.execute("TRUNCATE bronze_schema.bronze.scouting_snapshot;")
        conn.execute("TRUNCATE bronze_schema.bronze.squad_matchstats_snapshot;")
    finally:
        conn.close()


class TestIngestToBronzeSuccess:
    def test_inserts_rows_into_bronze_table(
        self, bronze_ingestion_processing, squad_parquet
    ):
        import duckdb
        from apps.core.db import get_pg_conn_string
        from .tasks import ingest_to_bronze

        ingestion = bronze_ingestion_processing

        with patch("celery.app.task.Task.request") as mock_request:
            mock_request.retries = 0
            mock_request.max_retries = 3
            mock_request.id = "test-success-id"
            mock_request.hostname = "test-host"

            ingest_to_bronze(ingestion.id)

        ingestion.refresh_from_db()
        assert ingestion.status == PipelineStatus.COMPLETED
        assert ingestion.source_row_count == 3
        assert ingestion.ingested_row_count == 3
        assert ingestion.column_count == 4

        pg_conn = get_pg_conn_string()
        conn = duckdb.connect()
        try:
            conn.execute(
                f"ATTACH '{pg_conn}' AS bronze_schema (TYPE postgres)"
            )
            result = conn.execute(
                "SELECT COUNT(*) FROM bronze_schema.bronze.squad_snapshot "
                "WHERE ingestion_id = $1",
                [ingestion.id],
            ).fetchone()[0]
            assert result == 3
        finally:
            conn.close()

    def test_updates_task_row_to_succeeded(
        self, bronze_ingestion_processing
    ):
        from .models import BronzeIngestionPhase, BronzeIngestionTask
        from .tasks import ingest_to_bronze

        ingestion = bronze_ingestion_processing

        with patch("celery.app.task.Task.request") as mock_request:
            mock_request.retries = 0
            mock_request.max_retries = 3
            mock_request.id = "test-success-id"
            mock_request.hostname = "test-host"

            ingest_to_bronze(ingestion.id)

        task_row = BronzeIngestionTask.objects.get(
            ingestion=ingestion, attempt=1
        )
        assert task_row.status == TaskStatus.SUCCEEDED
        assert task_row.phase == BronzeIngestionPhase.COMPLETED
        assert task_row.finished_at is not None

    def test_tracks_phase_transitions(
        self, bronze_ingestion_processing
    ):
        from .models import BronzeIngestionPhase, BronzeIngestionTask
        from .tasks import ingest_to_bronze

        ingestion = bronze_ingestion_processing

        with patch("celery.app.task.Task.request") as mock_request:
            mock_request.retries = 0
            mock_request.max_retries = 3
            mock_request.id = "test-phase-id"
            mock_request.hostname = "test-host"

            ingest_to_bronze(ingestion.id)

        task_row = BronzeIngestionTask.objects.get(
            ingestion=ingestion, attempt=1
        )
        assert task_row.phase == BronzeIngestionPhase.COMPLETED


class TestIngestToBronzeErrorHandling:
    def test_retry_on_transient_error(
        self, bronze_ingestion_processing
    ):
        from .tasks import ingest_to_bronze

        ingestion = bronze_ingestion_processing

        with patch(
            "apps.bronze.tasks.run_bronze_append"
        ) as mock_append:
            mock_append.side_effect = duckdb.IOException(
                "connection lost"
            )
            with patch(
                "celery.app.task.Task.request"
            ) as mock_request:
                mock_request.retries = 1
                mock_request.max_retries = 3
                mock_request.id = "test-retry-id"
                mock_request.hostname = "test-host"

                with pytest.raises(duckdb.IOException):
                    ingest_to_bronze(ingestion.id)

        ingestion.refresh_from_db()
        assert ingestion.status == PipelineStatus.RETRYING

        from .models import BronzeIngestionTask
        task_row = BronzeIngestionTask.objects.get(
            ingestion=ingestion, attempt=2
        )
        assert task_row.status == TaskStatus.RETRYING
        assert task_row.error_type == "IOException"
        assert "connection lost" in task_row.error_message

    def test_failed_permanently_when_retries_exhausted(
        self, bronze_ingestion_processing
    ):
        from .tasks import ingest_to_bronze

        ingestion = bronze_ingestion_processing

        with patch(
            "apps.bronze.tasks.run_bronze_append"
        ) as mock_append:
            mock_append.side_effect = duckdb.OperationalError(
                "PG connection refused after 3 retries"
            )
            with patch(
                "celery.app.task.Task.request"
            ) as mock_request:
                mock_request.retries = 3
                mock_request.max_retries = 3
                mock_request.id = "test-exhausted-id"
                mock_request.hostname = "test-host"

                with pytest.raises(duckdb.OperationalError):
                    ingest_to_bronze(ingestion.id)

        ingestion.refresh_from_db()
        assert ingestion.status == PipelineStatus.FAILED

        from .models import BronzeIngestionTask
        task_row = BronzeIngestionTask.objects.get(
            ingestion=ingestion, step="ingest", attempt=4
        )
        assert task_row.status == TaskStatus.FAILED

    def test_non_retryable_exception_surfaces_immediately(
        self, bronze_ingestion_processing
    ):
        from .tasks import ingest_to_bronze

        ingestion = bronze_ingestion_processing

        with patch(
            "apps.bronze.tasks.run_bronze_append"
        ) as mock_append:
            mock_append.side_effect = duckdb.CatalogException(
                "table squad_snapshot does not exist"
            )
            with patch(
                "celery.app.task.Task.request"
            ) as mock_request:
                mock_request.retries = 0
                mock_request.max_retries = 3
                mock_request.id = "test-catalog-id"
                mock_request.hostname = "test-host"

                with pytest.raises(duckdb.CatalogException):
                    ingest_to_bronze(ingestion.id)

        ingestion.refresh_from_db()
        assert ingestion.status == PipelineStatus.FAILED

        from .models import BronzeIngestionTask
        task_row = BronzeIngestionTask.objects.get(
            ingestion=ingestion, attempt=1
        )
        assert task_row.status == TaskStatus.FAILED


class TestBronzeIngestionPhase:
    def test_all_phase_values_present(self):
        from .models import BronzeIngestionPhase

        expected = {
            "attaching": "Attaching to PostgreSQL",
            "counting_source": "Counting source parquet rows",
            "inserting": "Inserting into bronze table",
            "counting_ingested": "Verifying ingested row count",
            "completed": "Ingestion completed successfully",
        }
        for value, label in expected.items():
            phase = BronzeIngestionPhase(value)
            assert phase.label == label


class TestEndToEndPipeline:
    def test_full_csv_to_bronze_pipeline(
        self, save, tmp_path, settings, bronze_data_tables
    ):
        from pathlib import Path

        import duckdb
        from apps.core.db import get_pg_conn_string
        from apps.landing.models import LandingUpload
        from apps.landing.tasks import parse_file, store_parquet
        from .models import BronzeIngestion

        settings.DATASOURCE_ROOT = str(tmp_path)

        ingame_date = datetime.date(2028, 7, 15)
        upload = LandingUpload.objects.create(
            save_master=save,
            snapshot_type="squad_snapshot",
            ingame_date=ingame_date,
            data_label="e2e_test",
            simulation_source="FM24",
            source_file_hash="e2e_hash",
            source_file_name="squad.csv",
            file_size_bytes=42,
        )

        incoming_dir = Path(upload.incoming_path()).parent
        incoming_dir.mkdir(parents=True, exist_ok=True)
        Path(upload.incoming_path()).write_text(
            "name,age,value\nPlayer A,25,10M\nPlayer B,22,8M\nPlayer C,30,15M"
        )

        parse_file(upload_id=upload.id)
        store_parquet(upload_id=upload.id)

        upload.refresh_from_db()
        assert upload.status == PipelineStatus.COMPLETED
        assert upload.parquet_path

        ingestion = BronzeIngestion.objects.get(landing_upload=upload)
        assert ingestion.status == PipelineStatus.COMPLETED
        assert ingestion.source_row_count == 3
        assert ingestion.ingested_row_count == 3

        pg_conn = get_pg_conn_string()
        conn = duckdb.connect()
        try:
            conn.execute(
                f"ATTACH '{pg_conn}' AS bronze_schema (TYPE postgres)"
            )
            result = conn.execute(
                "SELECT COUNT(*) FROM bronze_schema.bronze.squad_snapshot "
                "WHERE ingestion_id = $1",
                [ingestion.id],
            ).fetchone()[0]
            assert result == 3
        finally:
            conn.close()


class TestBronzeIngestionAdmin:
    def test_has_add_permission_returns_false(self, admin_client, completed_upload):
        url = "/admin/bronze/bronzeingestion/add/"
        resp = admin_client.get(url)
        assert resp.status_code == 403

    def test_change_view_renders(self, admin_client, bronze_ingestion):
        url = f"/admin/bronze/bronzeingestion/{bronze_ingestion.id}/change/"
        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert str(bronze_ingestion.id) in resp.content.decode()

    def test_changelist_renders(self, admin_client, bronze_ingestion):
        url = "/admin/bronze/bronzeingestion/"
        resp = admin_client.get(url)
        assert resp.status_code == 200


class TestBronzeIngestionTaskAdmin:
    def test_has_add_permission_returns_false(self, admin_client, bronze_task):
        url = "/admin/bronze/bronzeingestiontask/add/"
        resp = admin_client.get(url)
        assert resp.status_code == 403

    def test_change_view_renders_read_only(
        self, admin_client, bronze_task
    ):
        url = f"/admin/bronze/bronzeingestiontask/{bronze_task.id}/change/"
        resp = admin_client.get(url)
        assert resp.status_code == 200

    def test_changelist_renders(self, admin_client, bronze_task):
        url = "/admin/bronze/bronzeingestiontask/"
        resp = admin_client.get(url)
        assert resp.status_code == 200
