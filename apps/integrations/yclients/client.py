"""HTTP client for the YClients booking API.

Phase 1 / B1 (DRF-837). Ported from ``mysite/services_app/yclients_api.py``
(prod-validated, ~10 months on Формула-Тела). The mysite source had grown to
~900 LOC of inline logging + transitional helpers; this port keeps the seven
booking endpoints the platform actually needs and adopts the
``apps.integrations.ayla.nutrition_client`` skeleton (Sprint 9 / I1 / DRF-825)
for parity with the rest of the integrations package.

Adjustments from the source:

* Transport: ``requests`` + ``requests.Session`` + ``urllib3.util.retry.Retry``
  (3 retries, 0.5s backoff, 502/503/504). YClients lives behind a WAF that
  occasionally bounces requests with 503; the urllib3 retry policy was tuned
  against real prod traffic, so the port keeps it instead of switching to
  ``httpx`` like ``nutrition_client``. DRF-837 acceptance criterion 3.
* WAF-bypass headers preserved verbatim: ``User-Agent`` + ``X-Partner-Id:
  11958``. Without them YClients returns 403 — discovered the hard way in
  mysite (June 2025). DO NOT touch these values without a prod canary.
* Circuit breaker: inline ``_Circuit`` matching ``nutrition_client`` — 5
  failures / 60s window / 30s open. Telegram alert path borrowed from CR-3
  (lazy import so this module is safe to import without Django at boot).
* Logger: dropped the ~70 emoji-prefixed INFO lines. ``logger.debug`` /
  ``logger.warning`` carry the same signal at production levels.
* Removed: ``authenticate`` / ``from_credentials`` (interactive login flow —
  out of scope for the bot; tokens are operator-provided), ``get_staff_services``,
  ``get_book_dates`` (renamed to ``get_available_dates``), ``find_client_by_phone``
  (privacy-sensitive, deferred until handlers need it), the abandoned
  ``get_available_times_alternative`` dead code.

Settings glue (``config/settings/base.py``):

* ``YCLIENTS_PARTNER_TOKEN`` — operator-issued partner token (Bearer).
* ``YCLIENTS_USER_TOKEN``    — operator-issued user token (User auth).
* ``YCLIENTS_COMPANY_ID``    — salon company id.
* ``YCLIENTS_BASE_URL``      — defaults to ``https://api.yclients.com/api/v1``.

Empty defaults ship the integration dormant; ``get_yclients_client()`` raises
``ValueError`` on first use when env is missing. Per-tenant credential storage
on ``Tenant`` is a follow-up (requires a migration + crypto helper); the
B1 ticket spec calls this out explicitly.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests  # type: ignore[import-untyped]
from django.conf import settings
from requests.adapters import HTTPAdapter  # type: ignore[import-untyped]
from urllib3.util.retry import Retry


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_BASE_URL = "https://api.yclients.com/api/v1"

# YClients retro Y11: PII redaction patterns for 4xx response previews.
# YClients error bodies routinely echo the submitted phone / email
# back («Invalid phone: +7903…», «Email already used: x@y.z»), and we
# log those previews + propagate them into ``YClientsAPIError`` messages
# that bubble up the call chain into Sentry / ELK without the
# eventbus §6 PII walker getting a look.
#
# Phone: international ``+`` prefix OR Russian-format leading ``7`` / ``8``
# at a word boundary, then 7+ digits/separators. The eventbus §6 PII
# walker uses a conservative ``+``-only pattern because event payloads
# carry structured fields; here we redact log-line free-text where
# YClients tenant-traffic phones routinely arrive as ``8 999 123 45 67``
# without a ``+`` (mixed-format reality acknowledged in the
# ``_find_bot_user`` docstring). Closes reviewer Y11-1.
# Email: standard local@host.tld.
_PII_PHONE_RE = re.compile(r"(?:\+\d|\b[78])\d[\d\s\-\(\)]{7,}")
_PII_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")


def _redact_pii(text: str) -> str:
    """Replace phone / email patterns with `[REDACTED:phone]` / `[REDACTED:email]`.

    Best-effort: anchored on shape, not exhaustive. The goal is to stop
    YClients-echoed PII from landing in log aggregators or exception
    messages, not to certify the string is PII-free for downstream
    consumers.
    """

    redacted = _PII_PHONE_RE.sub("[REDACTED:phone]", text)
    redacted = _PII_EMAIL_RE.sub("[REDACTED:email]", redacted)
    return redacted


# Match nutrition_client's CR-3-aligned breaker policy.
CIRCUIT_FAILURE_WINDOW_S = 60.0
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_DURATION_S = 30.0
_BREAKER_NAME = "yclients.api"


def _fire_breaker_alert(transition: str, failures: int) -> None:
    """Borrow the CR-3 Telegram alert path on a breaker state transition.

    Lazy-imports the alert helper — keeps this module free of Django coupling
    at import time AND matches the alert-path principle: alerting is forensic,
    never on the critical path. Wraps all exceptions so a slow/broken Telegram
    channel can never block the breaker.
    """
    try:
        from apps.orchestrator.llm.telegram_alert import send_breaker_alert

        send_breaker_alert(
            provider=_BREAKER_NAME,
            transition=transition,
            details={"failures": failures},
        )
    except Exception:  # noqa: BLE001 — alerting MUST NEVER break the breaker
        logger.exception("yclients_client.alert_failed transition=%s", transition)


@dataclass
class _Circuit:
    """Tiny in-process circuit breaker, copied from ``nutrition_client``.

    Not thread-safe across worker processes — each worker tracks its own
    failures. If YClients drops, every worker independently opens its own
    breaker within seconds; no shared state is needed.
    """

    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def is_open(self, *, now: float) -> bool:
        if self.opened_at is None:
            return False
        if now - self.opened_at >= CIRCUIT_OPEN_DURATION_S:
            # Approximate half-open: clear state, let the next call probe.
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
                "yclients_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures),
                CIRCUIT_FAILURE_WINDOW_S,
            )
            _fire_breaker_alert("closed → open", len(self.failures))

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


# ─── exceptions ────────────────────────────────────────────────────────────


class YClientsAPIError(Exception):
    """Base for YClients client-visible failures (4xx, malformed JSON, etc.)."""


class YClientsUnavailableError(YClientsAPIError):
    """YClients is unreachable or the circuit is open — caller shows fallback."""


# ─── DTOs ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Service:
    """A bookable service offered by the salon."""

    id: int
    title: str
    price_min: float
    price_max: float
    duration_s: int
    category_id: int | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Staff:
    """A member of staff who can take bookings."""

    id: int
    name: str
    specialization: str
    rating: float
    avatar: str
    position: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DateSlot:
    """A single bookable date (``YYYY-MM-DD``)."""

    date: str  # ISO date


@dataclass(frozen=True)
class AvailableTime:
    """A bookable time slot for a staff member on a given date."""

    time: str  # ``HH:MM``
    datetime: str | None  # ISO ``YYYY-MM-DDTHH:MM:SS+ZZ:ZZ`` if YClients sent one
    seance_length_s: int | None  # session duration in seconds, if known


@dataclass(frozen=True)
class BookingRecord:
    """Result of ``create_record``. ``raw`` carries everything YClients sent."""

    record_id: int
    record_hash: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UserRecord:
    """A row from ``get_user_records`` — a client's existing booking."""

    id: int
    services: list[dict[str, Any]]
    company: dict[str, Any]
    staff: dict[str, Any]
    date: str  # ISO datetime
    datetime: str
    seance_length: int
    raw: dict[str, Any] = field(default_factory=dict)


