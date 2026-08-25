"""Canonical registry of Ayla's salon-admin surface — ``/api/v1/tenants/me/…``.

DRF-1346. Owner decision 24.08 (``docs/OD_SALON_P0_CONTRACT.md``,
«ДОПОЛНЕНИЕ 24.08.2026»): the pilot salon admin is the one that reads the
salon out of **Ayla**, not out of the bot's own ``/api/v1/admin/`` mirror.

### Why a registry and not just methods on the client

The surface lives in another repository. A route missing from
:class:`~apps.integrations.ayla.salon_client.AylaSalonClient` is invisible
here — nothing in this codebase goes red when Ayla opens a sixteenth one.
That is exactly how the surface reached fifteen routes with **zero** callers:
every PR that added one was complete on its own side of the boundary.

So the surface is declared here, once, as data. Two things then hold it:

* ``apps/integrations/ayla/tests/test_salon_surface.py`` — every entry is
  either bound to a client method that exists, or carries a written reason
  why nothing calls it; and every wire-touching client method appears here.
  A new client method without a row, or a row with neither caller nor
  reason, fails CI.
* the same module's live half — it reads Ayla's own OpenAPI schema and
  asserts this table *is* the surface. That is the half that catches route
  sixteen, and it is the one that needs a reachable Ayla.

### Reachability is not uniform, and that is the point

The three groups below are not a taxonomy invented here; they fall out of
what Ayla's own views declare. Read against the canonical source at Ayla
``dev`` d20efa56 — ``tenants/urls.py``, ``tenants/appointments_api.py``,
``tenants/day_api.py``, ``users/schedule_admin_api.py``,
``users/middleware.py`` — not guessed.

**Group 1 — the booking surface** (``appointments/…``, ``customers/``).
``authentication_classes = []`` and the permissions are the sole authority:
``IsBotServiceWithVerifiedClient`` + ``IsTenantAdmin`` (DRF-1231). Both reads
and writes are ours. These two prefixes are also the only ones listed in
``AppTypeMiddleware.EXCLUDED_PATH_PREFIXES``.

**Group 2 — the salon's own data** (``day/``, ``masters/…``, ``closures/…``).
``AylaServiceBearerAuthentication`` sits in front of the JWT authenticators,
so the service credential authenticates — and then
``ServiceCredentialIsReadOnly`` restricts it to ``GET``/``HEAD``/``OPTIONS``.
Every write here is refused **by design**, not by accident: owner decision
В-1, restated in ``OD_SALON_P0_CONTRACT.md`` §ЧАСТЬ 2.1 — *«`POST`/`PUT`/
`PATCH`/`DELETE` не должны открыться автоматически»*. A client method for one
of them would be a method that always answers 403.

**Group 3 — access revocation** (``relationships/{user_id}/revoke/``). The
view declares no ``authentication_classes`` at all, so it falls through to
``DEFAULT_AUTHENTICATION_CLASSES`` — JWT. A service Bearer is refused before
any permission runs. Not reachable from the bot at any method, and no owner
decision has said it should be.

### The header that is easy to miss

``AppTypeMiddleware`` excludes exactly two of the fifteen paths. For the other
thirteen, a request without ``X-App-Type`` is refused **403 APP_TYPE_MISSING
in middleware**, before authentication, and ``IsProApp`` then requires the
value to be ``pro``. So every salon call carries ``X-App-Type: pro``; on the
two excluded prefixes the middleware short-circuits and the header is simply
ignored, which is why the client sends it unconditionally rather than
per-route.

### Calibrating a probe against this surface

``TenantContextMiddleware`` answers **400 ``TENANT_REQUIRED``** for any
``/api/v1/`` path when ``X-Tenant`` is missing and ``MULTI_TENANT_STRICT`` is
on (it is, on dev). That runs *before* routing, so a made-up path answers 400
exactly like a real one — probing a route without the header proves nothing
about whether the route exists. Calibrate with a deliberately non-existent
path first: with correct headers it must answer **404**. Only then does a 404
on a real path mean anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: Path prefix every route below hangs off, relative to Ayla's ``/api/v1/``.
SALON_PREFIX = "tenants/me/"


class SalonRouteAccess(str, Enum):
    """What the bot's service credential can actually do with a route.

    Deliberately a status a caller can read rather than an exception a caller
    must catch — the same shape as
    ``apps.identity.services.personal_context.GateStatus``, and for the same
    reason. A screen that wants «отгул мастера» needs to learn that the write
    is closed *while it is being designed*, not at 15:40 on the pilot when the
    403 arrives. Handing back a status lets the caller decide: hide the
    control, show it disabled with the reason, or route the operator to the
    salon console. Raising would force every caller into one decision, and it
    would be the wrong one for most of them.
    """

    #: The bot calls this today; a client method exists.
    CALLABLE = "callable"

    #: Authenticates, but ``ServiceCredentialIsReadOnly`` refuses the method.
    #: Opening it is an owner decision (OD_SALON_P0_CONTRACT §ЧАСТЬ 2.1), not
    #: a code change here.
    SERVICE_READ_ONLY = "service_read_only"

    #: The view accepts no service credential at all — JWT only.
    JWT_ONLY = "jwt_only"


@dataclass(frozen=True)
class SalonRoute:
    """One ``(route, method)`` pair of Ayla's salon-admin surface."""

    #: Ayla's own ``path(..., name=...)`` — the join key against its schema.
    name: str
    method: str
    #: Path under :data:`SALON_PREFIX`, with ``{param}`` placeholders named
    #: exactly as Ayla's URLconf names them.
    path: str
    access: SalonRouteAccess
    #: Name of the :class:`AylaSalonClient` method that calls it, or ``None``.
    client_method: str | None = None
    #: Required when ``client_method`` is ``None``: why nothing calls it. A row
    #: with neither is what the guard test exists to refuse.
    reason: str = ""

    @property
    def full_path(self) -> str:
        """The path as it appears on the wire, including ``/api/v1/``."""

        return f"/api/v1/{SALON_PREFIX}{self.path}"


