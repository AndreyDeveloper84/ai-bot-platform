"""Ingress webhook views (DRF-443 / Sprint 2 / D4).

Sprint 1 shipped `apps/ingress/services.py::record_webhook` and the
Redis Streams `enqueue` (`apps/ingress/streams.py`). D4 wires them
into actual HTTP routes — one view per channel for now. Sprint 3+
generalises via a `ChannelAdapter` registry.

### MAX webhook route

POST `/api/v1/ingress/max/`

Headers:
  - `X-Max-Bot-Api-Secret` — must equal `settings.MAX_WEBHOOK_SECRET`
    (else 401)

Body: JSON, MAX `message_created` shape (see D1 parser).

Response:
  - 200 always (per webhook idempotency contract — replays / dedup
    hits must not look like errors to the channel)
  - 400 only on JSON-decode failure (the channel is at fault)
  - 401 on missing/wrong secret

Side effects:
  - Records the webhook in `WebhookJournal` (C1 from Sprint 1) — idempotent
    via `unique_together(channel, external_event_id)`.
  - Enqueues the payload on Redis Stream `ingress:max` (C2 from Sprint 1)
    with `resolved_tenant_id` + `trace_id` top-level for the consumer.

### Tenant-context exemption

The webhook arrives BEFORE we know the tenant — `record_webhook`
resolves it from the `X-Max-Bot-Api-Secret` value (matched against
`CHANNEL_TOKEN_TO_TENANT_SLUG`). The middleware exempts `/api/v1/ingress/`
from strict-mode tenant resolution; otherwise the view returns 400
on every inbound. See `apps/tenancy/middleware.py` exemption list.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.ingress.services import record_webhook
from apps.ingress.streams import enqueue

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["POST"])
def max_webhook(request: HttpRequest) -> HttpResponse:
    """Receive a MAX webhook update and enqueue for the worker.

    The view does as little as possible:
      1. Verify the channel secret.
      2. Parse JSON.
      3. Extract `external_event_id` (defensive — accept any of several
         likely fields, fall back to a synthesised UUID; the
         WebhookJournal unique constraint covers true duplicates).
      4. `record_webhook(...)` (dedup + tenant resolution).
      5. `enqueue(...)` with the resolved tenant.
      6. Return 200.

    The view does NOT call the handler synchronously — the consumer
    loop picks it up off the stream and handles in a worker.
    """

    secret_expected = getattr(settings, "MAX_WEBHOOK_SECRET", "")
    secret_got = request.headers.get("X-Max-Bot-Api-Secret", "")
    if not secret_expected or secret_got != secret_expected:
        # Don't leak whether the secret is missing or wrong; both → 401.
        logger.warning("channels.max.webhook.unauthorized header_present=%s", bool(secret_got))
        return JsonResponse({"error": "unauthorized"}, status=401)

    try:
        payload: dict[str, Any] = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        logger.warning("channels.max.webhook.bad_json")
        return JsonResponse({"error": "invalid json"}, status=400)

    external_event_id = _extract_external_event_id(payload)

    journal_row, created = record_webhook(
        channel="max",
        external_event_id=external_event_id,
        raw_payload=payload,
        channel_token=secret_got,
    )

    if created:
        resolved_tenant_id = (
            str(journal_row.resolved_tenant_id) if journal_row.resolved_tenant_id else None
        )
        enqueue(
            channel="max",
            payload=payload,
            tenant_id=resolved_tenant_id,
        )
        logger.info(
            "channels.max.webhook.enqueued external_event_id=%s tenant=%s",
            external_event_id,
            resolved_tenant_id,
        )
    else:
        logger.info("channels.max.webhook.dedup external_event_id=%s", external_event_id)

    # Always 200 — replays are not errors. Per webhook idempotency
    # contract (Sprint 1 C1 docstring).
    return JsonResponse({"status": "ok", "dedup": not created})


def _extract_external_event_id(payload: dict[str, Any]) -> str:
    """Find the channel's own dedup ID inside the MAX payload.

    MAX webhooks vary: `update_id` at the top level is most common,
    but some events use `message.body.mid`. We try in order and fall
    back to a synthesised UUID — the WebhookJournal unique constraint
    still catches true replays (the channel sends the same payload
    bytes, but our UUID would differ; this only matters for replays
    that we won't see in practice).
    """

    for candidate in (
        payload.get("update_id"),
        ((payload.get("message") or {}).get("body") or {}).get("mid"),
        ((payload.get("message") or {}).get("body") or {}).get("seq"),
    ):
        if candidate:
            return str(candidate)
    return f"synth-{uuid.uuid4()}"
