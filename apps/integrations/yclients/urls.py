"""URL routes for the YClients integration (Phase 1 / B2 / DRF-838).

Mounted at ``/api/v1/yclients/`` from :mod:`config.urls`. Currently
hosts the admin-side webhook receiver only; the booking-skill HTTP
surface (B3) lands here in a follow-up PR.
"""

from __future__ import annotations

from django.urls import path

from apps.integrations.yclients.webhooks import yclients_webhook

app_name = "yclients"

urlpatterns = [
    path("webhook/", yclients_webhook, name="webhook"),
]
