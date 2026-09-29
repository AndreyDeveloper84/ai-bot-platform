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

    ``address`` is ``Tenant.address`` verbatim (DRF-1609). It used to be the
    first non-empty address among the salon's bookable masters — OPEN_DECISIONS
    §45 called that a lottery, and it was one: confirm a new master, deactivate
    an old one, and the same salon shows a different address without moving.
    DRF-1587 gave the tenant its own column; this DTO carries it.

    **``None`` and "" are DIFFERENT values here and must stay different.**
    ``None`` — the source said nothing about the address (today that is every
    salon: the specialists feed has no ``tenant_address`` key yet). ``""`` —
    the source said there is no address. Collapsing the two would undo exactly
    what DRF-1587 separated, and would do it on the way OUT, where no reader
    can tell them apart any more. Renderers print both as nothing (see
    ``apps.orchestrator.discovery._salon_place``), so nothing is gained by
    merging them and a fact is lost.
    """

    tenant_id: UUID
    name: str
    city: str
    address: str | None
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

    ``has_bookable_master`` answers «is there anyone to book with for this
    service» — false is a NORMAL state (a salon may list a service none of its
    masters is mapped to). It gates the tap chip, not the line: the service is
    still real and still shown, it just leads nowhere yet.
    """

    tenant_id: UUID
    service_id: UUID
    name: str
    price_from: Decimal | None
    duration_min: int | None
    salon_name: str
    city: str
    has_bookable_master: bool = False