#: Ayla ``dev`` d20efa56 — ``tenants/urls.py`` lines 34-124: fifteen routes,
#: twenty ``(route, method)`` pairs. Ordered as the URLconf orders them so a
#: diff against it reads straight down.
SALON_ROUTES: tuple[SalonRoute, ...] = (
    # ── Group 3 — JWT only ────────────────────────────────────────────────
    SalonRoute(
        name="tenants-me-relationships-revoke",
        method="POST",
        path="relationships/{user_id}/revoke/",
        access=SalonRouteAccess.JWT_ONLY,
        reason=(
            "TenantRelationshipRevokeView declares no authentication_classes, "
            "so DEFAULT_AUTHENTICATION_CLASSES (JWT) is the only way in and a "
            "service Bearer is refused before permissions run. Reaching it "
            "would need an Ayla-side change plus an owner decision on whether "
            "the bot may sever a customer's tie to a salon at all — neither "
            "exists."
        ),
    ),
    # ── Group 2 — the salon's own data: reads ours, writes closed ────────
    SalonRoute(
        name="tenants-day",
        method="GET",
        path="day/",
        access=SalonRouteAccess.CALLABLE,
        client_method="get_day",
    ),
    # ── Group 1 — the booking surface: both halves ours ──────────────────
    SalonRoute(
        name="tenants-customer-lookup",
        method="GET",
        path="customers/",
        access=SalonRouteAccess.CALLABLE,
        client_method="search_customers",
    ),
    SalonRoute(
        name="tenants-booking-create",
        method="POST",
        path="appointments/",
        access=SalonRouteAccess.CALLABLE,
        client_method="create_appointment",
    ),
    SalonRoute(
        name="tenants-booking-reschedule",
        method="POST",
        path="appointments/{appointment_id}/reschedule/",
        access=SalonRouteAccess.CALLABLE,
        client_method="reschedule_appointment",
    ),
    SalonRoute(
        name="tenants-booking-cancel",
        method="POST",
        path="appointments/{appointment_id}/cancel/",
        access=SalonRouteAccess.CALLABLE,
        client_method="cancel_appointment",
    ),
    SalonRoute(
        name="tenants-booking-complete",
        method="POST",
        path="appointments/{appointment_id}/complete/",
        access=SalonRouteAccess.CALLABLE,
        client_method="complete_appointment",
    ),
    # ── Group 2 continued — master schedule, time-off, exceptions ────────
    SalonRoute(
        name="tenants-master-schedule",
        method="GET",
        path="masters/{specialist_id}/schedule/",
        access=SalonRouteAccess.CALLABLE,
        client_method="get_master_schedule",
    ),
    SalonRoute(
        name="tenants-master-schedule",
        method="PUT",
        path="masters/{specialist_id}/schedule/",
        access=SalonRouteAccess.SERVICE_READ_ONLY,
        reason=(
            "Weekly recurring template. Closed twice over: "
            "ServiceCredentialIsReadOnly refuses the method to a service "
            "Bearer, and OD_SALON_P0_CONTRACT В-4 row D declares the weekly "
            "shrink guard an open backend gap — «до закрытия gap Ayla weekly "
            "writes считаются unsupported». So this stays uncalled even if "
            "the credential question is settled."
        ),
    ),
    SalonRoute(
        name="tenants-master-schedule",
        method="PATCH",
        path="masters/{specialist_id}/schedule/",
        access=SalonRouteAccess.SERVICE_READ_ONLY,
        reason="Same weekly template as the PUT above — see that row.",
    ),
    SalonRoute(
        name="tenants-master-schedule-impact",
        method="GET",
        path="masters/{specialist_id}/schedule/impact/",
        access=SalonRouteAccess.CALLABLE,
        client_method="get_schedule_impact",
    ),
    SalonRoute(
        name="tenants-master-time-off",
        method="GET",
        path="masters/{specialist_id}/time-off/",
        access=SalonRouteAccess.CALLABLE,
        client_method="list_time_off",
    ),
    SalonRoute(
        name="tenants-master-time-off",
        method="POST",
        path="masters/{specialist_id}/time-off/",
        access=SalonRouteAccess.SERVICE_READ_ONLY,
        reason=(
            "Creating an absence is a consequential write; the service "
            "credential is read-only on this surface by owner decision "
            "(OD_SALON_P0_CONTRACT ЧАСТЬ 2.1). The impact preview that must "
            "precede it IS callable — see tenants-master-schedule-impact — so "
            "a screen can show the consequence honestly and hand the commit "
            "to the console."
        ),
    ),
    SalonRoute(
        name="tenants-master-time-off-detail",
        method="DELETE",
        path="masters/{specialist_id}/time-off/{pk}/",
        access=SalonRouteAccess.SERVICE_READ_ONLY,
        reason="Write on the read-only surface — same as the POST above.",
    ),
    SalonRoute(
        name="tenants-master-schedule-exceptions",
        method="GET",
        path="masters/{specialist_id}/schedule-exceptions/",
        access=SalonRouteAccess.CALLABLE,
        client_method="list_schedule_exceptions",
    ),
    SalonRoute(
        name="tenants-master-schedule-exceptions",
        method="PUT",
        path="masters/{specialist_id}/schedule-exceptions/",
        access=SalonRouteAccess.SERVICE_READ_ONLY,
        reason=(
            "Write on the read-only surface — the specific-date «не работаю» "
            "upsert. PUT rather than POST because there is one row per "
            "(master, date)."
        ),
    ),
    SalonRoute(
        name="tenants-master-schedule-exception-detail",
        method="DELETE",
        path="masters/{specialist_id}/schedule-exceptions/{date}/",
        access=SalonRouteAccess.SERVICE_READ_ONLY,
        reason="Write on the read-only surface — clears a specific-date exception.",
    ),
    SalonRoute(
        name="tenants-closures",
        method="GET",
        path="closures/",
        access=SalonRouteAccess.CALLABLE,
        client_method="list_closures",
    ),
    SalonRoute(
        name="tenants-closures",
        method="POST",
        path="closures/",
        access=SalonRouteAccess.SERVICE_READ_ONLY,
        reason="Write on the read-only surface — closes the salon for a period.",
    ),
    SalonRoute(
        name="tenants-closure-detail",
        method="DELETE",
        path="closures/{pk}/",
        access=SalonRouteAccess.SERVICE_READ_ONLY,
        reason="Write on the read-only surface — reopens a closed period.",
    ),
)


