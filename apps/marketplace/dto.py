"""Public discovery DTO for cross-tenant marketplace (#1018).

The marketplace is the sole sanctioned cross-tenant catalog carve-out. To
keep that carve-out safe, discovery returns ONLY this public-field DTO —
never the live catalog row, which also carries commercial / identity state
(``yclients_staff_id``, ``ayla_user_id``, ``invite_*``, ``linked_bot_user``,
``raw``, ``cache_version``, schedules, prices). The DTO boundary is what
makes the source swappable (local mirror today → Ayla provider-directory
API #249-#251 later) without leaking internal fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class MasterCard:
    """A single public master card. Exactly the fields #1018 sanctions:
    name, specialization, rating, photo, city, tenant_id (+ the master id
    so a caller can deep-link / proceed to booking).

    ``service_id`` / ``service_name`` (DRF-962): the public id + display name
    of the ONE service that matched the user's discovery query, when that
    match is unambiguous — so the booking handoff can carry the service
    context and the card tap does not dead-end on the booking skill's
    stale-context guard. ``service_id`` is the catalog mirror row id (the
    same public id family as ``master_id``), never a commercial/native id.
    ``None``/empty when the query had no service filter or several of the
    master's services matched — auto-picking one of several would silently
    book a service the user never chose."""

    tenant_id: UUID
    master_id: UUID
    name: str
    specialization: str
    rating: Decimal | None
    photo_url: str
    city: str
    service_id: UUID | None = None
    service_name: str = ""


@dataclass(frozen=True, slots=True)
class SalonCard:
    """A single public salon card (DRF-1304).

    A "salon" on the marketplace is a tenant that has at least one bookable
    master — the same predicate discovery applies to masters, one level up.
    Public fields only: name, city, address, and a count + sample of what is
    done there. ``tenant_id`` rides along so a caller can deep-link, same as
    ``MasterCard``.

    ``address`` is the only field whose source is per-master (the Ayla
    specialists feed carries it in the specialist payload, mirrored into
    ``CatalogMaster.raw``; ``Tenant`` has no address column). It is the first
    non-empty address among the salon's bookable masters, and may legitimately
    be "" — the pilot salon's masters carry no address at all.
    """

    tenant_id: UUID
    name: str
    city: str
    address: str
    master_count: int
    service_count: int
    sample_services: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ServiceCard:
    """A single public service card (DRF-1304).

    Name plus the two commercial facts the concierge may quote — ``price_from``
    and ``duration_min`` — and the salon they belong to. Both may be ``None``:
    the mirror is only as complete as the upstream feed (the pilot salon's
    canonical-template coverage is 0 of 58 rows), and a missing value must
    render as "not told", never as an invented number. A ``price_from`` of 0
    is kept as stored; renderers decide how (not) to show it.
    """

    tenant_id: UUID
    service_id: UUID
    name: str
    price_from: Decimal | None
    duration_min: int | None
    salon_name: str
    city: str
