"""HTTP client for the Ayla canonical booking backend (ADR-0009).

S1 / #1016 — the REST bridge that lets bot-platform read salon catalog/slots
from Ayla and drive the booking lifecycle (create / cancel / reschedule)
*through* Ayla, which owns the canonical booking state. The booking skill
historically talks to YClients directly and mirrors a local ``BookingRequest``;
ADR-0009 makes Ayla the source of record and the local row a mirror
(``RemoteBookingProxy``). This module is the bot-side seam for that migration.

The wire contract is locked (#1027) and the S2 endpoints are merged in Ayla
``dev`` (#193, ``/api/v1/internal/*``). Shapes here are aligned against the
merged serializers:

* catalog ids (``Service.id`` / ``Specialist.id``) are **UUIDs**, surfaced as
  strings on the DTOs (``AylaService.id`` / ``AylaMaster.id``);
* the slots endpoint is **single-day** (``?service_id=<uuid>&date=<YYYY-MM-DD>``)
  and returns bare ISO-8601 datetime strings — :meth:`get_available_dates`
  fans out over a date window because Ayla exposes no range endpoint;
* the create request body is ``{client_id, specialist_id, service_id,
  start_datetime}`` (singular ``service_id``); ``client_id`` is the Ayla user
  UUID the bearer + ``X-External-User-ID`` resolved to.

Auth (per #1016 ground-truth, NOT the nutrition ``X-Service-Token`` model):
``Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}``. Reads (catalog/slots)
authenticate as an internal service (``IsInternalBearer``); writes
(create/cancel/reschedule + ``me/bookings``) additionally carry
``X-External-User-ID: bot:{channel}:{id}`` so Ayla can bind the action to the
consenting client (``IsBotServiceWithVerifiedClient``) and verify the
``TenantUserRelationship`` server-side (ADR-0009 rule 6).
"""

from __future__ import annotations

import email.utils
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta, timezone as tz
from typing import Any, NoReturn, Protocol, runtime_checkable

import httpx
from django.conf import settings
from django.core.cache import cache

from apps.integrations.ayla.url_builder import AylaUrlBuilder


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0
# Policy aligned with the platform CR-3 breaker and the nutrition client:
# 5 failures in 60s → 30s cooldown.
CIRCUIT_FAILURE_WINDOW_S = 60.0
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_DURATION_S = 30.0
_BREAKER_NAME = "ayla.booking"

# DRF-997: bounded retry for transient 429 responses. Retry-After is respected
# up to a cap so a single slow backend header cannot block the worker forever.
# Total synchronous sleep budget per call is RATE_LIMIT_MAX_RETRIES sleeps,
# each capped at RATE_LIMIT_MAX_WAIT_S, currently ≤ 3 s to keep the single-
# threaded pilot consumer responsive.
RATE_LIMIT_MAX_RETRIES = 2
RATE_LIMIT_BACKOFF_BASE_S = 0.05
RATE_LIMIT_MAX_WAIT_S = 1.5

# DRF-997: short-lived cache for slots/dates lookups. TTL is long enough to
# absorb repeated picker renders / re-taps but short enough to keep the
# calendar fresh. Key includes specialist + service + date/window.
SLOT_CACHE_TTL_S = 60
SLOT_CACHE_KEY_PREFIX = "ayla.booking.slots.v1"

# Ayla's internal slots endpoint is single-day; ``get_available_dates`` fans
# out over this many days from today to derive the "which days are free"
# calendar the booking tools expect (S1 decision — no range endpoint exists).
AVAILABLE_DATES_WINDOW_DAYS = 14

# The only two sections ``me/bookings`` accepts (``records_api.py:295-300``);
# anything else is a 400 upstream. Validated here so a typo fails at the call
# site instead of costing a round trip.
_ME_BOOKINGS_SECTIONS = frozenset({"upcoming", "history"})

# S5-LOW2: hard ceiling on the fan-out. ``get_available_dates`` issues one
# HTTP call per day, so an oversized ``window_days`` (caller bug, future
# config drift) would hammer Ayla and stall the user. Clamp defensively.
MAX_AVAILABLE_DATES_WINDOW_DAYS = 31

# DRF-1004: hard ceiling on catalog pagination. A runaway ``next`` chain
# (upstream bug) must not loop forever — hitting the ceiling with pages
# still left is surfaced as ``catalog_incomplete``.
MAX_CATALOG_PAGES = 100


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
    breaker policy stays consistent across the Ayla integrations package.
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
    """Ayla is unreachable or the circuit is open — caller shows a fallback.

    5xx / timeout / network / circuit-open. **Trips** the breaker. Exception:
    ``catalog_incomplete`` (DRF-1004) is a data-integrity verdict, not a
    connectivity failure — it is raised without recording a breaker failure.
    """


class BookingRateLimitedError(BookingUnavailableError):
    """429 Too Many Requests after bounded retries — transient, no breaker trip.

    Surfaced to the user as a short "schedule service unavailable" message
    rather than a handoff, and must NOT be confused with "no slots".
    """


