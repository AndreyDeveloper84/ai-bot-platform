"""URL routes for the YooKassa integration (DRF-843 / Phase 1 / B7).

Mounted at ``/api/v1/yookassa/`` from :mod:`config.urls`. Single route
today — the notification webhook receiver.
"""

from __future__ import annotations

from django.urls import path

from apps.integrations.yookassa.webhooks import yookassa_webhook

app_name = "yookassa"

urlpatterns = [
    path("webhook/", yookassa_webhook, name="webhook"),
]
