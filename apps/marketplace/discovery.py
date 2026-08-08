"""Cross-tenant master discovery — the SOLE sanctioned ``all_tenants``
catalog carve-out (#1018, EPIC #1014).

Everywhere else in the platform, catalog reads are tenant-scoped via
``CatalogMaster.objects`` (auto-filtered by ``current_tenant()``). The
nationwide-discovery vision needs to read masters *across* tenants, so this
module — and only this module, enforced by the ``MKT1`` rule in
``tools/lint/import_boundaries.py`` — uses ``CatalogMaster.all_tenants`` and
maps each row to the public-field :class:`MasterCard` DTO.

Source today is the local catalog mirror; the DTO boundary keeps it
swappable to the Ayla provider-directory API (#249-#251) later.
"""

from __future__ import annotations

from typing import NamedTuple
from uuid import UUID

from django.core.paginator import Paginator
from django.db.models import F, Q, QuerySet

from apps.catalog.models import CatalogMaster
from apps.marketplace.dto import MasterCard

# Cap to keep a discovery call bounded; callers paginate by re-querying with
# a tighter filter for now (cursor pagination lands with the HTTP surface).
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# Page-based pagination defaults for the public HTTP directory (#249). The
# stdlib Django ``Paginator`` slices the queryset (COUNT + LIMIT/OFFSET), so
# page size — not the list cap above — bounds each call.
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 50

# Query tokens shorter than this are dropped — a one-character ILIKE
# ``%а%`` matches essentially every service name and adds no signal.
_MIN_TOKEN_LEN = 2

# Upper bound on tokens per query. Each one becomes an ILIKE against the same
# joined row, so an unbounded query would be an unbounded AND-chain.
_MAX_TOKENS = 5


def _query_tokens(raw: str) -> list[str]:
    """Normalize a discovery query into match tokens.

    Deliberately NOT ``apps.catalog.services.linking.normalize_name``, despite
    the overlap. That helper implements the C6 link contract, where **both**
    sides are normalized in Python before comparison, so its ``ё→е`` step is
    symmetric and safe. Here only the query is normalized — the service name
    is matched raw through ``icontains`` (ILIKE) — so folding ``ё→е`` on one
    side only would turn a perfectly matchable «ёлочный» query into a token
    that can never match the stored «ё». Casefolding is fine because ILIKE is
    already case-insensitive.
    """
    cleaned = raw.replace("«", " ").replace("»", " ")
    tokens = [t for t in cleaned.casefold().split() if len(t) >= _MIN_TOKEN_LEN]
    return tokens[:_MAX_TOKENS]


class PageMeta(NamedTuple):
    """Pagination envelope for a page of discovery results (#249)."""

    page: int
    page_size: int
    total_count: int
    num_pages: int


