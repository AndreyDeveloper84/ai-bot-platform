"""HTTP client for the Ayla canonical booking backend (ADR-0009).

S1 / #1016 — the REST bridge that lets bot-platform read salon catalog/slots
from Ayla and drive the booking lifecycle (create / cancel / reschedule)
*through Ayla*, which owns the canonical booking state. Today the booking
skill talks to YClients directly and mirrors a local ``BookingRequest``;
ADR-0009 makes Ayla the source of record and the local row a mirror. This
module is the bot-side seam for that migration.

Status — **real HTTP client, behind a feature flag (default OFF).**

The Ayla booking REST contract is **LOCKED** (S2 sign-off, #193 — see
``docs/architecture/ayla-booking-rest-contract.md``). The eight methods below
issue real ``httpx`` calls against the ``/api/v1/internal/`` surface. Flipping
``BOOKING_VIA_AYLA_REST`` ON (a separate, gated change) routes the booking
skill through this client; until then the YClients path stays authoritative.

What this module owns:

* the :class:`AylaBookingClient` Protocol — the seam the booking skill's
  provider-selector targets (behind ``BOOKING_VIA_AYLA_REST``);
* the DTOs the skill consumes (decoupled from the wire shape — the adapter
  in the booking skill maps these onto the existing YClients DTOs);
* the auth header construction (Bearer + ``X-External-User-ID``);
* the inline circuit breaker + singleton lifecycle.

Auth (per the locked contract §2, NOT the nutrition ``X-Service-Token`` model):
``Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}``. Reads (catalog/slots)
authenticate as an internal service (``IsInternalBearer``); writes
(create/cancel/reschedule) additionally carry ``X-External-User-ID`` so Ayla
can bind the action to the consenting client (``IsBotServiceWithVerifiedClient``)
and verify the ``TenantUserRelationship`` server-side (ADR-0009 rule 6).
``X-Idempotency-Key`` is sent on every write so a retried bot turn can't
double-book (contract §5).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from django.conf import settings


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0
# Policy aligned with the platform CR-3 breaker and the nutrition client:
# 5 failures in 60s → 30s cooldown.
CIRCUIT_FAILURE_WINDOW_S = 60.0
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_DURATION_S = 30.0
_BREAKER_NAME = "ayla.booking"


def _fire_breaker_alert(transition: str, failures: int) -> None:
    """Borrow the CR-3 Telegram alert path on a breaker state transition.

    Lazy-imports the alert helper and swallows every exception — alerting is
    forensic and must NEVER break the breaker (same contract as the nutrition
    client's ``_fire_breaker_alert``).
    """
    try:
        from apps.orchestrator.llm.telegram_alert import send_breaker_alert

        send_breaker_alert(
            provider=_BREAKER_NAME,
            transition=transition,
            details={"failures": failures},
        )
    except Exception:  # noqa: BLE001 — alerting must NEVER break the breaker
        logger.exception("booking_client.alert_failed transition=%s", transition)


@dataclass
class _Circuit:
    """Tiny in-process circuit breaker. Per-worker, no shared state.

    Identical policy to ``nutrition_client._Circuit`` — kept inline so the
    real HTTP implementation (post contract-lock) wires in without a refactor.
    """

    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def is_open(self, *, now: float) -> bool:
        if self.opened_at is None:
            return False
        if now - self.opened_at >= CIRCUIT_OPEN_DURATION_S:
            failures_before = len(self.failures)
            self.opened_at = None
            self.failures = []
            _fire_breaker_alert("open → closed", failures_before)
            return False
        return True

    def record_failure(self, *, now: float) -> None:
        cutoff = now - CIRCUIT_FAILURE_WINDOW_S
        self.failures = [t for t in self.failures if t >= cutoff]
        self.failures.append(now)
        if len(self.failures) >= CIRCUIT_FAILURE_THRESHOLD and self.opened_at is None:
            self.opened_at = now
            logger.warning(
                "booking_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures),
                CIRCUIT_FAILURE_WINDOW_S,
            )
            _fire_breaker_alert("closed → open", len(self.failures))

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


# ─── errors ──────────────────────────────────────────────────────────────────


class BookingAPIError(Exception):
    """Base for client-visible Ayla booking failures (4xx, malformed JSON)."""


class BookingUnavailableError(BookingAPIError):
    """Ayla is unreachable or the circuit is open — caller shows a fallback."""


class BookingBadRequestError(BookingAPIError):
    """Ayla rejected the request (validation, slot gone, consent missing).

    Distinct from :class:`BookingUnavailableError`: this is a business/input
    error the caller surfaces to the user, not an outage the circuit tracks.
    """


# ─── DTOs ──────────────────────────────────────────────────────────────────
#
# Deliberately decoupled from both the Ayla wire shape (not locked) and the
# YClients DTOs. The booking skill's adapter (S1 wave 2) maps these onto the
# existing ``yclients.client`` DTOs so the eight booking tools need no rewrite.
# Every DTO keeps ``raw`` for forensic logging / forward-compat fields.


@dataclass(frozen=True)
class AylaService:
    """A bookable service from Ayla's canonical catalog."""

    id: int
    title: str
    price_min: float
    price_max: float
    duration_s: int
    category_id: int | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AylaMaster:
    """A specialist who can take bookings (Ayla ``specialist``)."""

    id: int
    name: str
    specialization: str
    rating: float
    position: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AylaSlot:
    """A bookable time slot for a specialist on a given date."""

    time: str  # ``HH:MM``
    datetime: str | None  # ISO ``YYYY-MM-DDTHH:MM:SS+ZZ:ZZ`` when Ayla sends one
    duration_s: int | None  # session length in seconds, if known
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AylaBookingRecord:
    """Result of create/reschedule — Ayla's canonical appointment id."""

    appointment_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AylaUserRecord:
    """An existing appointment from ``get_user_appointments``."""

    appointment_id: str
    services: list[dict[str, Any]]
    master: dict[str, Any]
    datetime: str
    duration_s: int
    raw: dict[str, Any] = field(default_factory=dict)


# ─── protocol ────────────────────────────────────────────────────────────────


@runtime_checkable
class AylaBookingClient(Protocol):
    """The bot-side seam for canonical booking via Ayla.

    The booking skill's provider-selector (behind ``BOOKING_VIA_AYLA_REST``)
    targets this Protocol; ``FakeAylaBooking`` implements it for tests and
    :class:`AylaBookingHTTPClient` is the (skeleton) production implementation.

    Reads take no ``external_user_id`` — catalog/slots are tenant-public.
    Writes require it so Ayla can bind the action to the consenting client.
    """

    def get_services(self, *, master_id: int | None = ...) -> list[AylaService]: ...

    def get_masters(self, *, master_id: int | None = ...) -> list[AylaMaster]: ...

    def get_available_dates(
        self,
        *,
        master_id: int | None = ...,
        service_ids: list[int] | None = ...,
    ) -> list[str]: ...

    def get_available_times(
        self,
        *,
        master_id: int,
        date: str,
        service_ids: list[int] | None = ...,
    ) -> list[AylaSlot]: ...

    def create_appointment(
        self,
        *,
        external_user_id: str,
        master_id: int,
        service_ids: list[int],
        datetime: str,
        client_phone: str,
        client_name: str,
        comment: str | None = ...,
        idempotency_key: str | None = ...,
    ) -> AylaBookingRecord: ...

    def cancel_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        idempotency_key: str | None = ...,
    ) -> bool: ...

    def reschedule_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        datetime: str,
        idempotency_key: str | None = ...,
    ) -> AylaBookingRecord: ...

    def get_user_appointments(
        self,
        *,
        external_user_id: str,
    ) -> list[AylaUserRecord]: ...


