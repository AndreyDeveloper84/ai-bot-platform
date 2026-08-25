"""The salon surface is declared, and the declaration is held to the client.

DRF-1346. Ayla's ``/api/v1/tenants/me/…`` reached fifteen routes with zero
callers in the bot. Nothing went red, because nothing in this repository knew
the surface existed: the route table lives in another repository, and every PR
that widened it was complete on its own side of the boundary.

So the surface is declared here as data
(:mod:`apps.integrations.ayla.salon_surface`) and three layers hold it, in
increasing order of what they can catch and decreasing order of how often they
run:

1. :class:`TestRegistryShape` — the table is internally coherent: no duplicate
   ``(route, method)``, no row that is neither callable nor explained.
2. :class:`TestClientParity` — the table and
   :class:`~apps.integrations.ayla.salon_client.AylaSalonClient` agree in both
   directions. A new client method without a row fails here, and so does a row
   whose method was renamed away.
3. :class:`TestWireShape` — every callable row is *driven*, through the real
   client, over an in-memory transport, and the request it produces must match
   the row's own path and method. This is what stops the table from becoming
   prose: a row that claims ``GET day/`` while the client asks for
   ``day/?date`` on a different path fails.
4. :class:`TestAgainstAylaSchema` — the live half. Reads Ayla's own OpenAPI
   document and asserts the table **is** the surface. This is the only layer
   that can catch route sixteen, and the only one that needs a reachable Ayla,
   so it skips by default and is the thing to run against the pilot.

Layer 4 is deliberately the last and not the first. The first three run in CI
on every push with no network; if the fourth were the only guard, the same
silence that let fifteen routes go uncalled would simply move one level up.

The form is borrowed from ``apps/master_api/tests/test_pii_boundary.py`` —
classify every route or fail — because it earned it: that test is what would
have caught DRF-1360 at authoring time.
"""

from __future__ import annotations

import ast
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

import httpx
import pytest

