"""URL routes for the public marketplace directory API (#249, #250).

Mounted under ``/api/v1/providers/`` from :mod:`config.urls`. Public — no
auth decorators. ``/me/providers`` (#251) is intentionally absent: deferred
pending the TenantUserRelationship model (#246) + a cross-tenant ``/me``
auth mechanism.
"""

from __future__ import annotations

from django.urls import path

from apps.marketplace import views

app_name = "marketplace"

urlpatterns = [
    path("", views.providers_list, name="providers_list"),
    path("<uuid:provider_id>/", views.provider_detail, name="provider_detail"),
]
