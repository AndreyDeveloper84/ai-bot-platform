"""Admin REST view — book a customer in (UX contract §18 commit boundary).

``POST /api/v1/admin/bookings/``

Thin shell over :func:`apps.admin_api.services.booking.create_appointment_as`
(DRF-2154 moved the Ayla call and its §18 outcome mapping there so the
master surface books through the same code). Ayla owns booking state
(ADR-0009 rule 5); nothing here writes a booking row.

### Why the response says «what happened», not «ok / not ok»

§18 fixes four presentation outcomes and they are not interchangeable:

* ``committed`` — authoritative readback, the appointment exists;
* ``conflict`` — the interval went while the draft was open; keep the data,
  send the user back to a fresh selection;
* ``blocked`` — this actor may not do this; explain, do not retry;
* ``pending`` — **we do not know.** «Do not claim creation.»

The last one is the one that matters. A write that times out may well have
landed, so reporting it as a failure invites the receptionist to press again
and book the client twice. It is reported as unknown, and the client is told
to refresh before retrying.

### Attribution

The acting administrator's identity travels as ``X-External-User-ID``, built
from the caller's own ``BotUser``. It is never taken from the request body and
never substituted with the owner's — that substitution is exactly what path Б
was chosen to avoid, and there is a test that pins it.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.admin_api.services.booking import (
    Refusal,
    bookable_service,
    create_appointment_as,
)
from apps.admin_api.views import _get_master_or_404
from apps.identity.models import BotUser
from apps.integrations.ayla.user_proxy import external_user_id_for

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 150
MAX_PHONE_LEN = 20


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _outcome(outcome: str, detail: str, status: int, **extra: Any) -> JsonResponse:
    """Serialise one of the §18 outcomes.

    ``outcome`` travels as its own field rather than being inferred from the
    status code, because the surface branches on it and an HTTP code is a
    lossy stand-in — 409 could mean «slot taken» or «already exists», and the
    screen must react differently.
    """

    return JsonResponse({"outcome": outcome, "detail": detail, **extra}, status=status)


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def create_booking(request: HttpRequest) -> HttpResponse:
    """Create an appointment on behalf of the calling administrator."""

    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    master_id = str(body.get("master_id") or "").strip()
    service_id = str(body.get("service_id") or "").strip()
    start_at = str(body.get("start_at") or "").strip()
    idempotency_key = str(body.get("idempotency_key") or "").strip()

    missing = [
        name
        for name, value in (
            ("master_id", master_id),
            ("service_id", service_id),
            ("start_at", start_at),
        )
        if not value
    ]
    if missing:
        return _error("bad_request", f"required: {', '.join(missing)}", 400)

    client_id = str(body.get("client_id") or "").strip() or None
    client_name = str(body.get("client_name") or "").strip() or None
    client_phone = str(body.get("client_phone") or "").strip() or None

    if bool(client_id) == bool(client_name):
        return _error(
            "bad_request",
            "provide exactly one of client_id or client_name",
            400,
        )
    if client_name:
        if not client_phone:
            return _error("bad_request", "a new guest needs a name and a phone", 400)
        if len(client_name) > MAX_NAME_LEN or len(client_phone) > MAX_PHONE_LEN:
            return _error("bad_request", "name or phone too long", 400)

    # The master and the service must be this salon's. Resolving them locally
    # keeps a body value from ever naming somebody else's specialist, and
    # turns the Ayla ids into something the Mini App never has to hold.
    master = _get_master_or_404(tenant.id, master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    service = bookable_service(tenant.id, service_id)
    if isinstance(service, Refusal):
        return _error(service.slug, service.detail, service.status)

    # A caller that repeats a submission MUST repeat its key, or the retry
    # becomes a second booking (Ayla invents a key when the header is absent).
    # Generating one here when the client omits it keeps a single attempt
    # correct; the retry affordance depends on the client sending it back.
    if not idempotency_key:
        idempotency_key = str(uuid.uuid4())

    result = create_appointment_as(
        actor=external_user_id_for(bot_user),
        tenant=tenant,
        master=master,
        service=service,
        start_at=start_at,
        idempotency_key=idempotency_key,
        client_id=client_id,
        client_name=client_name,
        client_phone=client_phone,
        log="admin_api.create_booking",
        journal=logger,
    )
    return _outcome(result.outcome, result.detail, result.status, **result.extra)


__all__ = ["create_booking"]
