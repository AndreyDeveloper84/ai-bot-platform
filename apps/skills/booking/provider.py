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

DEFAULT OFF in config: the flag-ON path (real Ayla REST + ``RemoteBookingProxy``
mirror) is fully implemented and tested here, but production flips the flag in a
separate gated PR. Catalog ids on the Ayla path are UUID strings; the YClients
DTOs are int-typed, so the adapter fills them with strings under flag-ON and the
tools treat ids opaquely (anti-hallucination allow-sets branch on the flag).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from django.conf import settings

from apps.integrations.ayla.booking_client import (
    AylaBookingClient,
    AylaBookingRecord,
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
            # client_id = the Ayla user UUID the bearer + X-External-User-ID
            # resolves to (must match server-side or create 403s). Empty when
            # the BotUser isn't linked to Ayla yet → create fails gracefully.
            client_id=str(getattr(bot_user, "ayla_user_id", "") or ""),
        )

    from apps.integrations.yclients import get_yclients_client

    return get_yclients_client()


class AylaYClientsAdapter:
    """Adapts the Ayla booking client to the YClients-shaped tool interface.

    Constructed per request (binds ``external_user_id`` + ``client_id``).
    Translates DTOs and exceptions in both directions so the eight booking
    tools are unchanged. The canonical Ayla ``appointment_id`` (UUID) rides in
    ``raw["ayla_appointment_id"]``; ``record_id`` / ``UserRecord.id`` are set to
    ``0`` because the tools resolve the handle off the ``RemoteBookingProxy``
    mirror on the flag-ON path, not off an int id.
    """

    def __init__(
        self,
        *,
        client: AylaBookingClient,
        external_user_id: str,
        client_id: str = "",
    ) -> None:
        self._client = client
        self._external_user_id = external_user_id
        self._client_id = client_id

    # ── reads ──────────────────────────────────────────────────────────────

    def get_services(
        self,
        *,
        staff_id: int | str | None = None,
        category_id: int | str | None = None,
    ) -> list[Service]:
        with _translate_errors():
            rows = self._client.get_services(
                specialist_id=str(staff_id) if staff_id is not None else None
            )
        return [_to_yc_service(r) for r in rows]

    def get_staff(self, *, staff_id: int | str | None = None) -> list[Staff]:
        with _translate_errors():
            rows = self._client.get_masters(
                specialist_id=str(staff_id) if staff_id is not None else None
            )
        return [_to_yc_staff(r) for r in rows]

    def get_available_dates(
        self,
        *,
        staff_id: int | str | None = None,
        service_ids: list[int] | list[str] | None = None,
    ) -> list[str]:
        if staff_id is None:
            return []
        with _translate_errors():
            return self._client.get_available_dates(
                specialist_id=str(staff_id),
                service_id=_first_id(service_ids),
            )

    def get_available_times(
        self,
        *,
        staff_id: int | str,
        date: str,
        service_ids: list[int] | list[str] | None = None,
    ) -> list[AvailableTime]:
        with _translate_errors():
            rows = self._client.get_available_times(
                specialist_id=str(staff_id),
                date=date,
                service_id=_first_id(service_ids),
            )
        return [_to_yc_available_time(r) for r in rows]

    # ── writes ─────────────────────────────────────────────────────────────

    def create_record(
        self,
        *,
        staff_id: int | str,
        services: list[int] | list[str],
        datetime: str,
        client_phone: str,
        client_name: str,
        client_email: str = "",
        comment: str | None = None,
        notify_by_sms: int = 0,
        notify_by_email: int = 0,
    ) -> BookingRecord:
        # ``notify_*`` / phone / name are YClients-specific; Ayla owns
        # notifications and resolves the client from client_id + X-External-User-ID.
        if not self._client_id:
            raise YClientsAPIError(
                "ayla_client_id_missing: BotUser has no ayla_user_id — cannot "
                "create on behalf of an Ayla-unlinked user"
            )
        service_id = _first_id(services) or ""
        key = _idempotency_key(self._external_user_id, "create", staff_id, service_id, datetime)
        with _translate_errors():
            record = self._client.create_appointment(
                external_user_id=self._external_user_id,
                client_id=self._client_id,
                specialist_id=str(staff_id),
                service_id=service_id,
                start_datetime=datetime,
                idempotency_key=key,
            )
        return BookingRecord(record_id=0, record_hash="", raw=_mirror_raw(record))

    def cancel_record(self, *, record_id: int | str) -> bool:
        key = _idempotency_key(self._external_user_id, "cancel", record_id)
        with _translate_errors():
            return self._client.cancel_appointment(
                external_user_id=self._external_user_id,
                appointment_id=str(record_id),
                idempotency_key=key,
            )

    def reschedule_record(self, *, record_id: int | str, datetime: str) -> BookingRecord:
        """Native Ayla reschedule — preserves the canonical ``appointment_id``.

        The tools' YClients path reschedules as cancel+create (which would mint
        a new appointment id); the Ayla path moves the same booking so the
        mirror keeps one stable identity.
        """
        key = _idempotency_key(self._external_user_id, "reschedule", record_id, datetime)
        with _translate_errors():
            record = self._client.reschedule_appointment(
                external_user_id=self._external_user_id,
                appointment_id=str(record_id),
                new_start_datetime=datetime,
                idempotency_key=key,
            )
        return BookingRecord(record_id=0, record_hash="", raw=_mirror_raw(record))

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
    # Ayla catalog ids are UUID strings; the YClients ``Service`` DTO is
    # int-typed (anti-touch). On the flag-ON path the tools treat ids opaquely
    # (allow-sets branch on the flag), so the string flows through unchanged.
    return Service(
        id=svc.id,  # type: ignore[arg-type]
        title=svc.title,
        price_min=svc.price_min,
        price_max=svc.price_max,
        duration_s=svc.duration_s,
        category_id=svc.category_id,  # type: ignore[arg-type]
        raw=svc.raw,
    )


