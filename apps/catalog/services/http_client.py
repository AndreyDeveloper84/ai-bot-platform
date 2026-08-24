"""HTTP client for the Ayla internal **canonical catalog** (S3B / #1044).

Typed wrapper around :class:`httpx.Client` for Ayla's read-only internal
catalog surface (`SalonService` → `SpecialistService`, per
``docs/CATALOG_INTERNAL_API_CONTRACT.md`` on the Ayla repo). The upserter
(:mod:`apps.catalog.services.upserter`) consumes the returned DTOs.

Covers all three read surfaces the mirror needs:

* ``salon-services`` → ``CatalogService``
* ``/internal/specialists/`` → ``CatalogMaster``
* ``specialist-services`` → ``MasterService`` (the bookable master↔service
  edge; added for DRF-945 so service-specific discovery can join through a
  real relation instead of the free-text ``CatalogMaster.specialization``)

This replaces the retired mysite catalog client — bot-platform no longer
reads mysite's Postgres or its ``/api/v1/catalog/*`` HTTP surface (ADR-0009
strangler-fig: mysite is retired).

### Auth — service-to-service Bearer

Every request carries ``Authorization: Bearer <AYLA_INTERNAL_API_TOKEN>``
(the single s2s token Ayla validates via ``IsInternalBearer``; a
wrong/missing token fails closed → 403). No mobile JWT, no ``X-App-Type``.

### URL construction

Built through :class:`apps.integrations.ayla.url_builder.AylaUrlBuilder`
(#1049), which owns host-only validation of ``AYLA_BASE_URL`` and inserts
the ``api/v1`` version prefix — this module never hand-builds an
``f"{base}/..."`` string.

### Pagination

Ayla list responses use DRF ``PageNumberPagination``:
``{"count": N, "next": "<abs-url>|null", "previous": ..., "results": [...]}``.
Each ``fetch_*`` follows the ``next`` chain (absolute URLs) until exhausted.
The catalog is small (one pilot salon); in-memory buffering is fine.

### Retry policy

Three attempts, exponential backoff (0.5s, 1s, 2s), on 5xx + network
errors. 4xx raise immediately — retrying an auth/shape failure is wasted.

* :class:`CatalogAuthError` — 401/403. Token mismatch or missing.
* :class:`CatalogTransportError` — 5xx after retries exhausted / config gap.
* :class:`CatalogClientError` — 4xx other than auth. Bug on either side.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from django.conf import settings

from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogSalonServiceDTO:
    """One row of ``GET /api/v1/internal/catalog/salon-services/``.

    ``ayla_service_id`` is the stable ``SalonService.id`` (UUID str) the
    mirror re-keys on. Columnar fields the mirror stores map directly;
    ``template``/``category`` have no mirror column yet and ride in ``raw``.

    ``goals`` (DRF-1308) arrives **already resolved** by Ayla — a list of
    ``{"key", "label"}``. It has to: this platform has no category table at
    all, so the ``category`` UUID above is an opaque string here and the
    goal tree cannot be walked on this side. ADR-0009 — the mirror is a
    read-replica, never the source of truth. An empty list is the honest
    "no goal declared", not a sync failure.
    """

    ayla_service_id: str
    external_updated_at: datetime
    name: str
    is_active: bool = True
    requires_health_check: bool = False
    price_from: Decimal | None = None
    duration_min: int | None = None
    template: str | None = None
    category: str | None = None
    goals: list[dict[str, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogSpecialistDTO:
    """One row of ``GET /api/v1/internal/specialists/`` (S3B masters mirror).

    ``ayla_master_id`` is Ayla's SpecialistProfile.id (canonical UUID the
    mirror keys on); ``user_id`` is the Ayla User UUID carried by
    ``CatalogMaster.ayla_user_id`` (event/booking bridge, AMD-005).
    ``is_active`` mirrors status==active AND is_available upstream; the
    feed's queryset already filters to those, but the mapping stays
    explicit for forward-compat. Platform-owned fields (invite_status,
    photo_url, archived_at…) never ride here — sync must not touch them.

    ``tenant`` is the owning salon as Ayla states it (DRF-1313). It exists so
    the upsert can check the scope it asked for instead of trusting that the
    ``?tenant=`` filter was honoured — the same guard the edge DTO already
    carries. ``None`` when the upstream predates the field, which the guard
    treats as "cannot verify", not as "mismatch".
    """

    ayla_master_id: str
    user_id: str | None
    name: str
    external_updated_at: datetime
    tenant: str | None = None
    bio: str = ""
    experience: str = ""
    rating: Decimal | None = None
    review_count: int = 0
    is_active: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogSpecialistServiceDTO:
    """One row of ``GET /api/v1/internal/catalog/specialist-services/``.

    Ayla's canonical **bookable edge** (``SpecialistService``) — the
    master↔service relation the ``MasterService`` mirror is built from
    (DRF-945). Per ``docs/CATALOG_INTERNAL_API_CONTRACT.md`` §2:

    * ``ayla_specialist_service_id`` — ``SpecialistService.id``, the stable
      booking key and this mirror's provenance stamp.
    * ``specialist`` — ``SpecialistProfile.id``. This equals
      ``CatalogMaster.id`` locally: :func:`upsert_specialists` keys the master
      mirror on the same id (``/internal/specialists/`` ``row["id"]``).
    * ``salon_service`` — ``SalonService.id`` → ``CatalogService.ayla_service_id``.
    * ``user_id`` — Ayla ``User.id``. Deliberately NOT the same as
      ``specialist``; carried for cross-checks only, never as a join key.

    ``resolved_duration`` still rides in ``raw`` only — it belongs to the
    booking gate, not to discovery.

    ``resolved_requires_health_check`` (DRF-1353) is now a first-class field
    because the gate finally has a reader for it
    (``apps.skills.booking.skill._service_requires_health_check``). It is
    ``bool | None``: ``None`` means the upstream row did not carry the key at
    all — an older Ayla — and MUST NOT be read as "no screening needed". Only
    an explicit ``False`` opens the gate; ``None`` keeps it closed.
    """

    ayla_specialist_service_id: str
    salon_service: str
    specialist: str
    external_updated_at: datetime
    tenant: str | None = None
    user_id: str | None = None
    name: str = ""
    category_slug: str = ""
    is_active: bool = True
    resolved_requires_health_check: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeSnapshot:
    """A tenant's bookable edges plus whether the snapshot is trustworthy.

    ``complete`` is the licence to delete. Reconciliation infers "this edge no
    longer exists upstream" from absence, and absence is only meaningful in a
    snapshot known to be whole — so an incomplete walk downgrades the beat to
    additive-only instead of deleting rows that were merely missed.
    """

    edges: list[CatalogSpecialistServiceDTO]
    complete: bool = True


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CatalogError(Exception):
    """Base — anything sync-side that's not the happy path."""


