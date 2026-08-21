"""Admin REST view — cancel a booking (UX contract §19).

``POST /api/v1/admin/bookings/<appointment_id>/cancel/``

Thin shell over :meth:`AylaSalonClient.cancel_appointment`. Ayla owns the
booking; nothing here writes one.

### Why the outcome vocabulary is the same as create's

A cancellation has the same four honest answers as a booking (§18), and
for the same reason: the receptionist's next action differs in each case.

* ``committed`` — it is cancelled;
* ``blocked`` — this booking's own state forbids it (a finished visit
  cannot be un-happened) or this actor may not;
* ``pending`` — **we do not know.** The request may have been applied
  before the connection broke;
* ``conflict`` — somebody got there first.

``pending`` matters more here than anywhere else. A cancellation that is
reported as failed invites the receptionist to press again, and pressing
again on an already-cancelled booking is how a customer gets told twice
that their appointment is off.

### The identifier

The Mini App holds ``RemoteBookingProxy.appointment_id`` — the canonical
Ayla id — so it travels straight through. The proxy row is checked first
all the same, so that a booking of another salon reads as «not found»
rather than being forwarded for Ayla to refuse: the surface must not
confirm which appointment ids exist elsewhere.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.booking.models import RemoteBookingProxy
from apps.identity.models import BotUser
from apps.integrations.ayla.user_proxy import external_user_id_for

logger = logging.getLogger(__name__)

MAX_REASON_LEN = 500


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _outcome(outcome: str, detail: str, status: int, **extra: Any) -> JsonResponse:
    return JsonResponse({"outcome": outcome, "detail": detail, **extra}, status=status)


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def cancel_booking(request: HttpRequest, appointment_id: str) -> HttpResponse:
    """Cancel a booking on behalf of the calling administrator."""

    tenant = request.tenant  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(body, dict):
        return _error("bad_request", "body must be a JSON object", 400)

    reason = str(body.get("reason") or "").strip()
    if len(reason) > MAX_REASON_LEN:
        return _error("bad_request", "reason too long", 400)
    reason_code = str(body.get("reason_code") or "").strip() or None

    from apps.integrations.ayla.salon_client import (
        SalonAPIError,
        SalonForbidden,
        SalonNotAllowed,
        SalonNotConfigured,
        SalonNotFound,
        SalonSlotTaken,
        SalonStaleVersion,
        SalonUnauthorized,
        SalonUnavailable,
        SalonValidationError,
        get_salon_client,
    )

    # This salon's booking, or nothing. Checked against the mirror rather
    # than forwarded blind, so an id belonging to another salon cannot be
    # confirmed as existing by the shape of the refusal.
    proxy = RemoteBookingProxy.objects.filter(
        tenant_id=tenant.id, appointment_id=appointment_id
    ).first()
    if proxy is None:
        return _error("not_found", "booking not found", 404)

    actor = external_user_id_for(bot_user)

    try:
        get_salon_client().cancel_appointment(
            actor_external_id=actor,
            tenant_slug=tenant.slug,
            appointment_id=str(appointment_id),
            reason=reason,
            reason_code=reason_code,
        )
    except SalonValidationError as exc:
        return _outcome("blocked", str(exc), 400)
    except SalonNotConfigured as exc:
        logger.error("admin_api.cancel_booking.not_configured err=%s", exc)
        return _outcome("blocked", "cancellation is not configured", 503)
    except SalonUnauthorized as exc:
        logger.error(
            "admin_api.cancel_booking.upstream_unauthorized tenant=%s err=%s",
            tenant.id,
            exc,
        )
        return _outcome("blocked", "отмена сейчас недоступна — обратитесь к поддержке", 503)
    except SalonForbidden as exc:
        logger.warning(
            "admin_api.cancel_booking.forbidden actor=%s tenant=%s err=%s",
            actor,
            tenant.id,
            exc,
        )
        return _outcome("blocked", str(exc), 403)
    except SalonNotAllowed as exc:
        # The booking's own state, not the actor's rights. No retry will
        # help and no other person can change the answer, so it must not
        # read as a transient failure.
        return _outcome("blocked", str(exc), 409)
    except (SalonStaleVersion, SalonSlotTaken) as exc:
        # Somebody got there first. Refresh the day and look again.
        return _outcome("conflict", str(exc), 409)
    except SalonNotFound as exc:
        # The mirror said this salon has it and Ayla disagrees — a
        # divergence, not a user error. Reported as a conflict so the
        # screen refreshes instead of insisting.
        logger.warning(
            "admin_api.cancel_booking.mirror_divergence appointment=%s err=%s",
            appointment_id,
            exc,
        )
        return _outcome("conflict", "запись не найдена в расписании — обновите день", 409)
    except SalonUnavailable as exc:
        # May well have been applied. Never call this a failure.
        logger.warning("admin_api.cancel_booking.unknown actor=%s err=%s", actor, exc)
        return _outcome(
            "pending",
            "расписание не ответило — обновите день, прежде чем повторять",
            504,
        )
    except SalonAPIError as exc:
        logger.warning("admin_api.cancel_booking.error actor=%s err=%s", actor, exc)
        return _outcome("failed", str(exc), 502)

    logger.info(
        "admin_api.cancel_booking.committed appointment=%s actor=%s tenant=%s code=%s",
        appointment_id,
        actor,
        tenant.id,
        reason_code or "-",
    )
    return _outcome("committed", "booking cancelled", 200, appointment_id=str(appointment_id))


__all__ = ["cancel_booking"]
