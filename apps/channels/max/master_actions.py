"""What a master's own buttons do in the salon bot (DRF-2785, owner's variant «в»).

The master acts on THEIR OWN appointment through the catalog's specialist
endpoints (``AylaBookingHTTPClient.act_as_specialist``):

* «✅ Подтверждаю» on «У вас новая запись» — ``acknowledge``; the booking's
  status does not change;
* «❌ Не смогу» — ``cancel``, after a confirmation: it cannot be undone and
  the client gets the money back in full;
* «Да, состоялся» / «Не пришёл» under the master's own day — ``complete`` /
  ``no-show``.

### The version

Every action but ``cancel`` needs ``expected_version``. The bot sends the
last version it knows — ``RemoteBookingProxy.appointment_version``, raised
by the catalog's events — and 1 when none came (a booking older than the
version on the wire). If the record moved since, the catalog answers 409
``STALE_VERSION`` with the current version and time in ``details``. The
master is then SHOWN the new time and asked again with the new version.
Never retried silently: that would be confirming a time the master never saw.

Texts — staff register («вы»); approved by the main window on 05.10.2026.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from apps.channels.max.staff_menu import (
    CB_ACK_PREFIX,
    CB_CANT_OK_PREFIX,
    CB_CANT_PREFIX,
    CB_DAY,
    CB_MDONE_PREFIX,
    CB_MNOSHOW_PREFIX,
)
from apps.tenancy.timezones import salon_zone

logger = logging.getLogger(__name__)

LABEL_ACK = "✅ Подтверждаю"
LABEL_CANT = "❌ Не смогу"
LABEL_CANCEL_YES = "Да, отменить"
LABEL_CANCEL_NO = "Нет"

ACK_DONE = "Запись подтверждена."
CANCEL_QUESTION = "Отменить запись {when}? Клиенту вернём оплату полностью."
CANCEL_DONE = "Запись отменена. Клиент получит уведомление."
TIME_CHANGED = "Время записи изменилось: теперь {when}."
ALREADY_CLOSED = "Эта запись уже отменена или закрыта."
NOT_FOUND = "Запись не найдена."
FAILED = "Не получилось выполнить действие. Попробуйте ещё раз чуть позже."

#: Codes that mean «the record is no longer open for this».
_CLOSED_CODES = frozenset({"INVALID_STATUS", "APPOINTMENT_TERMINAL"})


@dataclass(frozen=True)
class Reply:
    """What to answer: the text and the rows of buttons under it."""

    text: str
    rows: list[list[dict[str, str]]] = field(default_factory=list)


def _ref(appointment_id: str, version: int | None) -> str:
    return f"{appointment_id}:{version or 0}"


def parse_ref(ref: str) -> tuple[str, int | None]:
    """``<appointment_id>:<version>`` → id and version (0 / garbage → None)."""

    appointment_id, _, raw = str(ref).rpartition(":")
    try:
        version = int(raw)
    except ValueError:
        version = 0
    return appointment_id, (version if version >= 1 else None)


def ack_rows(appointment_id: str, version: int | None) -> list[list[dict[str, str]]]:
    """«✅ Подтверждаю» | «❌ Не смогу» — under the new-booking notice."""

    ref = _ref(appointment_id, version)
    return [
        [
            {"label": LABEL_ACK, "callback": f"{CB_ACK_PREFIX}{ref}"},
            {"label": LABEL_CANT, "callback": f"{CB_CANT_PREFIX}{ref}"},
        ]
    ]


def _settle_rows(appointment_id: str, version: int | None) -> list[list[dict[str, str]]]:
    from apps.channels.max import staff_actions

    ref = _ref(appointment_id, version)
    return [
        [
            {"label": staff_actions.LABEL_VISIT_DONE, "callback": f"{CB_MDONE_PREFIX}{ref}"},
            {"label": staff_actions.LABEL_VISIT_NO_SHOW, "callback": f"{CB_MNOSHOW_PREFIX}{ref}"},
        ],
        [{"label": staff_actions.LABEL_VISIT_LATER, "callback": CB_DAY}],
    ]


def _cancel_rows(appointment_id: str, version: int | None) -> list[list[dict[str, str]]]:
    ref = _ref(appointment_id, version)
    return [
        [
            {"label": LABEL_CANCEL_YES, "callback": f"{CB_CANT_OK_PREFIX}{ref}"},
            {"label": LABEL_CANCEL_NO, "callback": CB_DAY},
        ]
    ]


def known_version(tenant: Any, appointment_id: str) -> int | None:
    """The last version the mirror learned from the catalog's events."""

    from apps.admin_api.services.visit_settle import own_booking

    proxy = own_booking(tenant.id, appointment_id)
    return getattr(proxy, "appointment_version", None) if proxy is not None else None


def _when(tenant: Any, value: Any) -> str:
    """«дд.мм в чч:мм» in the salon's zone; «—» when unreadable."""

    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return "—"
    if moment.tzinfo is None:
        return "—"
    return moment.astimezone(salon_zone(tenant)).strftime("%d.%m в %H:%M")


