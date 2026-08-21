"""Ayla salon-surface client — the salon acts, a named human is the actor.

Covers ``/api/v1/tenants/me/…``: the endpoints a salon administrator drives,
as opposed to ``/api/v1/internal/…`` where the bot acts for the customer who
consented. Read against the canonical source (`tenants/appointments_api.py`),
not guessed.

### Auth model (path Б, owner decision OD-B5-1)

The service authenticates the request; ``X-External-User-ID`` names the human
on whose behalf it runs; Ayla resolves that human and checks **their** rights
in the tenant. The point is attribution: a journal that says «the owner
cancelled» when an administrator pressed the button is worse than no journal,
because people believe it.

Concretely: ``Authorization: Bearer AYLA_INTERNAL_API_TOKEN`` plus
``X-External-User-ID``, which Ayla checks with
``IsBotServiceWithVerifiedClient``.

That permission does more than authenticate. It resolves the header into a
real Ayla ``User`` and puts it on ``request.user``, so the ownership filters
already written across the salon views keep working unchanged. For the
attribution path Б was chosen for, that is not a convenience — it is the
mechanism.

No separate salon secret exists on purpose. The surface is distinguished by
the resolved actor, not by a second shared key, so adding one would buy an
ops step and no isolation. ``IsServiceAccount`` — the ``X-Service-Token``
half — is scoped to nutrition by its own docstring and keyed to
``NUTRITION_SERVICE_TOKEN``; reusing it here would rebuild the conflation
#1050 unpicked.

### Idempotency is not optional here

``SalonBookingCreateView`` reads ``X-Idempotency-Key`` and, when it is absent,
**generates a fresh uuid per request**. So a caller that omits the header has
no idempotency at all: a retry after a timeout books the customer twice. The
UX contract §18 tells the surface to offer an «idempotent retry affordance» on
an unknown outcome — that affordance only exists if the key is stable, which
is why :meth:`create_appointment` requires one instead of defaulting it.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings

from apps.integrations.ayla.url_builder import AylaUrlBuilder

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0


class SalonAPIError(Exception):
    """Base for every salon-surface failure."""


class SalonNotConfigured(SalonAPIError):
    """No base URL or no service token — fail closed, never fail open."""


class SalonValidationError(SalonAPIError):
    """400 — Ayla rejected the payload (e.g. booking window)."""


class SalonUnauthorized(SalonAPIError):
    """401 — Ayla rejected our credentials before permissions ran.

    Not a booking problem and not this administrator's fault: the salon
    endpoints did not accept the service credential at all. Distinguished
    from :class:`SalonForbidden` because the remedy is entirely different —
    403 means «this person may not», 401 means «we may not», and only the
    second is an operator's page.

    Measured against live Ayla on 2026-08-21: the salon endpoints answer
    401 ``token_not_valid`` to a service Bearer, because their JWT
    authenticator rejects it before ``permission_classes`` are consulted
    (DRF-1231). When that is fixed this stops happening, and the contract
    test that pins it should go red and say so.
    """


class SalonForbidden(SalonAPIError):
    """403 — this actor is not an administrator of this salon."""


class SalonNotFound(SalonAPIError):
    """404 — the specialist or the customer does not belong to this salon."""


class SalonSlotTaken(SalonAPIError):
    """409 — the interval went while the draft was open."""


class SalonStaleVersion(SalonAPIError):
    """409 ``STALE_VERSION`` — somebody else moved this booking first.

    Shares a status code with :class:`SalonSlotTaken` and means something
    entirely different. «The time was taken» sends the receptionist to
    pick another slot; «the booking changed under you» sends them to
    refresh the day, because the booking they are looking at is not the
    booking that exists. Telling them the first when it is the second
    invites a second move on top of somebody else's — which is exactly
    what ``expected_version`` is there to prevent.
    """


class SalonNotAllowed(SalonAPIError):
    """422 — the booking's own state forbids this, whoever is asking.

    A finished visit cannot be cancelled and a cancelled one cannot be
    moved. Not a rights problem (403) and not a race (409): no retry and
    no other actor changes the answer.
    """


class SalonUnavailable(SalonAPIError):
    """Network, timeout or 5xx.

    On a **write** this is genuinely «unknown», not «failed»: the request may
    have been applied before the connection broke. Callers must surface it as
    pending and never as a clean failure (UX contract §18).
    """


class AylaSalonClient:
    """Thin sync client. One method per salon operation, no shared state."""

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise SalonNotConfigured("AYLA_BASE_URL is empty")
        if not service_token:
            raise SalonNotConfigured("AYLA_INTERNAL_API_TOKEN is empty")
        self._urls = AylaUrlBuilder(base_url)
        self._token = service_token
        self._timeout_s = timeout_s
        self._transport = transport

    def _headers(
        self,
        *,
        actor_external_id: str,
        tenant_slug: str,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        """Service proves the request; the headers name the human and the salon.

        ``X-Tenant`` is not decoration. Ayla's ``TenantContextMiddleware``
        resolves it into ``request.tenant``, and ``IsTenantAdmin`` — the
        permission that keeps an administrator of salon A out of salon B —
        refuses outright when it is None. Omitting it would not fail open;
        it would fail with a 403 that looks like a rights problem and is
        actually a missing header.

        The header states which salon we are acting **in**, which is a
        different claim from which salon the actor belongs to. Ayla compares
        the two; that comparison is the second factor against
        admin-of-A-acts-on-B.
        """

        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-External-User-ID": actor_external_id,
            "X-Tenant": tenant_slug,
            "Accept": "application/json",
        }
        # Reads carry no idempotency key: there is nothing to de-duplicate,
        # and sending one would suggest to the reader that there is.
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
            headers["Content-Type"] = "application/json"
        return headers

    def _post(
        self,
        endpoint: str,
        *,
        actor_external_id: str,
        idempotency_key: str,
        tenant_slug: str,
        json_body: dict[str, Any],
    ) -> dict[str, Any]:
        url = self._urls.build(f"tenants/me/{endpoint.lstrip('/')}")
        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as http:
                resp = http.post(
                    url,
                    headers=self._headers(
                        actor_external_id=actor_external_id,
                        idempotency_key=idempotency_key,
                        tenant_slug=tenant_slug,
                    ),
                    json=json_body,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            # Deliberately SalonUnavailable and not a failure: the write may
            # have landed. See the class docstring.
            logger.warning("salon_client.%s.network err=%s", endpoint, type(exc).__name__)
            raise SalonUnavailable(f"network: {type(exc).__name__}") from exc

        if resp.status_code in (200, 201):
            try:
                return resp.json()
            except ValueError as exc:
                raise SalonUnavailable("upstream returned non-JSON on success") from exc

        self._raise_for_status(resp)
        raise SalonAPIError("unreachable")  # pragma: no cover — _raise_for_status always raises

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        """Turn a non-2xx into the exception that says what to do about it.

        Shared by reads and writes so the two can never drift into
        disagreeing about what a 403 means.
        """

        detail = _detail(resp)
        code = _error_code(resp)
        if resp.status_code == 400:
            raise SalonValidationError(detail)
        if resp.status_code == 401:
            raise SalonUnauthorized(detail)
        if resp.status_code == 403:
            raise SalonForbidden(detail)
        if resp.status_code == 404:
            raise SalonNotFound(detail)
        if resp.status_code == 409:
            # One status, three meanings on this surface — the code is the
            # only thing that separates them, so it is read rather than
            # collapsed. Unknown 409s stay «slot taken»: it is the common
            # case and the safe instruction (look again), whereas guessing
            # «stale» would tell the user to refresh a booking that did
            # not change.
            if code == "STALE_VERSION":
                raise SalonStaleVersion(detail)
            raise SalonSlotTaken(detail)
        if resp.status_code == 422:
            raise SalonNotAllowed(detail)
        if resp.status_code >= 500:
            raise SalonUnavailable(f"upstream {resp.status_code}: {detail}")
        raise SalonAPIError(f"unexpected {resp.status_code}: {detail}")

    def _get(
        self,
        endpoint: str,
        *,
        actor_external_id: str,
        tenant_slug: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        url = self._urls.build(f"tenants/me/{endpoint.lstrip('/')}")
        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as http:
                resp = http.get(
                    url,
                    headers=self._headers(
                        actor_external_id=actor_external_id,
                        tenant_slug=tenant_slug,
                    ),
                    params=params,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("salon_client.%s.network err=%s", endpoint, type(exc).__name__)
            raise SalonUnavailable(f"network: {type(exc).__name__}") from exc

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise SalonUnavailable("upstream returned non-JSON on success") from exc

        self._raise_for_status(resp)
        raise SalonAPIError("unreachable")  # pragma: no cover — _raise_for_status always raises

    def create_appointment(
        self,
        *,
        actor_external_id: str,
        idempotency_key: str,
        tenant_slug: str,
        specialist_id: str,
        service_id: str,
        start_datetime: str,
        client_id: str | None = None,
        client_name: str | None = None,
        client_phone: str | None = None,
    ) -> dict[str, Any]:
        """Book a customer in. Mirrors ``SalonBookingCreateSerializer``.

        Exactly one identification path, enforced here as well as upstream:
        ``client_id`` for an existing customer of this salon, or
        ``client_name`` + ``client_phone`` for a new guest — the §14 minimum,
        which is the same pair the canonical serializer asks for.

        Checking it locally is not distrust of Ayla: sending both, or neither,
        is a caller bug, and a 400 round-trip is a slower way to learn that.
        """

        has_id = bool(client_id)
        has_name = bool(client_name)
        if has_id == has_name:
            raise SalonValidationError("provide exactly one of client_id or client_name")
        if has_name and not client_phone:
            raise SalonValidationError("a new guest needs a name and a phone")
        if not tenant_slug:
            # See _headers: a missing tenant becomes a 403 that reads like a
            # rights failure. Refuse locally where the message is honest.
            raise SalonValidationError("tenant_slug is required")
        if not idempotency_key:
            # Upstream would invent one per request, which silently turns a
            # retry into a second booking. Refuse rather than book twice.
            raise SalonValidationError("idempotency_key is required for a write")

        body: dict[str, Any] = {
            "specialist_id": specialist_id,
            "service_id": service_id,
            "start_datetime": start_datetime,
        }
        if has_id:
            body["client_id"] = client_id
        else:
            body["client_name"] = client_name
            body["client_phone"] = client_phone

        return self._post(
            "appointments/",
            actor_external_id=actor_external_id,
            idempotency_key=idempotency_key,
            tenant_slug=tenant_slug,
            json_body=body,
        )

    #: What a salon may legitimately assert about its own cancellation.
    #: Ayla's closed allowlist (``SALON_CANCELLATION_REASON_CODES``): the
    #: ``user_*`` codes are the customer's business and
    #: ``payment_hold_expired`` is the payment system's fact, so letting
    #: the salon claim either would let one party author another's
    #: attribution. Mirrored here to refuse locally rather than learn it
    #: from a 400.
    CANCELLATION_REASON_CODES = frozenset({"master_unavailable", "tenant_closed_slot", "other"})

    def cancel_appointment(
        self,
        *,
        actor_external_id: str,
        tenant_slug: str,
        appointment_id: str,
        reason: str = "",
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        """Cancel a booking of this salon, attributed to the acting admin.

        No ``expected_version``: cancellation is idempotent in intent —
        the end state is the same however many times you ask — so Ayla
        does not gate it on a version, and neither does this.

        ``reason`` is free text for the record; ``reason_code`` is the
        structured claim. Omitted, Ayla defaults it to ``other``, which is
        the honest value when the salon has not said why.
        """

        if not tenant_slug:
            raise SalonValidationError("tenant_slug is required")
        if not appointment_id:
            raise SalonValidationError("appointment_id is required")
        if reason_code is not None and reason_code not in self.CANCELLATION_REASON_CODES:
            raise SalonValidationError(
                f"reason_code must be one of {sorted(self.CANCELLATION_REASON_CODES)}"
            )

        body: dict[str, Any] = {"reason": reason or ""}
        if reason_code is not None:
            body["reason_code"] = reason_code

        return self._post(
            f"appointments/{appointment_id}/cancel/",
            actor_external_id=actor_external_id,
            # Cancel takes no idempotency key upstream (`command_key` is an
            # audit trace there, never queried), but the header is what
            # switches _post into write mode; the value is the audit trail.
            idempotency_key=f"cancel:{appointment_id}",
            tenant_slug=tenant_slug,
            json_body=body,
        )

    def reschedule_appointment(
        self,
        *,
        actor_external_id: str,
        tenant_slug: str,
        appointment_id: str,
        new_start_datetime: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Move a booking. ``expected_version`` is required, not optional.

        Upstream makes it required on this surface specifically (it is
        optional on the mobile path only because old app builds exist),
        and the reason is the whole concurrency story: the version says
        «the booking I am moving is the booking I was shown». Without it
        two people moving the same booking both succeed and the second
        silently wins.

        **Not wired to a screen yet, and deliberately so.** The version
        has to be the canonical one, and as of 2026-08-21 the bot has no
        canonical read that carries it: the day journal here is built
        from ``RemoteBookingProxy``, whose
        ``last_applied_appointment_version`` is NULL unless a canonical
        ``appointment.rescheduled`` event happened to have been applied.
        Measured on the pilot the same day: 2 of 23 mirrored bookings
        carry a version, and the single future confirmed booking — the
        only one anybody could actually move — carries none. Sending that
        NULL, or inventing a 1, would make every move fail as STALE or,
        worse, succeed against the wrong revision.

        So this method is complete and tested, and the caller is missing
        on purpose. See the report §31 for the two ways to supply the
        version; both are Ayla-side and neither is guesswork here.
        """

        if not tenant_slug:
            raise SalonValidationError("tenant_slug is required")
        if not appointment_id:
            raise SalonValidationError("appointment_id is required")
        if not isinstance(expected_version, int) or expected_version < 1:
            # A caller with no version must not reach the network: the
            # request would be answered, and answered wrongly.
            raise SalonValidationError("expected_version must be a positive integer")

        return self._post(
            f"appointments/{appointment_id}/reschedule/",
            actor_external_id=actor_external_id,
            idempotency_key=f"reschedule:{appointment_id}:{expected_version}",
            tenant_slug=tenant_slug,
            json_body={
                "new_start_datetime": new_start_datetime,
                "expected_version": expected_version,
            },
        )

    MIN_QUERY = 2

    def search_customers(
        self,
        *,
        actor_external_id: str,
        tenant_slug: str,
        query: str,
    ) -> list[dict[str, Any]]:
        """Find a returning customer of this salon. §13 of the UX contract.

        Upstream contract as read in ``SalonCustomerLookupView`` (Ayla,
        2026-08-21), not as guessed:

        * ``q`` — at least two characters, else 400;
        * matches a first/last name from the start, or a phone **exactly**
          (a prefix match on digits would make this a way to sweep the
          customer list a few keystrokes at a time);
        * answers ``{"data": {"results": [{"id", "name"}]}}``, at most 20;
        * **never returns a phone.** The number is an input you already
          have, never an output — DRF-1039.

        The two-character floor is enforced here as well so a one-letter
        keystroke costs nothing instead of a round-trip and a 400.
        """

        if not tenant_slug:
            raise SalonValidationError("tenant_slug is required")
        query = (query or "").strip()
        if len(query) < self.MIN_QUERY:
            raise SalonValidationError(f"query must be at least {self.MIN_QUERY} characters")

        payload = self._get(
            "customers/",
            actor_external_id=actor_external_id,
            tenant_slug=tenant_slug,
            params={"q": query},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            # A success shape we do not recognise is not «no customers».
            # §13: a failed search must never be rendered as proof that the
            # person is not there, so this fails loudly instead of quietly
            # returning [].
            raise SalonUnavailable("upstream returned an unrecognised search payload")
        return [row for row in results if isinstance(row, dict)]


def _error_code(resp: httpx.Response) -> str:
    """Ayla's machine-readable error code, or "" when there isn't one.

    Separate from :func:`_detail` because the two are read by different
    audiences: the message is shown to a human, the code decides which
    branch runs. Collapsing them would mean branching on prose.
    """

    try:
        payload = resp.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return str(err.get("code") or "")
    return ""


def _detail(resp: httpx.Response) -> str:
    """Best-effort human-readable detail from Ayla's error envelope."""

    try:
        payload = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or payload)[:200]
        return str(payload.get("detail") or payload)[:200]
    return str(payload)[:200]


def get_salon_client() -> AylaSalonClient:
    """Build a client from settings. Raises :class:`SalonNotConfigured`."""

    return AylaSalonClient(
        base_url=getattr(settings, "AYLA_BASE_URL", ""),
        service_token=getattr(settings, "AYLA_INTERNAL_API_TOKEN", ""),
    )


__all__ = [
    "AylaSalonClient",
    "SalonAPIError",
    "SalonForbidden",
    "SalonNotConfigured",
    "SalonNotFound",
    "SalonSlotTaken",
    "SalonUnauthorized",
    "SalonUnavailable",
    "SalonValidationError",
    "get_salon_client",
]