# ─── HTTP implementation ─────────────────────────────────────────────────────


class AylaBookingHTTPClient:
    """Production client for the Ayla booking bridge (locked contract #193).

    Sync ``httpx`` against ``{AYLA_BASE_URL}/api/v1/internal/``. The booking
    path is synchronous (the YClients client it replaces is sync, and the
    booking tools call it synchronously); only the nutrition client is async.

    Resilience: per-call 10s timeout + inline circuit breaker (5 failures /
    60s → 30s cooldown). ``BookingUnavailableError`` (5xx / timeout / network /
    circuit-open) trips the breaker; ``BookingBadRequestError`` (4xx) does not.

    ``transport`` is an optional ``httpx.BaseTransport`` so tests can inject an
    ``httpx.MockTransport`` without monkeypatching the network.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("AYLA_BASE_URL is empty — booking client cannot start")
        if not api_token:
            raise ValueError("AYLA_INTERNAL_API_TOKEN is empty — booking client cannot start")
        self._base_url = base_url.rstrip("/")
        self._token = api_token
        self._timeout_s = timeout_s
        self._transport = transport
        self._circuit = _Circuit()

    @property
    def _api_root(self) -> str:
        return f"{self._base_url}/api/v1/internal"

    def _headers(
        self,
        *,
        external_user_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        """Build auth headers per the locked contract §2/§5.

        Bearer for every call (``IsInternalBearer``); ``X-External-User-ID``
        added for writes so Ayla binds the action to the consenting client
        (``IsBotServiceWithVerifiedClient``); ``X-Idempotency-Key`` on writes
        so a retried turn can't double-book.
        """
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if external_user_id is not None:
            headers["X-External-User-ID"] = external_user_id
        if idempotency_key is not None:
            headers["X-Idempotency-Key"] = idempotency_key
        return headers

    # ── request plumbing ──────────────────────────────────────────────────────

    def _request(
        self,
        *,
        method: str,
        path: str,
        operation: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue one request, mapping transport failures to breaker trips.

        Network errors and timeouts record a circuit failure and raise
        :class:`BookingUnavailableError`. Status-code mapping (success vs
        4xx vs 5xx) is the caller's job via :meth:`_envelope`.
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise BookingUnavailableError("circuit_open")

        url = f"{self._api_root}/{path}"
        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as http:
                return http.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            logger.warning(
                "booking_client.%s.network err=%s",
                operation,
                type(exc).__name__,
            )
            raise BookingUnavailableError(f"network: {type(exc).__name__}") from exc

    def _envelope(self, resp: httpx.Response, *, operation: str) -> Any:
        """Parse a response envelope per contract §6.

        2xx → the ``data`` payload (envelope-wrapped; falls back to the bare
        body if no ``data`` key). 5xx / circuit-open / network →
        :class:`BookingUnavailableError` (trips breaker). 4xx →
        :class:`BookingBadRequestError` (does NOT trip breaker), carrying the
        ``error.code`` when present.
        """
        now = time.monotonic()
        if 200 <= resp.status_code < 300:
            self._circuit.record_success()
            try:
                body = resp.json()
            except ValueError:
                return {}
            if isinstance(body, dict) and "data" in body:
                return body["data"]
            return body

        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            logger.warning("booking_client.%s.5xx status=%d", operation, resp.status_code)
            raise BookingUnavailableError(f"http_{resp.status_code}")

        # 4xx — business / input error. Does NOT trip the breaker.
        err_code = ""
        try:
            err_code = (resp.json().get("error") or {}).get("code", "")
        except (ValueError, AttributeError):
            err_code = ""
        logger.info(
            "booking_client.%s.4xx status=%d code=%s",
            operation,
            resp.status_code,
            err_code,
        )
        raise BookingBadRequestError(f"http_{resp.status_code}_{err_code or 'unknown'}")

    # ── reads ────────────────────────────────────────────────────────────────

    def get_services(self, *, master_id: int | None = None) -> list[AylaService]:
        """GET ``specialists/{id}/services/`` (master-scoped) or ``services/``."""
        path = f"specialists/{master_id}/services/" if master_id is not None else "services/"
        resp = self._request(
            method="GET",
            path=path,
            operation="get_services",
            headers=self._headers(),
        )
        data = self._envelope(resp, operation="get_services")
        return [_to_service(row) for row in _as_list(data)]

    def get_masters(self, *, master_id: int | None = None) -> list[AylaMaster]:
        """GET ``specialists/`` (list) or ``specialists/{id}/`` (one)."""
        if master_id is not None:
            resp = self._request(
                method="GET",
                path=f"specialists/{master_id}/",
                operation="get_masters",
                headers=self._headers(),
            )
            data = self._envelope(resp, operation="get_masters")
            rows = _as_list(data)
        else:
            resp = self._request(
                method="GET",
                path="specialists/",
                operation="get_masters",
                headers=self._headers(),
            )
            rows = _as_list(self._envelope(resp, operation="get_masters"))
        return [_to_master(row) for row in rows]

    def get_available_dates(
        self,
        *,
        master_id: int | None = None,
        service_ids: list[int] | None = None,
    ) -> list[str]:
        """GET ``specialists/{id}/slots/`` → distinct ordered date strings.

        Ayla returns slots (optionally grouped by date); we derive the
        available-dates list the booking tools expect by projecting each
        slot's date and de-duplicating while preserving order.
        """
        if master_id is None:
            return []
        slots = self.get_available_times(
            master_id=master_id,
            date="",
            service_ids=service_ids,
        )
        seen: dict[str, None] = {}
        for slot in slots:
            date_part = (slot.datetime or "").split("T", 1)[0]
            if date_part:
                seen.setdefault(date_part, None)
        return sorted(seen)

    def get_available_times(
        self,
        *,
        master_id: int,
        date: str,
        service_ids: list[int] | None = None,
    ) -> list[AylaSlot]:
        """GET ``specialists/{id}/slots/?service_ids=&from=&to=``."""
        params: dict[str, Any] = {}
        if service_ids:
            params["service_ids"] = ",".join(str(s) for s in service_ids)
        if date:
            params["from"] = date
            params["to"] = date
        resp = self._request(
            method="GET",
            path=f"specialists/{master_id}/slots/",
            operation="get_available_times",
            headers=self._headers(),
            params=params,
        )
        data = self._envelope(resp, operation="get_available_times")
        return [_to_slot(row) for row in _as_slot_list(data)]

    # ── writes ───────────────────────────────────────────────────────────────

    def create_appointment(
        self,
        *,
        external_user_id: str,
        master_id: int,
        service_ids: list[int],
        datetime: str,
        client_phone: str,
        client_name: str,
        comment: str | None = None,
        idempotency_key: str | None = None,
    ) -> AylaBookingRecord:
        """POST ``appointments/`` → canonical ``appointment_id`` (UUID)."""
        body: dict[str, Any] = {
            "specialist_id": master_id,
            "service_ids": service_ids,
            "datetime": datetime,
            "client": {"name": client_name, "phone": client_phone},
        }
        if comment:
            body["comment"] = comment
        resp = self._request(
            method="POST",
            path="appointments/",
            operation="create_appointment",
            headers=self._headers(
                external_user_id=external_user_id,
                idempotency_key=idempotency_key,
            ),
            json_body=body,
        )
        data = self._envelope(resp, operation="create_appointment")
        return _to_booking_record(data)

    def cancel_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        idempotency_key: str | None = None,
    ) -> bool:
        """POST ``appointments/{uuid}/cancel/``. 404 → already gone → False."""
        resp = self._request(
            method="POST",
            path=f"appointments/{appointment_id}/cancel/",
            operation="cancel_appointment",
            headers=self._headers(
                external_user_id=external_user_id,
                idempotency_key=idempotency_key,
            ),
        )
        if resp.status_code == 404:
            # Already cancelled / never existed — idempotent no-op, not an
            # outage. Does not trip the breaker.
            self._circuit.record_success()
            return False
        self._envelope(resp, operation="cancel_appointment")
        return True

    def reschedule_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        datetime: str,
        idempotency_key: str | None = None,
    ) -> AylaBookingRecord:
        """POST ``appointments/{uuid}/reschedule/`` — native, same UUID."""
        resp = self._request(
            method="POST",
            path=f"appointments/{appointment_id}/reschedule/",
            operation="reschedule_appointment",
            headers=self._headers(
                external_user_id=external_user_id,
                idempotency_key=idempotency_key,
            ),
            json_body={"datetime": datetime},
        )
        data = self._envelope(resp, operation="reschedule_appointment")
        return _to_booking_record(data)

    def get_user_appointments(
        self,
        *,
        external_user_id: str,
    ) -> list[AylaUserRecord]:
        """GET ``me/bookings/`` for the resolved (verified) user."""
        resp = self._request(
            method="GET",
            path="me/bookings/",
            operation="get_user_appointments",
            headers=self._headers(external_user_id=external_user_id),
        )
        data = self._envelope(resp, operation="get_user_appointments")
        return [_to_user_record(row) for row in _as_list(data)]


# ─── wire → DTO mappers ──────────────────────────────────────────────────────


def _as_list(data: Any) -> list[dict[str, Any]]:
    """Coerce an envelope payload into a list of row dicts.

    Reads may return a bare list, or an object wrapping ``results`` /
    ``items`` (pagination). Anything else → empty list.
    """
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "bookings", "specialists", "services"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [row for row in inner if isinstance(row, dict)]
        # A single object (e.g. GET specialists/{id}/) → wrap.
        return [data]
    return []


def _as_slot_list(data: Any) -> list[dict[str, Any]]:
    """Flatten a slots payload — bare list, or grouped-by-date dict/list.

    Ayla may send ``[Slot]`` or ``[{date, slots: [Slot]}]`` (grouped). We
    flatten the grouped shape, propagating the group ``date`` onto each slot
    that lacks an absolute ``datetime``.
    """
    rows = _as_list(data)
    flat: list[dict[str, Any]] = []
    for row in rows:
        inner = row.get("slots") if isinstance(row, dict) else None
        if isinstance(inner, list):
            group_date = row.get("date") if isinstance(row, dict) else None
            for slot in inner:
                if isinstance(slot, dict):
                    if group_date and "datetime" not in slot and "date" not in slot:
                        slot = {**slot, "date": group_date}
                    flat.append(slot)
        else:
            flat.append(row)
    return flat


def _to_service(row: dict[str, Any]) -> AylaService:
    return AylaService(
        id=int(row.get("id") or 0),
        title=str(row.get("title") or row.get("name") or ""),
        price_min=float(row.get("price_min") or 0.0),
        price_max=float(row.get("price_max") or 0.0),
        duration_s=int(row.get("duration_s") or 0),
        category_id=row.get("category_id"),
        raw=row,
    )


def _to_master(row: dict[str, Any]) -> AylaMaster:
    return AylaMaster(
        id=int(row.get("id") or 0),
        name=str(row.get("name") or ""),
        specialization=str(row.get("specialization") or ""),
        rating=float(row.get("rating") or 0.0),
        position=str(row.get("position") or ""),
        raw=row,
    )


def _to_slot(row: dict[str, Any]) -> AylaSlot:
    dt = row.get("datetime")
    return AylaSlot(
        time=str(row.get("time") or ""),
        datetime=str(dt) if dt else None,
        duration_s=row.get("duration_s"),
        raw=row,
    )


def _to_booking_record(data: Any) -> AylaBookingRecord:
    row = data if isinstance(data, dict) else {}
    return AylaBookingRecord(
        appointment_id=str(row.get("appointment_id") or row.get("id") or ""),
        raw=row,
    )


def _to_user_record(row: dict[str, Any]) -> AylaUserRecord:
    return AylaUserRecord(
        appointment_id=str(row.get("appointment_id") or row.get("id") or ""),
        services=list(row.get("services") or []),
        master=dict(row.get("specialist") or row.get("master") or {}),
        datetime=str(row.get("datetime") or ""),
        duration_s=int(row.get("duration_s") or 0),
        raw=row,
    )


# ─── singleton ─────────────────────────────────────────────────────────────


_SINGLETON: AylaBookingHTTPClient | None = None


def get_ayla_booking_client() -> AylaBookingHTTPClient:
    """Module-level singleton. Lazy — fails loudly when env is unset."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = AylaBookingHTTPClient(
            base_url=getattr(settings, "AYLA_BASE_URL", ""),
            api_token=getattr(settings, "AYLA_INTERNAL_API_TOKEN", ""),
        )
    return _SINGLETON


def reset_ayla_booking_client() -> None:
    """Drop the singleton — used by tests to reset state between cases."""
    global _SINGLETON
    _SINGLETON = None
