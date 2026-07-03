"""Static contract test: bot Ayla clients vs Ayla's internal route-table (S0-C).

Closes **G-Contract**. Complement to the live nightly round-trip
(``tests/e2e/test_ayla_integration.py``, #1079): that one proves the routes
work against staging *with* the network; THIS one is a pure static diff that
runs in CI on every push *without* the network.

It exercises each bot-side Ayla client through its **real code** (the
``AylaUrlBuilder`` seam + the actual HTTP call, intercepted by an in-memory
transport — no socket is opened) and asserts the outgoing
``(method, path, auth header + secret)`` matches Ayla's declared internal
route-table. Any drift turns this test red:

* wrong path / method / token secret,
* a missing ``/api/v1`` prefix or an accidental double prefix,
* a dropped or added trailing slash,
* a query param smuggled into the path instead of ``params=``.

``ROUTE_TABLE`` below is a **hand-maintained mirror** of Ayla's internal API —
it is NOT auto-derived. When Ayla adds / renames / moves an internal route,
update this table (and ``docs/architecture/contract-matrix.md``) in the same
change. ``test_matcher_catches_drift`` proves the matcher actually rejects a
tampered request, so the mirror can't rot into a rubber stamp.

Secret binding: each surface is given a **distinct sentinel token value** per
setting, so a captured header proves the client read the *correct* setting —
this is exactly the S0-B bug it guards (profile/recs used to read a
non-existent ``AYLA_SERVICE_TOKEN`` Bearer instead of ``AYLA_INTERNAL_API_TOKEN``).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import httpx
import pytest


# Distinct per-setting sentinels — a captured header value pins which setting
# the client read (not just that *some* token was sent).
_INTERNAL_TOKEN = "INTERNAL-TOKEN-SENTINEL"  # noqa: S105  # pragma: allowlist secret
_NUTRITION_TOKEN = "NUTRITION-TOKEN-SENTINEL"  # noqa: S105  # pragma: allowlist secret
_BASE = "https://ayla.test"
_EXT_USER = "bot:test:contract"
_PROFILE_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")


class Auth(Enum):
    """Auth model a route requires (header + which secret + external-user)."""

    BEARER = "bearer"  # Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}
    BEARER_EXT = "bearer+ext"  # + X-External-User-ID
    SERVICE_EXT = "svc+ext"  # X-Service-Token {NUTRITION_SERVICE_TOKEN} + X-External-User-ID


@dataclass(frozen=True)
class Route:
    method: str
    path: str  # normalised — ``{id}`` for dynamic id segments
    auth: Auth


# ─── Ayla internal route-table mirror — KEEP IN SYNC WITH AYLA ──────────────
# One row per (method, path, auth) the bot clients are allowed to call. Sourced
# from the merged Ayla ``/api/v1/internal/*`` serializers (#193/#1016) + the
# nutrition contract (DRF-825) + payments (#427). Every row must be produced by
# some client below (bijection asserted); every produced request must match a
# row (contract asserted).
ROUTE_TABLE: tuple[Route, ...] = (
    # booking_client (#1016) — Bearer; writes + ``me`` also carry X-External-User-ID.
    Route("GET", "/api/v1/internal/services/", Auth.BEARER),
    Route("GET", "/api/v1/internal/specialists/{id}/services/", Auth.BEARER),
    Route("GET", "/api/v1/internal/specialists/", Auth.BEARER),
    Route("GET", "/api/v1/internal/specialists/{id}/", Auth.BEARER),
    Route("GET", "/api/v1/internal/specialists/{id}/slots/", Auth.BEARER),
    Route("POST", "/api/v1/internal/appointments/", Auth.BEARER_EXT),
    Route("POST", "/api/v1/internal/appointments/{id}/cancel/", Auth.BEARER_EXT),
    Route("POST", "/api/v1/internal/appointments/{id}/reschedule/", Auth.BEARER_EXT),
    Route("GET", "/api/v1/internal/me/bookings/", Auth.BEARER_EXT),
    # profile_client (#978).
    Route("GET", "/api/v1/internal/users/{id}/", Auth.BEARER),
    # recommendations_client (#1048).
    Route("POST", "/api/v1/internal/me/catalog/recommendations/", Auth.BEARER_EXT),
    # nutrition_client (#1050) — X-Service-Token + X-External-User-ID.
    Route("POST", "/api/v1/nutrition/internal/scan/", Auth.SERVICE_EXT),
    Route("POST", "/api/v1/nutrition/internal/food-log/", Auth.SERVICE_EXT),
    Route("GET", "/api/v1/nutrition/internal/summary/", Auth.SERVICE_EXT),
    Route("GET", "/api/v1/nutrition/internal/deficits/", Auth.SERVICE_EXT),
    Route("GET", "/api/v1/nutrition/internal/profile/", Auth.SERVICE_EXT),
    Route("POST", "/api/v1/nutrition/internal/profile/", Auth.SERVICE_EXT),
    Route("POST", "/api/v1/nutrition/internal/water/", Auth.SERVICE_EXT),
    Route("DELETE", "/api/v1/nutrition/internal/water/{id}/", Auth.SERVICE_EXT),
    Route("GET", "/api/v1/nutrition/internal/water/today/", Auth.SERVICE_EXT),
    Route("GET", "/api/v1/nutrition/internal/insights/cross_domain/", Auth.SERVICE_EXT),
    Route("POST", "/api/v1/nutrition/internal/insights/cross_domain/seen/{id}/", Auth.SERVICE_EXT),
    Route(
        "POST",
        "/api/v1/nutrition/internal/insights/cross_domain/dismiss/{id}/",
        Auth.SERVICE_EXT,
    ),
    Route(
        "POST",
        "/api/v1/nutrition/internal/insights/cross_domain/convert/{id}/",
        Auth.SERVICE_EXT,
    ),
    # ayla_payments/client (#427) — Bearer + Idempotence-Key; NO trailing slash.
    Route("POST", "/api/v1/payments/create", Auth.BEARER),
)


@dataclass
class Captured:
    """One outgoing request recorded from a client's real HTTP call."""

    method: str
    path: str
    headers: dict[str, str]  # lower-cased keys


