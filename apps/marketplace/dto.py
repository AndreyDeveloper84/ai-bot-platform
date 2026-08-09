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
