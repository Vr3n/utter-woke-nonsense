import json
import datetime
from unittest.mock import patch

import pytest
from django.urls import resolve, reverse
from django.utils import timezone

from apps.bronze.models import BronzeIngestion, BronzeIngestionTask
from apps.core.choices import PipelineStatus, TaskStatus
from apps.landing.models import LandingUpload, LandingZoneTask

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
def bronze_task(bronze_ingestion):
    return BronzeIngestionTask.objects.create(
        ingestion=bronze_ingestion,
        step="ingest",
        attempt=1,
        celery_task_id="t3",
        status=TaskStatus.SUCCEEDED,
    )


# ===================================================================
# DatasetEvent Model
# ===================================================================

class TestDatasetEventModel:
    def test_create_all_event_types(self, upload):
        from .models import DatasetEvent

        for et in DatasetEvent.EventType.values:
            event = DatasetEvent.objects.create(upload=upload, event_type=et)
            assert event.pk is not None
            assert event.event_type == et

    def test_default_payload_is_empty_dict(self, upload):
        from .models import DatasetEvent

        event = DatasetEvent.objects.create(
            upload=upload, event_type=DatasetEvent.EventType.PARSE_COMPLETED
        )
        assert event.payload == {}

    def test_created_at_auto_now_add(self, upload):
        from .models import DatasetEvent

        event = DatasetEvent.objects.create(
            upload=upload, event_type=DatasetEvent.EventType.PARSE_COMPLETED
        )
        assert event.created_at is not None

    def test_ordering_newest_first(self, upload):
        from .models import DatasetEvent

        e1 = DatasetEvent.objects.create(
            upload=upload, event_type=DatasetEvent.EventType.PARSE_COMPLETED
        )
        e2 = DatasetEvent.objects.create(
            upload=upload, event_type=DatasetEvent.EventType.STORE_COMPLETED
        )
        qs = list(DatasetEvent.objects.all())
        assert qs[0] == e2
        assert qs[1] == e1

    def test_str_representation(self, upload):
        from .models import DatasetEvent

        event = DatasetEvent.objects.create(
            upload=upload, event_type=DatasetEvent.EventType.PARSE_COMPLETED
        )
        text = str(event)
        assert "parse_completed" in text
        assert str(upload.id) in text

    def test_filter_by_upload_and_created_at_index(self, upload):
        from .models import DatasetEvent

        for _ in range(3):
            DatasetEvent.objects.create(
                upload=upload, event_type=DatasetEvent.EventType.PARSE_COMPLETED
            )
        qs = DatasetEvent.objects.filter(upload=upload)
        assert qs.count() == 3

    def test_cascade_on_upload_delete(self, upload):
        from .models import DatasetEvent

        DatasetEvent.objects.create(
            upload=upload, event_type=DatasetEvent.EventType.PARSE_COMPLETED
        )
        pk = upload.pk
        upload.delete()
        assert DatasetEvent.objects.filter(upload_id=pk).count() == 0

    def test_event_type_choices_match_expected(self):
        from .models import DatasetEvent

        expected = {
            "parse_completed": "Parse Completed",
            "parse_failed": "Parse Failed",
            "store_completed": "Store Completed",
            "store_failed": "Store Failed",
            "bronze_completed": "Bronze Completed",
            "bronze_failed": "Bronze Failed",
        }
        assert DatasetEvent.EventType.choices == list(expected.items())

    def test_payload_stores_arbitrary_data(self, upload):
        from .models import DatasetEvent

        payload = {"attempt": 1, "duration_s": 3.2, "rows": 500}
        event = DatasetEvent.objects.create(
            upload=upload,
            event_type=DatasetEvent.EventType.BRONZE_COMPLETED,
            payload=payload,
        )
        assert event.payload == payload


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

    def test_event_stream_url(self, upload):
        url = reverse("dataset_event_stream", kwargs={"upload_id": upload.id})
        assert url == f"/hx/datasets/sse/{upload.id}/"
        match = resolve(url)
        assert match.view_name == "dataset_event_stream"

    def test_listing_event_stream_url(self, save):
        url = reverse("dataset_listing_stream", kwargs={"save_slug": save.slug})
        assert url == f"/hx/datasets/sse/save/{save.slug}/"
        match = resolve(url)
        assert match.view_name == "dataset_listing_stream"

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

    def test_partition_completed_landing_includes_completed(self, client, save, completed_upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert completed_upload in response.context["completed_landing"]

    def test_partition_completed_landing_excludes_non_completed(self, client, save, upload):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert upload not in response.context["completed_landing"]

    def test_partition_completed_bronze_includes_bronze_completed(
        self, client, save, completed_upload, bronze_ingestion, bronze_task
    ):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert completed_upload in response.context["completed_bronze"]

    def test_partition_completed_bronze_excludes_bronze_not_completed(
        self, client, save, completed_upload
    ):
        response = client.get(reverse("save_datasets", kwargs={"save_slug": save.slug}))
        assert completed_upload not in response.context["completed_bronze"]

    def test_health_100_percent_when_all_completed(self, client, save, completed_upload):
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
        t2 = LandingZoneTask.objects.create(
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

    def test_context_has_dots(self, client, upload):
        response = client.get(
            reverse("dataset_listing_row", kwargs={"upload_id": upload.id})
        )
        assert len(response.context["dots"]) == 4

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
# SSE Event Streams
# ===================================================================

class TestEventStream:
    def _first_chunk(self, response):
        """Read the first chunk from a streaming response."""
        return next(iter(response.streaming_content))

    def test_event_stream_content_type(self, client, upload):
        from .models import DatasetEvent

        DatasetEvent.objects.create(
            upload=upload, event_type=DatasetEvent.EventType.PARSE_COMPLETED
        )
        response = client.get(
            reverse("dataset_event_stream", kwargs={"upload_id": upload.id})
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"

    def test_event_stream_yields_events(self, client, upload):
        from .models import DatasetEvent

        DatasetEvent.objects.create(
            upload=upload,
            event_type=DatasetEvent.EventType.PARSE_COMPLETED,
            payload={"attempt": 1},
        )
        response = client.get(
            reverse("dataset_event_stream", kwargs={"upload_id": upload.id})
        )
        chunk = self._first_chunk(response)
        assert b"event: dataset-event" in chunk
        assert b"parse_completed" in chunk

    def test_event_stream_returns_200_for_any_upload(self, client, save, upload):
        """Even when no events exist, the endpoint returns 200."""
        response = client.get(
            reverse("dataset_event_stream", kwargs={"upload_id": upload.id})
        )
        assert response.status_code == 200

    def test_listing_event_stream_content_type(self, client, save, upload):
        response = client.get(
            reverse("dataset_listing_stream", kwargs={"save_slug": save.slug})
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "text/event-stream"

    def test_listing_event_stream_filters_by_save(self, client, save, upload):
        from .models import DatasetEvent

        DatasetEvent.objects.create(
            upload=upload, event_type=DatasetEvent.EventType.PARSE_COMPLETED
        )
        response = client.get(
            reverse("dataset_listing_stream", kwargs={"save_slug": save.slug})
        )
        chunk = self._first_chunk(response)
        assert b"parse_completed" in chunk

    def test_event_data_format(self, client, upload):
        from .models import DatasetEvent

        DatasetEvent.objects.create(
            upload=upload,
            event_type=DatasetEvent.EventType.BRONZE_COMPLETED,
            payload={"attempt": 1, "rows": 500},
        )
        response = client.get(
            reverse("dataset_event_stream", kwargs={"upload_id": upload.id})
        )
        chunk = self._first_chunk(response).decode()
        assert chunk.startswith("event: dataset-event\n")
        assert "data: " in chunk
        data_line = [l for l in chunk.split("\n") if l.startswith("data: ")][0]
        parsed = json.loads(data_line.replace("data: ", ""))
        assert parsed["type"] == "bronze_completed"
        assert parsed["upload_id"] == upload.id
        assert parsed["payload"] == {"attempt": 1, "rows": 500}


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
# Task Integration — DatasetEvent created in Celery tasks
# ===================================================================

class TestParseFileDatasetEvents:
    @patch("apps.landing.tasks.ConversionContext")
    def test_creates_parse_completed_event(self, mock_ctx, save, tmp_path, settings):
        settings.DATASOURCE_ROOT = str(tmp_path)
        from apps.landing.tasks import parse_file

        incoming = tmp_path / "incoming"
        incoming.mkdir(parents=True)
        upload = LandingUpload.objects.create(
            save_master=save,
            snapshot_type="squad_snapshot",
            ingame_date="2028-07-15",
            data_label="summer_squad",
            simulation_source="FM24",
            source_file_hash="hash_parse_test",
            source_file_name="squad.csv",
            file_size_bytes=42,
        )
        src = incoming / str(upload.id)
        src.mkdir()
        (src / "squad.csv").write_bytes(b"a,b\n1,2\n")

        parse_file(upload.id)

        from .models import DatasetEvent
        events = DatasetEvent.objects.filter(upload=upload)
        assert events.filter(event_type="parse_completed").exists()
        payload = events.get(event_type="parse_completed").payload
        assert payload["attempt"] == 1

    @patch("apps.landing.tasks.ConversionContext")
    def test_creates_parse_failed_event_on_error(self, mock_ctx, save, tmp_path, settings):
        settings.DATASOURCE_ROOT = str(tmp_path)
        from apps.landing.tasks import parse_file

        mock_ctx.return_value.execute.side_effect = ValueError("corrupt file")

        incoming = tmp_path / "incoming"
        incoming.mkdir(parents=True)
        upload = LandingUpload.objects.create(
            save_master=save,
            snapshot_type="squad_snapshot",
            ingame_date="2028-07-15",
            data_label="summer_squad",
            simulation_source="FM24",
            source_file_hash="hash_parse_fail",
            source_file_name="squad.csv",
            file_size_bytes=42,
        )
        src = incoming / str(upload.id)
        src.mkdir()
        (src / "squad.csv").write_bytes(b"bad data")

        with pytest.raises(ValueError):
            parse_file(upload.id)

        from .models import DatasetEvent
        assert DatasetEvent.objects.filter(
            upload=upload, event_type="parse_failed"
        ).exists()


class TestStoreParquetDatasetEvents:
    def test_creates_store_completed_event(self, save, tmp_path, settings):
        settings.DATASOURCE_ROOT = str(tmp_path)
        from apps.landing.tasks import store_parquet

        staging = tmp_path / "staging"
        staging.mkdir()
        upload = LandingUpload.objects.create(
            save_master=save,
            snapshot_type="squad_snapshot",
            ingame_date="2028-07-15",
            data_label="summer_squad",
            simulation_source="FM24",
            source_file_hash="hash_store_test",
            source_file_name="squad.csv",
            file_size_bytes=42,
        )
        staging_path = staging / f"{upload.id}.parquet"
        staging_path.write_bytes(b"parquet content")

        with patch("apps.landing.tasks.dispatch_bronze_ingestion"):
            store_parquet(upload.id)

        from .models import DatasetEvent
        events = DatasetEvent.objects.filter(upload=upload)
        assert events.filter(event_type="store_completed").exists()
        payload = events.get(event_type="store_completed").payload
        assert payload["attempt"] == 1

    def test_creates_store_failed_event_on_filenotfound(self, save, tmp_path, settings):
        settings.DATASOURCE_ROOT = str(tmp_path)
        from apps.landing.tasks import store_parquet

        staging = tmp_path / "staging"
        staging.mkdir()
        upload = LandingUpload.objects.create(
            save_master=save,
            snapshot_type="squad_snapshot",
            ingame_date="2028-07-15",
            data_label="summer_squad",
            simulation_source="FM24",
            source_file_hash="hash_store_fail",
            source_file_name="squad.csv",
            file_size_bytes=42,
        )

        with pytest.raises(FileNotFoundError):
            store_parquet(upload.id)

        from .models import DatasetEvent
        assert DatasetEvent.objects.filter(
            upload=upload, event_type="store_failed"
        ).exists()

    def test_creates_store_failed_event_on_fileexists(self, save, tmp_path, settings):
        settings.DATASOURCE_ROOT = str(tmp_path)
        from apps.landing.tasks import store_parquet

        staging = tmp_path / "staging"
        staging.mkdir()
        import datetime
        upload = LandingUpload.objects.create(
            save_master=save,
            snapshot_type="squad_snapshot",
            ingame_date=datetime.date(2028, 7, 15),
            data_label="summer_squad",
            simulation_source="FM24",
            source_file_hash="hash_store_exists",
            source_file_name="squad.csv",
            file_size_bytes=42,
        )
        staging_path = staging / f"{upload.id}.parquet"
        staging_path.write_bytes(b"content")

        dest_dir = (
            tmp_path / upload.snapshot_type / save.slug / upload.ingame_date.isoformat()
        )
        dest_dir.mkdir(parents=True)
        # Use the actual upload_timestamp to match store_parquet's filename logic
        upload.refresh_from_db()
        dest_name = (
            f"{upload.data_label}_"
            f"{upload.upload_timestamp.strftime('%Y%m%d%H%M%S')}.parquet"
        )
        dest = dest_dir / dest_name
        dest.write_bytes(b"preexisting")

        with pytest.raises(FileExistsError):
            store_parquet(upload.id)

        from .models import DatasetEvent
        assert DatasetEvent.objects.filter(
            upload=upload, event_type="store_failed"
        ).exists()


class TestBronzeIngestionDatasetEvents:
    def test_creates_bronze_completed_event(self, save, completed_upload, tmp_path, settings):
        settings.DATASOURCE_ROOT = str(tmp_path)
        from apps.bronze.tasks import ingest_to_bronze

        ingestion = BronzeIngestion.objects.create(
            landing_upload=completed_upload,
            simulation_source="FM24",
            snapshot_type="squad_snapshot",
            source_file_hash="hash_bronze_test",
            started_at=timezone.now(),
        )

        with patch("apps.bronze.tasks.duckdb_pg_connect") as mock_connect:
            mock_conn = mock_connect.return_value.__enter__.return_value
            mock_conn.execute.return_value.fetchone.return_value = (500,)
            ingest_to_bronze(ingestion.id)

        from .models import DatasetEvent
        events = DatasetEvent.objects.filter(upload=completed_upload)
        assert events.filter(event_type="bronze_completed").exists()
        payload = events.get(event_type="bronze_completed").payload
        assert payload["attempt"] == 1
        assert payload["source_rows"] == 500

    def test_creates_bronze_failed_event_on_exception(self, save, completed_upload, tmp_path, settings):
        settings.DATASOURCE_ROOT = str(tmp_path)
        from apps.bronze.tasks import ingest_to_bronze
        import duckdb

        ingestion = BronzeIngestion.objects.create(
            landing_upload=completed_upload,
            simulation_source="FM24",
            snapshot_type="squad_snapshot",
            source_file_hash="hash_bronze_fail",
            started_at=timezone.now(),
        )

        with patch("apps.bronze.tasks.duckdb_pg_connect") as mock_connect:
            mock_conn = mock_connect.return_value.__enter__.return_value
            mock_conn.execute.side_effect = duckdb.IOException("connection lost")
            with pytest.raises(duckdb.IOException):
                ingest_to_bronze(ingestion.id)

        from .models import DatasetEvent
        assert DatasetEvent.objects.filter(
            upload=completed_upload, event_type="bronze_failed"
        ).exists()


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
