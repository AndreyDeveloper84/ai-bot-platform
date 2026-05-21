"""URL routes for the cross-service event ingest channel.

Mounted at ``/api/v1/internal/events/`` from :mod:`config.urls`. One
route — the ingest endpoint. Per-event-name dispatch happens inside
:class:`apps.eventbus.views.InternalEventsIngestView` against the
:mod:`apps.eventbus.ingest_dispatcher` registry; consumer modules
(#442-#446) register their handlers at app-ready time.
"""

from __future__ import annotations

from django.urls import path

from apps.eventbus.views import InternalEventsIngestView


app_name = "eventbus_internal"

urlpatterns = [
    # Trailing slash deliberately OMITTED — matches
    # `docs/architecture/event-contract.md` §6.1 exactly. A 301
    # ``APPEND_SLASH`` redirect would drop the body on POST, breaking
    # HMAC verification (signature is computed over the body).
    path("ingest", InternalEventsIngestView.as_view(), name="ingest"),
]
