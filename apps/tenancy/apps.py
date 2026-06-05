from django.apps import AppConfig


class TenancyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tenancy"

    def ready(self) -> None:
        # Tenancy retro «missing abstract base»: register a Django
        # ``checks`` integration that walks installed models, finds any
        # with a ``tenant`` FK, and verifies they have the canonical
        # manager pair (``objects = TenantScopedManager()`` plus an
        # ``all_tenants`` escape hatch). Pre-fix a model author who
        # forgot either declaration shipped a model with no scoping AND
        # no audit trail — neither omission was caught by CI. Wiring a
        # check is less invasive than refactoring every existing model
        # into an abstract ``TenantScopedModel`` base.
        from apps.tenancy import system_checks  # noqa: F401 — registers checks
