"""URL routes for the internal event ingest channel (Phase 0 / #432).

Mounted at ``/api/v1/internal/events/`` from :mod:`config.urls`. One
route today — the ingest stub at ``ingest/``. The per-event-name
dispatch handler that fans events to registered consumers lands with
Beta #441 (``event-contract.md``) + Gamma #442-#446.
"""

from __future__ import annotations

from django.urls import path

from apps.eventbus.views import InternalEventsIngestView


app_name = "eventbus_internal"

urlpatterns = [
    path("ingest/", InternalEventsIngestView.as_view(), name="ingest"),
]
