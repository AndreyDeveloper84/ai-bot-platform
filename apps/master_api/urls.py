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
    path("dashboard", views.dashboard, name="dashboard"),
    # M3 schedule self-service (master-mobile §M3, PR Tier1.2)
    path("schedule", views.schedule, name="schedule"),
    path("availability", views.availability_request, name="availability_request"),
    path(
        "availability/pending",
        views.availability_pending,
        name="availability_pending",
    ),
    # M5 conversations list (master-mobile §M5, PR Tier1.3)
    path("conversations", views.conversations_list, name="conversations_list"),
]
