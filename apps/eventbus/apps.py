from django.apps import AppConfig


class EventBusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.eventbus"
    verbose_name = "Domain event bus"

    def ready(self) -> None:
        from apps.eventbus import signals  # noqa: F401  — register post_save handlers