class BookingBadRequestError(BookingAPIError):
    """Ayla rejected the request (validation, slot gone, consent missing).

    4xx other than the handled 404s. A business/input error the caller
    surfaces to the user, NOT an outage — does **not** trip the breaker.

    Carries structured ``status_code`` / ``code`` (the wire ``error.code``,
    e.g. C1's ``SUBSCRIPTION_PAST_DUE``) so callers can branch on the
    reason without parsing the message string. Both default to None for
    legacy raise sites.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ScheduleBlockConflictError(BookingBadRequestError):
    """Ayla refused to block a period because live bookings sit in it.

    Separated from a plain 4xx because the caller owes the administrator a
    different answer: the request was valid and permitted, the time simply
    is not free. Retrying changes nothing — somebody has to decide what
    happens to the people already booked, and that decision belongs on a
    surface where a named human is the actor. Not an outage: does **not**
    trip the breaker.
    """


class RepeatIntentUnusableError(BookingAPIError):
    """``repeat-intent`` answered 200 with an id that is not a UUID.

    Upstream defect DRF-1049: the endpoint reads only the marketplace
    ``service_id`` and ignores the salon one, so for a salon booking — every
    booking this bot creates — it returns the literal string ``"None"`` with
    HTTP 200. Raised rather than returned as an empty value so the failure
    cannot be mistaken for "no prefill available"; the caller owes the user a
    graceful alternative, not a dead end. Not an outage: does **not** trip the
    breaker.

    Carries ``field`` (which id was bad) and ``value`` (what arrived) so an
    operator can tell the known DRF-1049 ``"None"`` from any other garbage —
    they are different tickets. Both are ids, never personal data.

    .. warning::

        Going through ``AylaYClientsAdapter``, ``_translate_errors``
        (``provider.py:341-359``) collapses every ``BookingAPIError`` into a
        generic ``YClientsAPIError``, which would make this indistinguishable
        from a transient failure. A caller that needs the graceful-alternative
        branch must catch this **before** the adapter, or the adapter needs its
        own translation arm — a follow-up this transport change leaves alone.
    """

    def __init__(self, field_name: str, *, value: str | None = None) -> None:
        super().__init__(f"repeat_intent_unusable:{field_name}")
        self.field = field_name
        self.value = value


# ─── DTOs ──────────────────────────────────────────────────────────────────
#
# Decoupled from the Ayla wire shape; the booking skill's adapter (provider.py)
# maps these onto the existing ``yclients.client`` DTOs so the eight booking
# tools need no rewrite. Ayla catalog ids are UUIDs → ``id`` fields are ``str``.
# Every DTO keeps ``raw`` for forensic logging / forward-compat fields.


@dataclass(frozen=True)
class AylaService:
    """A bookable service from Ayla's canonical catalog (UUID id)."""

    id: str
    title: str
    price_min: float
    price_max: float
    duration_s: int
    category_id: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AylaMaster:
    """A specialist who can take bookings (Ayla ``specialist``, UUID id)."""

    id: str
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
    """Result of create/reschedule — Ayla's canonical appointment id (UUID)."""

    appointment_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AylaUserRecord:
    """An existing appointment from ``get_user_appointments`` (me/bookings).

    ``derived_status`` and ``price`` are declared explicitly (rather than left
    to ``raw``) because the customer-facing display policy keys off them:
    DRF-1032 shows only visits that actually happened, and the visit line
    carries the historical price. Reading policy inputs out of ``raw`` hides
    that dependency from anyone reading the display code. Both fields sit at
    the end so existing positional construction keeps working.
    """

    appointment_id: str
    services: list[dict[str, Any]]
    master: dict[str, Any]
    datetime: str
    duration_s: int
    # ``repr=False``: ``get_booking_detail`` puts ``notes``,
    # ``cancellation_reason`` and payment rows in here. Nothing logs the DTO
    # today, but a future ``logger.info("...%s", record)`` would spill all of
    # it verbatim — cheap insurance against a leak nobody meant to write.
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    # Both ``None`` = the field was absent, which is NOT the same as an empty
    # or zero value. Zero is a real price on the payment-free pilot, and a
    # missing ``derived_status`` would silently fail the completed-only filter
    # for every row — indistinguishable from "this customer has no visits"
    # unless the two cases stay separable.
    derived_status: str | None = None
    price: float | None = None


@dataclass(frozen=True)
class AylaBookingPage:
    """One page of ``me/bookings`` — records plus the backend's cursor.

    The cursor is kept because the completed-only display policy filters
    client-side: a page of history may contain cancellations and no-shows, so
    reaching N shown visits can need a second page. That is an internal
    top-up, NOT user-facing pagination (owner decision OD-H3 forbids the
    latter and says nothing about the former).
    """

    records: list[AylaUserRecord]
    next_cursor: str | None = None


@dataclass(frozen=True)
class AylaAppointmentVersion:
    """The four canonical facts a console needs before acting on a booking.

    Deliberately not a booking card: everything else the salon console
    shows it already has from its own mirror, and this crosses a service
    boundary on every button press.
    """

    id: str
    version: int
    status: str
    start_datetime: str


@dataclass(frozen=True)
class AylaRepeatIntent:
    """Prefill for the «Записаться ещё» CTA (``me/bookings/{id}/repeat-intent/``).

    Historical facts only: the endpoint performs NO eligibility check — it does
    not verify that the service or the specialist is still active, nor that
    they are still linked (verified against ``records_api.py:413-431``). The
    caller must re-validate against current backend state before booking, and
    ``last_price`` must never be used as the price of a new appointment.
    """

    service_id: str
    specialist_id: str
    last_price: float | None
    suggested_slots: list[str]
    raw: dict[str, Any] = field(default_factory=dict)


# ─── protocol ────────────────────────────────────────────────────────────────


@runtime_checkable
class AylaBookingClient(Protocol):
    """The bot-side seam for canonical booking via Ayla.

    The booking skill's provider-selector (behind ``BOOKING_VIA_AYLA_REST``)
    targets this Protocol; ``FakeAylaBooking`` implements it for tests and
    :class:`AylaBookingHTTPClient` is the production implementation.

    Reads take no ``external_user_id`` — catalog/slots are tenant-public.
    Writes require it so Ayla can bind the action to the consenting client.
    Catalog ids are Ayla UUIDs (strings).
    """

    def get_services(self) -> list[AylaService]: ...

    def get_masters(self, *, specialist_id: str | None = ...) -> list[AylaMaster]: ...

    def get_available_dates(
        self,
        *,
        specialist_id: str,
        service_id: str,  # #1051: mandatory on the Ayla slots path
        window_days: int = ...,
    ) -> list[str]: ...

    def get_available_times(
        self,
        *,
        specialist_id: str,
        date: str,
        service_id: str,  # #1051: mandatory
    ) -> list[AylaSlot]: ...

    def create_appointment(
        self,
        *,
        external_user_id: str,
        client_id: str,
        specialist_id: str,
        service_id: str,
        start_datetime: str,
        idempotency_key: str | None = ...,
        payment_required: bool = ...,
    ) -> AylaBookingRecord: ...

    def cancel_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        idempotency_key: str | None = ...,
        specialist_id: str | None = ...,
        service_id: str | None = ...,
        date: str | None = ...,
    ) -> bool: ...

    def reschedule_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        new_start_datetime: str,
        expected_version: int | None = ...,
        idempotency_key: str | None = ...,
        specialist_id: str | None = ...,
        service_id: str | None = ...,
        old_date: str | None = ...,
    ) -> AylaBookingRecord: ...

    def get_user_appointments(
        self,
        *,
        external_user_id: str,
    ) -> list[AylaUserRecord]: ...

    def get_specialist_service_edges(
        self,
        *,
        specialist_id: str,
        service_id: str,
    ) -> list[dict[str, Any]]:
        """Active bookable edge rows for ONE (specialist, salon service) pair.

        DRF-1067 widened this Protocol with the narrow read that already
        existed on the concrete client: the quote path needs the edge's own
        ``price`` (what Ayla stamps onto a new appointment) and the catalog
        reads drop it. Raw dicts, not a DTO — the edge payload is a catalog
        row, and the two consumers (quote, repeat) pick single fields off it.
        """

    # NOTE: ``get_appointment_version`` (DRF-1233) is deliberately NOT a
    # member of this Protocol. This seam exists for the booking skill's
    # provider-selector, and the canonical version read belongs to the
    # salon console, which holds a concrete client. Declaring it here
    # would oblige every fake of the booking skill to implement a method
    # that skill never calls — which is exactly what happened when it was
    # first written this way, and what mypy caught.


