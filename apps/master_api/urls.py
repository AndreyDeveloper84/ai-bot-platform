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
    # M4 alias — same view, post-onboarding edit URL. Idempotent +
    # last-write-wins; audit event slug still reads MASTER_PROFILE_INITIALIZED
    # until the dedicated MASTER_PROFILE_UPDATED slug ships in a follow-up
    # backend cleanup ticket (Option B per the M4 frontend PR body).
    path("profile", views.onboarding_profile, name="profile"),
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
    # M6 conversation detail backend (master-mobile §M6, PR M6.1)
    path(
        "conversations/<uuid:conversation_id>",
        views.conversation_detail,
        name="conversation_detail",
    ),
    path(
        "conversations/<uuid:conversation_id>/messages",
        views.conversation_send_message,
        name="conversation_send_message",
    ),
    path(
        "conversations/<uuid:conversation_id>/mark-read",
        views.conversation_mark_read,
        name="conversation_mark_read",
    ),
    path(
        "conversations/<uuid:conversation_id>/promote",
        views.conversation_promote,
        name="conversation_promote",
    ),
    # M6 AI drafts (master-mobile §M6, Bundle B / item 4 backend)
    path(
        "conversations/<uuid:conversation_id>/drafts/generate",
        views.conversation_draft_generate,
        name="conversation_draft_generate",
    ),
    path(
        "conversations/<uuid:conversation_id>/drafts/<uuid:draft_id>/send-as-me",
        views.conversation_draft_send_as_me,
        name="conversation_draft_send_as_me",
    ),
    path(
        "conversations/<uuid:conversation_id>/drafts/<uuid:draft_id>/release-to-ai",
        views.conversation_draft_release_to_ai,
        name="conversation_draft_release_to_ai",
    ),
    # M7 notification preferences (master-mobile §M7, Bundle B / item 3)
    path(
        "notification-prefs/",
        views.notification_prefs,
        name="notification_prefs",
    ),
    # Tier 2 Phase 1 read-only roster + catalog (master-solo-surface §4.3 + §4.4)
    path("customers", views.customers_list, name="customers_list"),
    path("catalog", views.catalog_list, name="catalog_list"),
    # Billing status (C2) + payout preview (C3) proxies (pilot 2026-08-15)
    path("billing/status", views.billing_status, name="billing_status"),
    path("billing/card-setup", views.billing_card_setup, name="billing_card_setup"),
    path("payout-preview", views.payout_preview, name="payout_preview"),
]
