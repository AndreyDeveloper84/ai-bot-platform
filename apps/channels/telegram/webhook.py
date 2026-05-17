"""Telegram webhook view — Phase 1 / CH1 (DRF-848).

Receives Telegram ``Update`` POSTs at::

    /api/v1/channels/telegram/webhook/<slug:tenant_slug>/

and dispatches them to :func:`apps.channels.telegram.handler.handle_inbound`
after verifying the secret token.

### Security contract

1. **Tenant resolution from URL slug**. There is no global Telegram bot
   token — each tenant registers its own. The slug in the URL path
   selects which tenant's bot this update is for.
2. **Secret-token auth via header**. Telegram lets us register a
   ``secret_token`` when calling ``setWebhook``; it then echoes the
   value back in the ``X-Telegram-Bot-Api-Secret-Token`` header on
   every webhook POST. The view verifies with ``hmac.compare_digest``
   (constant-time, immune to timing leaks) against the tenant's
   ``telegram_webhook_secret`` field.
3. **404 on unknown tenant + 403 on bad secret**. Tenants that don't
   exist get 404 (don't leak existence). Tenants that exist but whose
   incoming secret mismatches get 403 + an audit row. Empty/missing
   secret on the tenant row is treated as "Telegram not configured"
   → 404 (same as missing slug, no information leak).
4. **Always 200 once authenticated**. Telegram retries 5xx forever and
   non-2xx leaves the user's button spinning. Per-message handler
   errors are caught + audited; the view itself returns 200 with an
   empty JSON body.
5. **Empty body tolerance**. Telegram occasionally sends pings or
   no-content POSTs (esp. when verifying the webhook on first
   ``setWebhook`` call). Empty bodies return 200 with no work done.

### NOT delegated to the Redis Streams ingress (unlike MAX)

The MAX webhook view enqueues onto a Redis Stream and a Celery
consumer dispatches the handler — that absorbs the back-pressure for
the high-throughput MAX channel. Telegram's per-bot throughput is
hard-capped at 30 msg/sec at the Telegram edge, so a direct sync
dispatch from the webhook view is both simpler AND latency-friendlier.
If a Phase 2 traffic spike makes this insufficient, the migration to a
Stream-based pipeline is mechanical (the existing MAX wiring is the
blueprint).
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.audit.services import write_audit
from apps.channels.telegram.handler import handle_inbound
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # pragma: allowlist secret


@csrf_exempt
@require_http_methods(["POST"])
def telegram_webhook(request: HttpRequest, tenant_slug: str) -> JsonResponse:
    """Receive a Telegram webhook for one tenant's bot.

    Flow:

    1. Resolve tenant from the URL slug. Unknown / inactive / not-Telegram-
       configured → 404.
    2. Verify ``X-Telegram-Bot-Api-Secret-Token`` against
       ``tenant.telegram_webhook_secret`` with ``hmac.compare_digest``.
       Mismatch → 403 + audit row.
    3. Parse JSON body. Empty / invalid → 200 with no work (Telegram's
       webhook verification probe is an empty POST).
    4. Enter ``tenant_scope(tenant)`` and call ``handle_inbound``.
    5. Return 200 unconditionally once authenticated. Handler-level
       failures are logged + audited but never escape.

    Args:
      request: Django request object. CSRF exempt (Telegram isn't a
               browser).
      tenant_slug: URL path segment naming the tenant.
    """
    tenant = _resolve_tenant_for_telegram(tenant_slug)
    if tenant is None:
        # Don't leak slug existence — every unknown / unconfigured slug
        # gets the same 404 + same body. Logging only includes the
        # slug, no header / body data.
        logger.info("channels.telegram.webhook.unknown_slug slug=%r", tenant_slug)
        return JsonResponse({"error": "not found"}, status=404)

    expected_secret = (tenant.telegram_webhook_secret or "").encode("utf-8")
    got_secret = (request.headers.get(_SECRET_HEADER, "") or "").encode("utf-8")

    if not expected_secret or not hmac.compare_digest(expected_secret, got_secret):
        # Audit + 403. The audit row carries tenant.id via the FK; the
        # secrets themselves never appear in the payload. We enter
        # ``tenant_scope`` for the audit write so ``write_audit`` (which
        # reads ``current_tenant()``) tags the row with the correct
        # tenant — without this the row would land with tenant=None and
        # operator queries scoped per-tenant would miss the audit trail.
        with tenant_scope(tenant):
            write_audit(
                "telegram.webhook.bad_signature",
                target="Tenant",
                target_id=tenant.id,
                payload={
                    "slug": tenant_slug,
                    "header_present": bool(got_secret),
                },
            )
        logger.warning(
            "channels.telegram.webhook.bad_signature tenant=%s header_present=%s",
            tenant.id,
            bool(got_secret),
        )
        return JsonResponse({"error": "forbidden"}, status=403)

    # Parse the body. Telegram's verification probe (the empty POST it
    # fires right after setWebhook) has body=b"" — accept silently.
    body = request.body or b""
    if not body.strip():
        logger.info("channels.telegram.webhook.empty_body tenant=%s", tenant.id)
        return JsonResponse({"ok": True})

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        logger.warning(
            "channels.telegram.webhook.bad_json tenant=%s body_len=%d",
            tenant.id,
            len(body),
        )
        # 200 still — Telegram doesn't retry on 4xx and a malformed
        # payload is the operator's problem to spot via audit, not
        # something Telegram should keep re-attempting.
        return JsonResponse({"ok": True})

    with tenant_scope(tenant):
        try:
            handle_inbound(payload, tenant=tenant)
        except Exception:  # noqa: BLE001 — webhook view must never 5xx Telegram
            # Audit row + Sentry breadcrumb via logger.exception. The
            # outer 200 keeps Telegram from retrying — the operator
            # dashboards alert on the audit volume.
            write_audit(
                "telegram.webhook.handler_error",
                target="Tenant",
                target_id=tenant.id,
                payload={
                    "update_keys": sorted(payload.keys()) if isinstance(payload, dict) else None
                },
            )
            logger.exception(
                "channels.telegram.webhook.handler_error tenant=%s",
                tenant.id,
            )

    return JsonResponse({"ok": True})


def _resolve_tenant_for_telegram(tenant_slug: str) -> Tenant | None:
    """Return the active tenant for the slug if it has Telegram configured.

    A tenant counts as "Telegram-configured" when BOTH the bot token
    AND the webhook secret are non-empty. Missing either is treated
    identically to an unknown slug (404) so an operator misconfiguration
    doesn't leak slug existence to anyone probing the URL.

    Uses ``Tenant.all_objects`` (the unfiltered manager) instead of
    ``Tenant.objects`` (which hides ``is_active=False``) so deactivated
    tenants still 404 cleanly — the active-tenant check is explicit
    below.
    """
    if not tenant_slug:
        return None
    try:
        tenant = Tenant.all_objects.get(slug=tenant_slug)
    except Tenant.DoesNotExist:
        return None
    if not tenant.is_active:
        return None
    if not tenant.telegram_bot_token or not tenant.telegram_webhook_secret:
        return None
    return tenant