# ─── client ────────────────────────────────────────────────────────────────


class YClientsAPI:
    """YClients HTTP client. One instance shared per process; circuit state local.

    Construct via :func:`get_yclients_client` — module-level singleton that
    reads ``settings.YCLIENTS_*``. Tests reset via :func:`reset_yclients_client`.
    """

    def __init__(
        self,
        *,
        partner_token: str,
        user_token: str,
        company_id: str,
        base_url: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not partner_token:
            raise ValueError("YCLIENTS_PARTNER_TOKEN is empty — client cannot start")
        if not user_token:
            raise ValueError("YCLIENTS_USER_TOKEN is empty — client cannot start")
        if not company_id:
            raise ValueError("YCLIENTS_COMPANY_ID is empty — client cannot start")

        self.partner_token = partner_token
        self.user_token = user_token
        self.company_id = str(company_id)
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout_s = timeout_s
        self._circuit = _Circuit()

        # WAF-bypass headers — DO NOT touch without a prod canary.
        self._headers = {
            "Accept": "application/vnd.yclients.v2+json",
            "Authorization": (f"Bearer {self.partner_token}, User {self.user_token}"),
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "X-Partner-Id": "11958",
        }

        # Session reuses TCP+TLS; urllib3 Retry handles WAF transient 5xx.
        #
        # YClients retro B2: ``allowed_methods`` is intentionally limited
        # to idempotent verbs (GET / HEAD / OPTIONS). Pre-fix POST / PUT /
        # DELETE were also retried; a successful YClients write followed
        # by a transient 5xx on the response leg would re-POST and
        # silently create a duplicate booking with no compensate path.
        # Per HTTP semantics, non-idempotent verbs must not be retried
        # without an idempotency key the upstream understands — YClients
        # has no such header, so we treat ``YClientsUnavailableError`` on
        # a write as «unknown outcome, caller schedules reconciliation»
        # rather than «failed».
        self._session = requests.Session()
        self._session.headers.update(self._headers)
        retry = Retry(
            total=3,
            backoff_factor=0.5,  # 0.5s, 1s, 2s
            status_forcelist=[502, 503, 504],
            allowed_methods=["GET", "HEAD", "OPTIONS"],
            raise_on_status=False,
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._session.mount("http://", HTTPAdapter(max_retries=retry))

    # ─── low-level request ────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP request through the breaker + session retry layer.

        Returns the full decoded JSON envelope (``{success, data, meta}``).
        ``data`` extraction is left to each public method since YClients
        returns either a list, a dict, or a dict-with-nested-list depending
        on the endpoint.
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise YClientsUnavailableError("circuit_open")

        url = f"{self._base_url}{endpoint}"
        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                timeout=self._timeout_s,
            )
        except requests.exceptions.Timeout as exc:
            self._circuit.record_failure(now=now)
            logger.warning("yclients_client.timeout method=%s url=%s", method, url)
            raise YClientsUnavailableError("timeout") from exc
        except requests.exceptions.ConnectionError as exc:
            self._circuit.record_failure(now=now)
            logger.warning(
                "yclients_client.connection_error method=%s url=%s",
                method,
                url,
            )
            raise YClientsUnavailableError("connection_error") from exc
        except requests.exceptions.RequestException as exc:
            self._circuit.record_failure(now=now)
            logger.warning(
                "yclients_client.request_error method=%s err=%s",
                method,
                type(exc).__name__,
            )
            raise YClientsUnavailableError(f"request_error: {type(exc).__name__}") from exc

        return self._parse_response(response, method=method, url=url)

    def _parse_response(
        self,
        response: requests.Response,
        *,
        method: str,
        url: str,
    ) -> dict[str, Any]:
        now = time.monotonic()
        status = response.status_code

        if status >= 500:
            self._circuit.record_failure(now=now)
            logger.warning(
                "yclients_client.5xx method=%s url=%s status=%d",
                method,
                url,
                status,
            )
            raise YClientsUnavailableError(f"http_{status}")

        if status >= 400:
            # Don't trip the breaker on 4xx — those are caller / business errors.
            # YClients retro Y11: redact PII patterns before logging /
            # surfacing the response body. YClients 4xx bodies often echo
            # the submitted phone / email back («Invalid phone: +79...»),
            # which would leak into INFO logs + into the exception
            # message that propagates up the stack. Redact before the
            # body touches either surface.
            body_preview = _redact_pii((response.text or "")[:300])
            logger.info(
                "yclients_client.4xx method=%s url=%s status=%d body=%s",
                method,
                url,
                status,
                body_preview,
            )
            raise YClientsAPIError(f"http_{status}: {body_preview}")

        try:
            payload = response.json()
        except ValueError as exc:
            self._circuit.record_failure(now=now)
            raise YClientsAPIError(f"invalid_json: {exc}") from exc

        if not isinstance(payload, dict):
            # YClients always returns an envelope dict; a bare list/string is
            # an upstream contract break — surface it loudly.
            raise YClientsAPIError(f"unexpected_payload_type: {type(payload).__name__}")

        self._circuit.record_success()
        return payload

    # ─── 1. services ──────────────────────────────────────────────────────

    def get_services(
        self,
        *,
        staff_id: int | None = None,
        category_id: int | None = None,
    ) -> list[Service]:
        """GET ``/company/{company_id}/services``. Optional staff / category filter."""
        params: dict[str, Any] = {}
        if staff_id is not None:
            params["staff_id"] = staff_id
        if category_id is not None:
            params["category_id"] = category_id

        payload = self._request(
            "GET",
            f"/company/{self.company_id}/services",
            params=params or None,
        )
        if not payload.get("success", False):
            logger.info("yclients_client.services.success_false meta=%s", payload.get("meta"))
            return []

        rows = payload.get("data") or []
        if not isinstance(rows, list):
            return []
        return [_to_service(row) for row in rows if isinstance(row, dict)]

    # ─── 2. staff ─────────────────────────────────────────────────────────

    def get_staff(self, *, staff_id: int | None = None) -> list[Staff]:
        """GET ``/company/{company_id}/staff``.

        Filters out inactive / hidden / fired members. Optional ``staff_id``
        narrows to a single member (YClients ignores the filter on the list
        endpoint, so we post-filter locally — keeps the API one method).
        """
        payload = self._request("GET", f"/company/{self.company_id}/staff")
        if not payload.get("success", False):
            logger.info("yclients_client.staff.success_false meta=%s", payload.get("meta"))
            return []

        rows = _extract_staff_rows(payload.get("data"))
        result: list[Staff] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if "active" in row and not row.get("active", True):
                continue
            if "bookable" in row and not row.get("bookable", True):
                continue
            if row.get("hidden", 0) == 1 or row.get("fired", 0) == 1:
                continue
            if staff_id is not None and row.get("id") != staff_id:
                continue
            result.append(_to_staff(row))
        return result

    # ─── 3. available dates ───────────────────────────────────────────────

    def get_available_dates(
        self,
        *,
        staff_id: int | None = None,
        service_ids: list[int] | None = None,
    ) -> list[str]:
        """GET ``/schedule/dates/{company_id}`` → list of ``YYYY-MM-DD`` strings.

        The mysite source uses ``/book_dates/{company_id}``; both endpoints
        live in YClients and return the same envelope. Spec ticket
        (DRF-837) calls out ``/schedule/dates/`` so we follow it — the
        envelope parsing handles either response shape.
        """
        params: dict[str, Any] = {}
        if staff_id is not None:
            params["staff_id"] = staff_id
        if service_ids:
            params["service_ids"] = ",".join(str(s) for s in service_ids)

        payload = self._request(
            "GET",
            f"/schedule/dates/{self.company_id}",
            params=params or None,
        )
        if not payload.get("success", False):
            return []

        dates = _extract_dates(payload.get("data"))
        dates.sort()
        return dates

    # ─── 4. available times ───────────────────────────────────────────────

    def get_available_times(
        self,
        *,
        staff_id: int,
        date: str,
        service_ids: list[int] | None = None,
    ) -> list[AvailableTime]:
        """GET ``/book_times/{company_id}/{staff_id}/{date}``."""
        params: dict[str, Any] = {}
        if service_ids:
            params["service_ids"] = service_ids

        payload = self._request(
            "GET",
            f"/book_times/{self.company_id}/{staff_id}/{date}",
            params=params or None,
        )
        if not payload.get("success", False):
            return []

        rows = payload.get("data") or []
        if not isinstance(rows, list):
            return []

        result: list[AvailableTime] = []
        for item in rows:
            if isinstance(item, str):
                result.append(AvailableTime(time=item, datetime=None, seance_length_s=None))
            elif isinstance(item, dict):
                slot = _to_available_time(item)
                if slot is not None:
                    result.append(slot)
        return result

    # ─── 5. create record ─────────────────────────────────────────────────

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
        """POST ``/book_record/{company_id}`` — create a booking.

        Body shape matches the YClients booking spec (verified in mysite):

        .. code-block:: python

            {
                "phone": "...",
                "fullname": "...",
                "email": "...",
                "appointments": [
                    {"id": 1, "services": [int, ...], "staff_id": int,
                     "datetime": "YYYY-MM-DDTHH:MM:SS"}
                ],
                "notify_by_sms": 0|1,
                "notify_by_email": 0|1,
                "comment": "..."  # optional
            }
        """
        body: dict[str, Any] = {
            "phone": client_phone,
            "fullname": client_name,
            "email": client_email,
            "appointments": [
                {
                    "id": 1,
                    "services": services,
                    "staff_id": staff_id,
                    "datetime": datetime,
                }
            ],
            "notify_by_sms": notify_by_sms,
            "notify_by_email": notify_by_email,
        }
        if comment:
            body["comment"] = comment

        payload = self._request(
            "POST",
            f"/book_record/{self.company_id}",
            json_body=body,
        )
        if not payload.get("success", False):
            error_msg = (payload.get("meta") or {}).get("message", "unknown")
            raise YClientsAPIError(f"create_record_failed: {error_msg}")

        rows = payload.get("data") or []
        if not rows or not isinstance(rows, list):
            raise YClientsAPIError("create_record_no_data")
        first = rows[0]
        if not isinstance(first, dict):
            raise YClientsAPIError("create_record_malformed_data")

        # YClients retro Y12: refuse to fabricate a zero record_id on
        # missing / malformed response. Pre-fix ``int(first.get(...) or 0)``
        # silently produced ``BookingRecord(record_id=0, ...)`` which
        # then flowed through callers — Hotfix D's compensating
        # ``cancel_record(record_id=0)`` would call YClients with a
        # garbage id, succeed-with-NOT_FOUND, and the original split-
        # brain would be hidden. Caller treats this as a YClients API
        # contract violation, NOT a transient error.
        raw_record_id: Any = first.get("record_id")
        if raw_record_id in (None, "", 0, "0"):
            raise YClientsAPIError("create_record_missing_record_id")
        try:
            record_id_int = int(raw_record_id)
        except (TypeError, ValueError) as exc:
            raise YClientsAPIError(f"create_record_malformed_record_id: {raw_record_id!r}") from exc

        return BookingRecord(
            record_id=record_id_int,
            record_hash=str(first.get("record_hash") or ""),
            raw=first,
        )

    # ─── 6. cancel record ─────────────────────────────────────────────────

    def cancel_record(self, *, record_id: int) -> bool:
        """DELETE ``/records/{company_id}/{record_id}``.

        Returns True on 2xx (record cancelled) or False on 404 (already gone).
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise YClientsUnavailableError("circuit_open")

        url = f"{self._base_url}/records/{self.company_id}/{record_id}"
        try:
            response = self._session.request(
                "DELETE",
                url,
                timeout=self._timeout_s,
            )
        except requests.exceptions.Timeout as exc:
            self._circuit.record_failure(now=now)
            raise YClientsUnavailableError("timeout") from exc
        except requests.exceptions.ConnectionError as exc:
            self._circuit.record_failure(now=now)
            raise YClientsUnavailableError("connection_error") from exc

        if 200 <= response.status_code < 300:
            self._circuit.record_success()
            return True
        if response.status_code == 404:
            self._circuit.record_success()
            return False
        if response.status_code >= 500:
            self._circuit.record_failure(now=now)
            raise YClientsUnavailableError(f"http_{response.status_code}")
        # 4xx other than 404 — caller / business error.
        body_preview = (response.text or "")[:300]
        raise YClientsAPIError(f"http_{response.status_code}: {body_preview}")

    # ─── 7. user records ──────────────────────────────────────────────────

    def get_user_records(self) -> list[UserRecord]:
        """GET ``/user/records`` — returns the *user_token's* bookings.

        Distinct from ``/records/{company_id}`` (salon-wide); this one is
        scoped to the user identified by the ``User <user_token>`` part of
        the ``Authorization`` header. Returns an empty list on failure.
        """
        payload = self._request("GET", "/user/records")
        if not payload.get("success", False):
            return []

        rows = payload.get("data") or []
        if not isinstance(rows, list):
            return []
        return [_to_user_record(row) for row in rows if isinstance(row, dict)]


# ─── DTO mappers ───────────────────────────────────────────────────────────


def _to_service(row: dict[str, Any]) -> Service:
    return Service(
        id=int(row.get("id") or 0),
        title=str(row.get("title") or ""),
        price_min=float(row.get("price_min") or 0.0),
        price_max=float(row.get("price_max") or 0.0),
        duration_s=int(row.get("duration") or row.get("seance_length") or 0),
        category_id=row.get("category_id"),
        raw=row,
    )


def _to_staff(row: dict[str, Any]) -> Staff:
    position = row.get("position")
    position_title = (
        position.get("title", "")
        if isinstance(position, dict)
        else (str(position) if position else "")
    )
    return Staff(
        id=int(row.get("id") or 0),
        name=str(row.get("name") or ""),
        specialization=str(row.get("specialization") or ""),
        rating=float(row.get("rating") or 0.0),
        avatar=str(row.get("avatar") or ""),
        position=position_title,
        raw=row,
    )


def _to_available_time(row: dict[str, Any]) -> AvailableTime | None:
    time_str = row.get("time")
    iso_dt = row.get("datetime") or row.get("seance_date")
    if not time_str and isinstance(iso_dt, str):
        # Pull "HH:MM" out of "2025-12-15T17:30:00+03:00".
        if "T" in iso_dt:
            time_str = iso_dt.split("T", 1)[1][:5]
        else:
            time_str = iso_dt[:5]
    if not time_str:
        return None
    seance = row.get("seance_length")
    return AvailableTime(
        time=str(time_str),
        datetime=str(iso_dt) if iso_dt else None,
        seance_length_s=int(seance) if isinstance(seance, (int, float)) else None,
    )


def _to_user_record(row: dict[str, Any]) -> UserRecord:
    services = row.get("services") or []
    return UserRecord(
        id=int(row.get("id") or 0),
        services=list(services) if isinstance(services, list) else [],
        company=row.get("company") or {},
        staff=row.get("staff") or {},
        date=str(row.get("date") or ""),
        datetime=str(row.get("datetime") or ""),
        seance_length=int(row.get("seance_length") or 0),
        raw=row,
    )


def _extract_staff_rows(data: Any) -> list[Any]:
    """Find the staff list within YClients' polymorphic ``data`` shape.

    YClients sometimes returns ``data: [staff...]`` and sometimes
    ``data: {"staff": [staff...]}`` for the same endpoint depending on
    company config. Tolerate both.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        inner = data.get("staff")
        if isinstance(inner, list):
            return inner
    return []


def _extract_dates(data: Any) -> list[str]:
    """Pull a list of ``YYYY-MM-DD`` strings out of YClients' date envelope."""
    if isinstance(data, dict):
        for key in ("booking_dates", "working_dates", "dates"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [str(d) for d in rows if isinstance(d, str)]
    if isinstance(data, list):
        out: list[str] = []
        for item in data:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and "date" in item:
                out.append(str(item["date"]))
        return out
    return []


# ─── singleton ─────────────────────────────────────────────────────────────


_SINGLETON: YClientsAPI | None = None


def get_yclients_client() -> YClientsAPI:
    """Module-level singleton. Lazy — fails loudly when env is unset."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = YClientsAPI(
            partner_token=getattr(settings, "YCLIENTS_PARTNER_TOKEN", ""),
            user_token=getattr(settings, "YCLIENTS_USER_TOKEN", ""),
            company_id=getattr(settings, "YCLIENTS_COMPANY_ID", ""),
            base_url=getattr(settings, "YCLIENTS_BASE_URL", "") or None,
        )
    return _SINGLETON


def reset_yclients_client() -> None:
    """Drop the singleton — used by tests to reset state between cases."""
    global _SINGLETON
    _SINGLETON = None
