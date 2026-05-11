"""Webhook ingest — record_webhook + tenant resolution (DRF-423 / C1).

Single entry point for every channel handler (MAX webhook view,
Telegram webhook view, web webhook view, etc.). Atomic insert with
dedup; tenant resolution happens here so the journal row is complete
on first write.

Sprint 1 ships a hardcoded ``CHANNEL_TOKEN_TO_TENANT_SLUG`` mapping
(env-driven). Sprint 4 swaps this for a real lookup against an
encrypted ``Tenant.channel_tokens`` field (per ADR-0006).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.audit.services import write_audit
from apps.events.services import emit
from apps.ingress.models import WebhookJournal
from apps.tenancy.context import current_trace_id, tenant_scope

logger = logging.getLogger(__name__)


def _channel_token_map() -> dict[str, str]:
    """Read the Sprint 1 hardcoded channel-token → tenant-slug map.

    Format: env var ``CHANNEL_TOKEN_TO_TENANT_SLUG`` =
    ``"token1=tenant-a,token2=tenant-b"``. Empty when unset.
    Sprint 4 replaces with encrypted-on-tenant lookup (ADR-0006).
    """

    raw = getattr(settings, "CHANNEL_TOKEN_TO_TENANT_SLUG", None) or os.environ.get(
        "CHANNEL_TOKEN_TO_TENANT_SLUG",
        "",
    )
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        # Defensive: a malformed env value (e.g. ``"token1=tenant-a,bad-token"``)
        # must not crash every ingest. Log + skip the bad pair, keep going.
        if "=" not in pair:
            logger.warning(
                "ingress.channel_token_map.malformed_pair pair=%r",
                pair,
            )
            continue
        token, slug = pair.split("=", 1)
        token = token.strip()
        slug = slug.strip()
        if not token or not slug:
            logger.warning(
                "ingress.channel_token_map.empty_field pair=%r",
                pair,
            )
            continue
        out[token] = slug
    return out


def _resolve_tenant(channel_token: str):
    """Look up the Tenant for a channel token. Returns None if unknown."""

    if not channel_token:
        return None
    slug = _channel_token_map().get(channel_token)
    if not slug:
        return None
    # Local import to avoid Django app-load order issues.
    from apps.tenancy.models import Tenant

    try:
        return Tenant.objects.get(slug=slug)
    except Tenant.DoesNotExist:
        return None


def record_webhook(
    *,
    channel: str,
    external_event_id: str,
    raw_payload: dict[str, Any],
    channel_token: str = "",
) -> tuple[WebhookJournal, bool]:
    """Record a webhook in the journal. Idempotent on (channel, external_event_id).

    Args:
      channel: ``"max"``, ``"telegram"``, ``"web"``.
      external_event_id: The channel's own unique identifier.
      raw_payload: Full untouched payload — preserved for replay.
      channel_token: The token from the webhook headers, used to
                     resolve tenant.

    Returns:
      ``(journal_row, created)``. ``created`` is False on a dedup hit
      (retry of an already-seen event).

    Side effects:
      - Writes audit log on every call (action ``ingress.webhook_received``
        on success, ``ingress.webhook_unknown_tenant`` when the channel
        token doesn't resolve).
      - Emits an event row (``ingress.webhook_received`` or
        ``ingress.webhook_duplicate``) for the replay timeline.
    """

    tenant = _resolve_tenant(channel_token)
    trace_id = current_trace_id() or ""

    try:
        # transaction.atomic — without it, IntegrityError aborts the
        # surrounding test transaction and blows up the test runner.
        # In production with autocommit, this is equally clean.
        with transaction.atomic():
            row = WebhookJournal.objects.create(
                channel=channel,
                external_event_id=external_event_id,
                raw_payload=raw_payload,
                resolved_tenant=tenant,
                trace_id=trace_id,
            )
        created = True
    except IntegrityError:
        # Dedup hit: same channel+external_event_id was already inserted.
        row = WebhookJournal.objects.get(channel=channel, external_event_id=external_event_id)
        created = False

    # Wrap emit / write_audit in tenant_scope(tenant) so the resolved
    # tenant ends up on the Event + AuditLog rows. Without this scope,
    # emit() reads current_tenant() == None and the (tenant, -created_at)
    # index Sprint 5 replay depends on is full of NULLs for ingress.
    with tenant_scope(tenant):
        if created:
            emit(
                "ingress.webhook_received",
                payload={
                    "channel": channel,
                    "external_event_id": external_event_id,
                    "tenant_resolved": tenant is not None,
                },
            )
            if tenant is None and channel_token:
                write_audit(
                    "ingress.webhook_unknown_tenant",
                    target="WebhookJournal",
                    target_id=row.id,
                    payload={
                        "channel": channel,
                        "external_event_id": external_event_id,
                        "token_prefix": channel_token[:8] + "..."
                        if len(channel_token) > 8
                        else channel_token,
                    },
                )
        else:
            emit(
                "ingress.webhook_duplicate",
                payload={
                    "channel": channel,
                    "external_event_id": external_event_id,
                },
            )

    return row, created
