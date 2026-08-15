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

import hashlib
import json
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.channels.bot_registry import LEGACY_SLUG
from apps.ingress.services import record_webhook, resolve_bot
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

    secret_got = request.headers.get("X-Max-Bot-Api-Secret", "")
    # DRF-1061: the header is matched against every registered bot, not a
    # single MAX_WEBHOOK_SECRET, and the matching entry IS the bot identity —
    # the same value authenticates the request and names its sender. Matching
    # is timing-safe (`hmac.compare_digest` per entry, no early break inside
    # the registry lookup); an unconfigured deployment has an empty registry
    # and rejects everything, as the empty-secret short-circuit did before.
    bot = resolve_bot(secret_got)
    if bot is None:
        # Don't leak whether the secret is missing, wrong, or belongs to an
        # unregistered bot; all → 401.
        logger.warning("channels.max.webhook.unauthorized header_present=%s", bool(secret_got))
        return JsonResponse({"error": "unauthorized"}, status=401)

    try:
        payload: dict[str, Any] = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        logger.warning("channels.max.webhook.bad_json")
        return JsonResponse({"error": "invalid json"}, status=400)

    # Sprint 8 / N2 (DRF-701) — edge nginx mirror sets `X-Shadow: 1` on the
    # mirrored copy. We tag the enqueued payload with `_shadow: True` so
    # the worker / orchestrator (S2 / DRF-717) can short-circuit step 19
    # (outbound) without persisting to the primary Conversation. The `_`
    # prefix marks an internal envelope key — MAX itself never sends it.
    if request.headers.get("X-Shadow", "") == "1":
        payload["_shadow"] = True

    external_event_id = _extract_external_event_id(payload)

    # DRF-1061 — namespace the dedup key per declared bot.
    #
    # The journal is unique on (channel, external_event_id), and `channel` is
    # the literal "max" for every bot on this endpoint. Two bots therefore
    # share one id space: if MAX ever issued the same update id to both, the
    # second event would dedup against the first, return created=False, and
    # never be enqueued — a silently dropped message. Whether MAX ids are
    # globally unique across bots is not documented, so this does not rely on
    # it.
    #
    # Only DECLARED bots get a namespace. The legacy fallback entry keeps the
    # bare id, so existing deployments write the same journal rows they always
    # did — and with one bot there is nothing to collide with anyway.
    if bot.slug != LEGACY_SLUG:
        external_event_id = f"{bot.slug}:{external_event_id}"

    journal_row, created = record_webhook(
        channel="max",
        external_event_id=external_event_id,
        raw_payload=payload,
        channel_token=secret_got,
    )

    if created:
        # The stream comes from the bot's registry entry. For the nationwide
        # bot that is `max_global` with `tenant_id=None`: the consumer enters
        # `tenant_scope(None)` and `GlobalMaxHandler(requires_tenant=False)`
        # runs discovery without a tenant, which is selected only at booking.
        # For a tenant-bound bot (the salon bot) it is that bot's own stream,
        # carrying the tenant resolved in `record_webhook`.
        resolved_tenant_id = (
            str(journal_row.resolved_tenant_id) if journal_row.resolved_tenant_id else None
        )
        enqueue(
            channel=bot.stream,
            payload=payload,
            tenant_id=resolved_tenant_id,
        )
        logger.info(
            "channels.max.webhook.enqueued external_event_id=%s bot=%s stream=%s tenant=%s",
            external_event_id,
            bot.slug,
            bot.stream,
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
    but some events use `message.body.mid` / `.seq`. Callback updates
    (`message_callback`) additionally carry `callback.callback_id`, which
    is unique per button tap even when `update_id` and `message.body.mid`
    are identical for the same source message (DRF-998). We try in order:

      1. `callback.callback_id` — per-tap unique id.
      2. `update_id` — envelope-level id for message_created etc.
      3. `message.body.mid` / `.seq` — message-level ids.
      4. Deterministic hash of the payload (Sprint 2.5 review H5).

    The result is capped to `WebhookJournal.external_event_id.max_length`
    (200) so it always fits the unique constraint. Before the fix, the
    fallback used `uuid.uuid4()` which guaranteed a fresh value on every
    retry, defeating the dedup contract for MAX events that omit all
    idempotency hints.
    """

    callback_id = (payload.get("callback") or {}).get("callback_id")
    for candidate in (
        callback_id,
        payload.get("update_id"),
        ((payload.get("message") or {}).get("body") or {}).get("mid"),
        ((payload.get("message") or {}).get("body") or {}).get("seq"),
    ):
        if candidate:
            return _normalize_event_id(str(candidate))
    # Deterministic fallback — same payload bytes always produce the
    # same id. Use json.dumps with sort_keys for stable serialization.
    payload_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(payload_bytes).hexdigest()[:32]
    return _normalize_event_id(f"synth-{digest}")


_EXTERNAL_EVENT_ID_MAX_LENGTH = 200


def _normalize_event_id(value: str) -> str:
    """Ensure the extracted event id fits the DB column.

    Callback ids are normally short UUID-like strings, but we guard
    against pathological upstream values by hashing anything that would
    overflow `external_event_id max_length=200`. Hashing is deterministic,
    so a replay of the same oversized id still dedups correctly.
    """
    if len(value) <= _EXTERNAL_EVENT_ID_MAX_LENGTH:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return f"long-{digest}"
