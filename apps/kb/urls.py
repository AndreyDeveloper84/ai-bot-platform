"""URL routes exposed by ``apps.kb``.

Currently only the inbound salon-knowledge webhook (Phase 5
convergence). Retrieval is invoked via ``search_kb()`` in-process,
not over HTTP — the bot has no need for an HTTP retrieval endpoint
on this side.
"""

from __future__ import annotations

from django.urls import path

from apps.kb import webhooks

app_name = "salon_knowledge_webhooks"

urlpatterns = [
    # KB-SYNC — Shiro-Py salon-knowledge service POSTs ``knowledge.approved``
    # here. See ``apps/kb/webhooks.py`` module docstring + the architectural
    # decision recorded in ``docs/design/2026-05-18-phase-5-architecture-comparison.md``.
    path("webhook/approved/", webhooks.knowledge_approved, name="approved"),
]
