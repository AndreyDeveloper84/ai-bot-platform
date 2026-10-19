"""Canonical master booking-action execution shared by HTTP and Ayla.

DRF-2943 established the specialist write contract. DRF-2945 reuses it
instead of giving the assistant a second mutation engine.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from apps.catalog.models import CatalogMaster
from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    BookingBadRequestError,
    BookingUnavailableError,
    get_ayla_booking_client,
)
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.master_api.services.bookings import own_booking

logger = logging.getLogger(__name__)

MASTER_BOOKING_ACTIONS = frozenset(
    {"acknowledge", "cancel", "complete", "no-show", "reschedule"}
)


@dataclass(frozen=True)
class BookingActionOutcome:
    status_code: int
    payload: dict[str, Any]


def _out(status_code: int, payload: dict[str, Any]) -> BookingActionOutcome:
    return BookingActionOutcome(status_code=status_code, payload=payload)


def execute_master_booking_action(
    *,
    master: CatalogMaster,
    bot_user: BotUser,
    appointment_id: uuid.UUID,
    action: Any,
    expected_version: Any = None,
    reason: Any = None,
    new_start_datetime: Any = None,
) -> BookingActionOutcome:
    """Execute one action against the canonical specialist endpoint.

    The caller may be the HTTP view or a confirmed Ayla action. Validation
    and unknown-result semantics are intentionally shared.
    """

    if own_booking(master, appointment_id) is None:
        return _out(404, {"error": "not_found", "detail": "booking not found"})

    if not isinstance(action, str) or action not in MASTER_BOOKING_ACTIONS:
        return _out(400, {"error": "bad_request", "detail": "unsupported action"})

    if action != "cancel":
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            return _out(
                409,
                {"outcome": "conflict", "reason_code": "version_unknown"},
            )
    elif expected_version is not None and (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 1
    ):
        return _out(
            400,
            {
                "error": "bad_request",
                "detail": "expected_version must be a positive integer",
            },
        )

    if action == "reschedule":
        if not isinstance(new_start_datetime, str) or not new_start_datetime.strip():
            return _out(
                400,
                {"error": "bad_request", "detail": "new_start_datetime is required"},
            )
        new_start_datetime = new_start_datetime.strip()
    else:
        new_start_datetime = None

    if reason is not None and not isinstance(reason, str):
        return _out(400, {"error": "bad_request", "detail": "reason must be a string"})

    try:
        result = get_ayla_booking_client().act_as_specialist(
            external_user_id=external_user_id_for(bot_user),
            specialist_id=catalog_specialist_id(master),
            appointment_id=str(appointment_id),
            action=action,
            expected_version=expected_version,
            reason=(reason.strip() if isinstance(reason, str) and reason.strip() else None),
            new_start_datetime=new_start_datetime,
        )
    except CatalogSpecialistUnresolved:
        logger.warning("master_api.booking_action.no_specialist_id master=%s", master.pk)
        return _out(202, {"outcome": "pending", "reason_code": "result_pending"})
    except BookingUnavailableError:
        logger.warning(
            "master_api.booking_action.unknown_result action=%s appointment=%s",
            action,
            appointment_id,
        )
        return _out(202, {"outcome": "pending", "reason_code": "result_pending"})
    except BookingBadRequestError as exc:
        code = str(getattr(exc, "code", "") or "")
        status = int(getattr(exc, "status_code", 400) or 400)
        if status == 404:
            return _out(404, {"error": "not_found", "detail": "booking not found"})
        if status == 403:
            return _out(403, {"error": "forbidden", "detail": "action unavailable"})
        if code == "STALE_VERSION":
            return _out(409, {"outcome": "conflict", "reason_code": "stale_version"})
        if code in {
            "INVALID_STATUS",
            "APPOINTMENT_TERMINAL",
            "CANCELLATION_NOT_ALLOWED",
        }:
            return _out(409, {"outcome": "conflict", "reason_code": "state_changed"})
        logger.info(
            "master_api.booking_action.refused action=%s appointment=%s status=%s code=%s",
            action,
            appointment_id,
            status,
            code,
        )
        return _out(400, {"outcome": "blocked", "reason_code": "action_unavailable"})

    return _out(
        200,
        {
            "outcome": "committed",
            "appointment_id": result.appointment_id,
            "status": result.status,
            "version": result.version,
            "start_at": result.start_at,
        },
    )


__all__ = [
    "BookingActionOutcome",
    "MASTER_BOOKING_ACTIONS",
    "execute_master_booking_action",
]
