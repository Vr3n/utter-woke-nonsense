from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'apps.core'

    def ready(self):
        import apps.core.metrics  # noqa: F401 — register Prometheus metrics
