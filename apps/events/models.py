"""Event model — replay-first telemetry (DRF-429 / E0 + DRF-459 / B1).

Per review revision 1A: emit ``trace_id`` + structured events from
Sprint 1 onwards so Sprint 5 replay infrastructure doesn't have to
retrofit every Sprint 1–4 code path. Sprint 1 ships the contract
(table + emit helper + ContextVar); Sprint 5 ships the consumer
(rehydrator, differ, golden fixtures).

### B1 canonical envelope (Sprint 3)

PHASE0_DESIGN §F0.7 specifies the canonical event shape:

    ``event_name + distinct_id + dialog_id + properties +
    tenant_id + trace_id + ts``

Sprint 3 lands the schema, ``emit()`` is upgraded in B3 to populate
``event_name`` / ``distinct_id`` / ``dialog_id`` / ``properties``
alongside the legacy ``event_type`` / ``payload`` for the back-compat
window. Sprint 4 drops ``event_type`` + ``payload`` once every caller
migrated.

Why both fields live side-by-side for one sprint: ~60 call sites of
``emit("foo", payload={...})`` across apps. Flipping them in one PR
would force a stop-the-world change. The B3 upgrade mirrors writes
to both shapes so old subscribers (Sprint 1 audit pipeline) continue
to see ``event_type/payload`` while new replay tooling (Sprint 5)
reads ``event_name/properties``.

Indices favour the canonical query patterns:
- ``(tenant, -created_at)`` — per-tenant timeline (legacy)
- ``(trace_id,)`` — replay reconstruction
- ``(event_type, -created_at)`` — legacy filter (kept for Sprint 4 grace)
- ``(tenant, event_name, -created_at)`` — canonical per-tenant by name
- ``(distinct_id, -created_at)`` — user-stream lookup
- ``(dialog_id, -created_at)`` — turn-level lookup
"""

from __future__ import annotations

import uuid

from django.db import models


class Event(models.Model):
    """A single structured event emitted somewhere in the pipeline.

    Examples of event_type values:
      ``ingress.webhook_received``    new webhook landed in C1 journal
      ``ingress.enqueued``            payload XADD'd to Redis Stream
      ``worker.consumed``             worker popped from stream
      ``worker.handler_completed``    handler returned cleanly
      ``worker.handler_failed``       handler raised; PEL retained
      ``llm.complete.success``        LLM call OK
      ``llm.complete.fallback``       breaker open, fallback served

    The schema deliberately uses plain string event_type rather than a
    Django choices field — new event types should be addable without a
    migration. Strong-typing the catalogue arrives in Sprint 5 when
    replay needs to dispatch on event_type.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="events",
        null=True,
        blank=True,
        help_text="Tenant in scope at emission. Null for system-level events.",
    )
    event_type = models.CharField(
        max_length=120,
        help_text="DEPRECATED — legacy event identifier. Use event_name. "
        "Mirrored to event_name by emit() during the Sprint 3 → Sprint 4 "
        "back-compat window; removed in Sprint 4.",
    )
    event_name = models.CharField(
        max_length=80,
        blank=True,
        default="",
        help_text="Canonical event name (F0.7). Namespaced verb, e.g. "
        "'consent_granted', 'webhook_received'. New code reads this field; "
        "old subscribers fall back to event_type during the back-compat window.",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="DEPRECATED — legacy structured payload. Use 'properties'. "
        "Mirrored from properties during the back-compat window; removed in Sprint 4.",
    )
    properties = models.JSONField(
        default=dict,
        blank=True,
        help_text="Canonical structured props (F0.7). Never raw PII — IDs, "
        "hashes, and consent-type/reason strings only.",
    )
    distinct_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text="Cross-channel user identity. Stringified BotUser.id (UUID) "
        "for now; cross-channel composite arrives in Sprint 4+.",
    )
    dialog_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Conversation.id for events attached to a turn. Null for "
        "system/background events (webhook ingestion, breaker rotation, etc.).",
    )
    trace_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Correlates events across a single pipeline turn. Set by "
        "ingress at request entry; propagated via ContextVar.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # No TenantScopedManager — emit() needs to write events from system
    # contexts too (worker boot, breaker rotation). Caller scopes reads
    # explicitly when querying.
    objects = models.Manager()

    class Meta:
        verbose_name = "Event"
        verbose_name_plural = "Events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["trace_id"]),
            models.Index(fields=["event_type", "-created_at"]),
            # Canonical query path (Sprint 3 / B1): per-tenant filter by name.
            models.Index(fields=["tenant", "event_name", "-created_at"]),
        ]

    def __str__(self) -> str:
        # Prefer canonical event_name when set; fall back to legacy event_type.
        label = self.event_name or self.event_type
        return f"Event[{label} trace={self.trace_id or '-'}]"
