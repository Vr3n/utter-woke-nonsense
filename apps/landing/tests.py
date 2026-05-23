from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.models import FootballManagerVersionMaster, SaveMaster

from .converter import ConversionContext, CsvConversionStrategy, HtmlConversionStrategy
from .models import LandingUpload, LandingZoneTask
from .tasks import dispatch_pipeline, parse_file, store_parquet


@pytest.fixture
def landing_root(tmp_path):
    root = tmp_path / "landing_zone"
    root.mkdir()
    return root


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="testuser", password="pass")


@pytest.fixture
def save(settings, landing_root, user):
    settings.DATASOURCE_ROOT = str(landing_root)
    fmv = FootballManagerVersionMaster.objects.create(game_version="FM24")
    return SaveMaster.objects.create(
        name="my-save",
        slug="my-save",
        start_date="2024-01-01",
        game_version=fmv,
        user=user,
    )


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def sample_csv():
    return SimpleUploadedFile(
        "squad.csv",
        b"name,age,value\nPlayer A,25,10M\nPlayer B,22,8M",
        content_type="text/csv",
    )


@pytest.fixture
def sample_html():
    return SimpleUploadedFile(
        "scouting.html",
        b"<table><tr><th>Name</th><th>Age</th></tr><tr><td>Player A</td><td>25</td></tr></table>",
        content_type="text/html",
    )


def _create_upload(save, **kwargs):
    params = dict(
        save_master=save,
        snapshot_type="squad_snapshot",
        ingame_date="2028-07-15",
        data_label="summer_squad",
        simulation_source="FM24",
        source_file_hash="abc123",
        source_file_name="squad.csv",
        file_size_bytes=42,
    )
    params.update(kwargs)
    return LandingUpload.objects.create(**params)


def _write_incoming(upload, content=b"name,age,value\nPlayer A,25,10M\nPlayer B,22,8M"):
    incoming_dir = Path(upload.incoming_path()).parent
    incoming_dir.mkdir(parents=True, exist_ok=True)
    Path(upload.incoming_path()).write_text(
        content.decode() if isinstance(content, bytes) else content
    )


def _staging_path(settings, upload_id):
    return Path(settings.DATASOURCE_ROOT) / "staging" / f"{upload_id}.parquet"


# === Page & Upload tests ===


@pytest.mark.django_db
def test_squad_page_returns_200(client, save):
    resp = client.get(f"/{save.slug}/squad/")
    assert resp.status_code == 200


@pytest.mark.django_db
def test_upload_creates_landing_upload_record(client, save, sample_csv):
    resp = client.post(
        f"/hx/{save.slug}/upload/",
        {
            "snapshot_type": "squad_snapshot",
            "ingame_date": "2028-07-15",
            "data_label": "summer_squad",
            "source_file": sample_csv,
        },
    )
    assert resp.status_code == 202
    assert LandingUpload.objects.count() == 1
    upload = LandingUpload.objects.first()
    assert upload.save_master == save
    assert upload.snapshot_type == "squad_snapshot"
    assert upload.ingame_date.isoformat() == "2028-07-15"
    assert upload.data_label == "summer_squad"
    assert upload.simulation_source == "FM24"


@pytest.mark.django_db
def test_upload_returns_202(client, save, sample_csv):
    resp = client.post(
        f"/hx/{save.slug}/upload/",
        {
            "snapshot_type": "squad_snapshot",
            "ingame_date": "2028-07-15",
            "data_label": "summer_squad",
            "source_file": sample_csv,
        },
    )
    assert resp.status_code == 202


@pytest.mark.django_db
def test_duplicate_file_hash_rejected(client, save, sample_csv):
    client.post(
        f"/hx/{save.slug}/upload/",
        {
            "snapshot_type": "squad_snapshot",
            "ingame_date": "2028-07-15",
            "data_label": "summer_squad",
            "source_file": sample_csv,
        },
    )
    assert LandingUpload.objects.count() == 1

    duplicate = SimpleUploadedFile(
        "squad.csv",
        b"name,age,value\nPlayer A,25,10M\nPlayer B,22,8M",
        content_type="text/csv",
    )
    resp = client.post(
        f"/hx/{save.slug}/upload/",
        {
            "snapshot_type": "squad_snapshot",
            "ingame_date": "2028-07-15",
            "data_label": "summer_squad",
            "source_file": duplicate,
        },
    )
    assert resp.status_code == 400
    assert LandingUpload.objects.count() == 1


# === Converter strategy tests ===


