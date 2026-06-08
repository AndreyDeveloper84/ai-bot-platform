"""Booking provider selection — YClients (default) or Ayla REST (flagged).

S1 / wave 2 (#1016, ADR-0009). The booking skill historically talks to
YClients directly. ADR-0009 makes Ayla the canonical owner of booking state,
with bot-platform reading catalog/slots and driving the lifecycle *through*
Ayla. This module is the seam that lets us re-point the skill at Ayla **behind
the ``BOOKING_VIA_AYLA_REST`` feature flag** without rewriting the eight
booking tools.

:func:`get_booking_provider` returns either the YClients client (flag OFF —
unchanged production path) or :class:`AylaYClientsAdapter`, which wraps the
Ayla booking client in the YClients-shaped interface the tools already call
(``get_services`` / ``get_staff`` / ``get_available_dates`` /
``get_available_times`` / ``create_record`` / ``cancel_record`` /
``get_user_records``). The adapter:

* maps Ayla DTOs onto the existing ``yclients.client`` DTOs, so the tools see
  the shapes they expect;
* **translates exceptions** — the tools catch ``YClientsUnavailableError`` /
  ``YClientsAPIError``, so Ayla's ``BookingUnavailableError`` /
  ``BookingAPIError`` MUST be re-raised as those types or the skill crashes;
* binds the caller's ``external_user_id`` for write calls (Ayla needs it to
  bind the action to the consenting client; the YClients interface doesn't
  pass it through per-call).

DEFAULT OFF: the real Ayla client now issues live HTTP calls (contract
LOCKED, #193), but the flag stays OFF until the separate gated cutover PR
flips it and retires the direct-YClients path. Flag ON is exercised via
``FakeAylaBooking`` in tests + the real client's own round-trip tests.

### appointment_id is a UUID

Ayla's canonical ``appointment_id`` is a UUID string (contract §4/§8). The
YClients tool interface is int-keyed (``BookingRecord.record_id`` /
``UserRecord.id``), so the adapter sets those numeric fields to ``0`` and
threads the real UUID through ``raw["ayla_appointment_id"]``. The booking
tools resolve the handle from the ``RemoteBookingProxy`` mirror (UUID PK)
on the flag-ON path, not from the numeric placeholder.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from django.conf import settings

from apps.integrations.ayla.booking_client import (
    AylaBookingClient,
    AylaMaster,
    AylaService,
    AylaSlot,
    AylaUserRecord,
    BookingAPIError,
    BookingUnavailableError,
)
from apps.integrations.yclients.client import (
    AvailableTime,
    BookingRecord,
    Service,
    Staff,
    UserRecord,
    YClientsAPIError,
    YClientsUnavailableError,
)

logger = logging.getLogger(__name__)


def get_booking_provider(*, bot_user: Any) -> Any:
    """Return the booking provider for this request.

    Flag OFF (default): the YClients client, unchanged. Flag ON: an
    :class:`AylaYClientsAdapter` bound to ``bot_user``'s Ayla external id.
    """
    if getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        from apps.integrations.ayla.booking_client import get_ayla_booking_client
        from apps.integrations.ayla.user_proxy import external_user_id_for

        return AylaYClientsAdapter(
            client=get_ayla_booking_client(),
            external_user_id=external_user_id_for(bot_user),
        )

    from apps.integrations.yclients import get_yclients_client

    return get_yclients_client()


class AylaYClientsAdapter:
    """Adapts the Ayla booking client to the YClients-shaped tool interface.

    Constructed per request (binds ``external_user_id``). Translates DTOs and
    exceptions in both directions so the eight booking tools are unchanged.
    """

    def __init__(self, *, client: AylaBookingClient, external_user_id: str) -> None:
        self._client = client
        self._external_user_id = external_user_id

    # ── reads ──────────────────────────────────────────────────────────────

    def get_services(
        self,
        *,
        staff_id: int | None = None,
        category_id: int | None = None,
    ) -> list[Service]:
        with _translate_errors():
            rows = self._client.get_services(master_id=staff_id)
        return [_to_yc_service(r) for r in rows]

    def get_staff(self, *, staff_id: int | None = None) -> list[Staff]:
        with _translate_errors():
            rows = self._client.get_masters(master_id=staff_id)
        return [_to_yc_staff(r) for r in rows]

    def get_available_dates(
        self,
        *,
        staff_id: int | None = None,
        service_ids: list[int] | None = None,
    ) -> list[str]:
        with _translate_errors():
            return self._client.get_available_dates(master_id=staff_id, service_ids=service_ids)

    def get_available_times(
        self,
        *,
        staff_id: int,
        date: str,
        service_ids: list[int] | None = None,
    ) -> list[AvailableTime]:
        with _translate_errors():
            rows = self._client.get_available_times(
                master_id=staff_id, date=date, service_ids=service_ids
            )
        return [_to_yc_available_time(r) for r in rows]

    # ── writes ─────────────────────────────────────────────────────────────

    def create_record(
        self,
        *,
        staff_id: int,
        services: list[int],
        datetime: str,
        client_phone: str,
        client_name: str,
        client_email: str = "",
        comment: str | None = None,
        notify_by_sms: int = 0,
        notify_by_email: int = 0,
    ) -> BookingRecord:
        # ``notify_*`` are YClients-specific; Ayla owns notifications.
        key = _idempotency_key(self._external_user_id, "create", staff_id, services, datetime)
        with _translate_errors():
            record = self._client.create_appointment(
                external_user_id=self._external_user_id,
                master_id=staff_id,
                service_ids=services,
                datetime=datetime,
                client_phone=client_phone,
                client_name=client_name,
                comment=comment,
                idempotency_key=key,
            )
        return BookingRecord(
            # Ayla ``appointment_id`` is a UUID — there is no int record id.
            # The numeric field is a placeholder; the real handle rides in
            # ``raw["ayla_appointment_id"]`` and the proxy mirror (UUID PK)
            # is the authoritative resolver on the flag-ON path.
            record_id=0,
            record_hash=str(record.raw.get("record_hash") or ""),
            raw={**record.raw, "ayla_appointment_id": record.appointment_id},
        )

    def cancel_record(self, *, record_id: int | str) -> bool:
        # ``record_id`` is the Ayla UUID (str) on the flag-ON path; accept int
        # too for signature compatibility with the YClients client the tools
        # type against. Stringify before the REST call.
        key = _idempotency_key(self._external_user_id, "cancel", record_id)
        with _translate_errors():
            return self._client.cancel_appointment(
                external_user_id=self._external_user_id,
                appointment_id=str(record_id),
                idempotency_key=key,
            )

    def reschedule_record(
        self,
        *,
        record_id: str,
        datetime: str,
    ) -> BookingRecord:
        """Native reschedule — preserves the same Ayla ``appointment_id``.

        Unlike the YClients cancel-and-recreate, Ayla owns a native reschedule
        endpoint, so the appointment keeps its UUID. The tools that drive this
        on the flag-ON path therefore never cancel+create.
        """
        key = _idempotency_key(self._external_user_id, "reschedule", record_id, datetime)
        with _translate_errors():
            record = self._client.reschedule_appointment(
                external_user_id=self._external_user_id,
                appointment_id=str(record_id),
                datetime=datetime,
                idempotency_key=key,
            )
        return BookingRecord(
            record_id=0,
            record_hash=str(record.raw.get("record_hash") or ""),
            raw={**record.raw, "ayla_appointment_id": record.appointment_id},
        )

    def get_user_records(self) -> list[UserRecord]:
        with _translate_errors():
            rows = self._client.get_user_appointments(external_user_id=self._external_user_id)
        return [_to_yc_user_record(r) for r in rows]


# ─── error translation ───────────────────────────────────────────────────────


class _translate_errors:
    """Context manager: Ayla booking errors → YClients errors.

    The booking tools only catch ``YClients*`` exceptions. ``NotImplementedError``
    (the skeleton guard) is deliberately NOT translated — it must surface loudly
    if the flag is flipped ON before the real client lands.
    """

    def __enter__(self) -> _translate_errors:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        # Always returns None → never swallows. Either re-raises the
        # translated YClients error, or lets the original propagate.
        if exc_type is None:
            return
        if issubclass(exc_type, BookingUnavailableError):
            raise YClientsUnavailableError(str(exc)) from exc
        if issubclass(exc_type, BookingAPIError):
            raise YClientsAPIError(str(exc)) from exc


# ─── DTO mappers ─────────────────────────────────────────────────────────────


def _to_yc_service(svc: AylaService) -> Service:
    return Service(
        id=svc.id,
        title=svc.title,
        price_min=svc.price_min,
        price_max=svc.price_max,
        duration_s=svc.duration_s,
        category_id=svc.category_id,
        raw=svc.raw,
    )


def _to_yc_staff(master: AylaMaster) -> Staff:
    return Staff(
        id=master.id,
        name=master.name,
        specialization=master.specialization,
        rating=master.rating,
        avatar="",  # Ayla doesn't expose an avatar; not used by the tools.
        position=master.position,
        raw=master.raw,
    )


def _to_yc_available_time(slot: AylaSlot) -> AvailableTime:
    return AvailableTime(
        time=slot.time,
        datetime=slot.datetime,
        seance_length_s=slot.duration_s,
    )


def _to_yc_user_record(rec: AylaUserRecord) -> UserRecord:
    return UserRecord(
        # ``id`` is the YClients int handle — Ayla has no int id, so it's a
        # placeholder. The UUID rides in ``raw["ayla_appointment_id"]`` and
        # the tools match on it (not on this numeric field) under flag ON.
        id=0,
        services=rec.services,
        company={},  # Ayla is single canonical backend; company is YClients-only.
        staff=rec.master,
        date=rec.datetime,
        datetime=rec.datetime,
        seance_length=rec.duration_s,
        raw={**rec.raw, "ayla_appointment_id": rec.appointment_id},
    )


def _idempotency_key(external_user_id: str, op: str, *parts: Any) -> str:
    """Deterministic idempotency key so a retried bot turn can't double-write."""
    seed = "|".join([external_user_id, op, *(str(p) for p in parts)])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
