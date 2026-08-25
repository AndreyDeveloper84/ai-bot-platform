from __future__ import annotations

from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"

    def ready(self) -> None:
        """Connect the MasterService write-provenance gate (DRF-975).

        Importing the module is what registers the receivers — they are
        declared with ``@receiver(..., sender="catalog.MasterService")``.
        Without this import the gate silently does not exist, which is the
        worst possible failure for a security-shaped control, so the
        provenance test suite asserts the receivers are connected rather
        than only asserting behaviour.
        """

        from apps.catalog import signals  # noqa: F401  (import registers receivers)
