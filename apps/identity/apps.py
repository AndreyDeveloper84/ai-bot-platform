from django.apps import AppConfig


class IdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.identity"

    def ready(self) -> None:
        # Wire signal handlers (P1 auto-create profile, P8 booking_completed).
        from apps.identity import signals as _signals  # noqa: F401
