"""Salon-Knowledge inbound webhook receiver (KB-SYNC / Phase 5 convergence).

Consumes ``knowledge.approved`` events from the colleague's
``Shiro-Py/salon-knowledge`` Django app. The architectural decision (see
``docs/design/2026-05-18-phase-5-architecture-comparison.md``): the bot
retrieves locally from ChromaDB, never query-time HTTP to Shiro-Py.
This webhook is how per-salon content lands in our store: an approved
KnowledgeDocument on the colleague's side fires here, we upsert a
:class:`apps.kb.models.KbDocument` in the matching tenant, and the K6
Celery ingester picks it up on its next pass.

### Security pipeline

Every request flows through three independent gates BEFORE any DB
write fires:

1. **Secret-configured gate** — ``settings.SALON_KNOWLEDGE_WEBHOOK_SECRET``
   must be a non-empty string. If the operator forgot to set it on
   this environment, every request returns 500 (loud). A silent 200
   would let a misconfigured prod deploy quietly drop every event;
   the bot would gradually stale-out without anyone noticing.
2. **HMAC-SHA256 signature** — the ``X-Signature-256: sha256=<hex>``
   header is computed by the colleague's
   ``apps.approval.webhooks._sign_payload`` over the raw body. We
   verify with :func:`hmac.compare_digest`. A failed signature returns
   200 (don't leak verifier behaviour to a prober) but DOES write an
   audit row.
3. **Tenant resolution** — ``salon_slug`` on the payload must match an
   existing :class:`apps.tenancy.models.Tenant`. Unknown slug → 200 +
   audit row + no write (soft fail; the colleague's salon may have
   been removed on our side, and 500-looping their retry queue is
   strictly worse than dropping the event).

### Always 200 (except misconfigured secret)

The colleague's :mod:`apps.approval.webhooks.deliver_webhook` task
retries with 20/40/80/160/320s backoff on any non-2xx. A transient bug
or a deliberately rejected event becomes a stream of duplicate
deliveries that mask the underlying problem. We return 200 from every
post-gate code path; operators investigate via the audit log.

Misconfigured secret is the one exception — that's a deploy bug, not
a runtime decision, and the colleague's retries are the right tool to
keep events alive while we fix the env.

### Idempotency

The colleague's webhook delivery has at-least-once semantics. Our
upsert logic is therefore mandatory-idempotent:

- ``KbDocument`` is versioned by ``(tenant, source_uri, version)``.
- We look up the latest existing version per ``(tenant, source_uri)``.
- If the SHA-256 checksum of the incoming ``content_md`` matches the
  existing row → SKIP (no write). A re-delivery of the same approved
  doc is a no-op.
- If content changed → CREATE a new row at ``version = max+1`` with
  ``embedded_at = NULL``. Old versions stay as forensic history; K6's
  next sweep embeds the new one.

### What this is NOT

- NOT a query-time proxy. The bot does not call us synchronously.
- NOT a complete tenant sync — we only consume what the colleague
  pushes. Deletion / cascade events are out of scope for the v1
  contract.
- NOT a Telegram-style channel adapter — there's no per-tenant
  ``webhook_secret`` on :class:`Tenant`. One process-wide secret
  configured via ``SALON_KNOWLEDGE_WEBHOOK_SECRET`` env var. Per-
  tenant keys can land later if the platform onboards multiple
  Shiro-Py deployments.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.audit.services import write_audit
from apps.kb.models import KbDocType, KbDocument
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# Audit slugs — single source of truth so test assertions, log greps,
# and forensic queries all agree.
AUDIT_UNCONFIGURED = "salon_knowledge.webhook.unconfigured"
AUDIT_BAD_SIGNATURE = "salon_knowledge.webhook.bad_signature"
AUDIT_MALFORMED = "salon_knowledge.webhook.malformed"
AUDIT_UNKNOWN_EVENT = "salon_knowledge.webhook.unknown_event"
AUDIT_UNKNOWN_TENANT = "salon_knowledge.webhook.unknown_tenant"
AUDIT_UNKNOWN_SCHEMA = "salon_knowledge.webhook.unknown_schema_type"
AUDIT_CREATED = "salon_knowledge.webhook.kb_document_created"
AUDIT_VERSIONED = "salon_knowledge.webhook.kb_document_versioned"
AUDIT_SKIPPED = "salon_knowledge.webhook.kb_document_unchanged"


# Event slugs the colleague's service publishes (see their
# ``apps.approval.webhooks.SUPPORTED_EVENTS``). We only act on the
# first one for v1; the rest are silently ignored at the audit layer.
EVENT_KNOWLEDGE_APPROVED = "knowledge.approved"


# Colleague's schema types → our KbDocType enum. CONTRAINDICATION and
# HELP_ARTICLE are intentionally not mappable from the colleague's side
# — those types belong to the ``global_kb`` shared-corpus path, not to
# per-salon content. If the colleague adds new schema types they need a
# new entry here (otherwise we ``AUDIT_UNKNOWN_SCHEMA`` + 200 + no
# write — fail-closed by design).
SCHEMA_TYPE_TO_DOC_TYPE = {
    "service": KbDocType.SERVICE,
    "staff": KbDocType.MASTER,
    "faq": KbDocType.FAQ,
    "policy": KbDocType.LEGAL,
}


@csrf_exempt
@require_POST
def knowledge_approved(request: HttpRequest) -> HttpResponse:
    """POST ``/api/v1/salon-knowledge/webhook/approved/``.

    Consumes one ``knowledge.approved`` event. Returns 200 from every
    post-secret-gate path so the colleague's retry chain doesn't
    interpret a deliberate rejection as a delivery failure. Returns
    500 only when the operator forgot to set
    ``SALON_KNOWLEDGE_WEBHOOK_SECRET`` — that's loud-fail-on-misconfig.
    """
    secret = getattr(settings, "SALON_KNOWLEDGE_WEBHOOK_SECRET", "")
    if not secret:
        write_audit(AUDIT_UNCONFIGURED, target="SalonKnowledgeWebhook", target_id=None)
        logger.error(
            "salon_knowledge.webhook.unconfigured — set "
            "SALON_KNOWLEDGE_WEBHOOK_SECRET env var; rejecting webhook with 500"
        )
        return JsonResponse(
            {"error": "webhook_consumer_unconfigured"},
            status=500,
        )

    body = request.body
    if not _verify_signature(body, request.headers.get("X-Signature-256", ""), secret):
        write_audit(
            AUDIT_BAD_SIGNATURE,
            target="SalonKnowledgeWebhook",
            target_id=None,
            payload={"event": request.headers.get("X-Event", "")},
        )
        return JsonResponse({"status": "rejected"}, status=200)

    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        write_audit(AUDIT_MALFORMED, target="SalonKnowledgeWebhook", target_id=None)
        return JsonResponse({"status": "rejected"}, status=200)

    event = payload.get("event", "")
    if event != EVENT_KNOWLEDGE_APPROVED:
        write_audit(
            AUDIT_UNKNOWN_EVENT,
            target="SalonKnowledgeWebhook",
            target_id=None,
            payload={"event": event},
        )
        return JsonResponse({"status": "ignored", "reason": "unknown_event"}, status=200)

    salon_slug = payload.get("salon_slug", "")
    tenant = Tenant.all_objects.filter(slug=salon_slug).first()
    if tenant is None:
        write_audit(
            AUDIT_UNKNOWN_TENANT,
            target="SalonKnowledgeWebhook",
            target_id=None,
            payload={"salon_slug": salon_slug, "event": event},
        )
        return JsonResponse(
            {"status": "ignored", "reason": "unknown_tenant"},
            status=200,
        )

    data = payload.get("data") or {}
    schema_type = data.get("schema_type", "")
    doc_type = SCHEMA_TYPE_TO_DOC_TYPE.get(schema_type)
    if doc_type is None:
        write_audit(
            AUDIT_UNKNOWN_SCHEMA,
            target="SalonKnowledgeWebhook",
            target_id=str(tenant.id),
            payload={"salon_slug": salon_slug, "schema_type": schema_type},
        )
        return JsonResponse(
            {"status": "ignored", "reason": "unknown_schema_type"},
            status=200,
        )

    source_uri = data.get("source_uri", "")
    content_md = data.get("content_md", "")
    if not source_uri or not content_md:
        # Required-field check. We treat this as a contract violation
        # rather than an exception path — the colleague's v1 dispatch
        # before issue #20 doesn't include these fields and we audit
        # so the gap shows up in forensics.
        write_audit(
            AUDIT_MALFORMED,
            target="SalonKnowledgeWebhook",
            target_id=str(tenant.id),
            payload={
                "salon_slug": salon_slug,
                "missing_fields": [
                    name
                    for name, value in [("source_uri", source_uri), ("content_md", content_md)]
                    if not value
                ],
            },
        )
        return JsonResponse(
            {"status": "rejected", "reason": "missing_required_fields"},
            status=200,
        )

    outcome = _upsert_kb_document(
        tenant=tenant,
        doc_type=doc_type,
        source_uri=source_uri,
        content=content_md,
        metadata={
            "shiro_py_knowledge_doc_id": data.get("knowledge_doc_id"),
            "shiro_py_queue_item_id": data.get("queue_item_id"),
            "shiro_py_version": data.get("version"),
            "schema_type": schema_type,
        },
    )

    return JsonResponse({"status": "ok", "outcome": outcome}, status=200)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_signature(body: bytes, header_value: str, secret: str) -> bool:
    """Constant-time HMAC-SHA256 check against ``sha256=<hex>`` header.

    Returns False on any malformed header (missing scheme, wrong length,
    non-hex). Never raises.
    """
    if not header_value or not header_value.startswith("sha256="):
        return False
    received = header_value[len("sha256=") :]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received, expected)


@transaction.atomic
def _upsert_kb_document(
    *,
    tenant: Tenant,
    doc_type: str,
    source_uri: str,
    content: str,
    metadata: dict[str, Any],
) -> str:
    """Idempotent CREATE-or-VERSION-BUMP for one (tenant, source_uri).

    Returns one of:
      ``"created"`` — first-ever row for this source_uri
      ``"skipped"`` — content unchanged (checksum match); no write
      ``"versioned"`` — content changed; new row at version=max+1

    All three are normal outcomes — the colleague's at-least-once
    delivery + our checksum guard makes idempotent replay safe.
    """
    proposed_checksum = KbDocument.compute_checksum(content)

    latest = (
        KbDocument.all_tenants.filter(tenant=tenant, source_uri=source_uri)
        .order_by("-version")
        .first()
    )

    if latest is None:
        new_row = KbDocument.all_tenants.create(
            tenant=tenant,
            doc_type=doc_type,
            source_uri=source_uri,
            version=1,
            content=content,
            metadata=metadata,
        )
        write_audit(
            AUDIT_CREATED,
            target="KbDocument",
            target_id=new_row.id,
            payload={
                "tenant_id": str(tenant.id),
                "doc_type": doc_type,
                "source_uri": source_uri,
            },
        )
        return "created"

    if latest.checksum == proposed_checksum:
        write_audit(
            AUDIT_SKIPPED,
            target="KbDocument",
            target_id=latest.id,
            payload={"tenant_id": str(tenant.id), "source_uri": source_uri},
        )
        return "skipped"

    new_row = KbDocument.all_tenants.create(
        tenant=tenant,
        doc_type=doc_type,
        source_uri=source_uri,
        version=latest.version + 1,
        content=content,
        metadata=metadata,
    )
    write_audit(
        AUDIT_VERSIONED,
        target="KbDocument",
        target_id=new_row.id,
        payload={
            "tenant_id": str(tenant.id),
            "source_uri": source_uri,
            "previous_version": latest.version,
            "new_version": latest.version + 1,
        },
    )
    return "versioned"