def _to_yc_staff(master: AylaMaster) -> Staff:
    return Staff(
        id=master.id,  # type: ignore[arg-type]  # UUID string on the Ayla path
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
        id=0,  # canonical UUID rides in raw; tools resolve via RemoteBookingProxy.
        services=rec.services,
        company={},  # Ayla is single canonical backend; company is YClients-only.
        staff=rec.master,
        date=rec.datetime,
        datetime=rec.datetime,
        seance_length=rec.duration_s,
        raw={**rec.raw, "ayla_appointment_id": rec.appointment_id},
    )


def _first_id(ids: list[int] | list[str] | None) -> str | None:
    """Ayla writes/slots take a single ``service_id``; the tools pass a list."""
    if not ids:
        return None
    first = ids[0]
    return str(first) if first is not None else None


def _mirror_raw(record: AylaBookingRecord) -> dict[str, Any]:
    """Normalise a create/reschedule result into the keys the booking tools
    upsert onto ``RemoteBookingProxy`` (typed UUID columns + schedule window)."""
    raw = dict(record.raw)
    service = raw.get("service") if isinstance(raw.get("service"), dict) else {}
    specialist = raw.get("specialist") if isinstance(raw.get("specialist"), dict) else {}
    return {
        **raw,
        "ayla_appointment_id": record.appointment_id,
        "start_at": raw.get("start_datetime") or raw.get("start_at"),
        "end_at": raw.get("end_datetime") or raw.get("end_at"),
        "service_id": (service or {}).get("id") or raw.get("service_id"),
        "specialist_id": (specialist or {}).get("id") or raw.get("specialist_id"),
        "status": raw.get("status"),
    }


def _idempotency_key(external_user_id: str, op: str, *parts: Any) -> str:
    """Deterministic idempotency key so a retried bot turn can't double-write."""
    seed = "|".join([external_user_id, op, *(str(p) for p in parts)])
    key = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
    # S5-LOW1: a write must NEVER carry an empty idempotency key — that would
    # defeat Ayla's server-side dedup and let a retried bot turn double-book.
    # sha256 hex is always non-empty, so this is a defence-in-depth tripwire
    # against a future refactor of the seed/digest (a real ``raise``, not an
    # ``assert`` that ``python -O`` would strip in prod).
    if not key:
        raise ValueError(f"empty idempotency key for op={op!r} — refusing to write")
    return key
