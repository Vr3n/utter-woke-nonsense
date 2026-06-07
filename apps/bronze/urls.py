from django.urls import path

from . import views

app_name = "bronze"

urlpatterns = [
    path("hx/bronze/dispatch/<int:upload_id>/", views.dispatch_bronze, name="dispatch_bronze"),
]
