"""WebhookJournal — every incoming webhook recorded (DRF-423 / C1).

Single source of truth for every webhook the platform receives. Dedup
is enforced at the DB layer via ``unique_together(channel, external_event_id)`` —
the channel's own idempotency hint is the key.

Why both ``raw_payload`` and ``resolved_tenant_id``:
  - raw_payload is preserved for replay (Sprint 5) and forensics —
    Sprint 1 contract per review revision 1A.
  - resolved_tenant_id is derived at ingest time so workers don't
    have to repeat the channel-token → tenant lookup.

The lookup itself happens in ``apps.ingress.services.record_webhook``;
the journal stores the result. If the channel token doesn't map to a
known tenant, resolved_tenant_id stays None and we audit-log the miss.
"""

from __future__ import annotations

import uuid

from django.db import models


class WebhookJournal(models.Model):
    """One row per incoming webhook from any channel."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.CharField(
        max_length=40,
        help_text="Source channel (max, telegram, web, ...).",
    )
    external_event_id = models.CharField(
        max_length=200,
        help_text="Channel-supplied unique event id (idempotency hint).",
    )
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full untouched webhook payload. Preserved for replay (Sprint 5).",
    )
    resolved_tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="webhook_journal",
        null=True,
        blank=True,
        help_text="Tenant the channel token resolved to. Null if unknown.",
    )
    trace_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Correlates with downstream stream + worker events.",
    )
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the consumer XACKs the matching stream entry.",
    )

    # Plain manager — ingress writes happen from non-tenant contexts
    # (the webhook arrived BEFORE we know the tenant). Reads via admin
    # or replay use ``.filter(resolved_tenant=...)`` explicitly.
    objects = models.Manager()

    class Meta:
        verbose_name = "Webhook journal"
        verbose_name_plural = "Webhook journal"
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_event_id"],
                name="ingress_journal_dedup",
            ),
        ]
        indexes = [
            models.Index(fields=["resolved_tenant", "-received_at"]),
            models.Index(fields=["trace_id"]),
            models.Index(fields=["channel", "-received_at"]),
            models.Index(fields=["processed_at"]),
        ]

    def __str__(self) -> str:
        return f"WebhookJournal[{self.channel}:{self.external_event_id}]"