#: Which salon reads carry customer identity, and in what form. Stated here
#: rather than left to be discovered by whoever first renders one of these
#: payloads on a screen.
#:
#: ``apps.master_api.pii`` — the DRF-1360 boundary — does **not** reach this
#: surface. It sweeps ``master_api.urls`` and AST-scans ``apps/master_api/**``
#: only, so an Ayla salon payload rendered anywhere else is outside it. Two
#: separate facts follow, and the second is the dangerous one:
#:
#: 1. No salon response carries a key from ``FORBIDDEN_PII_KEYS``. Measured
#:    against Ayla ``dev`` d20efa56 across every response-building path on the
#:    surface: no ``phone``, ``phone_masked``, ``email``, ``ltv``,
#:    ``client_last_name`` or ``client_full_name`` anywhere.
#: 2. **The appointment writes can return an unmasked phone as free text.**
#:    ``AppointmentDetailSerializer`` exposes ``notes``; the salon create path
#:    deliberately keeps the number out of it, but the master walk-in path
#:    (Ayla ``appointments/views.py:307-312``) writes
#:    ``f"Walk-in: {client_name} ({client_phone})"`` into that same column. So
#:    reschedule / cancel / complete of a walk-in-originated booking answer
#:    with the customer's full name and full number inside ``data.notes`` — not
#:    under a forbidden key, which is precisely why a key-name scan cannot see
#:    it.
#:
#: Consequence for a screen author: a salon-surface payload may be shown to a
#: salon administrator, who is not the «исполнитель» of DRF-1039. It must not
#: be piped to a master-facing surface without stripping ``notes`` — and that
#: strip does not exist yet. Building it is a separate owner decision.
SALON_CUSTOMER_IDENTITY: dict[str, str] = {
    "tenants-day": "bookings[].client_id, bookings[].client_name (first + last, never a phone)",
    "tenants-customer-lookup": "results[].id, results[].name (first + last; phone is input only)",
    "tenants-booking-create": "data.client_id; data.notes may carry free text",
    "tenants-booking-reschedule": "data.client_id; data.notes may carry a walk-in phone",
    "tenants-booking-cancel": "data.client_id; data.notes may carry a walk-in phone",
    "tenants-booking-complete": "data.client_id; data.notes may carry a walk-in phone",
    "tenants-master-schedule-impact": "none — AffectedBooking deliberately carries no client field",
}


def routes_by_access(access: SalonRouteAccess) -> tuple[SalonRoute, ...]:
    """Every route in one reachability class, in URLconf order."""

    return tuple(r for r in SALON_ROUTES if r.access is access)


def route_for(client_method: str) -> SalonRoute | None:
    """The route a client method calls, or ``None`` when it calls none."""

    for route in SALON_ROUTES:
        if route.client_method == client_method:
            return route
    return None


def capability(name: str, method: str = "GET") -> SalonRoute | None:
    """Look one route up by Ayla's own route name + HTTP method.

    Returns ``None`` for a name this bot has never heard of — which, to a
    caller, is the same answer as «not callable», but is worth telling apart
    in a log: an unknown name means Ayla moved and this table did not.
    """

    wanted = method.upper()
    for route in SALON_ROUTES:
        if route.name == name and route.method == wanted:
            return route
    return None


__all__ = [
    "SALON_CUSTOMER_IDENTITY",
    "SALON_PREFIX",
    "SALON_ROUTES",
    "SalonRoute",
    "SalonRouteAccess",
    "capability",
    "route_for",
    "routes_by_access",
]