def _bookable_qs(
    *,
    city: str | None = None,
    specialization: str | None = None,
) -> QuerySet[CatalogMaster]:
    """Cross-tenant queryset of bookable masters, optionally filtered.

    The SOLE ``all_tenants`` carve-out (MKT1). Only ``is_active`` +
    invite-``accepted`` masters (the same ``bookable`` predicate
    customer-facing reads use). Optional ``city`` (exact, case-insensitive, on
    the owning tenant) and ``specialization`` narrow it.

    ### Matching a service (DRF-945)

    ``specialization`` used to filter ``CatalogMaster.specialization`` alone.
    Nothing populates that field — the Ayla specialists feed carries no such
    value — so every service-specific query matched the empty string and
    returned zero masters. That was the live pilot failure: «Пенза,
    спортивный» produced the no-masters-found fallback even though the salon
    offers exactly that service.

    A master now matches when **either** side does:

    * a service they actually perform, joined through ``MasterService`` (the
      canonical relation, mirrored from Ayla's bookable ``SpecialistService``);
    * or the legacy free-text ``specialization``, kept as an OR so
      operator-maintained rows and any future feed that does populate it keep
      working.

    Tokens are AND-ed **within one joined row**, so «спортивный массаж» and
    «массаж спортивный» both match the service «Спортивный массаж», while
    «массаж» alone matches every massage service. Word order stops mattering
    without reaching for fuzzy or vector search.
    """
    qs = (
        CatalogMaster.all_tenants.filter(
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        .select_related("tenant")  # N+1-safe tenant.city / tenant_id
        .order_by("name", "id")
    )
    if city:
        qs = qs.filter(tenant__city__iexact=city)

    if specialization:
        tokens = _query_tokens(specialization)
        if tokens:
            # One filter() call, so every condition binds to the SAME joined
            # MasterService row — a master offering «Спортивный массаж» matches
            # «спортивный массаж», but one offering «Спортивный маникюр» plus a
            # separate «Тайский массаж» does not match on the split.
            #
            # A MasterService row existing IS the statement that the master
            # performs the service (there is no status column — see the model);
            # the service itself must still be active to be offered.
            service_match = Q(services_offered__service__is_active=True)
            # Belt-and-braces on an inherently cross-tenant queryset: the edge's
            # service must belong to the same tenant as the master. The sync
            # path cannot create a cross-tenant edge, but discovery is the one
            # reader that sees every tenant at once, so it should not depend on
            # a writer-side guarantee to avoid surfacing a master for a service
            # they do not offer.
            service_match &= Q(services_offered__service__tenant_id=F("tenant_id"))
            for token in tokens:
                service_match &= Q(services_offered__service__name__icontains=token)

            specialization_match = Q()
            for token in tokens:
                specialization_match &= Q(specialization__icontains=token)

            # DISTINCT is required: the join multiplies a master by every
            # matching service row, so a master offering both «Спортивный
            # массаж» and «Классический массаж» would otherwise render twice
            # for the query «массаж».
            qs = qs.filter(service_match | specialization_match).distinct()

    return qs


def discover_masters(
    *,
    city: str | None = None,
    specialization: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[MasterCard]:
    """Return bookable masters across ALL tenants as public DTOs.

    Only ``is_active`` + invite-``accepted`` masters are returned (the same
    ``bookable`` predicate customer-facing reads use). Optional ``city``
    (exact, case-insensitive, on the owning tenant) and ``specialization``
    narrow the result — the latter matches either a service the master
    actually performs or their free-text specialization (see
    :func:`_bookable_qs`). ``limit`` is clamped to ``_MAX_LIMIT``.
    """
    limit = max(1, min(limit, _MAX_LIMIT))
    qs = _bookable_qs(city=city, specialization=specialization)
    return [_to_card(master) for master in qs[:limit]]


def discover_masters_page(
    *,
    city: str | None = None,
    specialization: str | None = None,
    page: int = 1,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> tuple[list[MasterCard], PageMeta]:
    """Return one page of bookable masters across ALL tenants (#249).

    Page-based pagination via the stdlib :class:`~django.core.paginator.Paginator`
    over the cross-tenant queryset, so COUNT + slice happen in the DB rather
    than over a materialized list. ``page_size`` is clamped to
    ``[1, _MAX_PAGE_SIZE]``; an out-of-range ``page`` is clamped to the last
    page (empty directory still yields page 1). Returns the page's cards plus
    a :class:`PageMeta` envelope.
    """
    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))
    paginator = Paginator(_bookable_qs(city=city, specialization=specialization), page_size)
    page = max(1, min(page, paginator.num_pages))
    page_obj = paginator.page(page)
    cards = [_to_card(master) for master in page_obj.object_list]
    meta = PageMeta(
        page=page,
        page_size=page_size,
        total_count=paginator.count,
        num_pages=paginator.num_pages,
    )
    return cards, meta


def get_master(master_id: UUID) -> MasterCard | None:
    """Return a single bookable master's public card, or ``None`` (#250).

    Looks up by id across ALL tenants through the same ``bookable``
    predicate as discovery; an unknown id OR a non-bookable master
    (inactive / invite not accepted) yields ``None`` → 404 at the view.
    """
    master = _bookable_qs().filter(id=master_id).first()
    return _to_card(master) if master is not None else None


def _to_card(master: CatalogMaster) -> MasterCard:
    """Map a catalog row to the public DTO — the single projection point.

    Deliberately enumerates each public field by hand (no ``**vars``) so a
    new commercial field on ``CatalogMaster`` can never silently leak into
    discovery output. ``test_dto`` pins the allowed field set.
    """
    return MasterCard(
        tenant_id=master.tenant_id,
        master_id=master.id,
        name=master.name,
        specialization=master.specialization,
        rating=master.rating,
        photo_url=master.photo_url,
        city=master.tenant.city,
    )
