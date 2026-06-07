from unittest.mock import patch

import pytest
from django.urls import resolve, reverse
from django.utils import timezone

from apps.bronze.models import BronzeIngestion, BronzeIngestionTask
from apps.core.choices import PipelineStatus, TaskStatus
from apps.landing.models import LandingUpload, LandingZoneTask
from apps.silver.models import SilverIngestion, SilverIngestionTask

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def save():
    from apps.core.models import FootballManagerVersionMaster, SaveMaster

    fmv = FootballManagerVersionMaster.objects.create(game_version="FM24")
    return SaveMaster.objects.create(
        name="my-save",
        slug="my-save",
        start_date="2024-01-01",
        game_version=fmv,
    )


@pytest.fixture
def upload(save):
    return LandingUpload.objects.create(
        save_master=save,
        snapshot_type="squad_snapshot",
        ingame_date="2028-07-15",
        data_label="summer_squad",
        simulation_source="FM24",
        source_file_hash="hash_001",
        source_file_name="squad.csv",
        file_size_bytes=1024,
        status=PipelineStatus.PENDING,
    )


@pytest.fixture
def parse_task(upload):
    return LandingZoneTask.objects.create(
        upload=upload,
        step="parse",
        attempt=1,
        celery_task_id="t1",
        status=TaskStatus.SUCCEEDED,
    )


@pytest.fixture
def store_task(upload):
    return LandingZoneTask.objects.create(
        upload=upload,
        step="store",
        attempt=1,
        celery_task_id="t2",
        status=TaskStatus.SUCCEEDED,
    )


@pytest.fixture
def completed_upload(save):
    return LandingUpload.objects.create(
        save_master=save,
        snapshot_type="squad_snapshot",
        ingame_date="2028-07-15",
        data_label="summer_squad",
        simulation_source="FM24",
        source_file_hash="hash_002",
        source_file_name="squad.csv",
        file_size_bytes=2048,
        status=PipelineStatus.COMPLETED,
    )


@pytest.fixture
def bronze_ingestion(completed_upload):
    return BronzeIngestion.objects.create(
        landing_upload=completed_upload,
        simulation_source="FM24",
        snapshot_type="squad_snapshot",
        source_file_hash="hash_002",
        status=PipelineStatus.COMPLETED,
    )


@pytest.fixture
def silver_ingestion(bronze_ingestion):
    return SilverIngestion.objects.create(
        bronze_ingestion=bronze_ingestion,
        simulation_source="FM24",
        snapshot_type="squad_snapshot",
        source_file_hash="hash_002",
        status=PipelineStatus.COMPLETED,
    )


@pytest.fixture
def silver_task(silver_ingestion):
    return SilverIngestionTask.objects.create(
        ingestion=silver_ingestion,
        step="transform",
        attempt=1,
        celery_task_id="t4",
        status=TaskStatus.SUCCEEDED,
    )


@pytest.fixture
def bronze_task(bronze_ingestion):
    return BronzeIngestionTask.objects.create(
        ingestion=bronze_ingestion,
        step="ingest",
        attempt=1,
        celery_task_id="t3",
        status=TaskStatus.SUCCEEDED,
    )


# ===================================================================
# CaptureQueries
# ===================================================================

class TestCaptureQueries:
    def test_captures_zero_queries_outside_request(self):
        from .perf import CaptureQueries

        with CaptureQueries() as perf:
            pass
        assert perf.count == 0
        assert perf.duration >= 0
        assert perf.queries == []

    def test_captures_query_count(self):
        from .perf import CaptureQueries
        from apps.landing.models import LandingUpload

        with CaptureQueries() as perf:
            list(LandingUpload.objects.all())
        assert perf.count >= 0
        assert perf.duration >= 0

    def test_queries_property_returns_slice(self):
        from .perf import CaptureQueries

        with CaptureQueries() as perf:
            pass
        assert isinstance(perf.queries, list)

    def test_reusable_after_context(self):
        from .perf import CaptureQueries

        with CaptureQueries() as perf:
            pass
        count = perf.count
        duration = perf.duration
        assert isinstance(count, int)
        assert isinstance(duration, (int, float))


# ===================================================================
# URL Resolution
# ===================================================================

