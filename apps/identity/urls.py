"""URL routes for the unified identity API (PR 1.5 / ADR-0008).

Mounted at ``/api/v1/`` from :mod:`config.urls`. Today carries one
endpoint:

- ``GET /api/v1/me`` — returns the caller's :class:`RoleContext` for the
  Mini App launch screen. See :mod:`apps.identity.views` and ADR-0008.
"""

from __future__ import annotations

from django.urls import path

from apps.identity import views

app_name = "identity"

urlpatterns = [
    path("me", views.me_view, name="me"),
]
