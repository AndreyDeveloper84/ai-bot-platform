from django.apps import AppConfig


class HandoffConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.handoff"

    def ready(self) -> None:
        # DRF-1488 — registers handoff.E001 (no addressee configured).
        # Imported for its @register side effect; nothing here is called.
        from apps.handoff import checks  # noqa: F401
