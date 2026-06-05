"""URL routes for the YooKassa-retired temporary endpoint — #732.

Single path: ``/webhook/`` under the mount in ``config/urls.py``
(``/api/v1/yookassa/``). The combined URL ``/api/v1/yookassa/webhook/``
is the exact path YooKassa Personal Cabinets had configured pre-flip;
restoring that path with a 410 Gone view tells in-flight retries to
stop without losing the forensic trail.
"""

from __future__ import annotations

from django.urls import path

from apps.integrations.yookassa_retired import views


app_name = "yookassa_retired"

urlpatterns = [
    path("webhook/", views.gone_410, name="webhook"),
]