# ─── wire → DTO mappers ───────────────────────────────────────────────────────


def _unwrap(body: Any) -> Any:
    """Unwrap the ``{"data": ...}`` envelope where present.

    Ayla is inconsistent by design: ``success_response`` wraps in ``{"data"}``
    (create/reschedule, services catalog), while the inherited ``slots`` /
    ``services`` actions return the payload raw. This tolerates both.
    """
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _as_rows(payload: Any) -> list[dict[str, Any]]:
    """Normalise a list payload — handles raw lists and paginated ``results``."""
    if isinstance(payload, dict) and "results" in payload:
        rows = payload.get("results")
    else:
        rows = payload
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _service_from_wire(d: dict[str, Any]) -> AylaService:
    # DRF-1004: the canonical catalog (``catalog/salon-services/``) exposes the
    # price as ``base_price`` (string); the legacy feed used ``price``. Prefer
    # the canonical field, tolerate the old one. ``is not None`` (not truthiness)
    # so a numeric zero price can't fall through to the legacy field.
    raw_price = d.get("base_price")
    if raw_price is None:
        raw_price = d.get("price")
    price = float(raw_price or 0.0)
    dur_min = int(d.get("duration_minutes") or 0)
    category = d.get("category")
    return AylaService(
        id=str(d.get("id") or ""),
        title=str(d.get("name") or d.get("title") or ""),
        price_min=price,
        price_max=price,
        duration_s=dur_min * 60,
        category_id=str(category) if category else None,
        raw=d,
    )


def _master_from_wire(d: dict[str, Any]) -> AylaMaster:
    return AylaMaster(
        id=str(d.get("id") or ""),
        name=str(d.get("display_name") or d.get("name") or ""),
        specialization=str(d.get("specialization") or ""),
        rating=float(d.get("rating") or 0.0),
        position=str(d.get("position") or ""),
        raw=d,
    )


def _slot_from_wire(item: Any) -> AylaSlot:
    """Map one slot entry.

    The merged contract sends bare ISO-8601 local strings
    (``compute_specialist_day_slots`` → ``{"slots": [<iso>, ...]}``); tolerate
    a dict shape too for forward-compat.
    """
    if isinstance(item, dict):
        iso = item.get("datetime")
        dur = item.get("duration_s")
        return AylaSlot(
            time=str(item.get("time") or _iso_hhmm(iso)),
            datetime=iso,
            duration_s=int(dur) if dur is not None else None,
            raw=item,
        )
    iso = str(item)
    return AylaSlot(time=_iso_hhmm(iso), datetime=iso, duration_s=None, raw={"datetime": iso})


def _iso_hhmm(iso: Any) -> str:
    """Extract ``HH:MM`` from an ISO-8601 datetime string, best-effort."""
    if not iso:
        return ""
    text = str(iso)
    if "T" in text and len(text) >= text.index("T") + 6:
        return text[text.index("T") + 1 : text.index("T") + 6]
    return ""


def _iso_date(iso: Any) -> str:
    """Extract ``YYYY-MM-DD`` from an ISO-8601 datetime string, best-effort."""
    if not iso:
        return ""
    text = str(iso)
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10] if len(text) >= 10 else text


