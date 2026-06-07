from django.urls import path

from . import views

urlpatterns = [
    path(
        "<slug:save_slug>/datasets/",
        views.dataset_list,
        name="save_datasets",
    ),
    path(
        "<slug:save_slug>/datasets/<int:upload_id>/",
        views.dataset_detail,
        name="dataset_detail",
    ),
    path(
        "hx/datasets/<slug:save_slug>/pipeline-status/<int:upload_id>/",
        views.dataset_pipeline_poll,
        name="dataset_pipeline_poll",
    ),
    path(
        "hx/datasets/sections/<slug:save_slug>/",
        views.dataset_list_sections,
        name="dataset_list_sections",
    ),
    path(
        "hx/datasets/node/<int:upload_id>/<str:step>/",
        views.node_detail_partial,
        name="dataset_node_detail",
    ),
    path(
        "hx/datasets/row/<int:upload_id>/",
        views.listing_row_partial,
        name="dataset_listing_row",
    ),
    path(
        "<slug:save_slug>/datasets/<int:upload_id>/retry/",
        views.retry_landing,
        name="retry_landing",
    ),
]
