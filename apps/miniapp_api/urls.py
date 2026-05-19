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
    # Phase 2 / 5 — visits + reschedule
    path("visits", views.visits_list, name="visits_list"),
    path(
        "bookings/<uuid:booking_id>/reschedule", views.reschedule_booking, name="reschedule_booking"
    ),
]
