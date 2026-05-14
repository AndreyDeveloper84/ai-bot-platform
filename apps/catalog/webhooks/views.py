"""HMAC-verified delta-push receiver.

Sprint 10 / C3 (DRF-879). Listens on ``POST /api/v1/catalog/webhook/``,
verifies HMAC, queues the apply task, and **always responds 200**.

## Why always 200

mysite's webhook delivery (Phase 1 / DRF-726) uses Celery with retry-on-
non-2xx semantics — same pattern as ``mysite/maxbot/yclients_webhook.py``.
A 5xx reply causes retry storms that mask the original problem. Our
receiver writes an audit row for ANY non-success path (bad signature,
malformed body, unknown tenant), then returns 200. Operators read
the audit log for delivery failures, not the HTTP status.

## HMAC scheme

Body is the request bytes verbatim (no whitespace normalisation).
Signature header is ``X-Signature: sha256=<hex>`` over those bytes
using ``settings.MYSITE_WEBHOOK_HMAC_SECRET``. **Constant-time compare**
via :func:`hmac.compare_digest` defends against timing attacks.

A missing/wrong signature is a Sev2 security signal — audited and
returned as 200 (per "always 200" rule) but logged at WARNING. If
operators see these in volume, that's a security-incident.md trigger.

## Schema

```json
{
  "tenant_slug": "formula-tela",
  "model": "service" | "master" | "faq" | "help_article",
  "event": "created" | "updated" | "deleted",
  "external_id": 42,
  "external_updated_at": "2026-05-14T14:30:00+00:00"
}
```

Unknown ``model`` / ``event`` / missing required keys → audit row +
200; never crash.

## Auth note

The view sits at ``/api/v1/catalog/webhook/`` (NOT inside any tenant
URL prefix). The ``tenant_slug`` in the body is the authority signal.
The webhook is mysite-origin — a single shared secret per platform
deployment, NOT per-tenant. Per-tenant secrets would add rotation
complexity without security benefit (mysite is the only sender).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.audit.services import write_audit
from apps.catalog.webhooks.tasks import apply_delta

logger = logging.getLogger(__name__)


# ─── constants ────────────────────────────────────────────────────────────


_VALID_MODELS = frozenset({"service", "master", "faq", "help_article"})
_VALID_EVENTS = frozenset({"created", "updated", "deleted"})
_SIGNATURE_PREFIX = "sha256="

# Always-200 response. Helper to centralise.
_ACK = JsonResponse({"status": "received"}, status=200)


# ─── view ─────────────────────────────────────────────────────────────────


@csrf_exempt
@require_POST
def catalog_webhook(request: HttpRequest) -> JsonResponse:
    """Receive a mysite catalog delta. Always responds 200.

    Failure modes (each writes an audit row, returns 200):

    * Missing/invalid signature header
    * Body not JSON or not a dict
    * Missing required keys
    * Unknown ``model`` or ``event``
    * HMAC mismatch

    Success path: audit row ``catalog.webhook.received`` + enqueue
    ``apply_delta``.
    """
    body_bytes = request.body
    signature = request.headers.get("X-Signature", "")

    if not _verify_signature(body_bytes, signature):
        # Logged as warning; audited; ALWAYS 200.
        logger.warning(
            "catalog.webhook.bad_signature signature=%s body_len=%d",
            signature[:20],  # leak first 20 chars only — defence
            len(body_bytes),
        )
        write_audit(
            action="catalog.webhook.rejected",
            target="catalog.webhook",
            payload={"reason": "bad_signature"},
        )
        return _ACK

    payload = _parse_body(body_bytes)
    if payload is None:
        write_audit(
            action="catalog.webhook.rejected",
            target="catalog.webhook",
            payload={"reason": "malformed_json"},
        )
        return _ACK

    error = _validate_schema(payload)
    if error:
        write_audit(
            action="catalog.webhook.rejected",
            target="catalog.webhook",
            payload={"reason": error, "received_keys": sorted(payload.keys())},
        )
        return _ACK

    # All checks pass — record receipt + enqueue.
    write_audit(
        action="catalog.webhook.received",
        target=f"catalog.{payload['model']}",
        payload={
            "event": payload["event"],
            "external_id": payload["external_id"],
            "tenant_slug": payload["tenant_slug"],
            "external_updated_at": payload["external_updated_at"],
        },
    )
    apply_delta.delay(
        tenant_slug=payload["tenant_slug"],
        model=payload["model"],
        event=payload["event"],
        external_id=int(payload["external_id"]),
        external_updated_at=payload["external_updated_at"],
    )
    return _ACK


# ─── helpers ──────────────────────────────────────────────────────────────


def _verify_signature(body_bytes: bytes, signature: str) -> bool:
    """Constant-time HMAC-SHA256 verification.

    Returns False on:

    * Missing signature header
    * Wrong format (no ``sha256=`` prefix or hex of wrong length)
    * Missing/empty secret in settings (fail-closed — better than
      silently allowing unsigned webhooks in misconfigured deploy)
    * Mismatch
    """
    if not signature.startswith(_SIGNATURE_PREFIX):
        return False
    secret = getattr(settings, "MYSITE_WEBHOOK_HMAC_SECRET", "") or ""
    if not secret:
        # Fail-closed: an empty secret means the webhook is misconfigured.
        # Don't accept anything until ops sets it.
        return False
    sent_hex = signature[len(_SIGNATURE_PREFIX) :]
    expected = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent_hex, expected)


def _parse_body(body_bytes: bytes) -> dict[str, Any] | None:
    """Parse JSON body; return None on malformed or non-dict top-level."""
    try:
        parsed = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _validate_schema(payload: dict[str, Any]) -> str | None:
    """Return error code (snake_case) or None if valid.

    Validates required keys + enums but does NOT check types beyond
    "present and non-empty" — type errors surface in ``apply_delta``
    where they're a programming bug (different audit class).
    """
    required = ("tenant_slug", "model", "event", "external_id", "external_updated_at")
    for key in required:
        if key not in payload or payload[key] in (None, ""):
            return f"missing_{key}"

    if payload["model"] not in _VALID_MODELS:
        return "unknown_model"
    if payload["event"] not in _VALID_EVENTS:
        return "unknown_event"
    try:
        int(payload["external_id"])
    except (TypeError, ValueError):
        return "external_id_not_int"

    return None
