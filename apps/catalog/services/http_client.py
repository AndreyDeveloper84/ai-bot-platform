"""HTTP client for mysite catalog endpoints (DRF-573 / Sprint 7 / C2).

Typed wrapper around :class:`httpx.Client` for the four
``mysite/api/v1/catalog/`` endpoints that M1 (DRF-592) ships. The
upserter (C3 / DRF-574) consumes the returned DTOs.

### Auth — service-token

Every request carries ``X-Service-Token`` from
``settings.MYSITE_CATALOG_SERVICE_TOKEN`` (configured in C8). The
mysite middleware (M2 / DRF-593) ``hmac.compare_digest``-s against
its own copy.

### Retry policy

Three attempts, exponential backoff (0.5s, 1s, 2s), on 5xx + network
errors. 4xx (401/403/404) raise immediately — retrying an auth
failure is wasted effort.

* :class:`CatalogAuthError` — 401/403. Operator must fix the token.
* :class:`CatalogTransportError` — 5xx after retries exhausted.
* :class:`CatalogClientError` — 4xx other than auth. Bug on either
  side; bail and let the catch-up sync retry next beat.

### Cursor pagination

mysite responses carry ``{"results": [...], "next": "<url>|null"}``.
Each `fetch_*` follows the ``next`` chain until exhausted. We don't
buffer to disk — mysite catalog is small (≤ 10k rows total),
in-memory is fine. Sprint 8 reconsiders if catalog volume grows.

### `since` semantics

``since: datetime | None`` is rendered as ``?since=<ISO 8601 Z>``.
mysite filters ``updated_at > since`` (strict greater-than) so the
sync cursor is forward-only — no row appears twice across runs.
``since=None`` fetches the full catalog (M2 force-resync path).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogServiceDTO:
    external_id: int
    external_updated_at: datetime
    slug: str
    name: str
    short_description: str = ""
    description: str = ""
    price_from: Decimal | None = None
    duration_min: int | None = None
    is_active: bool = True
    is_popular: bool = False
    seo_title: str = ""
    seo_description: str = ""
    goals: list[str] = field(default_factory=list)
    requires_health_check: bool = False
    contraindications: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogMasterDTO:
    external_id: int
    external_updated_at: datetime
    name: str
    specialization: str = ""
    bio: str = ""
    experience: str = ""
    rating: Decimal | None = None
    is_active: bool = True
    yclients_staff_id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogFaqDTO:
    external_id: int
    external_updated_at: datetime
    question: str
    answer: str
    category_slug: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogHelpArticleDTO:
    external_id: int
    external_updated_at: datetime
    question: str
    answer: str
    order: int = 0
    is_active: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CatalogError(Exception):
    """Base — anything sync-side that's not the happy path."""


class CatalogAuthError(CatalogError):
    """401 / 403 from mysite. Token mismatch or missing."""


class CatalogClientError(CatalogError):
    """4xx other than auth. Misshapen request — operator/code bug."""


