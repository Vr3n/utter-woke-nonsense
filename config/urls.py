"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

from apps.core.views import home, metrics_view, save_create_partial, save_detail, save_list_partial

urlpatterns = [
    path('admin/', admin.site.urls),
    path('metrics/', metrics_view, name='metrics'),
    path('', home, name='home'),
    path('partials/saves/', save_list_partial, name='save_list_partial'),
    path('partials/saves/create/', save_create_partial, name='save_create_partial'),
    path('', include('apps.bronze.urls')),
    path('', include('apps.silver.urls')),
    path('', include('apps.landing.urls')),
    path('', include('apps.datasets.urls')),
    path('<slug:save_slug>/', save_detail, name='save_detail'),
    path("__reload__/", include("django_browser_reload.urls")),
]
