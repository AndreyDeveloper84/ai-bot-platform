from django.apps import AppConfig


class OrdersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.orders"

    def ready(self) -> None:
        # DRF-2340 — registers payments.W001 (which payment mode this
        # deployment is in). Imported for its @register side effect.
        from apps.orders import checks  # noqa: F401
