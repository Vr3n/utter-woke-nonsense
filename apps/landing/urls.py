from django.urls import path

from . import views

urlpatterns = [
    path("hx/<slug:save_slug>/upload/", views.upload_snapshot, name="upload_snapshot"),
    path(
        "hx/<slug:save_slug>/<str:snapshot_type>/table/",
        views.upload_table_partial,
        name="upload_table_partial",
    ),
    path(
        "hx/<slug:save_slug>/<str:snapshot_type>/pipeline-status/<int:upload_id>/",
        views.pipeline_status_poll,
        name="pipeline_status_poll",
    ),

    path(
        "hx/<slug:save_slug>/<int:upload_id>/retry/",
        views.upload_retry,
        name="upload_retry",
    ),
    path(
        "<slug:save_slug>/squad/",
        views.save_snapshot_page,
        {"snapshot_type": "squad_snapshot"},
        name="save_squad",
    ),
    path(
        "<slug:save_slug>/scouting/",
        views.save_snapshot_page,
        {"snapshot_type": "scouting_snapshot"},
        name="save_scouting",
    ),
    path(
        "<slug:save_slug>/matchdays/",
        views.save_snapshot_page,
        {"snapshot_type": "squad_matchstats_snapshot"},
        name="save_matchdays",
    ),
]
