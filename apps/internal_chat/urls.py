"""URL routes for the master↔admin internal chat (PR 6).

Mounted at ``/api/v1/internal-chat/`` from :mod:`config.urls`. Two
parallel sub-namespaces:

* ``/master/...`` — master-init-data gated (handoff §3 surface).
* ``/admin/...`` — admin-role gated (handoff §4 surface).
"""

from __future__ import annotations

from django.urls import path

from apps.internal_chat import views

app_name = "internal_chat"

urlpatterns = [
    # --- master surface ----------------------------------------------------
    path(
        "master/threads/",
        views.master_threads_collection,
        name="master_threads",
    ),
    path(
        "master/threads/<str:thread_id>/",
        views.master_thread_detail,
        name="master_thread_detail",
    ),
    path(
        "master/threads/<str:thread_id>/messages/",
        views.master_send_message,
        name="master_send_message",
    ),
    path(
        "master/threads/<str:thread_id>/mark-read/",
        views.master_mark_read,
        name="master_mark_read",
    ),
    path(
        "master/threads/<str:thread_id>/escalate-to-founder/",
        views.master_escalate_to_founder,
        name="master_escalate_to_founder",
    ),
    # --- admin surface -----------------------------------------------------
    path(
        "admin/threads/",
        views.admin_threads_collection,
        name="admin_threads",
    ),
    path(
        "admin/threads/<str:thread_id>/",
        views.admin_thread_detail,
        name="admin_thread_detail",
    ),
    path(
        "admin/threads/<str:thread_id>/messages/",
        views.admin_send_message,
        name="admin_send_message",
    ),
    path(
        "admin/threads/<str:thread_id>/mark-read/",
        views.admin_mark_read,
        name="admin_mark_read",
    ),
    path(
        "admin/threads/<str:thread_id>/close/",
        views.admin_close_thread,
        name="admin_close_thread",
    ),
]
