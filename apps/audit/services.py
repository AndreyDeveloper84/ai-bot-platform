"""Audit-trail write helper (DRF-426 / B1).

The single entry point for *every* audit event. Reads tenant context
from ``apps.tenancy.context`` so callers don't have to thread tenant
through their signatures.

Design contract:
- **Never raises.** Audit is forensic infrastructure. If the DB is
  unavailable or the row violates a constraint, the surrounding request
  must continue. Caller logic is the source of truth; audit is
  observational. We log the swallow path so the failure is visible in
  Sentry without breaking the request.
- **Tenant-aware via ContextVar.** Caller doesn't pass ``tenant`` —
  ``current_tenant()`` does. If no tenant is in scope (system events,
  cleanup tasks), the row is written with ``tenant=None``.
- **No raw PII in payload.** Callers must scrub. The audit table is the
  one place where redaction must already have happened upstream.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import UUID

from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)


# Audit retro Y1: payload size cap. Pre-fix the JSONField had no
# bound; a buggy caller passing a base64 image in metadata or a 50KB
# embedded blob would land a multi-MB row, bloat WAL, and slow GIN-less
# scans. 8 KB is generous for a forensic record-shape payload (envelope
# IDs + reason strings + a handful of contextual fields) while bounded
# enough that disk pressure can't compound from accidental misuse.
AUDIT_PAYLOAD_MAX_BYTES = 8 * 1024


def write_audit(
    action: str,
    *,
    target: str = "",
    target_id: UUID | str | None = None,
    payload: dict[str, Any] | None = None,
    actor_id: UUID | str | None = None,
) -> None:
    """Insert an AuditLog row for the current tenant.

    Args:
      action: Namespaced verb (``tenant.created``, ``cross_tenant.attempt``).
      target: Optional human-readable model name.
      target_id: Optional UUID of the affected row. Non-UUID values
        (e.g. integer YClients record IDs, opaque external strings)
        are coerced — stored under ``payload['target_id_raw']`` with
        the DB column set to ``None`` so the row still lands. See
        Audit retro B1.
      payload: Optional dict of extra structured data. Must not contain
               raw PII; callers redact upstream. Capped at
               :data:`AUDIT_PAYLOAD_MAX_BYTES`; oversize payloads are
               replaced with a ``{truncated, size, sha256}`` summary.
      actor_id: Optional UUID of the human actor. Non-UUID values are
        coerced like ``target_id`` (Audit retro B1).

    Behaviour:
      - Reads ``current_tenant()`` for the tenant FK (may be None).
      - Swallows all exceptions; logs them so Sentry can pick them up.
      - Returns None — caller never needs the AuditLog row back. If they
        do, that's a smell: audit is observational, not transactional.
    """

    # Local import to avoid Django app-loading order issues.
    from apps.audit.models import AuditLog

    tenant = current_tenant()

    # Audit retro B1: coerce target_id / actor_id to UUID. Pre-fix a
    # caller passing a non-UUID string (e.g. an integer YClients ID
    # "123456") triggered ValidationError inside .create(), which the
    # bare ``except Exception`` swallowed — the audit row silently
    # vanished. Forensic data loss masked as a clean write. Post-fix
    # non-UUID values flow into the payload as ``*_raw`` so the row
    # still lands with the contextual id visible to admin search.
    target_id_uuid, target_id_raw = _coerce_uuid_or_raw(target_id)
    actor_id_uuid, actor_id_raw = _coerce_uuid_or_raw(actor_id)

    merged_payload = _merge_otel_context(payload or {})
    if target_id_raw is not None:
        merged_payload["target_id_raw"] = target_id_raw
    if actor_id_raw is not None:
        merged_payload["actor_id_raw"] = actor_id_raw

    # Audit retro Y1: cap payload size. The summary preserves shape
    # (``{truncated, size, sha256}``) so admins can grep by hash and
    # the original payload can be recovered from the request-side logs
    # if the caller emitted both — a bounded forensic artefact is
    # strictly better than an unbounded one that silently corrupts
    # the table or fills disk.
    merged_payload = _cap_payload_size(merged_payload, action)

    try:
        AuditLog.all_tenants.create(
            tenant=tenant,
            actor_id=actor_id_uuid,
            action=action,
            target=target or "",
            target_id=target_id_uuid,
            payload=merged_payload,
        )
    except Exception:  # noqa: BLE001 — audit must never break the request
        logger.exception(
            "audit.write_failed action=%s tenant=%s target=%s:%s",
            action,
            tenant.id if tenant else None,
            target,
            target_id,
        )


def _coerce_uuid_or_raw(value: UUID | str | None) -> tuple[UUID | None, str | None]:
    """Return ``(uuid_or_none, raw_string_or_none)``.

    Audit retro B1: ``target_id`` / ``actor_id`` columns are
    ``UUIDField``; non-UUID strings would raise ValidationError at
    .create() time. Coerce here:
      * ``None`` → ``(None, None)``
      * UUID instance → ``(uuid, None)``
      * UUID-shaped string → ``(UUID(s), None)``
      * Non-UUID string → ``(None, raw)`` — caller writes the raw to
        ``payload`` so admin search by substring still works.
    """

    if value is None:
        return (None, None)
    if isinstance(value, UUID):
        return (value, None)
    raw = str(value).strip()
    # Empty / whitespace-only strings are treated as None — pre-fix
    # they would have landed as ``payload['target_id_raw'] = ""`` which
    # is noise with zero forensic signal (closes reviewer Y1).
    if not raw:
        return (None, None)
    try:
        return (UUID(raw), None)
    except (ValueError, AttributeError, TypeError):
        return (None, raw)


def _cap_payload_size(payload: dict[str, Any], action: str) -> dict[str, Any]:
    """Replace an oversize payload with a summary; pass through otherwise.

    Audit retro Y1: JSONField has no DB-level size cap. A runaway
    caller (e.g. an LLM call recording the entire prompt + completion)
    can blow the row size into MBs, bloat WAL, and slow every audit
    read. Cap at :data:`AUDIT_PAYLOAD_MAX_BYTES`. The summary keeps
    the row landable while preserving forensic correlation via the
    sha256.
    """

    try:
        # ``default=str`` casts un-serialisable objects to their repr so
        # the cap-check survives caller bugs. Round-trip via
        # ``json.loads`` so the returned payload is guaranteed safe for
        # Django's JSONField encoder (which doesn't use ``default=str``
        # — its bare ``JSONEncoder.default`` raises). Without the
        # round-trip, write_audit would still hit a TypeError inside
        # .create() and lose the row.
        serialised = json.dumps(payload, default=str, ensure_ascii=False)
        safe_payload = json.loads(serialised)
    except (TypeError, ValueError):
        # Payload isn't JSON-serialisable even with default=str —
        # degrade to a minimal summary rather than crashing the write.
        return {
            "truncated": True,
            "reason": "non_json_serialisable",
            "type_repr": type(payload).__name__,
        }
    size = len(serialised.encode("utf-8"))
    if size <= AUDIT_PAYLOAD_MAX_BYTES:
        return safe_payload
    digest = hashlib.sha256(serialised.encode("utf-8")).hexdigest()
    logger.warning(
        "audit.payload_truncated action=%s size_bytes=%d cap_bytes=%d sha256=%s",
        action,
        size,
        AUDIT_PAYLOAD_MAX_BYTES,
        digest,
    )
    return {
        "truncated": True,
        "size_bytes": size,
        "cap_bytes": AUDIT_PAYLOAD_MAX_BYTES,
        "sha256": digest,
    }


def _merge_otel_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Inject the current OTel span's ``trace_id`` + ``span_id`` into the
    audit payload under stable top-level keys (Sprint 8 / T3 / DRF-707).

    Returns a *new* dict so the caller-passed payload is never mutated
    in place (write_audit is observational; mutating the input would be
    surprising). Outside any active span the payload passes through
    unchanged — write_audit MUST be safe to call from system tasks /
    CLI / tests without OTel set up.

    Sprint 8 code review P1-1 collapsed the in-place try/import/getattr
    block to a single :func:`apps.observability.otel.get_current_span_ids`
    call shared with the logging filter and the replay recorder.
    """
    from apps.observability.otel import get_current_span_ids

    trace_id, span_id = get_current_span_ids()
    if not trace_id:
        return dict(payload)
    merged = dict(payload)
    # Don't clobber a payload-supplied trace_id (test fixtures sometimes
    # provide their own); the OTel context is a default, not an override.
    merged.setdefault("trace_id", trace_id)
    merged.setdefault("span_id", span_id)
    return merged