class TestUrls:
    def test_dataset_list_url(self, save):
        url = reverse("save_datasets", kwargs={"save_slug": save.slug})
        assert url == f"/{save.slug}/datasets/"
        match = resolve(url)
        assert match.view_name == "save_datasets"

    def test_dataset_detail_url(self, save, upload):
        url = reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        assert url == f"/{save.slug}/datasets/{upload.id}/"
        match = resolve(url)
        assert match.view_name == "dataset_detail"

    def test_node_detail_url(self, upload):
        url = reverse("dataset_node_detail", kwargs={"upload_id": upload.id, "step": "parse"})
        assert url == f"/hx/datasets/node/{upload.id}/parse/"
        match = resolve(url)
        assert match.view_name == "dataset_node_detail"

    def test_listing_row_url(self, upload):
        url = reverse("dataset_listing_row", kwargs={"upload_id": upload.id})
        assert url == f"/hx/datasets/row/{upload.id}/"
        match = resolve(url)
        assert match.view_name == "dataset_listing_row"

    def test_retry_landing_url(self, save, upload):
        url = reverse("retry_landing", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        assert url == f"/{save.slug}/datasets/{upload.id}/retry/"
        match = resolve(url)
        assert match.view_name == "retry_landing"


# ===================================================================
# dataset_list View
# ===================================================================

class TestDatasetListView:
    def test_returns_200(self, client, save, upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert response.status_code == 200

    def test_context_has_save_and_saves(self, client, save, upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert response.context["save"] == save
        assert save in response.context["saves"]

    def test_partition_active_failed_includes_non_completed(self, client, save, upload):
        upload.status = PipelineStatus.PROCESSING
        upload.save()
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert upload in response.context["active_failed"]

    def test_partition_awaiting_ingestion_includes_landing_only(self, client, save, completed_upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert completed_upload in response.context["awaiting_ingestion"]

    def test_partition_awaiting_ingestion_excludes_non_completed(self, client, save, upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert upload not in response.context["awaiting_ingestion"]

    def test_partition_awaiting_transformation_includes_bronze_completed(
        self, client, save, completed_upload, bronze_ingestion, bronze_task
    ):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert completed_upload in response.context["awaiting_transformation"]

    def test_partition_awaiting_transformation_excludes_bronze_not_completed(
        self, client, save, completed_upload
    ):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert completed_upload not in response.context["awaiting_transformation"]

    def test_partition_fully_processed_includes_silver_completed(
        self, client, save, completed_upload, bronze_ingestion, bronze_task, silver_ingestion, silver_task
    ):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert completed_upload in response.context["fully_processed"]

    def test_partition_fully_processed_excludes_silver_not_completed(
        self, client, save, completed_upload, bronze_ingestion, bronze_task
    ):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert completed_upload not in response.context["fully_processed"]

    def test_health_100_percent_when_all_fully_processed(self, client, save, completed_upload, bronze_ingestion, bronze_task, silver_ingestion, silver_task):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert response.context["health"] == 100

    def test_health_0_percent_when_none_completed(self, client, save, upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert response.context["health"] == 0

    def test_backlog_counts_pending_processing_retrying(self, client, save, upload):
        upload.status = PipelineStatus.PROCESSING
        upload.save()
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert response.context["backlog"] == 1

    def test_backlog_excludes_completed_and_failed(self, client, save, completed_upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert response.context["backlog"] == 0

    def test_data_volume_sums_file_sizes(self, client, save, upload, completed_upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert "3.0 KB" in response.context["data_volume"]

    def test_context_has_perf_object(self, client, save, upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert hasattr(response.context["perf"], "count")

    def test_total_uploads_count(self, client, save, upload, completed_upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert response.context["total_uploads"] == 2

    def test_uses_correct_template(self, client, save, upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert "pages/datasets/list.html" in [t.name for t in response.templates]

    def test_404_for_unknown_save(self, client):
        response = client.get("/unknown/datasets/")
        assert response.status_code == 404


# ===================================================================
# dataset_detail View
# ===================================================================

class TestDatasetDetailView:
    def test_returns_200(self, client, save, upload):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert response.status_code == 200

    def test_context_has_save(self, client, save, upload):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert response.context["save"] == save

    def test_context_has_upload(self, client, save, upload):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert response.context["upload"] == upload

    def test_file_size_formatted(self, client, save, upload):
        upload.file_size_bytes = 2048
        upload.save()
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert response.context["file_size"] == "2.0 KB"

    def test_file_size_dash_when_none(self, client, save, upload):
        upload.file_size_bytes = None
        upload.save()
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert response.context["file_size"] == "—"

    def test_pipeline_duration_from_bronze(self, client, save, completed_upload, bronze_ingestion):
        from django.utils import timezone
        import datetime

        bronze_ingestion.completed_at = timezone.now() + datetime.timedelta(seconds=45)
        bronze_ingestion.save()
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": completed_upload.id})
        )
        assert response.context["pipeline_duration"] is not None

    def test_pipeline_duration_none_without_bronze(self, client, save, upload):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert response.context["pipeline_duration"] is None

    def test_parse_attempts_in_context(self, client, save, upload, parse_task):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert len(response.context["parse_attempts"]) == 1
        assert response.context["parse_attempts"][0] == parse_task

    def test_store_attempts_in_context(self, client, save, upload, store_task):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert len(response.context["store_attempts"]) == 1
        assert response.context["store_attempts"][0] == store_task

    def test_bronze_attempts_in_context(self, client, save, completed_upload, bronze_ingestion, bronze_task):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": completed_upload.id})
        )
        assert len(response.context["bronze_attempts"]) == 1
        assert response.context["bronze_attempts"][0] == bronze_task

    def test_bronze_attempts_empty_without_bronze(self, client, save, upload):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert response.context["bronze_attempts"] == []

    def test_404_for_wrong_save(self, client, save, upload):
        response = client.get(f"/wrong/datasets/{upload.id}/")
        assert response.status_code == 404

    def test_404_for_missing_upload(self, client, save):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": 99999})
        )
        assert response.status_code == 404

    def test_uses_correct_template(self, client, save, upload):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert "pages/datasets/detail.html" in [t.name for t in response.templates]

    def test_bronze_ingestion_in_context_when_exists(
        self, client, save, completed_upload, bronze_ingestion
    ):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": completed_upload.id})
        )
        assert response.context["bronze_ingestion"] == bronze_ingestion

    def test_bronze_ingestion_none_when_missing(self, client, save, upload):
        response = client.get(
            reverse("dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert response.context["bronze_ingestion"] is None


# ===================================================================
# node_detail_partial View
# ===================================================================

class TestNodeDetailPartial:
    def test_returns_parse_attempts(self, client, upload, parse_task):
        response = client.get(
            reverse("dataset_node_detail", kwargs={"upload_id": upload.id, "step": "parse"})
        )
        assert response.status_code == 200
        assert parse_task in response.context["attempts"]

    def test_returns_store_attempts(self, client, upload, store_task):
        response = client.get(
            reverse("dataset_node_detail", kwargs={"upload_id": upload.id, "step": "store"})
        )
        assert response.status_code == 200
        assert store_task in response.context["attempts"]

    def test_returns_bronze_attempts(self, client, completed_upload, bronze_ingestion, bronze_task):
        response = client.get(
            reverse("dataset_node_detail", kwargs={"upload_id": completed_upload.id, "step": "bronze"})
        )
        assert response.status_code == 200
        assert bronze_task in response.context["attempts"]

    def test_404_for_unknown_upload(self, client):
        response = client.get("/hx/datasets/node/99999/parse/")
        assert response.status_code == 404

    def test_404_for_bronze_without_ingestion(self, client, upload):
        response = client.get(
            reverse("dataset_node_detail", kwargs={"upload_id": upload.id, "step": "bronze"})
        )
        assert response.status_code == 404

    def test_uses_correct_template(self, client, upload, parse_task):
        response = client.get(
            reverse("dataset_node_detail", kwargs={"upload_id": upload.id, "step": "parse"})
        )
        assert "datasets/partials/node_detail.html" in [t.name for t in response.templates]

    def test_context_has_step(self, client, upload, parse_task):
        response = client.get(
            reverse("dataset_node_detail", kwargs={"upload_id": upload.id, "step": "parse"})
        )
        assert response.context["step"] == "parse"

    def test_attempts_sorted_by_attempt(self, client, upload, parse_task):
        _ = LandingZoneTask.objects.create(
            upload=upload,
            step="parse",
            attempt=2,
            celery_task_id="t1_r",
        )
        response = client.get(
            reverse("dataset_node_detail", kwargs={"upload_id": upload.id, "step": "parse"})
        )
        attempts = response.context["attempts"]
        assert attempts[0].attempt == 1
        assert attempts[1].attempt == 2


# ===================================================================
# listing_row_partial View
# ===================================================================

class TestListingRowPartial:
    def test_returns_200(self, client, upload):
        response = client.get(
            reverse("dataset_listing_row", kwargs={"upload_id": upload.id})
        )
        assert response.status_code == 200

    def test_context_has_upload(self, client, upload):
        response = client.get(
            reverse("dataset_listing_row", kwargs={"upload_id": upload.id})
        )
        assert response.context["upload"] == upload

    def test_context_has_silver_annotation(self, client, upload):
        response = client.get(
            reverse("dataset_listing_row", kwargs={"upload_id": upload.id})
        )
        assert hasattr(response.context["upload"], "silver_status")

    def test_context_has_file_size(self, client, upload):
        response = client.get(
            reverse("dataset_listing_row", kwargs={"upload_id": upload.id})
        )
        assert response.context["file_size"] is not None

    def test_uses_correct_template(self, client, upload):
        response = client.get(
            reverse("dataset_listing_row", kwargs={"upload_id": upload.id})
        )
        assert "datasets/partials/listing_row.html" in [t.name for t in response.templates]

    def test_404_for_unknown_upload(self, client):
        response = client.get("/hx/datasets/row/99999/")
        assert response.status_code == 404


# ===================================================================
# retry_landing View
# ===================================================================

class TestRetryLandingView:
    def test_redirects_on_success(self, client, save):
        upload = LandingUpload.objects.create(
            save_master=save,
            snapshot_type="squad_snapshot",
            ingame_date="2028-07-15",
            data_label="summer_squad",
            simulation_source="FM24",
            source_file_hash="hash_fail",
            source_file_name="fail.csv",
            file_size_bytes=512,
            status=LandingUpload.Status.FAILED,
        )
        with patch("apps.datasets.views.dispatch_pipeline") as mock_dispatch:
            response = client.post(
                reverse("retry_landing", kwargs={"save_slug": save.slug, "upload_id": upload.id})
            )
        assert response.status_code == 302
        assert response.url == reverse(
            "dataset_detail", kwargs={"save_slug": save.slug, "upload_id": upload.id}
        )
        mock_dispatch.assert_called_once_with(upload.id)

    def test_404_for_non_failed_upload(self, client, save, upload):
        response = client.post(
            reverse("retry_landing", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert response.status_code == 404

    def test_404_for_missing_upload(self, client, save):
        response = client.post(
            reverse("retry_landing", kwargs={"save_slug": save.slug, "upload_id": 99999})
        )
        assert response.status_code == 404

    def test_requires_POST(self, client, save):
        upload = LandingUpload.objects.create(
            save_master=save,
            snapshot_type="squad_snapshot",
            ingame_date="2028-07-15",
            data_label="summer_squad",
            simulation_source="FM24",
            source_file_hash="hash_fail2",
            source_file_name="fail2.csv",
            file_size_bytes=512,
            status=LandingUpload.Status.FAILED,
        )
        response = client.get(
            reverse("retry_landing", kwargs={"save_slug": save.slug, "upload_id": upload.id})
        )
        assert response.status_code == 405


# ===================================================================
# _human_size helper
# ===================================================================

class TestHumanSize:
    def test_bytes(self):
        from .views import _human_size

        assert _human_size(512) == "512.0 B"

    def test_kilobytes(self):
        from .views import _human_size

        assert _human_size(1536) == "1.5 KB"

    def test_megabytes(self):
        from .views import _human_size

        assert _human_size(2_500_000) == "2.4 MB"

    def test_gigabytes(self):
        from .views import _human_size

        assert _human_size(3_200_000_000) == "3.0 GB"

    def test_none(self):
        from .views import _human_size

        assert _human_size(None) == "—"

    def test_zero(self):
        from .views import _human_size

        assert _human_size(0) == "0.0 B"


# ===================================================================
# _phase_dots helper
# ===================================================================

class TestPhaseDots:
    def _annotated(self, upload):
        """Return an upload annotated with the fields _phase_dots expects."""
        from django.db.models import OuterRef, Subquery
        from apps.landing.models import LandingZoneTask
        from apps.bronze.models import BronzeIngestion
        return LandingUpload.objects.annotate(
            parse_status=Subquery(
                LandingZoneTask.objects.filter(upload=OuterRef("pk"), step="parse")
                .order_by("-attempt")
                .values("status")[:1]
            ),
            store_status=Subquery(
                LandingZoneTask.objects.filter(upload=OuterRef("pk"), step="store")
                .order_by("-attempt")
                .values("status")[:1]
            ),
            bronze_status=Subquery(
                BronzeIngestion.objects.filter(landing_upload=OuterRef("pk"))
                .order_by("-started_at")
                .values("status")[:1]
            ),
        ).get(pk=upload.pk)

    def test_returns_four_phases(self, upload):
        from .views import _phase_dots

        dots = _phase_dots(self._annotated(upload))
        assert len(dots) == 4

    def test_first_phase_is_source(self, upload):
        from .views import _phase_dots

        dots = _phase_dots(self._annotated(upload))
        assert dots[0][0] == "Source"

    def test_last_phase_is_bronze(self, upload):
        from .views import _phase_dots

        dots = _phase_dots(self._annotated(upload))
        assert dots[3][0] == "Bronze"
