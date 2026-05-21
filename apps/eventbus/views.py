"""HTTP views for the internal events ingest channel.

Phase 0 / #432 scaffold. One view today —
:class:`InternalEventsIngestView` — returns 501 Not Implemented
until Beta #441 (``docs/architecture/event-contract.md``) lands.
The HMAC verifier skeleton lives in
:mod:`apps.eventbus.middleware`; it is intentionally not wired
into settings.MIDDLEWARE yet (a stub that silently 200s is worse
than a stub that loudly 501s).

See ``docs/plans/2026-05-20-phase-0-parallel-agent-runbook.md``
§Sync 4 for the unblocking condition. The dispatch handler that
fans events to consumers (#442-#446) follows the contract doc.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt


logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class InternalEventsIngestView(View):
    """``POST /api/v1/internal/events/ingest/`` — stub returning 501.

    A publisher hitting this endpoint should NOT retry on 501 — it
    means the channel is reserved but the contract is unfinalised.
    The publisher should hold the event until the contract lands and
    the handler is filled.

    The class-based view shape is chosen over a function so the
    follow-up PR can layer per-event-name dispatch methods (``_handle_booking``,
    ``_handle_payment``) without rewriting the call signature, and so
    middleware introspection (e.g. typed test fixtures) can target
    the class.
    """

    http_method_names: ClassVar[list[str]] = ["post"]

    def post(self, request: HttpRequest) -> JsonResponse:
        logger.info(
            "eventbus.ingest.stub_hit content_length=%s",
            request.META.get("CONTENT_LENGTH") or "0",
        )
        return JsonResponse(
            {
                "status": "not_implemented",
                "reason": (
                    "Event ingest channel reserved; handler awaits "
                    "Beta #441 event-contract.md (Phase 0 Sync 4)."
                ),
            },
            status=501,
        )
