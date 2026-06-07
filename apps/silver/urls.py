from django.urls import path

from . import views

app_name = "silver"

urlpatterns = [
    path("hx/silver/dispatch/<int:upload_id>/", views.dispatch_silver, name="dispatch_silver"),
]
