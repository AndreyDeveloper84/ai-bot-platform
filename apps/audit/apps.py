"""AppConfig for apps.audit.

Sprint 8 review P1-cycle1 — registers the audit writer with the
tenancy callback registry in ``ready()``. This inverts the previous
``tenancy → audit`` lazy import: tenancy now declares the hook,
audit fulfils it. Foundation no longer depends on domain.
"""

from __future__ import annotations

from typing import Any

from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"

    def ready(self) -> None:
        """Register the audit-writer callback with tenancy."""
        # Import inside ready() so app-loading respects Django's order
        # (apps.tenancy loads BEFORE apps.audit per INSTALLED_APPS).
        from apps.audit.services import write_audit
        from apps.tenancy.audit_hook import register_audit_writer

        def _writer(action: str, payload: dict[str, Any]) -> None:
            """Adapter: tenancy hook signature → write_audit kwargs."""
            write_audit(action, payload=payload)

        register_audit_writer(_writer)
