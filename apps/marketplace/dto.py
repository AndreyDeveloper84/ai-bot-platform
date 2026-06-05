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
    so a caller can deep-link / proceed to booking)."""

    tenant_id: UUID
    master_id: UUID
    name: str
    specialization: str
    rating: Decimal | None
    photo_url: str
    city: str
