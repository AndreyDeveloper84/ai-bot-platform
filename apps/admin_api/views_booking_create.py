"""Admin REST view — book a customer in (UX contract §18 commit boundary).

``POST /api/v1/admin/bookings/``

Thin shell over :class:`apps.integrations.ayla.salon_client.AylaSalonClient`.
Ayla owns booking state (ADR-0009 rule 5); nothing here writes a booking row.

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
from apps.admin_api.views import _get_master_or_404
from apps.catalog.models import CatalogService
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

    service = CatalogService.objects.filter(tenant_id=tenant.id, id=service_id).first()
    if service is None:
        return _error("not_found", "service not found", 404)
    if not service.ayla_service_id:
        return _error(
            "service_not_bookable",
            "this service is not linked to the booking system yet",
            409,
        )

    # A caller that repeats a submission MUST repeat its key, or the retry
    # becomes a second booking (Ayla invents a key when the header is absent).
    # Generating one here when the client omits it keeps a single attempt
    # correct; the retry affordance depends on the client sending it back.
    if not idempotency_key:
        idempotency_key = str(uuid.uuid4())

    from apps.integrations.ayla.salon_client import (
        SalonAPIError,
        SalonForbidden,
        SalonNotConfigured,
        SalonNotFound,
        SalonSlotTaken,
        SalonUnauthorized,
        SalonUnavailable,
        SalonValidationError,
        get_salon_client,
    )

    actor = external_user_id_for(bot_user)

    try:
        created = get_salon_client().create_appointment(
            actor_external_id=actor,
            idempotency_key=idempotency_key,
            specialist_id=str(master.id),
            service_id=str(service.ayla_service_id),
            start_datetime=start_at,
            client_id=client_id,
            client_name=client_name,
            client_phone=client_phone,
        )
    except SalonNotConfigured as exc:
        logger.error("admin_api.create_booking.not_configured err=%s", exc)
        return _outcome(
            "blocked",
            "booking is not configured on this deployment",
            503,
        )
    except SalonUnauthorized as exc:
        # Our credential, not this person's rights. Loud in the log because
        # only an operator can fix it, and neutral on screen because the
        # administrator did nothing wrong and can do nothing about it.
        logger.error(
            "admin_api.create_booking.upstream_unauthorized tenant=%s err=%s",
            tenant.id,
            exc,
        )
        return _outcome(
            "blocked",
            "запись сейчас недоступна — обратитесь к поддержке",
            503,
        )
    except SalonSlotTaken as exc:
        return _outcome("conflict", str(exc), 409)
    except SalonForbidden as exc:
        logger.warning(
            "admin_api.create_booking.forbidden actor=%s tenant=%s err=%s",
            actor,
            tenant.id,
            exc,
        )
        return _outcome("blocked", str(exc), 403)
    except SalonNotFound as exc:
        # Ayla knows the specialist or the customer is not this salon's. For
        # a customer that is an invitation to book them as a new guest, which
        # is what the screen offers — so it is a conflict, not a dead end.
        return _outcome("conflict", str(exc), 404)
    except SalonValidationError as exc:
        return _outcome("blocked", str(exc), 400)
    except SalonUnavailable as exc:
        # The write may have landed. Never call this a failure.
        logger.warning("admin_api.create_booking.unknown actor=%s err=%s", actor, exc)
        return _outcome(
            "pending",
            "the schedule did not answer — refresh the day before trying again",
            504,
            idempotency_key=idempotency_key,
        )
    except SalonAPIError as exc:
        logger.warning("admin_api.create_booking.error actor=%s err=%s", actor, exc)
        return _outcome("failed", str(exc), 502)

    appointment_id = str(created.get("id") or "") if isinstance(created, dict) else ""
    logger.info(
        "admin_api.create_booking.committed appointment=%s actor=%s tenant=%s",
        appointment_id,
        actor,
        tenant.id,
    )
    return _outcome("committed", "appointment created", 201, appointment_id=appointment_id)


__all__ = ["create_booking"]
