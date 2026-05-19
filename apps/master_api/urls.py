"""URL routes for the Master Mini App API (PR 1 / M0).

Mounted under ``/api/v1/master/`` from :mod:`config.urls`.
"""

from __future__ import annotations

from django.urls import path

from apps.master_api import views

app_name = "master_api"

urlpatterns = [
    path("onboarding/claim", views.onboarding_claim, name="onboarding_claim"),
    path("onboarding/accept", views.onboarding_accept, name="onboarding_accept"),
    path("onboarding/reject", views.onboarding_reject, name="onboarding_reject"),
    path("onboarding/profile", views.onboarding_profile, name="onboarding_profile"),
    path("me", views.me, name="me"),
]