def test_csv_conversion_writes_valid_parquet(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("name,age,value\nPlayer A,25,10M\nPlayer B,22,8M")
    dest = tmp_path / "output.parquet"

    ctx = ConversionContext(CsvConversionStrategy())
    ctx.execute(str(source), str(dest))

    assert dest.exists()
    df = duckdb.sql(f"SELECT * FROM '{dest}'").fetchdf()
    assert list(df.columns) == ["name", "age", "value"]
    assert len(df) == 2


def test_html_conversion_writes_valid_parquet(tmp_path):
    source = tmp_path / "input.html"
    source.write_text(
        "<table><tr><th>Name</th><th>Age</th></tr>"
        "<tr><td>Player A</td><td>25</td></tr></table>"
    )
    dest = tmp_path / "output.parquet"

    ctx = ConversionContext(HtmlConversionStrategy())
    ctx.execute(str(source), str(dest))

    assert dest.exists()
    df = pd.read_parquet(dest)
    assert list(df.columns) == ["Name", "Age"]
    assert len(df) == 1


# === Table partial tests ===


@pytest.mark.django_db
def test_table_partial_returns_200(client, save):
    resp = client.get(
        f"/hx/{save.slug}/squad_snapshot/table/"
    )
    assert resp.status_code == 200


@pytest.mark.django_db
def test_table_partial_shows_uploads(client, save):
    LandingUpload.objects.create(
        save_master=save,
        snapshot_type="squad_snapshot",
        ingame_date="2028-07-15",
        data_label="summer_squad",
        simulation_source="FM24",
        source_file_hash="abc123",
        source_file_name="squad.csv",
        status=LandingUpload.Status.COMPLETED,
        parquet_path="/some/path.parquet",
    )
    resp = client.get(
        f"/hx/{save.slug}/squad_snapshot/table/"
    )
    assert resp.status_code == 200
    assert "summer_squad" in resp.content.decode()


@pytest.mark.django_db
def test_table_partial_shows_empty_state(client, save):
    resp = client.get(
        f"/hx/{save.slug}/squad_snapshot/table/"
    )
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "No snapshots uploaded yet" in content


# === parse_file task tests ===


@pytest.mark.django_db
def test_parse_file_converts_csv_to_staging_parquet(save, settings):
    upload = _create_upload(save)
    _write_incoming(upload)

    parse_file(upload_id=upload.id)

    staging = _staging_path(settings, upload.id)
    assert staging.exists()
    df = duckdb.sql(f"SELECT * FROM '{staging}'").fetchdf()
    assert len(df) == 2

    upload.refresh_from_db()
    assert upload.parse_progress_current == 3
    assert upload.parse_progress_description == "Done"


@pytest.mark.django_db
def test_parse_file_converts_html_to_staging_parquet(save, settings):
    upload = _create_upload(save, source_file_name="scouting.html",
                            source_file_hash="def456")
    _write_incoming(upload, content=b"<table><tr><th>Name</th><th>Age</th></tr><tr><td>Player A</td><td>25</td></tr></table>")

    parse_file(upload_id=upload.id)

    staging = _staging_path(settings, upload.id)
    assert staging.exists()
    df = pd.read_parquet(staging)
    assert len(df) == 1

    upload.refresh_from_db()
    assert upload.parse_progress_current == 3
    assert upload.parse_progress_description == "Done"

    task_rows = LandingZoneTask.objects.filter(upload=upload, step="parse")
    assert task_rows.count() == 1
    assert task_rows.first().status == "succeeded"


@pytest.mark.django_db
def test_parse_file_creates_landing_zone_task_on_start(save):
    upload = _create_upload(save)
    _write_incoming(upload)

    parse_file(upload_id=upload.id)

    upload.refresh_from_db()
    assert upload.parse_progress_current == 3
    assert upload.parse_progress_description == "Done"

    task_row = LandingZoneTask.objects.get(upload=upload, step="parse", attempt=1)
    assert task_row.started_at is not None
    assert task_row.finished_at is not None
    assert task_row.status == "succeeded"


@pytest.mark.django_db
def test_parse_file_failure_sets_retrying_when_retries_remain(save, settings):
    upload = _create_upload(save, source_file_name="bad.html", source_file_hash="ghi789")
    _write_incoming(upload, content=b"<html><body>No tables</body></html>")

    with pytest.raises(ValueError):
        parse_file(upload_id=upload.id)

    upload.refresh_from_db()
    assert upload.status == LandingUpload.Status.RETRYING

    task_row = LandingZoneTask.objects.get(upload=upload, step="parse", attempt=1)
    assert task_row.status == "retrying"
    assert task_row.error_type
    assert task_row.error_message
    assert task_row.finished_at is not None


@pytest.mark.django_db
def test_parse_file_failure_sets_failed_when_retries_exhausted(save, settings):
    upload = _create_upload(save, source_file_name="bad.html", source_file_hash="ghi789")
    _write_incoming(upload, content=b"<html><body>No tables</body></html>")

    with (
        patch("celery.app.task.Task.request") as mock_request,
        patch("apps.landing.tasks.ProgressRecorder"),
    ):
        mock_request.retries = 3
        mock_request.max_retries = 3
        mock_request.id = "test-id"
        mock_request.hostname = "test-host"

        with pytest.raises(ValueError):
            parse_file(upload_id=upload.id)

    upload.refresh_from_db()
    assert upload.status == LandingUpload.Status.FAILED

    task_row = LandingZoneTask.objects.get(upload=upload, step="parse", attempt=4)
    assert task_row.status == "failed"


# === store_parquet task tests ===


@pytest.mark.django_db
def test_store_parquet_moves_staging_to_final(save, settings):
    upload = _create_upload(save)
    _write_incoming(upload)

    parse_file(upload_id=upload.id)

    store_parquet(upload_id=upload.id)

    upload.refresh_from_db()
    assert upload.status == LandingUpload.Status.COMPLETED
    assert upload.parquet_path

    final = Path(upload.parquet_path)
    assert final.exists()
    assert not _staging_path(settings, upload.id).exists()

    df = duckdb.sql(f"SELECT * FROM '{final}'").fetchdf()
    assert len(df) == 2

    task_rows = LandingZoneTask.objects.filter(upload=upload, step="store")
    assert task_rows.count() == 1
    assert task_rows.first().status == "succeeded"


@pytest.mark.django_db
def test_store_parquet_file_exists_error(save, settings):
    upload = _create_upload(save, source_file_hash="exists123", status=LandingUpload.Status.PROCESSING)
    upload.save(update_fields=["status"])
    _write_incoming(upload)

    parse_file(upload_id=upload.id)

    dest_dir = (
        Path(settings.DATASOURCE_ROOT)
        / upload.snapshot_type
        / upload.save_master.slug
        / upload.ingame_date
    )
    dest_name = f"{upload.data_label}_{upload.upload_timestamp.strftime('%Y%m%d%H%M%S')}.parquet"
    dest_path = dest_dir / dest_name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path.touch()

    with pytest.raises(FileExistsError):
        store_parquet(upload_id=upload.id)

    upload.refresh_from_db()
    assert upload.status == LandingUpload.Status.PROCESSING

    task_row = LandingZoneTask.objects.get(upload=upload, step="store", attempt=1)
    assert task_row.status == "running"
    assert not task_row.error_type
    assert task_row.finished_at is None


@pytest.mark.django_db
def test_store_parquet_file_not_found_error(save):
    upload = _create_upload(save, source_file_hash="notfound123", status=LandingUpload.Status.PROCESSING)
    upload.save(update_fields=["status"])

    with pytest.raises(FileNotFoundError):
        store_parquet(upload_id=upload.id)

    upload.refresh_from_db()
    assert upload.status == LandingUpload.Status.PROCESSING

    task_row = LandingZoneTask.objects.get(upload=upload, step="store", attempt=1)
    assert task_row.status == "running"
    assert not task_row.error_type
    assert task_row.finished_at is None


# === dispatch_pipeline / chain end-to-end tests ===


@pytest.mark.django_db
def test_dispatch_pipeline_completes_full_chain(save):
    upload = _create_upload(save)
    _write_incoming(upload)

    dispatch_pipeline(upload_id=upload.id)

    upload.refresh_from_db()
    assert upload.status == LandingUpload.Status.COMPLETED
    assert upload.parquet_path
    assert upload.entry_task_id

    final = Path(upload.parquet_path)
    assert final.exists()
    df = duckdb.sql(f"SELECT * FROM '{final}'").fetchdf()
    assert len(df) == 2

    parse_rows = LandingZoneTask.objects.filter(upload=upload, step="parse")
    assert parse_rows.count() == 1
    assert parse_rows.first().status == "succeeded"

    store_rows = LandingZoneTask.objects.filter(upload=upload, step="store")
    assert store_rows.count() == 1
    assert store_rows.first().status == "succeeded"


# === View file-writing test ===


@pytest.mark.django_db
def test_upload_view_saves_file_and_dispatches(client, save, sample_csv):
    resp = client.post(
        f"/hx/{save.slug}/upload/",
        {
            "snapshot_type": "squad_snapshot",
            "ingame_date": "2028-07-15",
            "data_label": "summer_squad",
            "source_file": sample_csv,
        },
    )
    assert resp.status_code == 202
    upload = LandingUpload.objects.first()
    from django.conf import settings
    incoming_file = (
        Path(settings.DATASOURCE_ROOT) / "incoming" / str(upload.id) / "squad.csv"
    )
    assert incoming_file.exists()
    assert incoming_file.read_text() == "name,age,value\nPlayer A,25,10M\nPlayer B,22,8M"


# === Progress / logging tests ===


@patch("apps.landing.tasks.ProgressRecorder")
@patch("apps.landing.tasks.logger")
@pytest.mark.django_db
def test_progress_fields_update_at_each_step(mock_logger, mock_recorder_cls, save, settings):
    upload = _create_upload(save)
    _write_incoming(upload)

    parse_file(upload_id=upload.id)

    upload.refresh_from_db()
    assert upload.parse_progress_current == 3
    assert upload.parse_progress_description == "Done"
    assert upload.parse_progress_percent == 100

    assert mock_logger.info.call_count >= 3
    assert mock_logger.warning.call_count == 0
    assert mock_logger.error.call_count == 0


@patch("apps.landing.tasks.ProgressRecorder")
@pytest.mark.django_db
def test_progress_fragment_returns_polling_during_process(mock_recorder_cls, save, settings, client_logged_in):
    upload = _create_upload(save, status=LandingUpload.Status.PROCESSING)
    upload.parse_progress_current = 1
    upload.parse_progress_description = "Parsing the file"
    upload.save(update_fields=["status", "parse_progress_current", "parse_progress_description"])

    resp = client_logged_in.get(f"/hx/progress/{upload.id}/")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "hx-trigger" in content
    assert "Parsing the file" in content
    assert "1/3" in content


@pytest.mark.django_db
def test_progress_fragment_returns_done_when_complete(save, settings, client_logged_in):
    upload = _create_upload(save, status=LandingUpload.Status.COMPLETED)
    upload.parse_progress_current = 3
    upload.parse_progress_description = "Done"
    upload.save(update_fields=["status", "parse_progress_current", "parse_progress_description"])

    resp = client_logged_in.get(f"/hx/progress/{upload.id}/")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "hx-trigger" not in content
    assert "Done" in content


@pytest.mark.django_db
def test_progress_fragment_returns_done_when_failed(save, settings, client_logged_in):
    upload = _create_upload(save, status=LandingUpload.Status.FAILED)
    upload.parse_progress_current = 2
    upload.parse_progress_description = "Converting to Parquet"
    upload.save(update_fields=["status", "parse_progress_current", "parse_progress_description"])

    resp = client_logged_in.get(f"/hx/progress/{upload.id}/")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "hx-trigger" not in content
    assert "Converting to Parquet" in content


@pytest.mark.django_db
def test_progress_fragment_accessible_by_id(save, settings, client):
    upload = _create_upload(save, status=LandingUpload.Status.COMPLETED)
    upload.parse_progress_current = 3
    upload.parse_progress_description = "Done"
    upload.save(update_fields=["status", "parse_progress_current", "parse_progress_description"])

    resp = client.get(f"/hx/progress/{upload.id}/")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Done" in content
    assert "hx-trigger" not in content


@pytest.mark.django_db
def test_status_poll_shows_last_error_on_failure(save, client):
    upload = _create_upload(save, status=LandingUpload.Status.FAILED)
    LandingZoneTask.objects.create(
        upload=upload,
        step=LandingZoneTask.Step.PARSE,
        attempt=1,
        celery_task_id="",
        status=LandingZoneTask.Status.FAILED,
        error_type="ValueError",
        error_message="bad data",
        finished_at=None,
    )

    resp = client.get(f"/hx/{save.slug}/squad_snapshot/status/{upload.id}/")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "ValueError" in content
    assert "bad data" in content


@pytest.mark.django_db
def test_retrying_status_renders_in_template(save, client):
    upload = _create_upload(save, status=LandingUpload.Status.RETRYING)
    resp = client.get(f"/hx/{save.slug}/squad_snapshot/status/{upload.id}/")
    assert resp.status_code == 200
    assert "Retrying" in resp.content.decode()
