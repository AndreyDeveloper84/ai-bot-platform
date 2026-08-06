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

import logging
import time
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta
from typing import Any, NoReturn, Protocol, runtime_checkable

import httpx
from django.conf import settings

from apps.integrations.ayla.url_builder import AylaUrlBuilder


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0
# Policy aligned with the platform CR-3 breaker and the nutrition client:
# 5 failures in 60s → 30s cooldown.
CIRCUIT_FAILURE_WINDOW_S = 60.0
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_DURATION_S = 30.0
_BREAKER_NAME = "ayla.booking"

# Ayla's internal slots endpoint is single-day; ``get_available_dates`` fans
# out over this many days from today to derive the "which days are free"
# calendar the booking tools expect (S1 decision — no range endpoint exists).
AVAILABLE_DATES_WINDOW_DAYS = 14

# S5-LOW2: hard ceiling on the fan-out. ``get_available_dates`` issues one
# HTTP call per day, so an oversized ``window_days`` (caller bug, future
# config drift) would hammer Ayla and stall the user. Clamp defensively.
MAX_AVAILABLE_DATES_WINDOW_DAYS = 31


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

    5xx / timeout / network / circuit-open. **Trips** the breaker.
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
    """An existing appointment from ``get_user_appointments`` (me/bookings)."""

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
    :class:`AylaBookingHTTPClient` is the production implementation.

    Reads take no ``external_user_id`` — catalog/slots are tenant-public.
    Writes require it so Ayla can bind the action to the consenting client.
    Catalog ids are Ayla UUIDs (strings).
    """

    def get_services(self, *, specialist_id: str | None = ...) -> list[AylaService]: ...

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
    ) -> bool: ...

    def reschedule_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        new_start_datetime: str,
        expected_version: int | None = ...,
        idempotency_key: str | None = ...,
    ) -> AylaBookingRecord: ...

    def get_user_appointments(
        self,
        *,
        external_user_id: str,
    ) -> list[AylaUserRecord]: ...


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
    price = float(d.get("price") or 0.0)
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
        :class:`BookingUnavailableError`; status handling is the caller's."""
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise BookingUnavailableError("circuit_open")

        url = self._urls.build(f"internal/{endpoint.lstrip('/')}")
        headers = self._headers(external_user_id=external_user_id)
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        try:
            http = self._client()
            resp = http.request(method, url, headers=headers, params=params, json=json_body)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            logger.warning("booking_client.%s.network err=%s", endpoint, type(exc).__name__)
            raise BookingUnavailableError(f"network: {type(exc).__name__}") from exc
        return resp

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
        4xx→BadRequest (no trip)."""
        now = time.monotonic()
        if resp.status_code in success:
            self._circuit.record_success()
            try:
                return _unwrap(resp.json())
            except ValueError:
                return {}
        self._fail_status(resp, now=now)

    # ── reads ────────────────────────────────────────────────────────────────

    def get_services(self, *, specialist_id: str | None = None) -> list[AylaService]:
        endpoint = f"specialists/{specialist_id}/services/" if specialist_id else "services/"
        resp = self._request("GET", endpoint)
        return [_service_from_wire(r) for r in _as_rows(self._ok(resp))]

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
        params: dict[str, Any] = {"date": date, "service_id": service_id}
        resp = self._request("GET", f"specialists/{specialist_id}/slots/", params=params)
        payload = self._ok(resp)
        slots = payload.get("slots") if isinstance(payload, dict) else payload
        return [_slot_from_wire(s) for s in slots] if isinstance(slots, list) else []

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
        """
        if not service_id:
            raise BookingBadRequestError("service_id_required")
        today = date_cls.today()
        out: list[str] = []
        clamped_window = min(max(window_days, 0), MAX_AVAILABLE_DATES_WINDOW_DAYS)
        for offset in range(clamped_window):
            day = (today + timedelta(days=offset)).isoformat()
            if self.get_available_times(
                specialist_id=specialist_id, date=day, service_id=service_id
            ):
                out.append(day)
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
            return True
        if resp.status_code == 404:
            # Already gone — idempotent from the caller's perspective.
            self._circuit.record_success()
            return False
        self._fail_status(resp, now=now)

    def reschedule_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        new_start_datetime: str,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
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
        # Native reschedule preserves the canonical appointment id; fall back
        # to the id we moved if the body omits it.
        return AylaBookingRecord(
            appointment_id=str(data.get("id") or appointment_id)
            if isinstance(data, dict)
            else appointment_id,
            raw=data if isinstance(data, dict) else {},
        )

    def get_user_appointments(
        self,
        *,
        external_user_id: str,
    ) -> list[AylaUserRecord]:
        resp = self._request("GET", "me/bookings/", external_user_id=external_user_id)
        payload = self._ok(resp)
        # me/bookings groups into {upcoming, history}; cancel/reschedule act on
        # upcoming, but expose both so the tools can match any handle.
        rows: list[dict[str, Any]] = []
        if isinstance(payload, dict) and ("upcoming" in payload or "history" in payload):
            for key in ("upcoming", "history"):
                section = payload.get(key) or []
                if isinstance(section, list):
                    rows.extend(r for r in section if isinstance(r, dict))
        else:
            rows = _as_rows(payload)
        return [_user_record_from_wire(r) for r in rows]


def _err_code(resp: httpx.Response) -> str:
    """Pull the ``error.code`` from a 4xx body, best-effort."""
    try:
        return (resp.json().get("error") or {}).get("code", "") or "unknown"
    except (ValueError, AttributeError):
        return "unknown"


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
