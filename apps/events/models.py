"""Event model — replay-first telemetry (DRF-429 / E0).

Per review revision 1A: emit ``trace_id`` + structured events from
Sprint 1 onwards so Sprint 5 replay infrastructure doesn't have to
retrofit every Sprint 1–4 code path. Sprint 1 ships the contract
(table + emit helper + ContextVar); Sprint 5 ships the consumer
(rehydrator, differ, golden fixtures).

Shape stays minimal — extra context lives in ``payload``. Indices
favour two query patterns:
- ``(tenant, -created_at)`` — per-tenant event timeline
- ``(trace_id,)`` — replay reconstruction by trace
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
        help_text="Namespaced event identifier (e.g. ingress.webhook_received).",
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured event data. Never raw PII — IDs and hashes only.",
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
        ]

    def __str__(self) -> str:
        return f"Event[{self.event_type} trace={self.trace_id or '-'}]"
