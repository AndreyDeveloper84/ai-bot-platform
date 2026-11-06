"""Read a visit's canonical version, and settle the visit — one place for both doors.

The admin Mini App (``views_booking_complete``) and the salon bot's chat
(``apps.channels.max.staff_actions``, DRF-2784) close a visit or mark a
no-show through the same Ayla write. Before this module the whole mapping
of Ayla's answers lived inside the view; a second door would have meant a
second copy, and the two would drift into telling the operator different
things about the same situation.

The rule both doors keep is the one in ``views_booking_complete``'s module
docstring: the version travels **through the operator**. :func:`read_version`
is the read the operator is shown; :func:`settle_visit` takes back the value
they saw. Neither function reads the version on the operator's behalf inside
a write — that would make the guard unfireable by construction (DRF-1232).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from apps.booking.models import RemoteBookingProxy
from apps.integrations.ayla.user_proxy import external_user_id_for

logger = logging.getLogger(__name__)

#: The two writes, by salon-client method name.
COMPLETE = "complete_appointment"
NO_SHOW = "mark_no_show"

#: What each visit-settling write says in its refusals. One mapping of
#: Ayla's answers for both, so «не пришёл» and «состоялся» can never drift
#: into telling the operator different things about the same situation.
SETTLE_COPY = {
    COMPLETE: {
        "log": "complete_booking",
        "not_configured": "закрытие визита не настроено",
        "unauthorized": "закрытие сейчас недоступно — обратитесь к поддержке",
        "committed": "visit closed",
    },
    NO_SHOW: {
        "log": "no_show_booking",
        "not_configured": "отметка неявки не настроена",
        "unauthorized": "отметка неявки сейчас недоступна — обратитесь к поддержке",
        "committed": "visit marked no-show",
    },
}


@dataclass(frozen=True)
class Settled:
    """How a settle attempt ended. ``outcome`` is the envelope word.

    ``committed`` / ``conflict`` / ``blocked`` / ``pending`` / ``failed`` —
    the five the screen already switches on — plus ``not_found``, which the
    view answers as an error rather than an outcome (the id is not this
    salon's, and the shape of the refusal must not say otherwise).
    """

    outcome: str
    detail: str
    status: int
    hint: str | None = None


@dataclass(frozen=True)
class VisitVersion:
    """The canonical facts about one booking, as Ayla answered them."""

    id: str
    version: int
    status: str
    start_datetime: Any


@dataclass(frozen=True)
class VersionUnavailable:
    """No version was read. ``slug`` is ``not_found`` or ``unavailable``."""

    slug: str
    detail: str
    status: int
    hint: str | None = None


def own_booking(tenant_id, appointment_id) -> RemoteBookingProxy | None:
    """This salon's booking, or None.

    Checked against the mirror before anything is forwarded, so an id
    belonging to another salon cannot be confirmed as existing by the
    shape of the refusal.
    """

    from django.core.exceptions import ValidationError

    try:
        return RemoteBookingProxy.objects.filter(
            tenant_id=tenant_id, appointment_id=appointment_id
        ).first()
    except (ValidationError, ValueError, TypeError):
        # Not a UUID at all — the same answer as an id of another salon.
        # A chat button's ref arrives as text from the wire.
        return None


def read_version(*, tenant: Any, bot_user: Any, appointment_id: str):
    """The canonical version of one of this salon's bookings, straight from Ayla.

    Returns :class:`VisitVersion` or :class:`VersionUnavailable`; never
    raises for an upstream refusal. No version means no action: the caller
    must not offer a button it would have to aim blind.
    """

    if own_booking(tenant.id, appointment_id) is None:
        return VersionUnavailable("not_found", "booking not found", 404)

    from apps.integrations.ayla.booking_client import (
        BookingAPIError,
        BookingUnavailableError,
        get_ayla_booking_client,
    )

    actor = external_user_id_for(bot_user)

    try:
        record = get_ayla_booking_client().get_appointment_version(
            external_user_id=actor,
            booking_id=str(appointment_id),
        )
    except BookingUnavailableError as exc:
        logger.warning("admin_api.booking_version.unavailable err=%s", exc)
        return VersionUnavailable(
            "unavailable",
            "booking version unavailable upstream",
            503,
            hint="расписание не ответило — попробуйте ещё раз",
        )
    except BookingAPIError as exc:
        logger.warning("admin_api.booking_version.error err=%s", exc)
        return VersionUnavailable(
            "unavailable", "booking version read failed", 503, hint="не удалось прочитать запись"
        )

    return VisitVersion(
        id=record.id,
        version=record.version,
        status=record.status,
        start_datetime=record.start_datetime,
    )


def settle_visit(
    *,
    tenant: Any,
    bot_user: Any,
    appointment_id: str,
    expected_version: int,
    write: str,
) -> Settled:
    """Settle a visit through Ayla's state machine on behalf of a salon employee.

    ``write`` is the salon-client method: :data:`COMPLETE` or :data:`NO_SHOW`.
    The bot never sets a status itself — Ayla re-checks the transition on the
    locked row and the mirror follows its event.

    ``expected_version`` must be the value the operator was shown. It is
    validated here as well as by the caller: a door that forgot to check
    must still not reach the network with a guess.
    """

    copy = SETTLE_COPY[write]
    log = copy["log"]

    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        return Settled("bad_request", "expected_version is required", 400)
    if expected_version < 1:
        return Settled("bad_request", "expected_version must be positive", 400)

    if own_booking(tenant.id, appointment_id) is None:
        return Settled("not_found", "booking not found", 404)

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

    actor = external_user_id_for(bot_user)

    try:
        getattr(get_salon_client(), write)(
            actor_external_id=actor,
            tenant_slug=tenant.slug,
            appointment_id=str(appointment_id),
            expected_version=expected_version,
        )
    except SalonValidationError as exc:
        return Settled("blocked", str(exc), 400)
    except SalonNotConfigured as exc:
        logger.error("admin_api.%s.not_configured err=%s", log, exc)
        return Settled("blocked", "not configured for this write", 503, copy["not_configured"])
    except SalonUnauthorized as exc:
        logger.error(
            "admin_api.%s.upstream_unauthorized tenant=%s err=%s",
            log,
            tenant.id,
            exc,
        )
        return Settled("blocked", "unauthorized for this write", 503, copy["unauthorized"])
    except SalonForbidden as exc:
        logger.warning(
            "admin_api.%s.forbidden actor=%s tenant=%s err=%s",
            log,
            actor,
            tenant.id,
            exc,
        )
        return Settled("blocked", str(exc), 403)
    except SalonStaleVersion:
        # The guard fired: the booking changed after the operator looked.
        # Not an error on their part — send them back to a fresh read.
        return Settled(
            "conflict",
            "version conflict: booking changed since it was read",
            409,
            "запись изменилась — обновите день и попробуйте снова",
        )
    except SalonNotAllowed as exc:
        # Cancelled, or already settled. Settled, not contended.
        return Settled("blocked", str(exc), 409)
    except SalonSlotTaken as exc:
        return Settled("conflict", str(exc), 409)
    except SalonNotFound as exc:
        logger.warning(
            "admin_api.%s.mirror_divergence appointment=%s err=%s",
            log,
            appointment_id,
            exc,
        )
        return Settled(
            "conflict",
            "mirror diverged: appointment missing upstream",
            409,
            "запись не найдена в расписании — обновите день",
        )
    except SalonUnavailable as exc:
        # May have been applied. Never a failure — a second press on an
        # already-settled visit is refused, but the operator should be
        # told to look rather than to retry blindly.
        logger.warning("admin_api.%s.unknown actor=%s err=%s", log, actor, exc)
        return Settled(
            "pending",
            "salon did not answer; the write may already have applied",
            504,
            "расписание не ответило — обновите день, прежде чем повторять",
        )
    except SalonAPIError as exc:
        logger.warning("admin_api.%s.error actor=%s err=%s", log, actor, exc)
        return Settled("failed", str(exc), 502)

    logger.info(
        "admin_api.%s.committed appointment=%s actor=%s tenant=%s",
        log,
        appointment_id,
        actor,
        tenant.id,
    )
    return Settled("committed", copy["committed"], 200)


__all__ = [
    "COMPLETE",
    "NO_SHOW",
    "SETTLE_COPY",
    "Settled",
    "VersionUnavailable",
    "VisitVersion",
    "own_booking",
    "read_version",
    "settle_visit",
]
