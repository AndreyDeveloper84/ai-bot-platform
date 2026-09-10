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

Every walk asks for ``page_size=100`` (``_PAGE_SIZE``) on its first request;
Ayla's ``next`` links carry the parameter forward, so the whole chain runs at
that width. See ``_PAGE_SIZE`` for why the width is a quota question rather
than a latency one.

### Retry policy

Three attempts, exponential backoff (0.5s, 1s, 2s), on 5xx + network
errors. 4xx raise immediately — retrying an auth/shape failure is wasted.

``429`` is the one 4xx that is NOT wasted to retry, and since DRF-1595 it
is handled apart from its neighbours: Ayla answers over-quota reads with
``{"error":{"code":"THROTTLED","details":{"wait_seconds":54}}}``, i.e. it
tells us exactly when to come back. Before DRF-1595 that number was parsed
by nobody and the response fell into the generic 4xx branch below, so the
pilot's head salon went three days without a catalog refresh while the bot
told clients its services do not exist. We now sleep the time Ayla asked
for and retry — but only as far as a shared
:class:`~apps.catalog.services.throttle.ThrottleWaitBudget` allows, because
the beat that drives this has a soft time limit and sleeping per request is
how one broken salon becomes ten.

* :class:`CatalogAuthError` — 401/403. Token mismatch or missing.
* :class:`CatalogTransportError` — 5xx after retries exhausted / config gap.
* :class:`CatalogClientError` — 4xx other than auth/throttle. Bug either side.
* :class:`CatalogThrottledError` — 429 we could not wait out. Not a failure
  of the salon: the caller records it as *skipped*, and the next beat puts
  that salon first.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TypeVar

import httpx
from django.conf import settings

from apps.catalog.services.throttle import ThrottleWaitBudget
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
    # DRF-1588 — гео. ``address`` трёхзначен и обязан таким остаться:
    # ``None`` — ключа в строке НЕ БЫЛО (не знаем), ``""`` — ключ был и нёс
    # пустое (источник ответил «адреса нет»), строка — адрес. Ровно тот же
    # приём, что у ``resolved_requires_health_check`` ниже, и по той же
    # причине: отсутствие, свёрнутое в значение, читается как факт.
    # Координаты — ``None`` при любом отсутствии и НИКОГДА не ``0``.
    address: str | None = None
    location_lat: Decimal | None = None
    location_lng: Decimal | None = None
    # DRF-1588 — адрес САЛОНА, отдельным ключом ``tenant_address``, а не тем
    # же ``address``, что у мастера: в одной строке приезжают оба. Складывать
    # их здесь нечем и незачем — правило старшинства это DRF-1589. Ключа
    # сегодня ещё нет (его заводит DRF-1587), поэтому ``None`` — штатное
    # состояние, а не дефект.
    tenant_address: str | None = None
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
      ``specialist``; a cross-check first and a join key of last resort
      (DRF-1507): the edge upsert reaches for it only when ``specialist``
      resolves to no ``CatalogMaster`` at all, which is the invite-born row
      that lives under its own ``uuid4`` primary key.

    ``resolved_duration`` still rides in ``raw`` only — it belongs to the
    booking gate, not to discovery.

    ``resolved_requires_health_check`` (DRF-1353) is now a first-class field
    because the gate finally has a reader for it
    (``apps.skills.booking.skill._service_requires_health_check``). It is
    ``bool | None``: ``None`` means "no readable value" and MUST NOT be read
    as "no screening needed". Only an explicit ``False`` opens the gate.

    ``health_check_key_present`` splits that ``None`` in two, and the split
    is load-bearing:

    * **key absent** (``False``) — this payload does not speak about the
      field at all: an older Ayla, a partial serializer, a transport hiccup.
      The mirror must KEEP what it already knows. Overwriting a known
      verdict with "unknown" on that basis would make a medical gate
      flicker on every upstream wobble.
    * **key present, value ``null``** (``True``, value ``None``) — the
      catalog is speaking, and what it says is *"I do not know"*. That is an
      answer, and the mirror must record it as ``NULL``, which the booking
      gate reads as "screening required".

    Before this split both arrived as the same Python ``None``, so the
    upserter could only pick one behaviour for both — and it picked "keep",
    correctly, to protect against the hiccup. The price was that an explicit
    "unknown" could never reach the mirror at all. The catalog only started
    sending one once ``SpecialistService.resolved_requires_health_check``
    stopped turning a missing template into ``False``.
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
    #: Нёс ли ключ сам ответ. См. докстринг выше: отличает «поле не
    #: прислали» от «прислали null». Умолчание `False` — консервативное:
    #: вызывающий, собравший DTO руками и про поле не сказавший, получает
    #: прежнее поведение «сохранить, что было».
    health_check_key_present: bool = False
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