from apps.integrations.ayla.salon_client import AylaSalonClient
from apps.integrations.ayla.salon_surface import (
    SALON_CUSTOMER_IDENTITY,
    SALON_PREFIX,
    SALON_ROUTES,
    SalonRoute,
    SalonRouteAccess,
    capability,
    route_for,
    routes_by_access,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
CLIENT_SOURCE = REPO_ROOT / "apps" / "integrations" / "ayla" / "salon_client.py"

TOKEN = "surface-token-under-test"  # pragma: allowlist secret
ACTOR = "bot:max:83146139"
TENANT = "formula-tela"

#: Stand-ins for the path parameters, one per placeholder Ayla's URLconf uses.
#: Values are deliberately not UUID-shaped: the normaliser below matches on the
#: placeholder, and a value that happens to look like a date or an id would
#: hide a client that interpolated the wrong one.
PATH_ARGS: dict[str, str] = {
    "{specialist_id}": "SPECIALIST-ARG",
    "{appointment_id}": "APPOINTMENT-ARG",
    "{user_id}": "USER-ARG",
    "{pk}": "PK-ARG",
    "{date}": "DATE-ARG",
}

_PLACEHOLDER_RE = re.compile(r"\{[^}]+\}")


def _shape(path: str) -> str:
    """Collapse every ``{param}`` to ``{}`` so two spellings of the same route
    compare equal. Ayla's URLconf and its OpenAPI document do not always agree
    on a parameter's *name* (``pk`` renders as ``id`` under some generators);
    they always agree on where the parameter is."""

    return _PLACEHOLDER_RE.sub("{}", path)


def _concrete(path: str) -> str:
    """Substitute the sentinels, leaving anything unrecognised intact so a new
    placeholder shows up as itself in the failure message."""

    for placeholder, value in PATH_ARGS.items():
        path = path.replace(placeholder, value)
    return path


# ── 1. the table is coherent on its own ──────────────────────────────────


class TestRegistryShape:
    def test_no_duplicate_route_and_method(self) -> None:
        seen: list[tuple[str, str]] = [(r.name, r.method) for r in SALON_ROUTES]
        duplicates = sorted({pair for pair in seen if seen.count(pair) > 1})
        assert not duplicates, f"duplicate (route, method) rows: {duplicates}"

    def test_every_row_is_callable_or_explained(self) -> None:
        """The row that says nothing is the row this whole file exists to stop.

        A route with no client method and no reason is indistinguishable from
        a route someone forgot — which is the state the surface was in before
        DRF-1346.
        """

        silent = [
            f"{r.method} {r.path}"
            for r in SALON_ROUTES
            if r.client_method is None and not r.reason.strip()
        ]
        assert not silent, (
            "salon route(s) with neither a client method nor a written reason: "
            f"{silent}. Either bind one, or say in the row why nothing calls it."
        )

    def test_a_callable_row_names_a_method_and_a_blocked_row_does_not(self) -> None:
        for route in SALON_ROUTES:
            if route.access is SalonRouteAccess.CALLABLE:
                assert route.client_method, (
                    f"{route.method} {route.path} is CALLABLE with no method"
                )
            else:
                assert route.client_method is None, (
                    f"{route.method} {route.path} is {route.access.value} but names "
                    f"client method {route.client_method!r} — a method on a route the "
                    "service credential cannot use would always answer 403."
                )

    def test_paths_are_relative_to_the_salon_prefix(self) -> None:
        for route in SALON_ROUTES:
            assert not route.path.startswith("/"), route.path
            assert not route.path.startswith(SALON_PREFIX), (
                f"{route.path} restates the prefix; the row holds the path under it"
            )
            assert route.path.endswith("/"), (
                f"{route.path} drops the trailing slash — Ayla's DRF routes are "
                "trailing-slash-canonical and a dropped one is a 404."
            )
            assert route.full_path.startswith(f"/api/v1/{SALON_PREFIX}")

    def test_methods_are_upper_case_verbs(self) -> None:
        allowed = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        bad = sorted({r.method for r in SALON_ROUTES} - allowed)
        assert not bad, bad

    def test_lookups_agree_with_the_table(self) -> None:
        assert capability("tenants-day", "GET") is not None
        assert capability("tenants-day", "POST") is None, "day/ is GET-only upstream"
        assert capability("no-such-route") is None
        assert route_for("get_day") is not None
        assert route_for("no_such_method") is None
        assert len(routes_by_access(SalonRouteAccess.CALLABLE)) + len(
            routes_by_access(SalonRouteAccess.SERVICE_READ_ONLY)
        ) + len(routes_by_access(SalonRouteAccess.JWT_ONLY)) == len(SALON_ROUTES)


# ── 2. the table and the client agree, both ways ─────────────────────────


def _public_client_methods() -> list[str]:
    """Public methods on :class:`AylaSalonClient`, read from the source.

    An AST read rather than ``dir()`` so an inherited or dynamically attached
    attribute cannot quietly satisfy the parity check — the question is what
    this class *declares*, which is what a reviewer sees.
    """

    tree = ast.parse(CLIENT_SOURCE.read_text(encoding="utf-8"), filename=str(CLIENT_SOURCE))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AylaSalonClient":
            return [
                child.name
                for child in node.body
                if isinstance(child, ast.FunctionDef) and not child.name.startswith("_")
            ]
    raise AssertionError("AylaSalonClient not found in salon_client.py")


class TestClientParity:
    def test_every_callable_row_binds_a_real_method(self) -> None:
        missing = [
            f"{r.method} {r.path} -> {r.client_method}"
            for r in routes_by_access(SalonRouteAccess.CALLABLE)
            if not callable(getattr(AylaSalonClient, r.client_method or "", None))
        ]
        assert not missing, (
            f"registry rows naming a method AylaSalonClient does not have: {missing}"
        )

    def test_every_public_client_method_is_a_registry_row(self) -> None:
        """The other direction — the one that catches a hand-rolled call.

        Without this, someone adding ``def get_masters(...)`` straight onto the
        client gets a working method that the surface table has never heard of,
        and the table quietly stops describing reality.
        """

        unregistered = [name for name in _public_client_methods() if route_for(name) is None]
        assert not unregistered, (
            f"public AylaSalonClient method(s) {sorted(unregistered)} have no row in "
            "SALON_ROUTES. Add the route they call — with its access class — so the "
            "surface stays declared in one place."
        )

    def test_no_two_rows_claim_the_same_method(self) -> None:
        bound = [r.client_method for r in SALON_ROUTES if r.client_method]
        duplicates = sorted({m for m in bound if bound.count(m) > 1})
        assert not duplicates, f"one method bound to several routes: {duplicates}"

    def test_pii_notes_cover_every_route_that_returns_a_customer(self) -> None:
        """Every route named in the PII note must still exist.

        The note is the only place a screen author is told that
        ``data.notes`` can carry an unmasked walk-in phone. A stale entry
        there is worse than none, because it reads as current.
        """

        known = {r.name for r in SALON_ROUTES}
        stale = sorted(set(SALON_CUSTOMER_IDENTITY) - known)
        assert not stale, f"PII note mentions route(s) that no longer exist: {stale}"


# ── 3. the client actually builds what the table claims ──────────────────


def _client(sink: list[httpx.Request]) -> AylaSalonClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sink.append(request)
        # A shape every read unwraps to nothing useful is fine: the assertion
        # is on the request, and an empty envelope keeps the parse honest.
        body: Any = [] if request.method == "GET" else {"id": "x"}
        return httpx.Response(200, json={"data": body})

    return AylaSalonClient(
        base_url="https://ayla.example",
        service_token=TOKEN,
        transport=httpx.MockTransport(handler),
    )


#: One call per CALLABLE row, keyed by the row's client method. Arguments are
#: the minimum each method requires; the point is the request, not the answer.
CALLS: dict[str, Any] = {
    "get_day": lambda c: c.get_day(actor_external_id=ACTOR, tenant_slug=TENANT, date="2026-08-25"),
    "search_customers": lambda c: c.search_customers(
        actor_external_id=ACTOR, tenant_slug=TENANT, query="Мар"
    ),
    "create_appointment": lambda c: c.create_appointment(
        actor_external_id=ACTOR,
        idempotency_key="key-1",
        tenant_slug=TENANT,
        specialist_id="SPECIALIST-ARG",
        service_id="svc-1",
        start_datetime="2026-08-25T15:00:00+03:00",
        client_id="client-1",
    ),
    "reschedule_appointment": lambda c: c.reschedule_appointment(
        actor_external_id=ACTOR,
        tenant_slug=TENANT,
        appointment_id="APPOINTMENT-ARG",
        new_start_datetime="2026-08-25T16:30:00+03:00",
        expected_version=3,
    ),
    "cancel_appointment": lambda c: c.cancel_appointment(
        actor_external_id=ACTOR,
        tenant_slug=TENANT,
        appointment_id="APPOINTMENT-ARG",
        reason_code="master_unavailable",
    ),
    "complete_appointment": lambda c: c.complete_appointment(
        actor_external_id=ACTOR,
        tenant_slug=TENANT,
        appointment_id="APPOINTMENT-ARG",
        expected_version=3,
    ),
    "get_master_schedule": lambda c: c.get_master_schedule(
        actor_external_id=ACTOR, tenant_slug=TENANT, specialist_id="SPECIALIST-ARG"
    ),
    "get_schedule_impact": lambda c: c.get_schedule_impact(
        actor_external_id=ACTOR,
        tenant_slug=TENANT,
        specialist_id="SPECIALIST-ARG",
        start_at="2026-08-25T09:00:00+03:00",
        end_at="2026-08-25T18:00:00+03:00",
    ),
    "list_time_off": lambda c: c.list_time_off(
        actor_external_id=ACTOR,
        tenant_slug=TENANT,
        specialist_id="SPECIALIST-ARG",
        date_from="2026-08-01",
        date_to="2026-08-31",
    ),
    "list_schedule_exceptions": lambda c: c.list_schedule_exceptions(
        actor_external_id=ACTOR,
        tenant_slug=TENANT,
        specialist_id="SPECIALIST-ARG",
        date_from="2026-08-01",
    ),
    "list_closures": lambda c: c.list_closures(
        actor_external_id=ACTOR, tenant_slug=TENANT, date_to="2026-09-30"
    ),
}


def _drive(route: SalonRoute) -> httpx.Request:
    sink: list[httpx.Request] = []
    call = CALLS[route.client_method or ""]
    try:
        call(_client(sink))
    except Exception:  # noqa: BLE001 — the recorded request is the assertion
        pass
    assert sink, f"{route.client_method} opened no request"
    return sink[0]


class TestWireShape:
    def test_every_callable_row_has_an_exerciser(self) -> None:
        """A row with no call below is a row nothing proves."""

        expected = {r.client_method for r in routes_by_access(SalonRouteAccess.CALLABLE)}
        assert set(CALLS) == expected, (
            f"exercisers missing for {sorted(expected - set(CALLS))}, "
            f"stale for {sorted(set(CALLS) - expected)}"
        )

    @pytest.mark.parametrize(
        "route",
        routes_by_access(SalonRouteAccess.CALLABLE),
        ids=lambda r: f"{r.method}-{r.name}",
    )
    def test_request_matches_the_declared_route(self, route: SalonRoute) -> None:
        request = _drive(route)
        assert request.method == route.method
        assert request.url.path == _concrete(route.full_path), (
            f"{route.client_method} asked for {request.url.path}, the table declares "
            f"{_concrete(route.full_path)}"
        )

    @pytest.mark.parametrize(
        "route",
        routes_by_access(SalonRouteAccess.CALLABLE),
        ids=lambda r: f"{r.method}-{r.name}",
    )
    def test_every_call_carries_the_four_headers(self, route: SalonRoute) -> None:
        """Bearer, actor, tenant, app-type. The fourth is the one that was
        missing: thirteen of the fifteen routes are refused in middleware
        without ``X-App-Type``, before authentication, with a 403 that names
        neither the token nor the tenant."""

        headers = {k.lower(): v for k, v in _drive(route).headers.items()}
        assert headers.get("authorization") == f"Bearer {TOKEN}"
        assert headers.get("x-external-user-id") == ACTOR
        assert headers.get("x-tenant") == TENANT
        assert headers.get("x-app-type") == "pro", (
            "AppTypeMiddleware refuses /api/v1/tenants/me/ without it (403 "
            "APP_TYPE_MISSING) on every path except appointments/ and customers/, "
            "and IsProApp then requires the value to be 'pro'."
        )

    def test_a_read_carries_no_idempotency_key(self) -> None:
        """Nothing to de-duplicate on a GET; sending a key would tell the
        reader there is."""

        for route in routes_by_access(SalonRouteAccess.CALLABLE):
            if route.method != "GET":
                continue
            headers = {k.lower() for k in _drive(route).headers}
            assert "x-idempotency-key" not in headers, route.name


# ── 4. the live half: is the table still the surface? ────────────────────

#: Set to Ayla's schema URL (e.g. ``https://api-dev.gobeauty.site/api/schema/``)
#: to run the layer that catches route sixteen. Unset, it skips — CI has no
#: Ayla, and a test that silently passes without one would be worse than
#: absent.
SCHEMA_URL_ENV = "AYLA_SCHEMA_URL"


def _load_schema() -> dict[str, Any]:
    url = os.environ[SCHEMA_URL_ENV]
    req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


@pytest.mark.skipif(
    not os.environ.get(SCHEMA_URL_ENV),
    reason=f"set {SCHEMA_URL_ENV} to Ayla's /api/schema/ to run the live surface diff",
)
class TestAgainstAylaSchema:
    """Reads Ayla's own OpenAPI document; the table must *be* the surface.

    ``/api/schema/`` is in ``AppTypeMiddleware.EXCLUDED_PATH_PREFIXES`` and in
    ``TenantContextMiddleware.EXCLUDED_PATH_PREFIXES``, so it answers without
    ``X-App-Type``, without ``X-Tenant`` and without a credential — which is
    why this layer needs no secret and prints none.
    """

    @staticmethod
    def _declared() -> set[tuple[str, str]]:
        schema = _load_schema()
        prefix = f"/api/v1/{SALON_PREFIX}"
        found: set[tuple[str, str]] = set()
        for path, operations in (schema.get("paths") or {}).items():
            if not path.startswith(prefix):
                continue
            for method in operations:
                if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    found.add((method.upper(), _shape(path)))
        return found

    def test_no_ayla_route_is_unclassified(self) -> None:
        ours = {(r.method, _shape(r.full_path)) for r in SALON_ROUTES}
        unknown = sorted(self._declared() - ours)
        assert not unknown, (
            f"Ayla serves salon route(s) this bot has never heard of: {unknown}. "
            "Add each to SALON_ROUTES — with a client method, or with a written "
            "reason why the service credential cannot use it."
        )

    def test_no_declared_route_has_disappeared(self) -> None:
        ours = {(r.method, _shape(r.full_path)) for r in SALON_ROUTES}
        gone = sorted(ours - self._declared())
        assert not gone, (
            f"SALON_ROUTES declares route(s) Ayla no longer serves: {gone}. "
            "A client method pointing at one of these is a 404 waiting for the pilot."
        )


# ── 5. how far the DRF-1039 boundary reaches (it stops short) ────────────


class TestPiiBoundaryReach:
    """``apps.master_api.pii`` does not cover this surface, and could not.

    The parent question for DRF-1346 was whether the DRF-1360 guard already
    protects the salon routes. It does not, for two independent reasons, and
    both are pinned below so the answer is executable rather than remembered:

    * **Scope.** ``test_pii_boundary.py`` sweeps ``master_api.urls`` and
      AST-scans ``apps/master_api/**``. An Ayla salon payload arriving through
      this client is outside both.
    * **Mechanism.** The guard matches *key names*. The one real exposure on
      this surface is not a key — it is the customer's full name and unmasked
      number written as free text into ``notes`` by Ayla's master walk-in path
      (``appointments/views.py:307-312``), and returned verbatim by the
      reschedule / cancel / complete responses.

    Extending the boundary to reach it is a separate owner decision: a salon
    administrator is not the «исполнитель» of DRF-1039, so the right answer is
    probably a strip at the master-facing seam rather than a ban here. Nothing
    in this PR builds that strip; this class only stops the gap from being
    rediscovered from scratch.
    """

    def test_a_salon_day_payload_carries_no_forbidden_key(self) -> None:
        """Fact one: no salon response uses a banned key name. Pinned so a new
        Ayla field of that class shows up here as a diff rather than on a
        screen."""

        from apps.master_api.pii import find_forbidden_pii

        day = {
            "date": "2026-08-25",
            "masters": [
                {
                    "specialist_id": "m-1",
                    "display_name": "Денис",
                    "bookings": [
                        {
                            "appointment_id": "a-1",
                            "version": 3,
                            "status": "confirmed",
                            "client_id": "c-1",
                            "client_name": "Мария И",
                            "service_name": "Маникюр",
                            "price": "2500.00",
                        }
                    ],
                }
            ],
            "summary": {"masters": 1, "bookings": 1},
        }
        assert find_forbidden_pii(day) == []

    def test_the_guard_cannot_see_a_phone_inside_notes(self) -> None:
        """Fact two — the gap. ``notes`` is free text, so a key-name scan
        walks straight past a full number sitting in it.

        This asserts current behaviour, not desired behaviour. When the
        master-facing strip is decided and built, this test should be the one
        that goes red and says so.
        """

        from apps.master_api.pii import find_forbidden_pii

        walk_in_response = {
            "data": {
                "id": "a-1",
                "client_id": "c-1",
                "status": "confirmed",
                "notes": "Walk-in: Мария Иванова (+79997775544)",
            }
        }
        assert find_forbidden_pii(walk_in_response) == [], (
            "if this now reports a finding, the boundary grew to reach free "
            "text — update the salon_surface PII note, which currently tells "
            "screen authors it does not."
        )
        assert "+79997775544" in walk_in_response["data"]["notes"]

    def test_the_pii_note_names_notes_as_the_exposure(self) -> None:
        """The note in ``salon_surface`` is where a screen author meets this.
        If the wording drifts away from ``notes``, the warning stops warning."""

        for name in (
            "tenants-booking-reschedule",
            "tenants-booking-cancel",
            "tenants-booking-complete",
        ):
            assert "notes" in SALON_CUSTOMER_IDENTITY[name], name
