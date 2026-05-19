"""Canonical event envelope — event-taxonomy.md §2.

Frozen dataclass mirroring the JSON shape every domain event carries
across module boundaries. The DB row (``DomainEvent``) is a projection;
``from_row`` lifts a row back to an envelope for replay/test paths.

PII rules §6 are enforced separately in :mod:`apps.eventbus.validation` —
this dataclass does NOT validate, only carries.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from apps.eventbus.models import DomainEvent


ActorType = Literal["ai", "human", "system", "external"]
ActorRole = Literal["owner", "admin", "master", "customer", "ai_assistant"]


@dataclass(frozen=True)
class Actor:
    """Who emitted the event. event-taxonomy.md §2.

    ``type`` is always set. ``id`` and ``role`` are optional but
    present when known.
    """

    type: ActorType
    id: str | None = None
    role: ActorRole | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "id": self.id, "role": self.role}


@dataclass(frozen=True)
class Envelope:
    """In-process representation of an event-taxonomy.md §2 envelope."""

    event_id: str  # ULID
    event_name: str  # dot.notation from §3 catalog
    event_version: str  # SemVer "1.0"
    occurred_at: dt.datetime  # UTC
    actor: Actor
    data: dict[str, Any] = field(default_factory=dict)
    tenant_id: uuid.UUID | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: "DomainEvent") -> "Envelope":
        """Lift a DomainEvent DB row into an Envelope."""

        actor_dict = row.actor or {}
        return cls(
            event_id=row.event_id,
            event_name=row.event_name,
            event_version=row.event_version,
            occurred_at=row.occurred_at,
            tenant_id=row.tenant_id,
            actor=Actor(
                type=actor_dict.get("type", "system"),
                id=actor_dict.get("id"),
                role=actor_dict.get("role"),
            ),
            correlation_id=row.correlation_id or None,
            causation_id=row.causation_id or None,
            data=row.data or {},
            metadata=row.metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "event_version": self.event_version,
            "occurred_at": self.occurred_at.isoformat(),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "actor": self.actor.to_dict(),
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "data": self.data,
            "metadata": self.metadata,
        }
