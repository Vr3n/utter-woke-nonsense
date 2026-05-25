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
        "hx/datasets/sse/<int:upload_id>/",
        views.event_stream,
        name="dataset_event_stream",
    ),
    path(
        "hx/datasets/sse/save/<slug:save_slug>/",
        views.listing_event_stream,
        name="dataset_listing_stream",
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
]
