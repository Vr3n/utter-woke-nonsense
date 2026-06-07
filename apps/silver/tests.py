import datetime
from unittest.mock import patch

import duckdb
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone

from apps.core.choices import PipelineStatus, TaskStatus


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
def completed_landing_upload(save, tmp_path, settings):
    from apps.landing.models import LandingUpload

    settings.DATASOURCE_ROOT = str(tmp_path)
    ingame_date = datetime.date(2028, 7, 15)
    return LandingUpload.objects.create(
        save_master=save,
        snapshot_type="squad_snapshot",
        ingame_date=ingame_date,
        data_label="summer_squad",
        simulation_source="FM24",
        source_file_hash="test_silver_hash",
        source_file_name="squad.csv",
        file_size_bytes=42,
        status=PipelineStatus.COMPLETED,
    )


@pytest.fixture
def bronze_ingestion(completed_landing_upload, db):
    from apps.bronze.models import BronzeIngestion

    return BronzeIngestion.objects.create(
        landing_upload=completed_landing_upload,
        simulation_source="FM24",
        snapshot_type="squad_snapshot",
        source_file_hash="test_silver_bronze_hash",
        status=PipelineStatus.COMPLETED,
        source_row_count=3,
    )


@pytest.fixture
def silver_ingestion(bronze_ingestion, db):
    from .models import SilverIngestion

    return SilverIngestion.objects.create(
        bronze_ingestion=bronze_ingestion,
        simulation_source=bronze_ingestion.simulation_source,
        snapshot_type=bronze_ingestion.snapshot_type,
        source_file_hash=bronze_ingestion.source_file_hash,
    )


@pytest.fixture
def silver_task(silver_ingestion, db):
    from .models import SilverIngestionTask

    return SilverIngestionTask.objects.create(
        ingestion=silver_ingestion,
        step="transform",
        attempt=1,
        celery_task_id="test-silver-task-id",
    )


@pytest.fixture
def admin_client(client, db):
    User = get_user_model()
    User.objects.create_superuser(
        username="admin", password="adminpass", email="admin@example.com"
    )
    client.login(username="admin", password="adminpass")
    return client


class TestSilverIngestionModel:
    def test_creates_with_default_pending_status(self, silver_ingestion):
        assert silver_ingestion.status == PipelineStatus.PENDING

    def test_bronze_ingestion_fk_linked(self, silver_ingestion, bronze_ingestion):
        assert silver_ingestion.bronze_ingestion == bronze_ingestion

    def test_duration_seconds_requires_both_timestamps(self, silver_ingestion):
        assert silver_ingestion.duration_seconds is None

    def test_duration_seconds_returns_diff(self, silver_ingestion):
        silver_ingestion.started_at = timezone.now()
        silver_ingestion.completed_at = (
            silver_ingestion.started_at + datetime.timedelta(seconds=30)
        )
        assert silver_ingestion.duration_seconds == 30

    def test_str_representation(self, silver_ingestion):
        text = str(silver_ingestion)
        assert str(silver_ingestion.id) in text
        assert silver_ingestion.snapshot_type in text

    def test_dropped_row_count_defaults_to_zero(self, silver_ingestion):
        assert silver_ingestion.dropped_row_count == 0

    def test_parse_progress_percent_zero_when_no_total(self, silver_ingestion):
        silver_ingestion.parse_progress_total = 0
        assert silver_ingestion.parse_progress_percent == 0

    def test_parse_progress_percent_computes_correctly(self, silver_ingestion):
        silver_ingestion.parse_progress_current = 3
        silver_ingestion.parse_progress_total = 5
        assert silver_ingestion.parse_progress_percent == 60

    def test_parse_progress_default_total_is_five(self, silver_ingestion):
        assert silver_ingestion.parse_progress_total == 5


