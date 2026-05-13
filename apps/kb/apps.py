from django.apps import AppConfig


class KbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.kb"

    def ready(self) -> None:
        # Sprint 7 / K11 (DRF-569) — import for side effect of
        # registering pre_delete handlers on Tenant.
        from apps.kb import signals  # noqa: F401
