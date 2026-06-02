"""URL routes for the Customer Mini App API (Phase 0b).

Mounted under ``/api/v1/customer/`` from :mod:`config.urls`.
"""

from __future__ import annotations

from django.urls import path

from apps.miniapp_api import views

app_name = "miniapp_api"

urlpatterns = [
    path("auth/verify", views.auth_verify, name="auth_verify"),
    path("slots", views.slots, name="slots"),
    # 4a — catalog read + booking creation
    path("services", views.services_list, name="services_list"),
    path("services/<uuid:service_id>", views.service_detail, name="service_detail"),
    path("masters", views.masters_list, name="masters_list"),
    path("masters/<uuid:master_id>", views.master_detail, name="master_detail"),
    path("bookings", views.create_booking, name="create_booking"),
    # Customer cancel + reschedule (customer-cancellation-reschedule-spec).
    path("bookings/list", views.bookings_list, name="bookings_list"),
    path("bookings/<uuid:booking_id>", views.booking_detail, name="booking_detail"),
    path(
        "bookings/<uuid:booking_id>/cancel",
        views.booking_cancel_request,
        name="booking_cancel_request",
    ),
    path(
        "bookings/<uuid:booking_id>/cancel/confirm",
        views.booking_cancel_confirm,
        name="booking_cancel_confirm",
    ),
    path(
        "bookings/<uuid:booking_id>/cancel/undo",
        views.booking_cancel_undo,
        name="booking_cancel_undo",
    ),
    path(
        "bookings/<uuid:booking_id>/reschedule",
        views.booking_reschedule_request,
        name="booking_reschedule_request",
    ),
    path(
        "bookings/<uuid:booking_id>/reschedule/confirm",
        views.booking_reschedule_confirm,
        name="booking_reschedule_confirm",
    ),
    # Phase 3 / F4 — profile read / update / data deletion
    path("me", views.me, name="me"),
    path("me/delete", views.delete_me, name="delete_me"),
    # Phase 4 / F5 — post-visit feedback
    path(
        "bookings/<uuid:booking_id>/feedback",
        views.submit_feedback,
        name="submit_feedback",
    ),
    # Catalog recommendations — proxy onto Ayla per identity-bridging
    # contract. Unblocks the Mini App's stub→real swap (W1).
    path(
        "recommendations",
        views.customer_recommendations,
        name="customer_recommendations",
    ),
    # Wellness dashboard — composition of Ayla nutrition reads.
    path(
        "wellness/today",
        views.customer_wellness_today,
        name="customer_wellness_today",
    ),
    # Dashboard rollup — next booking + this-week count (bookings-only).
    path(
        "recent-activity",
        views.customer_recent_activity,
        name="customer_recent_activity",
    ),
    # Food scanner (Веха 2). Contract:
    # ``docs/architecture/food-scanner-api-contract.md``.
    path("food/consent", views.customer_food_consent, name="customer_food_consent"),
    path("food/scan", views.customer_food_scan, name="customer_food_scan"),
    path("food/log", views.customer_food_log, name="customer_food_log"),
    path("food/diary", views.customer_food_diary, name="customer_food_diary"),
    path("health-flags", views.customer_health_flags, name="customer_health_flags"),
]