class CatalogThrottledError(CatalogError):
    """429 from Ayla that this run could not wait out (DRF-1595).

    Deliberately a sibling of :class:`CatalogClientError`, not a subclass,
    even though 429 *is* a 4xx. The whole point of the ticket is that this
    condition is not the same fact as "we sent a misshapen request": the
    salon is healthy, our request was fine, and the correct response is to
    stand down and come back — which the caller can only do if it can tell
    the two apart with an ``except`` clause. Sub-classing would have made
    every existing ``except CatalogClientError`` swallow it silently.

    ``wait_seconds`` is what Ayla asked for (``None`` when it did not say).
    ``budget_exhausted`` distinguishes "we ran out of permission to wait"
    from "we waited the full number of attempts and Ayla is still closed".
    """

    def __init__(
        self,
        message: str,
        *,
        wait_seconds: float | None = None,
        budget_exhausted: bool = False,
    ) -> None:
        super().__init__(message)
        self.wait_seconds = wait_seconds
        self.budget_exhausted = budget_exhausted


class CatalogTransportError(CatalogError):
    """5xx / network failure after retries exhausted, or a config gap."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


_BACKOFF_SECONDS = (0.5, 1.0, 2.0)

# Rows per page to ask Ayla for. 100 is ``core.pagination.DefaultPagination
# .max_page_size`` upstream — the documented ceiling, not a guess — and the
# parameter name is that class's ``page_size_query_param``.
#
# Sending it on ALL THREE walks is a DRF-1595 change. Only the edge walk had
# it; the other two ran at Ayla's ``PAGE_SIZE = 20`` default and therefore
# spent five requests per hundred rows where they could spend one. That
# multiplier lands on a quota far tighter than it looks: the internal catalog
# viewsets carry ``authentication_classes = []`` (bot Bearer is not a JWT), so
# ``request.user`` is anonymous and the throttle that fires is ``anon`` at
# 30/min — not the 120/min ``user`` rate. Halving our own request count is the
# cheapest lever we own on the limit that caused this ticket.
_PAGE_SIZE = 100


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
        wait_budget: ThrottleWaitBudget | None = None,
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
        # One wait budget per RUN, not per request (DRF-1595). The beat hands
        # the same object to every client it builds so ten salons cannot each
        # sleep out their own 429 and blow the task's soft time limit between
        # them. A caller that passes none (one-shot `manage.py sync_catalog`,
        # onboarding) still gets a ceiling rather than an unbounded one.
        self._wait_budget = (
            wait_budget if wait_budget is not None else ThrottleWaitBudget.from_settings()
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
            # page_size=100 — see PAGE_SIZE note on the class. Omitting it
            # here (until DRF-1595) meant this walk ran at Ayla's PAGE_SIZE=20
            # default, i.e. five requests where one would do, against a quota
            # that turns out to be the anonymous one.
            params={"tenant": tenant_id, "page_size": _PAGE_SIZE},
        )
        dtos, _failed = _parse_rows(
            rows, _parse_salon_service, path="internal/catalog/salon-services/"
        )
        return dtos

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
            # page_size=100 — see PAGE_SIZE note on the class (DRF-1595).
            params={"tenant": tenant_id, "page_size": _PAGE_SIZE},
        )
        dtos, _failed = _parse_rows(rows, _parse_specialist, path="internal/specialists/")
        return dtos

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
            params={"tenant": tenant_id, "page_size": _PAGE_SIZE},
        )
        edges, failed = _parse_rows(
            rows, _parse_specialist_service, path="internal/catalog/specialist-services/"
        )
        return EdgeSnapshot(
            edges=edges,
            # An edge this side could not read is not an edge upstream deleted.
            # Reconciliation deletes on absence, so a dropped row must downgrade
            # the run to additive-only exactly as a shifted page window does --
            # otherwise skipping one malformed edge would unbook a real master.
            complete=complete and failed == 0,
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
                # 429 is checked BEFORE the generic 4xx branch below, and the
                # order is the whole fix (DRF-1595). It used to fall through to
                # that branch, which raises terminally — so the one 4xx that
                # tells us how to succeed was the one we threw away.
                if response.status_code == 429:
                    self._wait_for_throttle(url, response, attempt=attempt, attempts=attempts)
                    continue
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
            except CatalogThrottledError:
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

    def _wait_for_throttle(
        self, url: str, response: httpx.Response, *, attempt: int, attempts: int
    ) -> None:
        """Sleep off one ``429``, or raise :class:`CatalogThrottledError`.

        Returns normally only when the caller should retry immediately after
        the sleep. Two ways it refuses instead, each a different fact:

        * this was the last attempt — Ayla is still closed and we are out of
          tries (``budget_exhausted=False``);
        * the run's wait budget will not cover what Ayla asked for — we are
          out of *permission* to wait (``budget_exhausted=True``, which the
          beat reads to stand down for the rest of the cycle).

        Both surface as the same exception type because both mean "this salon
        did not sync and it is not the salon's fault". Neither is a
        :class:`CatalogClientError` — see that class for why the distinction
        has to survive as far as the caller.
        """
        wait = _throttle_wait_seconds(response)
        if wait is None:
            # Ayla returned 429 without saying when to come back. We are not
            # entitled to invent a number, so fall back to the same backoff
            # ladder a 5xx would get — cheap, and if it is still closed the
            # next pass hits the "out of tries" branch above with the truth.
            wait = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
        if attempt == attempts - 1:
            raise CatalogThrottledError(
                f"Ayla catalog throttled: HTTP 429 url={url} — still limited after "
                f"{attempts} attempts (last wait_seconds={wait})",
                wait_seconds=wait,
            )
        if not self._wait_budget.consume(wait):
            raise CatalogThrottledError(
                f"Ayla catalog throttled: HTTP 429 url={url} — asked to wait {wait}s, "
                f"run budget has {self._wait_budget.remaining_seconds}s of "
                f"{self._wait_budget.total_seconds}s left",
                wait_seconds=wait,
                budget_exhausted=True,
            )
        logger.warning(
            "catalog.http.throttled attempt=%s wait_seconds=%s budget_remaining=%s url=%s",
            attempt + 1,
            wait,
            self._wait_budget.remaining_seconds,
            url,
        )
        time.sleep(wait)

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
# Throttle parsing
# ---------------------------------------------------------------------------


def _coerce_positive_seconds(raw: Any) -> float | None:
    """``raw`` → a usable sleep duration, or ``None``.

    Rejects the unusable rather than clamping it: a negative or
    unparseable ``wait_seconds`` is upstream telling us nothing, and
    silently turning nothing into ``0`` would spin the retry loop against a
    limiter that is still closed.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value != value or value == float("inf"):
        return None
    return value