class CatalogAuthError(CatalogError):
    """401 / 403 from Ayla. Bearer token mismatch or missing."""


class CatalogClientError(CatalogError):
    """4xx other than auth. Misshapen request — operator/code bug."""


class CatalogTransportError(CatalogError):
    """5xx / network failure after retries exhausted, or a config gap."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


_BACKOFF_SECONDS = (0.5, 1.0, 2.0)


class CatalogHttpClient:
    """Fetch Ayla internal-catalog rows with Bearer auth + retry + pagination.

    Construction params are mostly settings overrides for tests; prod code
    calls ``CatalogHttpClient()`` and reads ``AYLA_BASE_URL`` /
    ``AYLA_INTERNAL_API_TOKEN`` from Django settings.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: int | None = None,
        retries: int | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = (
            base_url if base_url is not None else getattr(settings, "AYLA_BASE_URL", "")
        )
        self._token = (
            token if token is not None else getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
        )
        self._timeout = (
            timeout if timeout is not None else getattr(settings, "CATALOG_SYNC_HTTP_TIMEOUT", 30)
        )
        self._retries = (
            retries if retries is not None else getattr(settings, "CATALOG_SYNC_HTTP_RETRIES", 3)
        )
        # Injected client for tests (pytest-httpx). Real callers leave this
        # None — we build a session on first use.
        self._http: httpx.Client | None = http_client

    # ------------------------------------------------------------------
    # Public fetch methods
    # ------------------------------------------------------------------

    def fetch_salon_services(self, *, tenant_id: str) -> list[CatalogSalonServiceDTO]:
        """Salon services for one tenant (→ ``CatalogService``).

        ``tenant_id`` is the salon's Ayla Tenant UUID — the bot's
        ``Tenant.id`` is the same UUID (ported from Ayla ``tenants``), so
        the caller passes ``str(tenant.id)`` for the ``?tenant=`` filter.
        """
        rows = self._fetch_all(
            "internal/catalog/salon-services/",
            params={"tenant": tenant_id},
        )
        return [_parse_salon_service(row) for row in rows]

    def fetch_specialists(self, *, tenant_id: str) -> list[CatalogSpecialistDTO]:
        """Specialists for one tenant (→ ``CatalogMaster``) — S3B masters mirror.

        ``tenant_id`` is the salon's Ayla Tenant UUID, same as
        :meth:`fetch_salon_services` and :meth:`fetch_specialist_services`.

        The ``?tenant=`` filter landed upstream in DRF-1313. Before it, this
        pull was the full active roster of the platform and every syncing
        tenant upserted the same set: on 2026-08-23 the five masters of four
        newly loaded salons all landed under whichever tenant synced first,
        and three of five salons could not be booked at all. Sending the
        filter is therefore not an optimisation — it is what makes the mirror
        mean anything with more than one salon on the platform.

        The rows carry their own ``tenant``; :func:`upsert_specialists`
        re-checks it rather than trusting that the filter was honoured.
        """
        rows = self._fetch_all(
            "internal/specialists/",
            params={"tenant": tenant_id},
        )
        return [_parse_specialist(row) for row in rows]

    def fetch_specialist_services(self, *, tenant_id: str) -> EdgeSnapshot:
        """Bookable master↔service edges for one tenant (→ ``MasterService``).

        The ``?tenant=`` filter (contract §2) scopes the pull, so the returned
        list is the tenant's edge snapshot — which is what makes sync
        reconciliation possible (DRF-945). ``/internal/specialists/`` takes the
        same filter since DRF-1313; this handle simply had it first.

        Returns an :class:`EdgeSnapshot` rather than a bare list because the
        caller deletes rows on absence and therefore needs to know whether
        absence can be trusted.
        """
        rows, complete = self._fetch_all_checked(
            "internal/catalog/specialist-services/",
            # page_size=100 (the contract's documented maximum) is a
            # correctness requirement, not a performance tweak: reconciliation
            # deletes owned rows absent from this snapshot, and upstream orders
            # by a non-unique ``created_at``. With the default PAGE_SIZE=20 a
            # tie or a concurrent insert between page fetches can drop a row
            # from the snapshot, which would read as "deleted upstream".
            # Fewer pages ⇒ fewer seams where that can happen.
            params={"tenant": tenant_id, "page_size": 100},
        )
        return EdgeSnapshot(
            edges=[_parse_specialist_service(row) for row in rows],
            complete=complete,
        )

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _fetch_all(self, path: str, *, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Walk the pagination chain. Returns a flat list of raw row dicts."""
        return self._fetch_all_checked(path, params=params)[0]

    def _fetch_all_checked(
        self, path: str, *, params: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        """Paginated fetch plus a completeness verdict.

        The second element is False when the walk collected a different number
        of rows than the first page's ``count`` advertised. That happens when
        an upstream insert shifts the LIMIT/OFFSET window between page fetches
        (upstream orders by a non-unique ``created_at``), which silently drops
        a row from the snapshot.

        Callers that only add rows can ignore the flag. Callers that DELETE on
        absence must not: "no exception was raised" is not evidence that a
        snapshot is complete, and a dropped row is indistinguishable from a
        deleted one.
        """
        rows: list[dict[str, Any]] = []
        advertised: int | None = None
        try:
            url: str | None = AylaUrlBuilder(self._base_url).build(path)
        except AylaUrlError as exc:
            # A malformed / empty AYLA_BASE_URL is a config gap, not an Ayla
            # outage. Surface as transport-error so the orchestrator records
            # it and the beat retries next cycle once the env is fixed.
            raise CatalogTransportError(f"invalid AYLA_BASE_URL: {exc}") from exc
        if not self._token:
            raise CatalogTransportError("AYLA_INTERNAL_API_TOKEN not configured")

        request_params: dict[str, Any] | None = params
        # After the first hop, `next` is an absolute URL with its own query
        # string (tenant + page) baked in — pass no params.
        while url:
            payload = self._get_with_retry(url, params=request_params)
            if advertised is None:
                count = payload.get("count")
                advertised = int(count) if isinstance(count, int) else None
            rows.extend(payload.get("results", []))
            url = payload.get("next") or None
            request_params = None

        complete = advertised is None or advertised == len(rows)
        if not complete:
            logger.warning(
                "catalog.http.snapshot_incomplete path=%s advertised=%s collected=%d — "
                "upstream page window shifted mid-walk; reconciliation must not treat "
                "this as proof of absence.",
                path,
                advertised,
                len(rows),
            )
        return rows, complete

    def _get_with_retry(self, url: str, *, params: dict[str, Any] | None) -> dict[str, Any]:
        last_exc: Exception | None = None
        attempts = self._retries
        for attempt in range(attempts):
            try:
                client = self._client()
                response = client.get(
                    url,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/json",
                    },
                    timeout=self._timeout,
                )
                if response.status_code in (401, 403):
                    raise CatalogAuthError(
                        f"Ayla catalog auth failed: HTTP {response.status_code} "
                        f"(token prefix={self._token[:4]!r}…)"
                    )
                if 400 <= response.status_code < 500:
                    raise CatalogClientError(
                        f"Ayla catalog 4xx: HTTP {response.status_code} url={url} "
                        f"body={response.text[:200]!r}"
                    )
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError("5xx", request=response.request, response=response)
                return response.json()
            except CatalogAuthError:
                raise
            except CatalogClientError:
                raise
            except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt == attempts - 1:
                    break
                pause = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "catalog.http.retry attempt=%s pause=%s url=%s exc=%s",
                    attempt + 1,
                    pause,
                    url,
                    exc.__class__.__name__,
                )
                time.sleep(pause)
        raise CatalogTransportError(
            f"Ayla catalog: exhausted {attempts} retries on {url}"
        ) from last_exc

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> CatalogHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# DTO parsers
# ---------------------------------------------------------------------------


def _parse_dt(raw: str) -> datetime:
    """ISO 8601 with optional trailing ``Z`` → aware datetime."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _parse_decimal(raw: Any) -> Decimal | None:
    if raw in (None, ""):
        return None
    return Decimal(str(raw))


def _parse_int(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    return int(raw)


def _parse_optional_bool(raw: Any) -> bool | None:
    """Tri-state bool for a field whose ABSENCE must not read as ``False``.

    DRF-1353: ``resolved_requires_health_check`` gates a medical screening.
    A missing key (older Ayla, partial serializer) is "unknown", and the
    booking gate treats unknown as CLOSED. Coercing it to ``False`` here
    would silently open the gate for every edge on an upstream that never
    sends the field — exactly the fail-OPEN regress #1121 warned about.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
        return None
    if isinstance(raw, int):
        return bool(raw)
    return None


def _parse_goals(raw: Any) -> list[dict[str, str]]:
    """Ayla ``goals`` → mirror shape, defensively (DRF-1308).

    The field is additive on the Ayla contract, so an older upstream simply
    omits it. A malformed entry is dropped rather than aborting the row:
    a goal is enrichment, and losing the whole service over it would be a
    worse outcome than losing one label.
    """
    if not isinstance(raw, list):
        return []
    parsed: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        key, label = entry.get("key"), entry.get("label")
        if isinstance(key, str) and isinstance(label, str) and key and label:
            parsed.append({"key": key, "label": label})
    return parsed


def _parse_salon_service(row: dict[str, Any]) -> CatalogSalonServiceDTO:
    return CatalogSalonServiceDTO(
        ayla_service_id=str(row["id"]),
        external_updated_at=_parse_dt(row["updated_at"]),
        name=row.get("name", ""),
        is_active=bool(row.get("is_active", True)),
        requires_health_check=bool(row.get("requires_health_check", False)),
        price_from=_parse_decimal(row.get("base_price")),
        duration_min=_parse_int(row.get("duration_minutes")),
        template=row.get("template"),
        category=row.get("category"),
        goals=_parse_goals(row.get("goals")),
        raw=row,
    )


def _parse_specialist_service(row: dict[str, Any]) -> CatalogSpecialistServiceDTO:
    """Parse one bookable-edge row. Raises ``KeyError`` on a missing join key.

    ``id`` / ``salon_service`` / ``specialist`` are mandatory — an edge without
    them cannot be mirrored at all.

    Note this raises out of ``fetch_specialist_services`` and therefore aborts
    the whole tenant's edge batch, NOT just the offending row: parsing happens
    before the upserter's per-row savepoints. That is deliberate — it fails
    *safe* (nothing written, reconciliation never runs, the other two mirrors
    still land), and a malformed join key means the snapshot can no longer be
    trusted to prove absence, which is exactly when deleting rows is most
    dangerous. Loud and inert beats silent and destructive.

    ``updated_at`` is optional upstream; falls back to now (same policy as
    :func:`_parse_specialist`).
    """
    return CatalogSpecialistServiceDTO(
        ayla_specialist_service_id=str(row["id"]),
        salon_service=str(row["salon_service"]),
        specialist=str(row["specialist"]),
        external_updated_at=(
            _parse_dt(row["updated_at"]) if row.get("updated_at") else datetime.now(timezone.utc)
        ),
        tenant=str(row["tenant"]) if row.get("tenant") else None,
        user_id=str(row["user_id"]) if row.get("user_id") else None,
        name=row.get("name") or "",
        category_slug=row.get("category_slug") or "",
        is_active=bool(row.get("is_active", True)),
        resolved_requires_health_check=_parse_optional_bool(
            row.get("resolved_requires_health_check")
        ),
        raw=row,
    )


def _parse_specialist(row: dict[str, Any]) -> CatalogSpecialistDTO:
    experience_years = row.get("experience_years")
    return CatalogSpecialistDTO(
        ayla_master_id=str(row["id"]),
        user_id=str(row["user_id"]) if row.get("user_id") else None,
        name=row.get("display_name") or "",
        external_updated_at=(
            _parse_dt(row["updated_at"]) if row.get("updated_at") else datetime.now(timezone.utc)
        ),
        tenant=str(row["tenant"]) if row.get("tenant") else None,
        bio=row.get("bio") or "",
        experience=str(experience_years) if experience_years is not None else "",
        rating=_parse_decimal(row.get("rating")),
        review_count=int(row.get("reviews_count") or 0),
        is_active=bool(
            str(row.get("status", "")).lower() == "active" and row.get("is_available", True)
        ),
        raw=row,
    )