def _proxy_when(tenant: Any, appointment_id: str) -> str:
    from apps.admin_api.services.visit_settle import own_booking

    proxy = own_booking(tenant.id, appointment_id)
    return _when(tenant, proxy.start_at) if proxy is not None else "—"


def visit_question(*, tenant: Any, appointment_id: str) -> Reply:
    """«Визит состоялся?» for the master's own visit, from the mirror."""

    from apps.channels.max import staff_actions

    from apps.admin_api.services.visit_settle import own_booking
    from apps.master_api.services.visit_source import _to_rows

    proxy = own_booking(tenant.id, appointment_id)
    if proxy is None:
        return Reply(NOT_FOUND, [[{"label": staff_actions.LABEL_VISIT_LATER, "callback": CB_DAY}]])
    row = _to_rows([proxy], tenant.id)[0]
    line = f"{row.client_name} · {_when(tenant, proxy.start_at)}"
    if row.service_name:
        line += f" · {row.service_name}"
    text = f"*{staff_actions.VISIT_QUESTION}*\n{line}"
    return Reply(text, _settle_rows(appointment_id, known_version(tenant, appointment_id)))


def cancel_question(*, tenant: Any, appointment_id: str, version: int | None) -> Reply:
    """The confirmation before «Не смогу» — the cancellation cannot be undone."""

    return Reply(
        CANCEL_QUESTION.format(when=_proxy_when(tenant, appointment_id)),
        _cancel_rows(appointment_id, version),
    )


def act(*, tenant: Any, bot_user: Any, master: Any, action: str, ref: str) -> Reply:
    """Run one of the master's actions. Never raises.

    ``action`` — ``acknowledge`` / ``cancel`` / ``complete`` / ``no-show``.
    """

    from apps.catalog.specialist_ref import CatalogSpecialistUnresolved, catalog_specialist_id
    from apps.channels.max import staff_actions
    from apps.integrations.ayla.booking_client import (
        BookingBadRequestError,
        get_ayla_booking_client,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    appointment_id, version = parse_ref(ref)
    if not appointment_id:
        return Reply(NOT_FOUND)
    if version is None and action != "cancel":
        # The notice was built before any version was known: send what the
        # mirror knows now, else 1 (a booking never moved). A wrong guess is
        # a 409 with the real time, shown to the master — never a blind write.
        version = known_version(tenant, appointment_id) or 1

    try:
        get_ayla_booking_client().act_as_specialist(
            external_user_id=external_user_id_for(bot_user),
            # The resolver right in the call (DRF-1933 guard): the mirror's PK
            # is not the catalog's id for solo and merged masters.
            specialist_id=catalog_specialist_id(master),
            appointment_id=appointment_id,
            action=action,
            expected_version=version,
        )
    except CatalogSpecialistUnresolved:
        logger.warning("master_actions.no_specialist_id master=%s", getattr(master, "pk", None))
        return Reply(FAILED)
    except BookingBadRequestError as exc:
        return _refusal(tenant, appointment_id, action, exc)
    except Exception:  # noqa: BLE001 — a tap must not raise
        logger.warning(
            "master_actions.failed action=%s appointment=%s",
            action,
            appointment_id,
            exc_info=True,
        )
        return Reply(FAILED)

    if action == "acknowledge":
        return Reply(ACK_DONE)
    if action == "cancel":
        return Reply(CANCEL_DONE)
    write = "complete_appointment" if action == "complete" else "mark_no_show"
    return Reply(staff_actions.SETTLE_REPLY[write]["committed"])


def _refusal(tenant: Any, appointment_id: str, action: str, exc: Any) -> Reply:
    code = getattr(exc, "code", None) or ""
    status = getattr(exc, "status_code", None)
    if code == "STALE_VERSION":
        details = getattr(exc, "details", None) or {}
        current = details.get("current_version")
        current = current if isinstance(current, int) and not isinstance(current, bool) else None
        text = TIME_CHANGED.format(when=_when(tenant, details.get("start_at")))
        # Asked again, with the version that describes the time just shown.
        if action == "acknowledge":
            return Reply(text, ack_rows(appointment_id, current))
        if action == "cancel":
            return Reply(
                f"{text}\n\n{CANCEL_QUESTION.format(when=_when(tenant, details.get('start_at')))}",
                _cancel_rows(appointment_id, current),
            )
        return Reply(text, _settle_rows(appointment_id, current))
    if code in _CLOSED_CODES:
        return Reply(ALREADY_CLOSED)
    if status in (403, 404):
        return Reply(NOT_FOUND)
    logger.warning(
        "master_actions.refused action=%s appointment=%s status=%s code=%s",
        action,
        appointment_id,
        status,
        code,
    )
    return Reply(FAILED)


__all__ = [
    "ACK_DONE",
    "ALREADY_CLOSED",
    "CANCEL_DONE",
    "CANCEL_QUESTION",
    "FAILED",
    "LABEL_ACK",
    "LABEL_CANCEL_NO",
    "LABEL_CANCEL_YES",
    "LABEL_CANT",
    "NOT_FOUND",
    "Reply",
    "TIME_CHANGED",
    "ack_rows",
    "act",
    "cancel_question",
    "known_version",
    "parse_ref",
    "visit_question",
]
