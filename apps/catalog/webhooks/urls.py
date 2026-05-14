"""URL routes for the catalog webhook (Sprint 10 / C3 / DRF-879)."""

from __future__ import annotations

from django.urls import path

from apps.catalog.webhooks.views import catalog_webhook

app_name = "catalog_webhooks"

urlpatterns = [
    path("webhook/", catalog_webhook, name="webhook"),
]