# id args passed into client methods; normalised back to ``{id}`` for matching.
_ID_SENTINELS = {"SPECID", "APPTID", "ENTRYID", "SHOWNID"}
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _normalise(path: str) -> str:
    """Collapse dynamic id segments to ``{id}``, preserving the trailing slash."""
    trailing = path.endswith("/")
    segs = [s for s in path.split("/") if s]
    norm = ["{id}" if (s in _ID_SENTINELS or _UUID_RE.match(s)) else s for s in segs]
    out = "/" + "/".join(norm)
    return out + "/" if trailing else out


def _auth_violation(auth: Auth, headers: dict[str, str]) -> str | None:
    """Return a human-readable violation, or None when the auth model matches."""
    if auth in (Auth.BEARER, Auth.BEARER_EXT):
        got = headers.get("authorization")
        if got != f"Bearer {_INTERNAL_TOKEN}":
            return f"expected 'Bearer {{AYLA_INTERNAL_API_TOKEN}}', got {got!r}"
    if auth is Auth.SERVICE_EXT:
        got = headers.get("x-service-token")
        if got != _NUTRITION_TOKEN:
            return f"expected 'X-Service-Token {{NUTRITION_SERVICE_TOKEN}}', got {got!r}"
        if "authorization" in headers:
            return "nutrition must NOT send a Bearer Authorization header"
    if auth in (Auth.BEARER_EXT, Auth.SERVICE_EXT):
        if "x-external-user-id" not in headers:
            return "missing X-External-User-ID"
    return None


def _match(cap: Captured) -> tuple[Route | None, str | None]:
    """Find the route for a captured request + any auth violation on it."""
    npath = _normalise(cap.path)
    for route in ROUTE_TABLE:
        if route.path == npath and route.method == cap.method.upper():
            return route, _auth_violation(route.auth, cap.headers)
    return None, f"no route-table entry for {cap.method} {npath}"


# ─── capture harness ────────────────────────────────────────────────────────


