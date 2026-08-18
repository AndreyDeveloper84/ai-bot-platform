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
    views_invite,
    views_staff_invite,
    views_master_deactivation,
    views_services_mapping,
)

app_name = "admin_api"

urlpatterns = [
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
