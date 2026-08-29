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
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings

from apps.booking.models import RemoteBookingProxy

from apps.integrations.ayla.booking_client import (
    AylaBookingClient,
    AylaBookingRecord,
    AylaMaster,
    AylaService,
    AylaSlot,
    AylaUserRecord,
    BookingAPIError,
    BookingBadRequestError,
    BookingRateLimitedError,
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
        from apps.identity.services.ayla_link import ensure_ayla_link
        from apps.integrations.ayla.booking_client import get_ayla_booking_client
        from apps.integrations.ayla.user_proxy import external_user_id_for

        # DRF-1035: creating a booking is the archetypal identity-dependent
        # action, so this is where the link gets established if it does not
        # exist yet. Reading the field directly (the pre-DRF-1035 behaviour)
        # was fatal for every user Ayla had never resolved — which, absent a
        # writer for the field, was everyone who was not provisioned by hand.
        #
        # `ensure_ayla_link` never raises: on an Ayla outage it returns None,
        # client_id stays empty, and `create_record` degrades exactly as
        # before (ayla_client_id_missing → AdminTask → operator notified).
        ayla_user_id = ensure_ayla_link(bot_user, trigger="booking")

        return AylaYClientsAdapter(
            client=get_ayla_booking_client(),
            external_user_id=external_user_id_for(bot_user),
            # client_id = the Ayla user UUID the bearer + X-External-User-ID
            # resolves to (must match server-side or create 403s). Empty when
            # identity could not be resolved → create fails gracefully.
            client_id=str(ayla_user_id or ""),
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

    def get_services(self, *, category_id: int | str | None = None) -> list[Service]:
        """The tenant's whole active catalog.

        DRF-1019: the YClients-shaped ``staff_id`` filter is gone from this
        side of the seam. It forwarded to an Ayla-client branch that no
        caller ever reached, and keeping the parameter after removing the
        branch would mean accepting a filter and silently ignoring it. A
        caller that wants one master's services should read the local
        ``catalog.MasterService`` mirror (SQL join, no HTTP); one
        (master, service) pair comes from
        ``AylaBookingClient.get_specialist_service_edges``.

        ``category_id`` is accepted for parity with the YClients client the
        tools were written against and is likewise not honoured on the Ayla
        path — no caller passes it either. Kept, rather than removed, only
        because the tools' call shape is duck-typed across both providers.
        """
        with _translate_errors():
            rows = self._client.get_services()
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
            # #1051: service_id is mandatory on the Ayla slots path. Pass the
            # selected service through ("" when absent → the client raises a
            # clear BookingBadRequestError, not a 14-day 400 cascade).
            return self._client.get_available_dates(
                specialist_id=str(staff_id),
                service_id=_first_id(service_ids) or "",
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
                service_id=_first_id(service_ids) or "",  # #1051: mandatory
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
        payment_required: bool = True,
    ) -> BookingRecord:
        # ``notify_*`` / phone / name are YClients-specific; Ayla owns
        # notifications and resolves the client from client_id + X-External-User-ID.
        if not self._client_id:
            raise YClientsAPIError(
                "ayla_client_id_missing: BotUser has no ayla_user_id — cannot "
                "create on behalf of an Ayla-unlinked user"
            )
        service_id = _first_id(services) or ""
        # AMD-002: payment_required rides the create body AND the idempotency
        # seed — a retry with the same intent dedups; a deliberate intent flip
        # (online pay → no prepay) is a NEW appointment, never swallowed.
        key = _idempotency_key(
            self._external_user_id, "create", staff_id, service_id, datetime, payment_required
        )
        with _translate_errors():
            record = self._client.create_appointment(
                external_user_id=self._external_user_id,
                client_id=self._client_id,
                specialist_id=str(staff_id),
                service_id=service_id,
                start_datetime=datetime,
                idempotency_key=key,
                payment_required=payment_required,
            )
        return BookingRecord(
            record_id=0,
            record_hash="",
            raw=_mirror_raw(
                record,
                requested_service_id=service_id or None,
                requested_specialist_id=str(staff_id) or None,
            ),
        )

    def cancel_record(self, *, record_id: int | str) -> bool:
        key = _idempotency_key(self._external_user_id, "cancel", record_id)
        # DRF-997: pass the proxy's specialist/service/date so the client can
        # invalidate the short-lived slot/dates cache after a successful cancel.
        specialist_id: str | None = None
        service_id: str | None = None
        date: str | None = None
        try:
            proxy = RemoteBookingProxy.all_tenants.get(appointment_id=uuid.UUID(str(record_id)))
            specialist_id = str(proxy.specialist_id) if proxy.specialist_id else None
            service_id = str(proxy.service_id) if proxy.service_id else None
            date = proxy.start_at.date().isoformat() if proxy.start_at else None
        except (RemoteBookingProxy.DoesNotExist, ValueError):
            pass
        with _translate_errors():
            return self._client.cancel_appointment(
                external_user_id=self._external_user_id,
                appointment_id=str(record_id),
                idempotency_key=key,
                specialist_id=specialist_id,
                service_id=service_id,
                date=date,
            )

    def reschedule_record(
        self,
        *,
        record_id: int | str,
        datetime: str,
        expected_version: int | None = None,
    ) -> BookingRecord:
        """Native Ayla reschedule — preserves the canonical ``appointment_id``.

        The tools' YClients path reschedules as cancel+create (which would mint
        a new appointment id); the Ayla path moves the same booking so the
        mirror keeps one stable identity.
        """
        key = _idempotency_key(self._external_user_id, "reschedule", record_id, datetime)
        # DRF-997: pass the proxy's specialist/service/old_date so the client can
        # invalidate the short-lived slot/dates cache after a successful move.
        specialist_id: str | None = None
        service_id: str | None = None
        old_date: str | None = None
        try:
            proxy = RemoteBookingProxy.all_tenants.get(appointment_id=uuid.UUID(str(record_id)))
            specialist_id = str(proxy.specialist_id) if proxy.specialist_id else None
            service_id = str(proxy.service_id) if proxy.service_id else None
            old_date = proxy.start_at.date().isoformat() if proxy.start_at else None
        except (RemoteBookingProxy.DoesNotExist, ValueError):
            pass
        with _translate_errors():
            record = self._client.reschedule_appointment(
                external_user_id=self._external_user_id,
                appointment_id=str(record_id),
                new_start_datetime=datetime,
                expected_version=expected_version,
                idempotency_key=key,
                specialist_id=specialist_id,
                service_id=service_id,
                old_date=old_date,
            )
        # The move does not change what was booked; these come from the
        # existing mirror row a few lines above, so a response that omits
        # them cannot blank out what we already knew.
        return BookingRecord(
            record_id=0,
            record_hash="",
            raw=_mirror_raw(
                record,
                requested_service_id=service_id or None,
                requested_specialist_id=specialist_id or None,
            ),
        )

    def get_user_records(self) -> list[UserRecord]:
        with _translate_errors():
            rows = self._client.get_user_appointments(external_user_id=self._external_user_id)
        return [_to_yc_user_record(r) for r in rows]

    def get_specialist_service_price(
        self,
        *,
        staff_id: int | str,
        service_id: int | str,
    ) -> Decimal | None:
        """Price of the master+service edge — what a NEW booking would cost.

        DRF-1067. Ayla creates the appointment at ``SpecialistService.price``
        (the price of the concrete pair «мастер + услуга»), while the catalog
        mirror the quote reads carries ``SalonService.base_price``. The two
        agree on the pilot today but diverge the moment a salon sets
        per-master prices — quoting the base then shows the customer one
        price and books them at another. When a specific master is chosen,
        the quote must come from the edge.

        ``None`` means "no edge price known" (no active row, or an
        unparseable value): the caller falls back to the mirror's base price
        — the behaviour before this change. Errors are translated like every
        other read so the caller can degrade instead of crashing the quote.
        """
        with _translate_errors():
            rows = self._client.get_specialist_service_edges(
                specialist_id=str(staff_id),
                service_id=str(service_id),
            )
        if not rows:
            return None
        return _parse_edge_price(rows[0].get("price"))


# ─── error translation ───────────────────────────────────────────────────────


class YClientsSpecialistUnavailableError(YClientsAPIError):
    """C1 neutral surface: the specialist cannot take a NEW booking now.

    Raised on the flag-ON path when Ayla rejects ``create`` with HTTP 409
    ``SUBSCRIPTION_PAST_DUE`` (PILOT_CONTRACTS_2026-08-15 §2). The C1
    privacy rule forbids leaking the debt reason to the customer — the
    concierge/miniapp must render a NEUTRAL «запись к этому специалисту
    недоступна» + offer another master/time, so this error carries no
    debt semantics in its type or message beyond the neutral slug. It
    subclasses ``YClientsAPIError`` so unspecialised handlers keep
    working; the create path catches it FIRST.
    """


class YClientsScheduleUnavailableError(YClientsUnavailableError):
    """Transient schedule-service outage (e.g. backend 429).

    DRF-997: the bot must show an honest "schedule service is unavailable"
    message and let the user retry shortly, NOT hand off to a manager.
    Subclasses ``YClientsUnavailableError`` so generic outage handlers keep
    working, but the booking skill catches this subtype first to avoid a
    false handoff.
    """


class YClientsStaleVersionError(YClientsAPIError):
    """Optimistic concurrency conflict on Ayla reschedule.

    Raised on the flag-ON path when Ayla rejects ``reschedule`` with HTTP 409
    ``stale_version``. The caller surfaces a user-visible conflict message
    and does NOT retry automatically, so a concurrent change to the same
    appointment is never silently overwritten.
    """


def _is_c1_debt_block(exc: BaseException) -> bool:
    """True when the Ayla 4xx is the C1 billing-eligibility rejection."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status_code", None)
    return status == 409 and isinstance(code, str) and code.lower() == "subscription_past_due"


def _is_stale_version(exc: BaseException) -> bool:
    """True when the Ayla 4xx is an optimistic-concurrency stale version."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status_code", None)
    return status == 409 and isinstance(code, str) and code.lower() == "stale_version"


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
        if issubclass(exc_type, BookingBadRequestError) and _is_c1_debt_block(exc):
            raise YClientsSpecialistUnavailableError(str(exc)) from exc
        if issubclass(exc_type, BookingBadRequestError) and _is_stale_version(exc):
            raise YClientsStaleVersionError(str(exc)) from exc
        # DRF-997: 429 after retries is a transient schedule outage, not a
        # generic YClients outage, so the skill can reply "try again in a
        # minute" instead of handing off to a manager.
        if issubclass(exc_type, BookingRateLimitedError):
            raise YClientsScheduleUnavailableError(str(exc)) from exc
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


def _parse_edge_price(value: Any) -> Decimal | None:
    """Money as ``Decimal``, never float arithmetic; unparseable → ``None``.

    ``None`` (absent), never ``0``: a silent zero would quote "бесплатно"
    for a service the salon charges for. Converting through ``str`` keeps a
    numeric wire value (the records views bypass DRF serializers, so a JSON
    number can arrive where the schema declares a string) exact.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _mirror_raw(
    record: AylaBookingRecord,
    *,
    requested_service_id: str | None = None,
    requested_specialist_id: str | None = None,
) -> dict[str, Any]:
    """Normalise a create/reschedule result into the keys the booking tools
    upsert onto ``RemoteBookingProxy`` (typed UUID columns + schedule window).

    ``requested_*`` are what WE asked Ayla to book, and they are the last
    resort rather than the first: the response is authoritative when it
    says anything.

    They are needed because on a salon booking the response says nothing.
    Ayla stores the salon service in ``Appointment.salon_service`` and
    leaves the marketplace ``service`` null, and
    ``AppointmentDetailSerializer`` does not expose ``salon_service`` at
    all — so the service simply is not in the payload to read back.
    Measured on the pilot 2026-08-22: of 23 mirrored bookings, all 17 from
    ``mobile_app`` carry a service and all 6 from ``automation`` — this
    path — carry none. On the day board those six render with no service
    name, and the front desk cannot tell what the customer is booked for.

    Ayla exposing the salon service is the real fix and is reported
    separately; this stops the bot discarding a value it had in its hand.
    """
    raw = dict(record.raw)
    service = raw.get("service") if isinstance(raw.get("service"), dict) else {}
    specialist = raw.get("specialist") if isinstance(raw.get("specialist"), dict) else {}
    return {
        **raw,
        "ayla_appointment_id": record.appointment_id,
        "start_at": raw.get("start_datetime") or raw.get("start_at"),
        "end_at": raw.get("end_datetime") or raw.get("end_at"),
        "service_id": ((service or {}).get("id") or raw.get("service_id") or requested_service_id),
        "specialist_id": (
            (specialist or {}).get("id") or raw.get("specialist_id") or requested_specialist_id
        ),
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