def _recording_handler(sink: list[Captured]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        sink.append(
            Captured(
                method=request.method,
                path=request.url.path,
                headers={k.lower(): v for k, v in request.headers.items()},
            )
        )
        return httpx.Response(200, json={})

    return handler


@pytest.fixture
def captured(settings: Any, monkeypatch: pytest.MonkeyPatch) -> list[Captured]:
    """Settings + in-memory transport so every client's real HTTP call is
    recorded instead of hitting the network."""
    settings.AYLA_BASE_URL = _BASE
    settings.AYLA_INTERNAL_API_TOKEN = _INTERNAL_TOKEN
    settings.NUTRITION_SERVICE_TOKEN = _NUTRITION_TOKEN
    settings.AYLA_PAYMENTS_TEST_MODE = False  # force the live payments request path

    sink: list[Captured] = []
    handler = _recording_handler(sink)
    real_client = httpx.Client
    real_async = httpx.AsyncClient

    def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    def async_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    monkeypatch.setattr(httpx, "AsyncClient", async_factory)
    return sink


def _swallow(fn: Any) -> None:
    """Run a client call for its request side effect; the recorded request is
    what we assert on, so a downstream parse error is irrelevant."""
    try:
        fn()
    except Exception:  # noqa: BLE001 — we only need the captured request
        pass


# ─── per-client exercisers ──────────────────────────────────────────────────


def _exercise_booking() -> None:
    from apps.integrations.ayla.booking_client import (
        get_ayla_booking_client,
        reset_ayla_booking_client,
    )

    reset_ayla_booking_client()
    c = get_ayla_booking_client()  # reads settings.AYLA_INTERNAL_API_TOKEN
    _swallow(lambda: c.get_services())
    _swallow(lambda: c.get_services(specialist_id="SPECID"))
    _swallow(lambda: c.get_masters())
    _swallow(lambda: c.get_masters(specialist_id="SPECID"))
    _swallow(lambda: c.get_available_times(specialist_id="SPECID", date="2026-07-03"))
    _swallow(
        lambda: c.create_appointment(
            external_user_id=_EXT_USER,
            client_id="CLIENTID",
            specialist_id="SPECID",
            service_id="SVCID",
            start_datetime="2026-07-03T10:00:00+03:00",
        )
    )
    _swallow(lambda: c.cancel_appointment(external_user_id=_EXT_USER, appointment_id="APPTID"))
    _swallow(
        lambda: c.reschedule_appointment(
            external_user_id=_EXT_USER,
            appointment_id="APPTID",
            new_start_datetime="2026-07-03T11:00:00+03:00",
        )
    )
    _swallow(lambda: c.get_user_appointments(external_user_id=_EXT_USER))


def _exercise_profile() -> None:
    from apps.integrations.ayla import profile_client

    profile_client._circuit.record_success()
    _swallow(lambda: profile_client.fetch_profile_fields(_PROFILE_UUID))


def _exercise_recommendations() -> None:
    from apps.integrations.ayla.recommendations_client import (
        fetch_recommendations,
        reset_recommendations_circuit,
    )

    reset_recommendations_circuit()
    _swallow(lambda: fetch_recommendations(external_user_id=_EXT_USER, payload={"goal": "relax"}))


def _exercise_payments(sink: list[Captured]) -> None:
    # ayla_payments uses ``requests``, not httpx — capture via the session mock.
    from apps.integrations.ayla_payments import (
        get_ayla_payments_client,
        reset_ayla_payments_client,
    )

    reset_ayla_payments_client()
    c = get_ayla_payments_client()  # reads settings.AYLA_INTERNAL_API_TOKEN + TEST_MODE
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"payment_id": "p", "checkout_url": "u", "status": "pending"}
    resp.text = ""
    with patch.object(c._session, "post", return_value=resp) as mock_post:
        _swallow(
            lambda: c.create_payment(
                amount_rub=Decimal("1000.00"),
                description="contract-probe",
                idempotence_key=uuid.UUID("22222222-3333-4444-5555-666666666666"),
            )
        )
    if mock_post.call_args is not None:
        args, kwargs = mock_post.call_args
        url = args[0] if args else kwargs.get("url", "")
        headers = {k.lower(): v for k, v in (kwargs.get("headers") or {}).items()}
        sink.append(Captured("POST", urlsplit(url).path, headers))


