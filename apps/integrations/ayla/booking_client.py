"""HTTP client for the Ayla canonical booking backend (ADR-0009).

S1 / #1016 — the REST bridge that lets bot-platform read salon catalog/slots
from Ayla and drive the booking lifecycle (create / cancel / reschedule)
*through Ayla*, which owns the canonical booking state. Today the booking
skill talks to YClients directly and mirrors a local ``BookingRequest``;
ADR-0009 makes Ayla the source of record and the local row a mirror. This
module is the bot-side seam for that migration.

Status — **SKELETON, behind a feature flag.**

The Ayla booking endpoints are not live yet: the wire contract is still a
DRAFT pending S2 sign-off (see
``docs/architecture/ayla-booking-rest-contract.md``), and auth converges on a
shared s2s ADR. Until the contract is locked, the HTTP methods here raise
``NotImplementedError`` — writing the real ``requests`` calls now would
freeze the wrong shape. What *is* real and reviewable:

* the :class:`AylaBookingClient` Protocol — the seam the booking skill's
  provider-selector targets (S1 wave 2, behind ``BOOKING_VIA_AYLA_REST``);
* the DTOs the skill consumes (decoupled from the wire shape — the adapter
  in the booking skill maps these onto the existing YClients DTOs);
* the auth header construction, so the Bearer + ``X-External-User-ID`` shape
  is testable ahead of the live endpoints;
* the inline circuit breaker + singleton lifecycle, ported from
  ``nutrition_client`` so the eventual implementation drops straight in.

Auth (per #1016 ground-truth, NOT the nutrition ``X-Service-Token`` model):
``Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}``. Reads (catalog/slots)
authenticate as an internal service (``IsInternalBearer``); writes
(create/cancel/reschedule) additionally carry ``X-External-User-ID`` so Ayla
can bind the action to the consenting client (``IsBotServiceWithVerifiedClient``)
and verify the ``TenantUserRelationship`` server-side (ADR-0009 rule 6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

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


# ─── HTTP implementation (skeleton) ──────────────────────────────────────────


class AylaBookingHTTPClient:
    """Production client for the Ayla booking bridge — **skeleton**.

    The wire contract (#1016) is not locked, so the public methods raise
    ``NotImplementedError`` rather than hit an undefined endpoint. The auth
    scaffold (:meth:`_headers`), circuit breaker and singleton lifecycle are
    real so the eventual ``requests``-based implementation drops in cleanly
    and so the Bearer + ``X-External-User-ID`` shape is reviewable now.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not base_url:
            raise ValueError("AYLA_BASE_URL is empty — booking client cannot start")
        if not api_token:
            raise ValueError("AYLA_INTERNAL_API_TOKEN is empty — booking client cannot start")
        self._base_url = base_url.rstrip("/")
        self._token = api_token
        self._timeout_s = timeout_s
        self._circuit = _Circuit()

    def _headers(self, *, external_user_id: str | None = None) -> dict[str, str]:
        """Build auth headers per #1016.

        Bearer for every call (``IsInternalBearer``); ``X-External-User-ID``
        added for writes so Ayla binds the action to the consenting client
        (``IsBotServiceWithVerifiedClient``).
        """
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if external_user_id is not None:
            headers["X-External-User-ID"] = external_user_id
        return headers

    @staticmethod
    def _pending(operation: str) -> NotImplementedError:
        """Uniform 'contract not locked yet' failure.

        Surfaced loudly if ``BOOKING_VIA_AYLA_REST`` is flipped ON before the
        Ayla endpoints + signed contract (#1016) land.
        """
        return NotImplementedError(
            f"AylaBookingHTTPClient.{operation}: Ayla booking REST contract not locked "
            "(#1016 pending S2 sign-off); real client deferred per "
            "docs/architecture/ayla-booking-rest-contract.md"
        )

    # ── reads ────────────────────────────────────────────────────────────────

    def get_services(self, *, master_id: int | None = None) -> list[AylaService]:
        raise self._pending("get_services")

    def get_masters(self, *, master_id: int | None = None) -> list[AylaMaster]:
        raise self._pending("get_masters")

    def get_available_dates(
        self,
        *,
        master_id: int | None = None,
        service_ids: list[int] | None = None,
    ) -> list[str]:
        raise self._pending("get_available_dates")

    def get_available_times(
        self,
        *,
        master_id: int,
        date: str,
        service_ids: list[int] | None = None,
    ) -> list[AylaSlot]:
        raise self._pending("get_available_times")

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
        raise self._pending("create_appointment")

    def cancel_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        idempotency_key: str | None = None,
    ) -> bool:
        raise self._pending("cancel_appointment")

    def reschedule_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        datetime: str,
        idempotency_key: str | None = None,
    ) -> AylaBookingRecord:
        raise self._pending("reschedule_appointment")

    def get_user_appointments(
        self,
        *,
        external_user_id: str,
    ) -> list[AylaUserRecord]:
        raise self._pending("get_user_appointments")


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