class CatalogTransportError(CatalogError):
    """5xx or network failure after retries exhausted."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


_BACKOFF_SECONDS = (0.5, 1.0, 2.0)


class CatalogHttpClient:
    """Fetch mysite catalog rows by type with auth + retry + pagination.

    Construction params are mostly settings overrides for tests; prod
    code calls ``CatalogHttpClient()`` and reads from Django settings.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        service_token: str | None = None,
        timeout: int | None = None,
        retries: int | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = (
            base_url or getattr(settings, "MYSITE_CATALOG_BASE_URL", "") or ""
        ).rstrip("/")
        self._token = service_token or getattr(settings, "MYSITE_CATALOG_SERVICE_TOKEN", "") or ""
        self._timeout = (
            timeout if timeout is not None else getattr(settings, "CATALOG_SYNC_HTTP_TIMEOUT", 30)
        )
        self._retries = (
            retries if retries is not None else getattr(settings, "CATALOG_SYNC_HTTP_RETRIES", 3)
        )
        # Injected client for tests (pytest-httpx). Real callers leave
        # this None — we build a session on first use.
        self._http: httpx.Client | None = http_client

    # ------------------------------------------------------------------
    # Public fetch methods
    # ------------------------------------------------------------------

    def fetch_services(self, *, since: datetime | None = None) -> list[CatalogServiceDTO]:
        return [_parse_service(row) for row in self._fetch_all("services", since=since)]

    def fetch_masters(self, *, since: datetime | None = None) -> list[CatalogMasterDTO]:
        return [_parse_master(row) for row in self._fetch_all("masters", since=since)]

    def fetch_faqs(self, *, since: datetime | None = None) -> list[CatalogFaqDTO]:
        return [_parse_faq(row) for row in self._fetch_all("faqs", since=since)]

    def fetch_help_articles(self, *, since: datetime | None = None) -> list[CatalogHelpArticleDTO]:
        return [_parse_help_article(row) for row in self._fetch_all("help-articles", since=since)]

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _fetch_all(self, resource: str, *, since: datetime | None) -> list[dict[str, Any]]:
        """Walk the cursor chain. Returns flat list of raw row dicts."""
        rows: list[dict[str, Any]] = []
        url: str | None = urljoin(self._base_url + "/", f"api/v1/catalog/{resource}/")
        params: dict[str, Any] | None = None
        if since is not None:
            params = {"since": since.isoformat()}
        # After the first hop, `next` is an absolute URL with its own
        # query string baked in.
        while url:
            payload = self._get_with_retry(url, params=params)
            rows.extend(payload.get("results", []))
            url = payload.get("next") or None
            params = None  # only on the first hop
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
                    headers={"X-Service-Token": self._token},
                    timeout=self._timeout,
                )
                if response.status_code in (401, 403):
                    raise CatalogAuthError(
                        f"mysite catalog auth failed: HTTP {response.status_code} "
                        f"(token prefix={self._token[:4]!r}…)"
                    )
                if 400 <= response.status_code < 500:
                    raise CatalogClientError(
                        f"mysite catalog 4xx: HTTP {response.status_code} url={url} "
                        f"body={response.text[:200]!r}"
                    )
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError("5xx", request=response.request, response=response)
                return response.json()
            except CatalogAuthError:
                # Don't retry auth — escalate immediately.
                raise
            except CatalogClientError:
                # Don't retry 4xx — escalate immediately.
                raise
            except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                # Last attempt — give up.
                if attempt == attempts - 1:
                    break
                # Backoff before retrying. `_BACKOFF_SECONDS[i]` covers
                # attempts 0/1/2; bound the lookup to that table.
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
            f"mysite catalog: exhausted {attempts} retries on {url}"
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
    # Python datetime.fromisoformat accepts "...+00:00" but not "...Z"
    # until 3.11+. We normalise here to keep the call site clean.
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _parse_decimal(raw: Any) -> Decimal | None:
    if raw in (None, ""):
        return None
    return Decimal(str(raw))


def _parse_int(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    return int(raw)


def _parse_service(row: dict[str, Any]) -> CatalogServiceDTO:
    return CatalogServiceDTO(
        external_id=int(row["id"]),
        external_updated_at=_parse_dt(row["updated_at"]),
        slug=row.get("slug", ""),
        name=row.get("name", ""),
        short_description=row.get("short_description", "") or "",
        description=row.get("description", "") or "",
        price_from=_parse_decimal(row.get("price_from")),
        duration_min=_parse_int(row.get("duration_min")),
        is_active=bool(row.get("is_active", True)),
        is_popular=bool(row.get("is_popular", False)),
        seo_title=row.get("seo_title", "") or "",
        seo_description=row.get("seo_description", "") or "",
        goals=list(row.get("goals") or []),
        requires_health_check=bool(row.get("requires_health_check", False)),
        contraindications=row.get("contraindications", "") or "",
        raw=row,
    )


def _parse_master(row: dict[str, Any]) -> CatalogMasterDTO:
    return CatalogMasterDTO(
        external_id=int(row["id"]),
        external_updated_at=_parse_dt(row["updated_at"]),
        name=row.get("name", ""),
        specialization=row.get("specialization", "") or "",
        bio=row.get("bio", "") or "",
        experience=row.get("experience", "") or "",
        rating=_parse_decimal(row.get("rating")),
        is_active=bool(row.get("is_active", True)),
        yclients_staff_id=_parse_int(row.get("yclients_staff_id")),
        raw=row,
    )


def _parse_faq(row: dict[str, Any]) -> CatalogFaqDTO:
    return CatalogFaqDTO(
        external_id=int(row["id"]),
        external_updated_at=_parse_dt(row["updated_at"]),
        question=row.get("question", ""),
        answer=row.get("answer", ""),
        category_slug=row.get("category_slug", "") or "",
        raw=row,
    )


def _parse_help_article(row: dict[str, Any]) -> CatalogHelpArticleDTO:
    return CatalogHelpArticleDTO(
        external_id=int(row["id"]),
        external_updated_at=_parse_dt(row["updated_at"]),
        question=row.get("question", ""),
        answer=row.get("answer", ""),
        order=int(row.get("order", 0)),
        is_active=bool(row.get("is_active", True)),
        raw=row,
    )