async def _exercise_nutrition() -> None:
    from apps.integrations.ayla import get_nutrition_client, reset_nutrition_client

    reset_nutrition_client()
    c = get_nutrition_client()  # reads settings.NUTRITION_SERVICE_TOKEN

    async def guard(coro: Any) -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001 — capture-only
            pass

    await guard(c.scan_photo(external_user_id=_EXT_USER, image_bytes=b"x"))
    await guard(c.log_meal(external_user_id=_EXT_USER, dish_name="apple", meal_type="snack"))
    await guard(c.daily_summary(external_user_id=_EXT_USER))
    await guard(c.weekly_deficits(external_user_id=_EXT_USER))
    await guard(c.get_profile(external_user_id=_EXT_USER))
    await guard(c.upsert_profile(external_user_id=_EXT_USER, data={}))
    await guard(c.add_water(external_user_id=_EXT_USER, ml=250))
    await guard(c.undo_water(external_user_id=_EXT_USER, entry_id="ENTRYID"))
    await guard(c.get_water_today(external_user_id=_EXT_USER))
    await guard(c.get_cross_domain_insights(external_user_id=_EXT_USER))
    await guard(c.post_cross_domain_seen(external_user_id=_EXT_USER, shown_id="SHOWNID"))
    await guard(c.post_cross_domain_dismiss(external_user_id=_EXT_USER, shown_id="SHOWNID"))
    await guard(
        c.post_cross_domain_convert(
            external_user_id=_EXT_USER, shown_id="SHOWNID", appointment_id="APPTID"
        )
    )


# ─── the contract test ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_clients_match_route_table(captured: list[Captured]) -> None:
    """Every request a bot client builds must match a route-table row, and every
    row must be produced by some client (bijection — no drift, no stale rows)."""
    _exercise_booking()
    _exercise_profile()
    _exercise_recommendations()
    _exercise_payments(captured)
    await _exercise_nutrition()

    assert captured, "no requests captured — the interception harness is broken"

    violations: list[str] = []
    matched: set[Route] = set()
    for cap in captured:
        route, err = _match(cap)
        if route is None or err is not None:
            violations.append(f"  {cap.method} {cap.path} → {err}")
        else:
            matched.add(route)
    assert not violations, "route-table contract violations:\n" + "\n".join(violations)

    # Bijection: a route-table row never produced by any client is stale/renamed.
    unexercised = sorted(f"  {r.method} {r.path}" for r in ROUTE_TABLE if r not in matched)
    assert not unexercised, (
        "route-table rows not produced by any client (stale mirror or a client "
        "stopped calling them):\n" + "\n".join(unexercised)
    )


# ─── the matcher must actually catch drift (else the mirror is a rubber stamp) ─


def _rejects(cap: Captured) -> bool:
    route, err = _match(cap)
    return route is None or err is not None


def test_matcher_catches_drift() -> None:
    """Prove the matcher rejects each drift class without touching prod code."""
    uid = "11111111-2222-3333-4444-555555555555"
    bearer = {"authorization": f"Bearer {_INTERNAL_TOKEN}"}

    ok = Captured("GET", f"/api/v1/internal/users/{uid}/", bearer)
    route, err = _match(ok)
    assert route is not None and err is None, "sanity: the good request must match"

    drift = {
        "missing /api/v1 prefix": Captured("GET", f"/internal/users/{uid}/", bearer),
        "double /api/v1 prefix": Captured("GET", f"/api/v1/api/v1/internal/users/{uid}/", bearer),
        "wrong method": Captured("POST", f"/api/v1/internal/users/{uid}/", bearer),
        "dropped trailing slash": Captured("GET", f"/api/v1/internal/users/{uid}", bearer),
        "wrong token value": Captured(
            "GET", f"/api/v1/internal/users/{uid}/", {"authorization": "Bearer WRONG"}
        ),
        "missing X-External-User-ID on a write": Captured(
            "POST", "/api/v1/internal/me/catalog/recommendations/", bearer
        ),
        "nutrition sending a Bearer instead of X-Service-Token": Captured(
            "POST",
            "/api/v1/nutrition/internal/scan/",
            {"authorization": f"Bearer {_INTERNAL_TOKEN}", "x-external-user-id": "e"},
        ),
    }
    for label, cap in drift.items():
        assert _rejects(cap), f"matcher FAILED to reject drift: {label}"
