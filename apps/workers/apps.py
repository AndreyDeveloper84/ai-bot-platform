from __future__ import annotations

from django.apps import AppConfig


class WorkersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.workers"

    def ready(self) -> None:
        """Boot-time hook: emit ``worker.subscriber_audit`` once per
        process so the strict-mode flip checklist can verify the
        registered handler inventory from the audit table instead of
        a manual ``grep``.

        Issue #502 (B2 boot-time inventory). Implementation in
        :mod:`apps.workers.subscriber_audit` — defensive try/except so
        a missing audit table during ``manage.py migrate`` doesn't
        crash boot, and skip-on-management-command guard so common
        Django commands stay quiet.

        Runs AFTER ``apps.channels.ready()`` per ``LOCAL_APPS`` order
        in ``config/settings/base.py``, so MaxHandler (the only
        production TenantAwareTask subclass today) is already
        registered by the time we read the registry.

        DRF-1153: under ASGI (``uvicorn config.asgi:application``) this
        method runs inside a running event loop, where the ORM refuses
        synchronous queries. ``emit_subscriber_audit()`` detects that
        and moves its INSERT to a worker thread — this call site stays
        a plain synchronous call, and a failure here still cannot crash
        boot.
        """

        # Defensive lazy import so import-cycles can't break collection.
        from apps.workers import subscriber_audit

        subscriber_audit.emit_subscriber_audit()
