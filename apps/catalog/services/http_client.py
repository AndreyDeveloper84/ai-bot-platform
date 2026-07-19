"""HTTP client for the Ayla internal **canonical catalog** (S3B / #1044).

Typed wrapper around :class:`httpx.Client` for Ayla's read-only internal
catalog surface (`SalonService` → `SpecialistService`, per
``docs/CATALOG_INTERNAL_API_CONTRACT.md`` on the Ayla repo). The upserter
(:mod:`apps.catalog.services.upserter`) consumes the returned DTOs.

**S3B STAGE 2 PR-1 scope:** only ``salon-services`` (→ ``CatalogService``).
``specialist-services`` (bookable → ``MasterService``) and the specialist
enrichment endpoint (``/internal/specialists/`` → ``CatalogMaster``) land in
PR-2, which owns the tenant-scoped master mirror.

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
from datetime import datetime
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
    raw: dict[str, Any] = field(default_factory=dict)


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

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _fetch_all(self, path: str, *, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Walk the pagination chain. Returns a flat list of raw row dicts."""
        rows: list[dict[str, Any]] = []
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
            rows.extend(payload.get("results", []))
            url = payload.get("next") or None
            request_params = None
        return rows

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
        raw=row,
    )
