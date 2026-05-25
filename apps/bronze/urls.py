from django.urls import path

from . import views

app_name = "bronze"

urlpatterns = [
    path("hx/bronze/status/<int:upload_id>/", views.bronze_status_poll, name="bronze_status"),
]