def _throttle_wait_seconds(response: httpx.Response) -> float | None:
    """How long Ayla asked us to wait, per its 429 envelope (DRF-1595).

    The contract (Ayla ``djangoProject/exception_handler.py``, DRF
    ``Throttled`` branch)::

        {"error": {"code": "THROTTLED", "message": "Expected available in
         54 seconds", "details": {"wait_seconds": 54}}}

    ``details`` is omitted entirely when DRF's ``Throttled.wait`` is falsy,
    so its absence is normal and means "unknown", not "zero".

    ``Retry-After`` is read as a fallback because it is the standard header
    for this and costs three lines — only its numeric form, since the
    HTTP-date form would need a clock we do not trust more than the body we
    already have.
    """
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 — a 429 with a non-JSON body is still a 429
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            details = error.get("details")
            if isinstance(details, dict):
                seconds = _coerce_positive_seconds(details.get("wait_seconds"))
                if seconds is not None:
                    return seconds
    return _coerce_positive_seconds(response.headers.get("Retry-After"))


# ---------------------------------------------------------------------------
# DTO parsers
# ---------------------------------------------------------------------------


_RowT = TypeVar("_RowT")


def _parse_rows(
    rows: list[dict[str, Any]],
    parser: Callable[[dict[str, Any]], _RowT],
    *,
    path: str,
) -> tuple[list[_RowT], int]:
    """Parse a page-walk row by row. Returns ``(parsed, failed_count)``.

    ### The defect this exists for (DRF-1494)

    The three fetchers used to parse their rows in a bare list
    comprehension. One unreadable row therefore raised out of the whole
    fetch, and :meth:`CatalogSyncService._run_locked` turned that into
    ``SyncResult(ran=True, error=...)``: no cursor advance, no upsert, no
    mirror. A single upstream row with a ``base_price`` of ``"от 1500"``
    or a ``duration_minutes`` of ``"60 мин"`` was enough to freeze an
    entire salon's catalog -- every fifteen minutes, indefinitely, while
    the bot went on telling clients that services it had merely failed to
    fetch do not exist.

    ### Why isolation belongs on this rung specifically

    This file already isolates one level below (a malformed ``goals``
    entry is dropped rather than failing its row -- :func:`_parse_goals`),
    and ``apps.catalog.services.upserter`` isolates one level above (a row
    that will not upsert is counted, not raised). Only the rung between
    them -- parsing the row -- was all-or-nothing, so the blast radius of
    one bad field was the whole salon rather than the field.

    A dropped row is a real loss and is logged at ERROR with its id --
    the level Sentry captures -- so the operator learns *which* row Ayla
    is serving badly. Losing that one row is strictly better than losing
    the hundreds beside it.
    """
    parsed: list[_RowT] = []
    failed = 0
    for row in rows:
        try:
            parsed.append(parser(row))
        except Exception as exc:  # noqa: BLE001 — one bad row must not cost the rest
            failed += 1
            logger.error(
                "catalog.http.row_unparseable path=%s row_id=%s exc=%s: %s",
                path,
                row.get("id", "?"),
                exc.__class__.__name__,
                exc,
            )
    if failed:
        logger.error(
            "catalog.http.rows_dropped path=%s dropped=%d of=%d — these rows stay "
            "absent from the mirror until Ayla serves them readably.",
            path,
            failed,
            len(rows),
        )
    return parsed, failed


