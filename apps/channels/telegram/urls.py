"""Telegram channel URL routing (Phase 1 / CH1, DRF-848).

Mounted under ``/api/v1/channels/telegram/`` from ``config/urls.py``::

    /api/v1/channels/telegram/webhook/<slug:tenant_slug>/

The single route receives Telegram ``Update`` POSTs. Tenant resolution
happens from the URL slug (NOT a header — Telegram has no equivalent
of MAX's ``X-Tenant`` knob). See :mod:`apps.channels.telegram.webhook`
for the full security contract.
"""

from __future__ import annotations

from django.urls import path

from apps.channels.telegram import webhook

app_name = "telegram"

urlpatterns = [
    path(
        "webhook/<slug:tenant_slug>/",
        webhook.telegram_webhook,
        name="webhook",
    ),
]