def _parse_price(value: Any) -> float | None:
    """Best-effort price parse, tolerant of number and string.

    The records views build plain dicts and hand them to ``success_response``,
    so DRF's serializers never run and its encoder maps ``Decimal`` → float —
    the wire carries a NUMBER even though the declared schema says string.
    Accept both rather than betting on either. Unparseable → ``None`` (absent),
    never ``0.0``: a silent zero would read as "free" in the UI.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    # ``inf``/``nan`` would render as "inf ₽" in a chat message. Unreachable
    # from a ``DecimalField(max_digits=10)`` today, but the cost of the guard
    # is one call and the cost of missing it is a nonsense price.
    return parsed if math.isfinite(parsed) else None


def _user_record_from_wire(d: dict[str, Any]) -> AylaUserRecord:
    start = str(d.get("start_datetime") or d.get("datetime") or "")
    end = str(d.get("end_datetime") or "")
    service = d.get("service") or {}
    specialist = d.get("specialist") or {}
    services = [service] if isinstance(service, dict) and service else list(d.get("services") or [])
    return AylaUserRecord(
        appointment_id=str(d.get("id") or d.get("appointment_id") or ""),
        services=services,
        master=specialist if isinstance(specialist, dict) else {},
        datetime=start,
        duration_s=_duration_s(start, end),
        raw=d,
        derived_status=(str(raw_status) if (raw_status := d.get("derived_status")) else None),
        price=_parse_price(d.get("price")),
    )


def _duration_s(start_iso: str, end_iso: str) -> int:
    """Best-effort duration in seconds between two ISO-8601 datetimes."""
    from django.utils.dateparse import parse_datetime

    if not start_iso or not end_iso:
        return 0
    start = parse_datetime(start_iso)
    end = parse_datetime(end_iso)
    if start is None or end is None:
        return 0
    return max(int((end - start).total_seconds()), 0)


# ─── HTTP implementation ──────────────────────────────────────────────────────


class AylaBookingHTTPClient:
    """Production client for the Ayla booking bridge (#1016).

    Sync ``httpx`` — the booking skill calls it synchronously through the
    provider adapter. An optional ``transport`` makes the wire fakeable in
    tests (``httpx.MockTransport``). The inline circuit breaker + singleton
    lifecycle match the nutrition client.
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
        # Single URL seam (#1049): the builder owns host-only validation + the
        # ``/api/v1`` prefix so this client never hand-builds an f-string URL.
        self._urls = AylaUrlBuilder(base_url)
        self._base_url = self._urls.origin
        self._token = api_token
        self._timeout_s = timeout_s
        self._transport = transport
        self._circuit = _Circuit()
        # CR-SF1: one persistent httpx.Client reused across calls so the
        # ``get_available_dates`` fan-out (one request per day) shares a
        # connection pool instead of building/tearing one client per HTTP
        # call. The client is a singleton (``get_ayla_booking_client``), so
        # the pool lives for the process lifetime.
        self._http: httpx.Client | None = None

    def _client(self) -> httpx.Client:
        """Lazily build + reuse the connection-pooled HTTP client (CR-SF1)."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.Client(timeout=self._timeout_s, transport=self._transport)
        return self._http

    def close(self) -> None:
        """Close the pooled client. Optional — for tests / graceful shutdown."""
        if self._http is not None and not self._http.is_closed:
            self._http.close()
        self._http = None

    def _headers(self, *, external_user_id: str | None = None) -> dict[str, str]:
        """Build auth headers per #1016.

        Bearer for every call (``IsInternalBearer``); ``X-External-User-ID``
        added for writes / ``me`` reads so Ayla binds the action to the
        consenting client (``IsBotServiceWithVerifiedClient``).
        """
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if external_user_id is not None:
            headers["X-External-User-ID"] = external_user_id
        return headers

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        external_user_id: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        """Issue one request through the breaker. Maps network/timeout to
        :class:`BookingUnavailableError`; 429 is retried with backoff.

        Status handling for non-429 responses is the caller's.

        .. note::

            The bounded 429 retry uses ``time.sleep``, which blocks the
            calling thread. This client is synchronous and is invoked from
            async skill code, so the sleep blocks the caller's coroutine
            for up to :data:`RATE_LIMIT_MAX_WAIT_S` s per attempt. The total
            synchronous sleep budget is ≤{RATE_LIMIT_MAX_RETRIES * RATE_LIMIT_MAX_WAIT_S:.1f} s
            ({RATE_LIMIT_MAX_RETRIES} retries × {RATE_LIMIT_MAX_WAIT_S} s cap), which keeps the
            single-threaded pilot consumer responsive. The proper long-term fix
            is to make the client async or run it in a thread pool; that
            refactor is out of scope for DRF-997.
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise BookingUnavailableError("circuit_open")

        url = self._urls.build(f"internal/{endpoint.lstrip('/')}")
        headers = self._headers(external_user_id=external_user_id)
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        try:
            http = self._client()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            # Client construction only touches local state; treat as network.
            self._circuit.record_failure(now=now)
            logger.warning("booking_client.%s.network err=%s", endpoint, type(exc).__name__)
            raise BookingUnavailableError(f"network: {type(exc).__name__}") from exc

        for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
            try:
                resp = http.request(method, url, headers=headers, params=params, json=json_body)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self._circuit.record_failure(now=now)
                logger.warning("booking_client.%s.network err=%s", endpoint, type(exc).__name__)
                raise BookingUnavailableError(f"network: {type(exc).__name__}") from exc

            if resp.status_code != 429:
                return resp

            retry_after = _parse_retry_after(resp.headers.get("retry-after"))
            wait = max(retry_after, RATE_LIMIT_BACKOFF_BASE_S * (2**attempt))
            wait = min(wait, RATE_LIMIT_MAX_WAIT_S)
            # DRF-997: jitter the exponential backoff so concurrent workers do
            # not stampede the backend the moment Retry-After / cap expires.
            wait = random.uniform(0.75, 1.0) * wait
            logger.warning(
                "booking_client.429 attempt=%d/%d retry_after=%.2f wait=%.2f endpoint=%s",
                attempt,
                RATE_LIMIT_MAX_RETRIES,
                retry_after,
                wait,
                endpoint,
            )
            if attempt < RATE_LIMIT_MAX_RETRIES and wait:
                # Sync sleep — blocks the caller's thread/coroutine. See the
                # method docstring for the async-refactor caveat.
                time.sleep(wait)

        # All retries consumed — surface as transient unavailability without
        # opening the breaker. The provider layer maps this to a user-facing
        # "schedule service unavailable" message instead of a handoff.
        raise BookingRateLimitedError(f"rate_limited_{resp.status_code}")

    def _fail_status(self, resp: httpx.Response, *, now: float) -> NoReturn:
        """Map a non-success status to an error (CR-SF2 — single source).

        5xx → :class:`BookingUnavailableError` (trips the breaker); any other
        (4xx) → :class:`BookingBadRequestError` (no trip). Shared by
        :meth:`_ok` and :meth:`cancel_appointment` so the mapping can't drift.
        """
        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            logger.warning("booking_client.5xx status=%d", resp.status_code)
            raise BookingUnavailableError(f"http_{resp.status_code}")
        # Structured status_code/code ride along (dev C1 — the provider
        # maps 409 SUBSCRIPTION_PAST_DUE to the neutral
        # specialist_unavailable surface without string-parsing).
        raise BookingBadRequestError(
            f"http_{resp.status_code}_{_err_code(resp)}",
            status_code=resp.status_code,
            code=_err_code(resp),
        )

    def _ok(self, resp: httpx.Response, *, success: tuple[int, ...] = (200, 201)) -> Any:
        """Validate status + unwrap the body. Maps 5xx→Unavailable (trips),
        4xx→BadRequest (no trip). A successful status with unparseable JSON
        is treated as unavailable so it can never be silently read as "empty".
        """
        now = time.monotonic()
        if resp.status_code in success:
            self._circuit.record_success()
            try:
                return _unwrap(resp.json())
            except ValueError as exc:
                logger.warning("booking_client.malformed_json status=%d", resp.status_code)
                raise BookingUnavailableError("malformed_response") from exc
        self._fail_status(resp, now=now)

    # ── reads ────────────────────────────────────────────────────────────────

    def get_services(self) -> list[AylaService]:
        """The tenant's whole active service catalog (DRF-1004).

        The legacy ``services/`` feed is dead upstream (legacy ``Service``
        queryset is globally empty); the live catalog is
        ``catalog/salon-services/``. The read is scoped by the active
        tenant — a call without tenant scope is an error, not an empty
        catalog.

        Whole-catalog on purpose (DRF-1019). This used to take a
        ``specialist_id`` that fetched the tenant's bookable edges as a
        SECOND full page-walk and intersected the two lists in memory. No
        caller ever passed it: the bot's single call site
        (``apps/skills/booking/skill.py``) wants the whole catalog, because
        it builds the service-id allow-set and the name lookup for the whole
        dialog. The branch was removed rather than wired up, because both
        readers that genuinely need "what does this master do" are already
        cheaper than it was:

        * one (specialist, service) pair → :meth:`get_specialist_service_edges`
          — a single narrow request that also keeps the edge ``price``;
        * a master's whole roster → the local ``catalog.MasterService``
          mirror, joined in SQL (``apps/orchestrator/handoff.py``), no HTTP
          at all.
        """
        tenant_id = _require_tenant_id()
        rows = self._get_all_rows(
            "catalog/salon-services/",
            params={"tenant": tenant_id, "is_active": "true"},
        )
        return [_service_from_wire(r) for r in rows]

    def _get_all_rows(self, endpoint: str, *, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Walk a paginated DRF list endpoint to completion.

        Never returns a partial catalog silently: when the envelope advertises
        ``count``, a mismatch with the collected rows raises
        :class:`BookingUnavailableError` (DRF-1004 — a truncated catalog reads
        downstream as spurious ``unknown_service`` on ``pick_slot``, the exact
        defect class being fixed).
        """
        rows: list[dict[str, Any]] = []
        advertised: int | None = None
        page = 1
        for _ in range(MAX_CATALOG_PAGES):
            payload = self._ok(self._request("GET", endpoint, params={**params, "page": page}))
            if not (isinstance(payload, dict) and "results" in payload):
                # Non-paginated payload (raw list) — nothing to walk.
                return _as_rows(payload)
            batch = [r for r in payload.get("results") or [] if isinstance(r, dict)]
            if advertised is None:
                count = payload.get("count")
                try:
                    advertised = int(count) if count is not None else None
                except (TypeError, ValueError):
                    advertised = None
            rows.extend(batch)
            if not payload.get("next") or not batch:
                break
            page += 1
        else:
            # Page ceiling hit with ``next`` still set — treat as incomplete.
            raise BookingUnavailableError("catalog_incomplete")
        if advertised is not None and advertised != len(rows):
            logger.warning(
                "booking_client.catalog_incomplete endpoint=%s advertised=%d collected=%d",
                endpoint,
                advertised,
                len(rows),
            )
            raise BookingUnavailableError("catalog_incomplete")
        return rows

    def get_masters(self, *, specialist_id: str | None = None) -> list[AylaMaster]:
        if specialist_id:
            resp = self._request("GET", f"specialists/{specialist_id}/")
            payload = self._ok(resp)
            return [_master_from_wire(payload)] if isinstance(payload, dict) and payload else []
        resp = self._request("GET", "specialists/")
        return [_master_from_wire(r) for r in _as_rows(self._ok(resp))]

    def get_available_times(
        self,
        *,
        specialist_id: str,
        date: str,
        service_id: str,
    ) -> list[AylaSlot]:
        # #1051: Ayla's slots action REQUIRES service_id — without it the
        # endpoint returns 400 MISSING_PARAM. Fail fast with a clear, non-
        # retryable error (BookingBadRequestError → YClientsAPIError) BEFORE the
        # HTTP call, so a service-less query never hits the wire (and, via the
        # get_available_dates fan-out, never repeats per day). NOTE: whether the
        # Ayla booking flow guarantees service-before-slots or needs a
        # service-less fallback is decided in #1016 — this guard only makes the
        # service-less case fail cleanly, it does not itself order the flow.
        if not service_id:
            raise BookingBadRequestError("service_id_required")

        cache_key = _slots_cache_key(specialist_id, service_id, date)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        params: dict[str, Any] = {"date": date, "service_id": service_id}
        resp = self._request("GET", f"specialists/{specialist_id}/slots/", params=params)
        payload = self._ok(resp)
        slots = payload.get("slots") if isinstance(payload, dict) else payload
        result = [_slot_from_wire(s) for s in slots] if isinstance(slots, list) else []
        cache.set(cache_key, result, SLOT_CACHE_TTL_S)
        return result

    def get_available_dates(
        self,
        *,
        specialist_id: str,
        service_id: str,
        window_days: int = AVAILABLE_DATES_WINDOW_DAYS,
    ) -> list[str]:
        """Derive the free-day calendar by fanning out over a date window.

        Ayla exposes no range endpoint; the single-day ``slots`` action is
        called per day and days with at least one slot are returned. The S2
        slot cache keeps the fan-out cheap.

        #1051: ``service_id`` is REQUIRED — validated once up front so a
        service-less request fails immediately (one clear BookingBadRequestError,
        zero HTTP) rather than entering the per-day fan-out loop at all.

        DRF-997: failures during the fan-out are propagated instead of being
        silently dropped from the calendar (no "error day" == "busy day").
        """
        if not service_id:
            raise BookingBadRequestError("service_id_required")

        cache_key = _dates_cache_key(specialist_id, service_id, window_days)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        today = date_cls.today()
        out: list[str] = []
        clamped_window = min(max(window_days, 0), MAX_AVAILABLE_DATES_WINDOW_DAYS)
        for offset in range(clamped_window):
            day = (today + timedelta(days=offset)).isoformat()
            # get_available_times is cached; any failure raises and stops the
            # fan-out so we never present a partial/error result as "no slots".
            times = self.get_available_times(
                specialist_id=specialist_id, date=day, service_id=service_id
            )
            if times:
                out.append(day)
        cache.set(cache_key, out, SLOT_CACHE_TTL_S)
        return out

    # ── writes ───────────────────────────────────────────────────────────────

    def create_appointment(
        self,
        *,
        external_user_id: str,
        client_id: str,
        specialist_id: str,
        service_id: str,
        start_datetime: str,
        idempotency_key: str | None = None,
        payment_required: bool = True,
    ) -> AylaBookingRecord:
        # AMD-002 (D6): payment_required=false → запись без предоплаты,
        # Ayla подтверждает сразу (CONFIRMED + booking.confirmed), Payment
        # не создаётся. default true — обратная совместимость контракта.
        body = {
            "client_id": client_id,
            "specialist_id": specialist_id,
            "service_id": service_id,
            "start_datetime": start_datetime,
            "payment_required": payment_required,
        }
        resp = self._request(
            "POST",
            "appointments/",
            external_user_id=external_user_id,
            json_body=body,
            idempotency_key=idempotency_key,
        )
        data = self._ok(resp, success=(200, 201))
        # DRF-997: a successful write may consume the slot we cached, so
        # drop the affected date(s) from the short-lived slot/dates cache.
        _invalidate_slot_date_caches(
            specialist_id=specialist_id,
            service_id=service_id,
            dates=[_iso_date(start_datetime)],
            window_days=AVAILABLE_DATES_WINDOW_DAYS,
        )
        return AylaBookingRecord(
            appointment_id=str(data.get("id") or "") if isinstance(data, dict) else "",
            raw=data if isinstance(data, dict) else {},
        )

    def cancel_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        idempotency_key: str | None = None,
        specialist_id: str | None = None,
        service_id: str | None = None,
        date: str | None = None,
    ) -> bool:
        resp = self._request(
            "POST",
            f"appointments/{appointment_id}/cancel/",
            external_user_id=external_user_id,
            json_body={},
            idempotency_key=idempotency_key,
        )
        now = time.monotonic()
        if resp.status_code in (200, 204):
            self._circuit.record_success()
            succeeded = True
        elif resp.status_code == 404:
            # Already gone — idempotent from the caller's perspective.
            self._circuit.record_success()
            succeeded = False
        else:
            self._fail_status(resp, now=now)

        # DRF-997: the cancelled slot is free again; invalidate it when the
        # caller supplies enough context to build the cache key.
        if specialist_id and service_id and date:
            _invalidate_slot_date_caches(
                specialist_id=specialist_id,
                service_id=service_id,
                dates=[date],
                window_days=AVAILABLE_DATES_WINDOW_DAYS,
            )
        return succeeded

    def reschedule_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        new_start_datetime: str,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        specialist_id: str | None = None,
        service_id: str | None = None,
        old_date: str | None = None,
    ) -> AylaBookingRecord:
        json_body: dict[str, Any] = {"new_start_datetime": new_start_datetime}
        if expected_version is not None:
            json_body["expected_version"] = expected_version
        resp = self._request(
            "POST",
            f"appointments/{appointment_id}/reschedule/",
            external_user_id=external_user_id,
            json_body=json_body,
            idempotency_key=idempotency_key,
        )
        data = self._ok(resp, success=(200, 201))
        # DRF-997: both the old and new dates may have changed occupancy.
        if specialist_id and service_id:
            dates = {d for d in (_iso_date(new_start_datetime), old_date) if d}
            _invalidate_slot_date_caches(
                specialist_id=specialist_id,
                service_id=service_id,
                dates=sorted(dates),
                window_days=AVAILABLE_DATES_WINDOW_DAYS,
            )
        # Native reschedule preserves the canonical appointment id; fall back
        # to the id we moved if the body omits it.
        return AylaBookingRecord(
            appointment_id=str(data.get("id") or appointment_id)
            if isinstance(data, dict)
            else appointment_id,
            raw=data if isinstance(data, dict) else {},
        )

    def create_specialist_time_off(
        self,
        *,
        specialist_id: str,
        tenant_id: str,
        start_at: str,
        end_at: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Block a specialist's time in Ayla (DRF-1062).

        Ayla owns the schedule, so an approved day-off has to land there:
        written into the bot's own store it would change nothing a
        customer can see, while telling the administrator it worked.

        ``tenant_id`` is mandatory — the route treats it as a claim and
        404s if the specialist does not belong to it, so a leaked bearer
        cannot block time across tenants by iterating specialist ids.

        Raises :class:`ScheduleBlockConflictError` when live bookings
        overlap the period, which is a decision for a human rather than a
        retry: somebody is booked into the time being closed.
        """
        resp = self._request(
            "POST",
            f"specialists/{specialist_id}/time-off/",
            json_body={
                "tenant_id": tenant_id,
                "start_at": start_at,
                "end_at": end_at,
                "reason": reason,
            },
        )
        if resp.status_code == 409:
            # Distinct from a generic 4xx: the request was well-formed and
            # the caller is allowed — the time simply is not free.
            raise ScheduleBlockConflictError("has_active_appointments")
        return self._ok(resp, success=(200, 201))

    def get_specialist_service_edges(
        self,
        *,
        specialist_id: str,
        service_id: str,
    ) -> list[dict[str, Any]]:
        """Active bookable edges for ONE (specialist, salon service) pair.

        Narrow on purpose, and — since DRF-1019 removed ``get_services``'s
        unreachable per-specialist branch — the ONLY reader of
        ``catalog/specialist-services/`` on this client. The removed branch
        walked the whole tenant edge list, needed an active tenant scope, and
        kept only the ids: the row's own ``price`` (the amount Ayla stamps
        onto a new appointment) was dropped. The quote (DRF-1067) and repeat
        need that price and work on the tenant-less global path, so they ask
        for the single row instead of filtering a catalog.

        No tenant filter: ``(specialist, salon_service)`` is unique upstream
        (``specialistservice_specialist_salon_uniq``), so the pair already
        names at most one row.
        """
        return self._get_all_rows(
            "catalog/specialist-services/",
            params={
                "specialist": specialist_id,
                "salon_service": service_id,
                "is_active": "true",
            },
        )

    def get_user_bookings_page(
        self,
        *,
        external_user_id: str,
        section: str = "upcoming",
        limit: int | None = None,
        cursor: str | None = None,
    ) -> AylaBookingPage:
        """One page of the customer's bookings (DRF-1032).

        ``section`` is ALWAYS sent. Omitting it makes the backend default to
        ``upcoming`` (``records_api.py:295``), so a history read would silently
        answer with future bookings — a wrong answer, not an error.

        Note what ``section="history"`` means upstream: terminal statuses OR
        anything already in the past (``records_api.py:331-337``). Cancellations,
        no-shows and stale ``confirmed`` rows all land here. Selecting the
        visits that actually happened is the CALLER's job, via
        ``AylaUserRecord.derived_status`` — this client does not editorialise.
        """
        if section not in _ME_BOOKINGS_SECTIONS:
            raise ValueError(
                f"section must be one of {sorted(_ME_BOOKINGS_SECTIONS)}, got {section!r}"
            )
        params: dict[str, Any] = {"section": section}
        if limit is not None:
            try:
                # Not clamped to the backend's MAX_LIMIT: the ceiling is Ayla's
                # rule to own (it silently caps at 100, ``records_api.py:308``),
                # and duplicating it here would drift the day it changes.
                params["limit"] = int(limit)
            except (TypeError, ValueError):
                raise ValueError(f"limit must be an integer, got {limit!r}") from None
        if cursor:
            params["cursor"] = cursor
        resp = self._request(
            "GET", "me/bookings/", external_user_id=external_user_id, params=params
        )
        payload = self._ok(resp)
        # Canonical shape after the ``{"data": ...}`` envelope is stripped:
        # ``{"items": [...], "next_cursor": "<iso>"|null}``. A bare list is
        # tolerated defensively.
        #
        # ANY other shape is an outage, not an empty result. That includes the
        # ``{upcoming, history}`` shape this client used to expect: returning
        # ``[]`` for an unrecognised body is exactly the failure this change
        # exists to remove — the customer is told "you have no bookings" while
        # the backend is answering with data, and nothing anywhere goes red.
        # Same reasoning as ``_ok`` applies to unparseable JSON: a 200 we
        # cannot read must never be silently read as "empty".
        next_cursor: str | None = None
        if isinstance(payload, list):
            rows = [r for r in payload if isinstance(r, dict)]
        elif isinstance(payload, dict) and isinstance(payload.get("items"), list):
            raw_items = payload["items"]
            rows = [r for r in raw_items if isinstance(r, dict)]
            raw_cursor = payload.get("next_cursor")
            next_cursor = str(raw_cursor) if raw_cursor else None
        else:
            logger.warning(
                "booking_client.me_bookings_unexpected_shape section=%s type=%s keys=%s",
                section,
                type(payload).__name__,
                sorted(payload)[:10] if isinstance(payload, dict) else None,
            )
            raise BookingUnavailableError("malformed_response")
        return AylaBookingPage(
            records=[_user_record_from_wire(r) for r in rows],
            next_cursor=next_cursor,
        )

    def get_user_appointments(
        self,
        *,
        external_user_id: str,
        section: str = "upcoming",
        limit: int | None = None,
        cursor: str | None = None,
    ) -> list[AylaUserRecord]:
        """Records of one page, cursor dropped — the ``AylaBookingClient`` seam.

        The defaults keep ``AylaYClientsAdapter.get_user_records``
        (``provider.py:273-276``) reading upcoming bookings and receiving a
        plain list, exactly as before.

        Worth stating precisely, because it explains how the parsing defect
        survived: with ``BOOKING_VIA_AYLA_REST`` **on** — the pilot
        configuration, and the only one in which this adapter is selected —
        that method has no reachable caller at all. Both call sites branch
        away first (``tools.py:1698-1703`` to ``_proxy_master_service``,
        ``tools.py:2710-2712`` to ``_show_my_bookings_ayla``). So "unchanged
        behaviour" here means the seam is preserved for the flag-off path and
        for tests, not that live traffic depends on it.

        The methods DRF-1032 adds (``get_user_bookings_page``,
        ``get_booking_detail``, ``get_repeat_intent``) are deliberately absent
        from the ``AylaBookingClient`` Protocol: widening it would break the
        typed fakes in suites this change is scoped out of. A later change that
        wants records through the seam has to widen the Protocol and update
        those fakes together.
        """
        return self.get_user_bookings_page(
            external_user_id=external_user_id,
            section=section,
            limit=limit,
            cursor=cursor,
        ).records

    def get_booking_detail(
        self,
        *,
        external_user_id: str,
        booking_id: str,
    ) -> AylaUserRecord:
        """One booking's full card (``GET me/bookings/{id}/``).

        The response carries more than a customer should see — ``notes``,
        ``cancellation_reason``, payment rows, tenant ids. They stay in ``raw``;
        picking the customer-relevant subset is the display layer's job.
        A booking belonging to someone else answers 404, identically to one
        that does not exist (info-hidden), and surfaces as a 4xx error here.
        """
        resp = self._request("GET", f"me/bookings/{booking_id}/", external_user_id=external_user_id)
        payload = self._ok(resp)
        if not isinstance(payload, dict):
            # Same rule as the list read: a 200 we cannot read is an outage,
            # not an empty card. Building a record out of ``{}`` would show the
            # customer a visit with no service, no master and no date.
            logger.warning(
                "booking_client.me_booking_detail_unexpected_shape type=%s",
                type(payload).__name__,
            )
            raise BookingUnavailableError("malformed_response")
        return _user_record_from_wire(payload)

    def get_appointment_version(
        self,
        *,
        external_user_id: str,
        booking_id: str,
    ) -> AylaAppointmentVersion:
        """The canonical `version` of one booking (``GET appointments/{id}/``).

        DRF-1233. Exists because Ayla requires ``expected_version`` on every
        reschedule and closure — deliberately, since it is what stops two
        people acting on the same booking with the second silently winning —
        and the bot had nowhere to read it. The day journal here is built
        from ``RemoteBookingProxy``, whose
        ``last_applied_appointment_version`` is NULL unless a canonical
        ``appointment.rescheduled`` event happened to be applied; measured on
        the pilot 2026-08-21, two of twenty-three mirrored bookings carried a
        version and the single future confirmed booking carried none.

        The value must reach the write **through the operator**: read it, show
        the operator what it describes, and send back what they saw. Reading
        it inside the write instead would make the guard unfireable by
        construction — the same defect DRF-1232 fixed, where a fresh
        idempotency key was invented per request and a unique constraint
        stood but never triggered.

        A booking this actor may not see answers 404 identically to one that
        does not exist (info-hidden upstream), surfacing here as a 4xx.
        """
        resp = self._request(
            "GET", f"appointments/{booking_id}/", external_user_id=external_user_id
        )
        payload = self._ok(resp)
        if not isinstance(payload, dict):
            # A 200 we cannot read is an outage, not «no version». Returning
            # a default would send a guess into a concurrency guard.
            logger.warning(
                "booking_client.appointment_version_unexpected_shape type=%s",
                type(payload).__name__,
            )
            raise BookingUnavailableError("malformed_response")
        try:
            version = int(payload["version"])
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "booking_client.appointment_version_missing payload_keys=%s",
                sorted(payload) if isinstance(payload, dict) else "?",
            )
            raise BookingUnavailableError("version_missing") from exc
        return AylaAppointmentVersion(
            id=str(payload.get("id") or booking_id),
            version=version,
            status=str(payload.get("status") or ""),
            start_datetime=str(payload.get("start_datetime") or ""),
        )

    def get_repeat_intent(
        self,
        *,
        external_user_id: str,
        booking_id: str,
    ) -> AylaRepeatIntent:
        """Prefill for «Записаться ещё» (``POST me/bookings/{id}/repeat-intent/``).

        Guards the known upstream defect (DRF-1049): the endpoint reads only the
        marketplace ``service_id`` and ignores the salon one, even though the two
        are XOR at the schema level. For a salon booking — every booking the bot
        creates — it answers HTTP 200 carrying the literal string ``"None"``.
        Both ids are therefore validated as UUIDs, and a malformed one raises
        :class:`RepeatIntentUnusableError` instead of travelling further: a
        caller that got an object back may trust its ids.
        """
        resp = self._request(
            "POST", f"me/bookings/{booking_id}/repeat-intent/", external_user_id=external_user_id
        )
        payload = self._ok(resp)
        data = payload if isinstance(payload, dict) else {}
        ids: dict[str, str] = {}
        for field_name in ("service_id", "specialist_id"):
            value = str(data.get(field_name) or "")
            try:
                # Normalised: ``uuid.UUID`` also accepts braced and dash-less
                # forms, and passing those through unchanged would hand the
                # booking flow an id shaped differently from every other one.
                ids[field_name] = str(uuid.UUID(value))
            except ValueError:
                logger.warning(
                    "booking_client.repeat_intent_unusable booking_id=%s field=%s value=%r",
                    booking_id,
                    field_name,
                    value,
                )
                raise RepeatIntentUnusableError(field_name, value=value) from None
        slots = data.get("suggested_slots")
        return AylaRepeatIntent(
            service_id=ids["service_id"],
            specialist_id=ids["specialist_id"],
            last_price=_parse_price(data.get("last_price")),
            # Only strings: the field is typed as ISO-8601 slots, and
            # stringifying whatever arrives would put ``"None"`` or
            # ``"{'a': 1}"`` into a list the booking flow reads as timestamps.
            suggested_slots=[s for s in slots if isinstance(s, str)]
            if isinstance(slots, list)
            else [],
            raw=data,
        )


def _err_code(resp: httpx.Response) -> str:
    """Pull the ``error.code`` from a 4xx body, best-effort."""
    try:
        return (resp.json().get("error") or {}).get("code", "") or "unknown"
    except (ValueError, AttributeError):
        return "unknown"


def _parse_retry_after(value: str | None) -> float:
    """Parse a Retry-After header.

    First tries an integer/decimal second delta. If that fails, tries an
    HTTP-date string (e.g. ``Retry-After: Wed, 21 Oct 2025 07:28:00 GMT``)
    and returns the seconds until that instant. Non-parseable values fall
    back to 0.0 so the caller uses exponential backoff.
    """
    if not value:
        return 0.0
    try:
        return max(float(value), 0.0)
    except (ValueError, TypeError):
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz.utc)
        delta = when.timestamp() - time.time()
        return max(delta, 0.0)
    except (ValueError, TypeError, OverflowError):
        # Non-parseable values are treated as "no hint".
        return 0.0


