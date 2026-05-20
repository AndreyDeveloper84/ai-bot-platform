"""URL routes for the Admin REST API (PR 2 / MM1-MM3).

Mounted under ``/api/v1/admin/`` from :mod:`config.urls`.

The ``master_detail`` view handles both ``GET`` (read) and ``PATCH``
(edit) on the ``/masters/<id>/`` path — Django's ``@require_http_methods``
decorator does the verb gating inside the view callable.
"""

from __future__ import annotations

from django.urls import path

from apps.admin_api import views, views_invite

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
]
