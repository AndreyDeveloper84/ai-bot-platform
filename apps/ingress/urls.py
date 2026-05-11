"""Ingress URL routing (DRF-443 / Sprint 2 / D4).

Mounted under `/api/v1/ingress/` from `config/urls.py`. One route per
channel for Sprint 2; Sprint 3+ generalises via a ChannelAdapter
registry that auto-mounts based on `apps/channels/<slug>/` entries.
"""

from __future__ import annotations

from django.urls import path

from apps.ingress import views

app_name = "ingress"

urlpatterns = [
    path("max/", views.max_webhook, name="max_webhook"),
]
