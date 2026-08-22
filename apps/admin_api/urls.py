"""URL routes for the Admin REST API (PR 2 / MM1-MM3).

Mounted under ``/api/v1/admin/`` from :mod:`config.urls`.

The ``master_detail`` view handles both ``GET`` (read) and ``PATCH``
(edit) on the ``/masters/<id>/`` path — Django's ``@require_http_methods``
decorator does the verb gating inside the view callable.
"""

from __future__ import annotations

from django.urls import path

from apps.admin_api import (
    views,
    views_availability,
    views_availability_slots,
    views_booking_cancel,
    views_booking_complete,
    views_booking_create,
    views_customers,
    views_day,
    views_invite,
    views_staff_invite,
    views_staff_revoke,
    views_master_deactivation,
    views_services_mapping,
)

app_name = "admin_api"

urlpatterns = [
    # Phase 2 — the salon's day. Declared first because it is the screen
    # the front desk opens most often; ordering is cosmetic here (no
    # wildcard can swallow a literal "day" segment at this level).
    path("day/", views_day.salon_day, name="salon_day"),
    # Phase 2 — bookable starts for the manual-booking flow. Wraps Ayla's
    # canonical slots read; see the module docstring for why it refuses
    # rather than returning an empty list on upstream failure.
    # Phase 2 — the commit boundary of manual booking. Ayla owns the
    # booking; this is a shell that names the acting administrator.
    path("bookings/", views_booking_create.create_booking, name="create_booking"),
    # Phase 2 — cancellation. No expected_version: the end state is the
    # same however many times you ask, so Ayla does not gate it on one.
    path(
        "bookings/<uuid:appointment_id>/cancel/",
        views_booking_cancel.cancel_booking,
        name="cancel_booking",
    ),
    # Phase 2 — closing the visit. Two endpoints on purpose: the canonical
    # version is read first and travels back through the operator, because
    # a read folded into the write would make the guard match every time
    # and protect nothing (the DRF-1232 defect, one repo over).
    path(
        "bookings/<uuid:appointment_id>/",
        views_booking_complete.booking_version,
        name="booking_version",
    ),
    path(
        "bookings/<uuid:appointment_id>/complete/",
        views_booking_complete.complete_booking,
        name="complete_booking",
    ),
    path(
        "bookings/<uuid:appointment_id>/reschedule/",
        views_booking_complete.reschedule_booking,
        name="reschedule_booking",
    ),
    # Phase 2 — customer search, the first step of the booking flow (§13).
    # Refuses rather than returning an empty list when it cannot ask, for
    # the same reason as booking-slots: «nothing found» and «could not
    # look» are opposite instructions to the front desk.
    path("customers/", views_customers.search_customers, name="search_customers"),
    path(
        "booking-slots/",
        views_availability_slots.booking_slots,
        name="booking_slots",
    ),
    path("masters/", views.masters_list, name="masters_list"),
    # PR 3 / MM2 — must precede masters/<id>/ so the literal "invite"
    # segment is not consumed as a master_id. Django's path resolver is
    # order-sensitive for ``str`` converters (greedy match).
    path(
        "masters/invite/",
        views_invite.master_invite_create,
        name="master_invite_create",
    ),
    # DRF-1061 block 2.4 — staff access codes (owner/admin/receptionist,
    # plus linking an EXISTING master). Separate from masters/invite/,
    # which creates a catalog master rather than granting access.
    path(
        "staff/invite/",
        views_staff_invite.staff_invite_create,
        name="staff_invite_create",
    ),
    # DRF-1227 — the other half of the invite: taking access back. Also
    # before masters/<id>/ so "staff" is never read as a master id.
    path(
        "staff/revoke/",
        views_staff_revoke.staff_revoke,
        name="staff_revoke",
    ),
    path(
        "masters/<str:master_id>/",
        views.master_detail,
        name="master_detail",
    ),
    path(
        "masters/<str:master_id>/photo/",
        views.master_photo_upload,
        name="master_photo_upload",
    ),
    path(
        "masters/<str:master_id>/audit/",
        views.master_audit_feed,
        name="master_audit_feed",
    ),
    # PR Tier1.1 / MM5 — deactivation cascade.
    path(
        "masters/<str:master_id>/deactivation-preview/",
        views_master_deactivation.master_deactivation_preview,
        name="master_deactivation_preview",
    ),
    path(
        "masters/<str:master_id>/deactivate/",
        views_master_deactivation.master_deactivate,
        name="master_deactivate",
    ),
    path(
        "masters/<str:master_id>/reactivate/",
        views_master_deactivation.master_reactivate,
        name="master_reactivate",
    ),
    # PR 4 / MM4 — services ↔ masters matrix editor. The bulk route is
    # declared BEFORE the bare GET so the literal "bulk" segment can never
    # be misread as a wildcard slug; Django's order-sensitive path matching
    # makes the placement explicit even though there is no wildcard here.
    path(
        "services-mapping/bulk/",
        views_services_mapping.services_mapping_bulk,
        name="services_mapping_bulk",
    ),
    path(
        "services-mapping/",
        views_services_mapping.services_mapping_get,
        name="services_mapping_get",
    ),
    # Bundle B / M3-admin — approve/reject master availability requests.
    # Action paths declared BEFORE the bare list so Django's order-sensitive
    # path resolver can never misread a UUID for a literal action segment
    # (belt + braces; the list path has no <uuid> parameter so collision is
    # theoretical, but the convention matches services-mapping above).
    path(
        "availability-requests/<str:request_id>/approve/",
        views_availability.availability_request_approve,
        name="availability_request_approve",
    ),
    path(
        "availability-requests/<str:request_id>/reject/",
        views_availability.availability_request_reject,
        name="availability_request_reject",
    ),
    path(
        "availability-requests/",
        views_availability.availability_requests_list,
        name="availability_requests_list",
    ),
]