def _parse_dt(raw: str) -> datetime:
    """ISO 8601 with optional trailing ``Z`` → aware datetime."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _optional_str(row: dict[str, Any], key: str) -> str | None:
    """Строка из ``row[key]`` так, чтобы ОТСУТСТВИЕ не стало ПУСТЫМ (DRF-1588).

    Три исхода, и все три различимы у вызывающего:

    * ключа в строке нет      → ``None``  («источник не сказал ничего»);
    * ключ есть и это ``null``→ ``None``  (то же самое молчание, явным словом);
    * ключ есть и это строка  → она сама, ДОСЛОВНО, включая ``""``.

    Дословно — то есть без ``.strip()`` и без нормализации: ``raw`` это
    сырой слепок чужой системы, и выводить из него что-либо, кроме того,
    что там лежит буквально, — способ получить значение, неотличимое от
    настоящего. Обрезкой и разбором занимается читатель, у которого есть
    на это основание; у зеркала основания нет.

    Привычное ``row.get(key) or ""`` делает ровно обратное: сворачивает
    все три исхода в один и печатает «адреса нет» там, где верный ответ —
    «не знаем».
    """
    if key not in row:
        return None
    value = row[key]
    if value is None:
        return None
    return str(value)


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

    Since DRF-1494 this raise is caught by :func:`_parse_rows`, which drops
    the row and marks the snapshot ``complete=False``. The safety property
    the previous behaviour bought — a malformed join key must never license
    a delete — is preserved exactly: an incomplete snapshot downgrades the
    run to additive-only, so reconciliation still cannot act on it. What
    changes is that the edges Ayla DID serve readably now land instead of
    being discarded alongside the one it did not. Loud and inert was better
    than silent and destructive; loud and partial is better than both.

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
        # `in`, а не `.get() is not None`: присланный `null` — это ОТВЕТ
        # «не знаю», и он обязан отличаться от «ключа не было». Оба дают
        # питоновский `None`, и до этой строки различить их было нечем.
        health_check_key_present="resolved_requires_health_check" in row,
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
        # DRF-1588 — ``_optional_str``/``_parse_decimal``, а не ``or ""`` /
        # ``or 0``: последние стирают ровно ту разницу, ради которой поле
        # заводилось. ``row.get("address") or ""`` превратил бы отсутствие
        # ключа в пустой адрес, а ``or 0`` — отсутствие координаты в точку
        # в Гвинейском заливе.
        address=_optional_str(row, "address"),
        location_lat=_parse_decimal(row.get("location_lat")),
        location_lng=_parse_decimal(row.get("location_lng")),
        tenant_address=_optional_str(row, "tenant_address"),
        raw=row,
    )