def _tenant_id_for_cache() -> str:
    """Return the current tenant id for cache-key scoping, or a sentinel.

    Lazy-imported to avoid a circular import: ``apps.tenancy.context`` is
    cross-cutting and this module is imported from booking provider/tests.
    """
    from apps.tenancy.context import current_tenant

    tenant = current_tenant()
    return str(tenant.id) if tenant is not None else "_none_"


def _require_tenant_id() -> str:
    """Active tenant id for catalog scoping — or a loud call error.

    Unlike :func:`_tenant_id_for_cache` (cache keys, where a sentinel is
    harmless), a catalog read without a tenant must not degrade to anything
    silent: an unscoped/empty catalog is exactly the DRF-1004 defect.
    """
    from apps.tenancy.context import current_tenant

    tenant = current_tenant()
    if tenant is None:
        raise BookingBadRequestError("tenant_scope_required")
    return str(tenant.id)


def _slots_cache_key(specialist_id: str, service_id: str, date: str) -> str:
    """Cache key for a single day's slots.

    Isolation invariant: the key is scoped by ``tenant.id`` *and* by the
    specialist/service/date tuple. On the Ayla path those identifiers are
    UUIDs, so two tenants that happen to share a numeric-looking YClients id
    cannot collide. The ``_none_`` sentinel keeps the key deterministic when
    no tenant context is active (tests / management commands).
    """
    tenant_id = _tenant_id_for_cache()
    return f"{SLOT_CACHE_KEY_PREFIX}:times:{tenant_id}:{specialist_id}:{service_id}:{date}"


def _dates_cache_key(specialist_id: str, service_id: str, window_days: int) -> str:
    """Cache key for the free-day calendar over a window.

    Same tenant-scoping invariant as :func:`_slots_cache_key`.
    """
    tenant_id = _tenant_id_for_cache()
    return f"{SLOT_CACHE_KEY_PREFIX}:dates:{tenant_id}:{specialist_id}:{service_id}:{window_days}"


def _invalidate_slot_date_caches(
    *,
    specialist_id: str,
    service_id: str,
    dates: list[str],
    window_days: int | None = None,
) -> None:
    """DRF-997: drop stale slot/dates cache entries after a write.

    ``dates`` are the ISO ``YYYY-MM-DD`` strings whose per-day slot cache
    may now be wrong. ``window_days`` controls which dates-cache key is
    cleared; callers commonly use :data:`AVAILABLE_DATES_WINDOW_DAYS`.
    Other window sizes may leave stale entries — that is accepted because
    the cache TTL is short (60 s) and non-default windows are rare.
    """
    for day in dates:
        cache.delete(_slots_cache_key(specialist_id, service_id, day))
    if window_days is not None:
        cache.delete(_dates_cache_key(specialist_id, service_id, window_days))


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