class TestSilverIngestionTaskModel:
    def test_creates_with_default_pending_status(self, silver_task):
        assert silver_task.status == TaskStatus.PENDING

    def test_creates_with_empty_phase(self, silver_task):
        assert silver_task.phase == ""

    def test_fk_to_silver_ingestion(self, silver_task, silver_ingestion):
        assert silver_task.ingestion == silver_ingestion

    def test_unique_constraint_on_ingestion_step_attempt(self, silver_ingestion):
        from .models import SilverIngestionTask

        SilverIngestionTask.objects.create(
            ingestion=silver_ingestion,
            step="transform",
            attempt=1,
        )
        with pytest.raises(IntegrityError):
            SilverIngestionTask.objects.create(
                ingestion=silver_ingestion,
                step="transform",
                attempt=1,
            )

    def test_duration_seconds_requires_both_timestamps(self, silver_task):
        assert silver_task.duration_seconds is None

    def test_duration_seconds_returns_diff(self, silver_task):
        silver_task.started_at = timezone.now()
        silver_task.finished_at = (
            silver_task.started_at + datetime.timedelta(seconds=45)
        )
        assert silver_task.duration_seconds == 45

    def test_worker_hostname_defaults_to_empty(self, silver_task):
        assert silver_task.worker_hostname == ""


class TestSilverIngestionPhase:
    def test_all_phase_values_present(self):
        from .models import SilverIngestionPhase

        expected = {
            "attaching": "Attaching to PostgreSQL",
            "counting_source": "Counting source bronze rows",
            "transforming": "Transforming and cleaning data",
            "inserting": "Inserting into silver table",
            "counting_silver": "Verifying silver row count",
            "completed": "Ingestion completed successfully",
        }
        for value, label in expected.items():
            phase = SilverIngestionPhase(value)
            assert phase.label == label


class TestBuildSilverPipeline:
    def test_returns_sql_and_empty_extra_for_squad_snapshot(self):
        from .tasks import build_silver_pipeline

        sql, extras, column_count = build_silver_pipeline("squad_snapshot")
        assert "pg_db.bronze.{0}" in sql
        assert "pg_db.silver.{0}" in sql
        assert extras == []

    def test_returns_sql_and_empty_extra_for_scouting_snapshot(self):
        from .tasks import build_silver_pipeline

        sql, extras, column_count = build_silver_pipeline("scouting_snapshot")
        assert "pg_db.bronze.{0}" in sql
        assert "pg_db.silver.{0}" in sql
        assert extras == []

    def test_includes_extra_columns_for_matchstats(self):
        from .tasks import build_silver_pipeline

        sql, extras, column_count = build_silver_pipeline("squad_matchstats_snapshot")
        assert extras == ["opponent", "ingame_matchdate"]
        assert "opponent_str" in sql
        assert "ingame_matchdate_str" in sql
        assert "opponent" in sql
        assert "ingame_matchdate" in sql

    def test_sql_contains_source_cte(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert "source AS" in sql

    def test_sql_contains_unpacked_cte(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert "unpacked AS" in sql

    def test_sql_contains_cleaned_cte(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert "cleaned AS" in sql

    def test_sql_contains_final_cte(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert "final AS" in sql

    def test_sql_uses_try_cast_for_unique_id(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert "TRY_CAST" in sql

    def test_sql_uses_select_distinct(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert "SELECT DISTINCT" in sql

    def test_sql_uses_positional_placeholder(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert "$1" in sql

    def test_sql_filters_null_unique_id(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert "WHERE unique_id IS NOT NULL" in sql

    def test_new_columns_in_unpacked(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert 'best_pos_str' in sql
        assert 'rating_str' in sql
        assert 'height_str' in sql
        assert 'offsides_str' in sql
        assert 'shots_outside_box_per90_str' in sql

    def test_new_columns_in_cleaned(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert 'parse_height(height_str)' in sql
        assert 'NULLIF(NULLIF(rating_str' in sql
        assert 'NULLIF(NULLIF(offsides_str' in sql
        assert 'NULLIF(NULLIF(goals_per90_str' in sql
        assert 'NULLIF(best_pos_str' in sql
        assert 'NULLIF(position_str' in sql

    def test_new_columns_in_final_select(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert 'best_pos,' in sql
        assert 'rating,' in sql
        assert 'height,' in sql
        assert 'offsides,' in sql
        assert 'shots_outside_box_per90,' in sql
        assert 'shts_blckd_per90' in sql

    def test_recommendation_absent(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert 'recommendation' not in sql
        assert 'Recommendation' not in sql

    def test_best_pos_str_is_extracted(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert '$."Best Pos"' in sql

    def test_off_str_is_mapped_to_offsides(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert '$."Off"' in sql
        assert 'offsides' in sql

    def test_long_column_name_in_unpacked(self):
        from .tasks import build_silver_pipeline

        sql, _, column_count = build_silver_pipeline("squad_snapshot")
        assert '$."Shots From Outside The Box Per 90 minutes"' in sql

    def test_expires_handles_iso_format(self):
        from .tasks import build_silver_pipeline
        sql, _, _ = build_silver_pipeline("squad_snapshot")
        assert 'expires_str::DATE' in sql
        assert "WHEN expires_str ~" in sql

    def test_expires_handles_slash_format(self):
        from .tasks import build_silver_pipeline
        sql, _, _ = build_silver_pipeline("squad_snapshot")
        assert "str_split(expires_str, '/')" in sql

    def test_expires_uses_cast_for_iso(self):
        from .tasks import build_silver_pipeline
        sql, _, _ = build_silver_pipeline("squad_snapshot")
        assert "expires_str::DATE" in sql or "expires_str::date" in sql.lower()

    def test_ingame_matchdate_handles_iso(self):
        from .tasks import build_silver_pipeline
        sql, _, _ = build_silver_pipeline("squad_matchstats_snapshot")
        assert ("ingame_matchdate_str ~" in sql
                and "ingame_matchdate_str::DATE" in sql)

    def test_nation_fallsback_to_nation(self):
        from .tasks import build_silver_pipeline
        sql, _, _ = build_silver_pipeline("squad_snapshot")
        assert '$."Nation of Birth"' in sql
        assert '$."Nation"' in sql
        assert "COALESCE" in sql

    def test_scouting_nation_unaffected(self):
        from .tasks import build_silver_pipeline
        sql, _, _ = build_silver_pipeline("scouting_snapshot")
        assert '$."Nation of Birth"' in sql
        assert '$."Nation"' in sql
        assert "COALESCE" in sql

    def test_scouting_has_new_columns(self):
        from .tasks import build_silver_pipeline

        sql, extras, column_count = build_silver_pipeline("scouting_snapshot")
        assert extras == []
        assert 'best_pos_str' in sql
        assert 'rating_str' in sql
        assert 'height_str' in sql
        assert 'best_pos' in sql
        assert 'rating' in sql
        assert 'height' in sql


class TestStripSuffix:
    def test_strips_pw_suffix(self):
        from .tasks import _strip_suffix
        assert _strip_suffix("£4.6K p/w") == "£4.6K"
        assert _strip_suffix("£230 - £320 p/w") == "£230 - £320"
        assert _strip_suffix("£10K") == "£10K"
        assert _strip_suffix("Unknown") == "Unknown"
        assert _strip_suffix("") == ""

    def test_strips_pw_various_spacing(self):
        from .tasks import _strip_suffix
        assert _strip_suffix("£1.3K p/w") == "£1.3K"
        assert _strip_suffix("£825 p/w") == "£825"


class TestDocstringUDFs:
    """Test that the documented UDF behaviour matches implementation."""

    def test_parse_currency_min_simple_k(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("£10K") == 10000

    def test_parse_currency_min_simple_m(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("£5M") == 5000000

    def test_parse_currency_min_range(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("£10K - £20K") == 10000

    def test_parse_currency_min_unknown(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("Unknown") is None

    def test_parse_currency_min_na(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("N/A") is None

    def test_parse_currency_min_dash(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("-") is None

    def test_parse_currency_min_none(self):
        from .tasks import parse_currency_min

        assert parse_currency_min(None) is None

    def test_parse_currency_min_empty(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("") is None

    def test_parse_currency_min_dollar_prefix(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("$2M") == 2000000

    def test_parse_currency_min_euro_prefix(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("€1.5K") == 1500

    def test_parse_currency_min_no_prefix(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("1M") == 1000000

    def test_parse_currency_min_range_with_different_prefix(self):
        from .tasks import parse_currency_min

        assert parse_currency_min("€100K - £200K") == 100000

    def test_parse_currency_max_simple(self):
        from .tasks import parse_currency_max

        assert parse_currency_max("£10K") == 10000

    def test_parse_currency_max_range(self):
        from .tasks import parse_currency_max

        assert parse_currency_max("£10K - £20K") == 20000

    def test_parse_currency_max_no_range_same_as_min(self):
        from .tasks import parse_currency_max

        assert parse_currency_max("£5M") == 5000000

    def test_parse_currency_min_strips_pw_suffix(self):
        from .tasks import parse_currency_min
        assert parse_currency_min("£4.6K p/w") == 4600
        assert parse_currency_min("£825 p/w") == 825

    def test_parse_currency_min_pw_range(self):
        from .tasks import parse_currency_min
        assert parse_currency_min("£230 - £320 p/w") == 230
        assert parse_currency_min("£100 p/w") == 100

    def test_parse_currency_max_strips_pw_suffix(self):
        from .tasks import parse_currency_max
        assert parse_currency_max("£4.6K p/w") == 4600
        assert parse_currency_max("£1.3K p/w") == 1300

    def test_parse_currency_max_pw_range(self):
        from .tasks import parse_currency_max
        assert parse_currency_max("£230 - £320 p/w") == 320
        assert parse_currency_max("£65 - £100 p/w") == 100

    def test_parse_currency_max_unknown(self):
        from .tasks import parse_currency_max

        assert parse_currency_max("Unknown") is None

    def test_parse_starts_simple(self):
        from .tasks import parse_starts

        assert parse_starts("6") == 6

    def test_parse_starts_with_subs(self):
        from .tasks import parse_starts

        assert parse_starts("6 (1)") == 6

    def test_parse_starts_zero(self):
        from .tasks import parse_starts

        assert parse_starts("0") == 0

    def test_parse_starts_unknown(self):
        from .tasks import parse_starts

        assert parse_starts("Unknown") is None

    def test_parse_starts_none(self):
        from .tasks import parse_starts

        assert parse_starts(None) is None

    def test_parse_starts_empty(self):
        from .tasks import parse_starts

        assert parse_starts("") is None

    def test_parse_subs_with_value(self):
        from .tasks import parse_subs

        assert parse_subs("6 (1)") == 1

    def test_parse_subs_no_subs_returns_zero(self):
        from .tasks import parse_subs

        assert parse_subs("6") == 0

    def test_parse_subs_zero_starts_with_subs(self):
        from .tasks import parse_subs

        assert parse_subs("0 (0)") == 0

    def test_parse_subs_unknown(self):
        from .tasks import parse_subs

        assert parse_subs("Unknown") is None

    def test_parse_subs_none(self):
        from .tasks import parse_subs

        assert parse_subs(None) is None

    def test_parse_subs_empty(self):
        from .tasks import parse_subs

        assert parse_subs("") is None

    def test_parse_subs_multi_digit(self):
        from .tasks import parse_subs

        assert parse_subs("12 (3)") == 3


class TestParseHeight:
    def test_parse_height_cm(self):
        from .tasks import parse_height

        assert parse_height("185 cm") == 185

    def test_parse_height_na(self):
        from .tasks import parse_height

        assert parse_height("N/A") is None

    def test_parse_height_dash(self):
        from .tasks import parse_height

        assert parse_height("-") is None

    def test_parse_height_unknown(self):
        from .tasks import parse_height

        assert parse_height("Unknown") is None

    def test_parse_height_empty(self):
        from .tasks import parse_height

        assert parse_height("") is None

    def test_parse_height_none(self):
        from .tasks import parse_height

        assert parse_height(None) is None

    def test_parse_height_lower_sentinel(self):
        from .tasks import parse_height

        assert parse_height("n/a") is None
        # Case-sensitive sentinel check: "n/a" not in sentinel list,
        # and int("n/a") fails → None


class TestSilverIngestionAdmin:
    def test_silver_ingestion_has_add_permission_returns_false(
        self, admin_client, silver_ingestion
    ):
        url = "/admin/silver/silveringestion/add/"
        resp = admin_client.get(url)
        assert resp.status_code == 403

    def test_silver_ingestion_change_view_renders(
        self, admin_client, silver_ingestion
    ):
        url = f"/admin/silver/silveringestion/{silver_ingestion.id}/change/"
        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert str(silver_ingestion.id) in resp.content.decode()

    def test_silver_ingestion_changelist_renders(
        self, admin_client, silver_ingestion
    ):
        url = "/admin/silver/silveringestion/"
        resp = admin_client.get(url)
        assert resp.status_code == 200

    def test_task_has_add_permission_returns_false(
        self, admin_client, silver_task
    ):
        url = "/admin/silver/silveringestiontask/add/"
        resp = admin_client.get(url)
        assert resp.status_code == 403

    def test_task_change_view_renders(self, admin_client, silver_task):
        url = f"/admin/silver/silveringestiontask/{silver_task.id}/change/"
        resp = admin_client.get(url)
        assert resp.status_code == 200

    def test_task_changelist_renders(self, admin_client, silver_task):
        url = "/admin/silver/silveringestiontask/"
        resp = admin_client.get(url)
        assert resp.status_code == 200


class TestDispatchSilverIngestion:
    def test_creates_silver_ingestion_with_processing_status(
        self, bronze_ingestion
    ):
        from .models import SilverIngestion
        from .tasks import dispatch_silver_ingestion

        with patch(
            "apps.silver.tasks.transform_to_silver.delay"
        ) as mock_delay:
            mock_delay.return_value.id = "mock-silver-task-id"
            dispatch_silver_ingestion(bronze_ingestion)

        ingestion = SilverIngestion.objects.get(
            bronze_ingestion=bronze_ingestion
        )
        assert ingestion.status == PipelineStatus.PROCESSING
        assert ingestion.simulation_source == bronze_ingestion.simulation_source
        assert ingestion.snapshot_type == bronze_ingestion.snapshot_type
        assert ingestion.source_file_hash == bronze_ingestion.source_file_hash
        assert ingestion.started_at is not None

    def test_sets_celery_task_id_after_dispatch(self, bronze_ingestion):
        from .models import SilverIngestion
        from .tasks import dispatch_silver_ingestion

        with patch(
            "apps.silver.tasks.transform_to_silver.delay"
        ) as mock_delay:
            mock_delay.return_value.id = "celery-silver-abc"
            dispatch_silver_ingestion(bronze_ingestion)

        ingestion = SilverIngestion.objects.get(
            bronze_ingestion=bronze_ingestion
        )
        assert ingestion.celery_task_id == "celery-silver-abc"

    def test_dispatches_transform_task_with_ingestion_id(
        self, bronze_ingestion
    ):
        from .models import SilverIngestion
        from .tasks import dispatch_silver_ingestion

        with patch(
            "apps.silver.tasks.transform_to_silver.delay"
        ) as mock_delay:
            mock_delay.return_value.id = "mock-silver-task-id"
            dispatch_silver_ingestion(bronze_ingestion)

        ingestion = SilverIngestion.objects.get(
            bronze_ingestion=bronze_ingestion
        )
        mock_delay.assert_called_once_with(ingestion.id)


class TestTransformToSilverIdempotency:
    def test_completed_ingestion_for_same_bronze_returns_early(
        self, bronze_ingestion, silver_ingestion
    ):
        from .models import SilverIngestion
        from .tasks import transform_to_silver

        silver_ingestion.status = PipelineStatus.COMPLETED
        silver_ingestion.save(update_fields=["status"])

        new_ingestion = SilverIngestion.objects.create(
            bronze_ingestion=bronze_ingestion,
            simulation_source=bronze_ingestion.simulation_source,
            snapshot_type=bronze_ingestion.snapshot_type,
            source_file_hash=bronze_ingestion.source_file_hash,
            status=PipelineStatus.PROCESSING,
            started_at=timezone.now(),
        )

        with patch(
            "celery.app.task.Task.request"
        ) as mock_request:
            mock_request.retries = 0
            mock_request.max_retries = 3
            mock_request.id = "test-silver-idempotency-id"
            mock_request.hostname = "test-host"

            transform_to_silver(new_ingestion.id)

        new_ingestion.refresh_from_db()
        assert new_ingestion.status == PipelineStatus.SKIPPED
        assert new_ingestion.error_type == "DuplicateIngestion"


class TestTransformToSilverErrorHandling:
    def test_non_retryable_exception_surfaces_immediately(
        self, silver_ingestion
    ):
        from .tasks import transform_to_silver

        ingestion = silver_ingestion

        mock_request = patch(
            "celery.app.task.Task.request"
        )
        mock_build = patch(
            "apps.silver.tasks.build_silver_pipeline",
            side_effect=duckdb.CatalogException("silver table missing"),
        )

        with mock_request as mr, mock_build:
            mr.retries = 0
            mr.max_retries = 3
            mr.id = "test-nonretry-id"
            mr.hostname = "test-host"

            with pytest.raises(duckdb.CatalogException):
                transform_to_silver(ingestion.id)

        ingestion.refresh_from_db()
        assert ingestion.status == PipelineStatus.FAILED
        assert ingestion.error_type == "CatalogException"
        assert "missing" in ingestion.error_message
