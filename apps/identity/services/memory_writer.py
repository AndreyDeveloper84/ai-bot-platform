"""Memory writer — the single sanctioned write path for MemoryEntry.

Per spec `docs/specs/memory-entry-schema.md` §11 + ADR-0011 §10.2
(minor protection fail-closed) + §11.2 (zone promotion as fresh
consent event).

# Responsibilities

1. **Minor protection (fail-CLOSED).** Yellow/red writes call a DOB
   lookup against Ayla REST to verify the user is an adult. If the
   lookup is unavailable, `MinorProtectionLookupFailed` is raised
   internally, caught here, and translated into:
     - the write is silently dropped (`write_entry` returns None);
     - a `RedZoneAccessLog` row with `access_type='write_rejected_
       dob_lookup'` is appended for forensic trail.
   Green writes bypass the check entirely — green data has no minor-
   protection consideration.

2. **Minor_lock guard.** When `UPC.minor_lock` is True (set by the
   reconciliation job in #597 when a post-fact minor is detected),
   yellow/red writes are pre-emptively rejected — no DOB lookup,
   same audit row, returns None.

3. **Zone-promotion guard.** Use `promote_zone` to change an entry's
   `sensitivity_zone`. Green→yellow/red REQUIRES a `consent_token`
   argument — otherwise `ZonePromotionRequiresConsent` (ADR-0011
   §11.2). Demotion (yellow→green, red→yellow) is always allowed.

# Phase 0 reality

The Ayla REST DOB endpoint does not exist yet (tracked in #597). Per
tech-lead direction (2026-05-23) the `_check_minor_protection` stub
ALWAYS raises `MinorProtectionLookupFailed`. Practical effect: in
production, yellow/red writes are silently dropped 100% of the time
until #597 lands the real lookup. Tests verify this is the observed
behaviour.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import timedelta
from typing import Any, Optional

from django.db import transaction
from django.utils import timezone

from apps.identity.models import MemoryEntry, RedZoneAccessLog, UserPersonalContext
from apps.identity.services.exceptions import (
    MinorProtectionLookupFailed,
    ZonePromotionRequiresConsent,
)


def _check_minor_protection(user_id: uuid.UUID) -> None:
    """Verify the user is not a minor before allowing yellow/red writes.

    Phase 0 stub — always raises MinorProtectionLookupFailed because
    the Ayla REST DOB endpoint isn't implemented yet (#597).

    This is correct fail-closed behaviour: until DOB can be verified,
    no yellow/red writes are allowed. The caller catches the exception
    and converts it to a silent drop + audit row.

    After #597 lands this function will:
      - HTTP GET /api/v1/users/{user_id}/dob (Ayla REST)
      - on 200 with `is_adult=true` → return None
      - on 200 with `is_adult=false` → flip `UPC.minor_lock=True` and
        raise MinorProtectionLookupFailed (caller-side handling identical)
      - on timeout / 5xx / unknown → raise MinorProtectionLookupFailed
    """
    raise MinorProtectionLookupFailed(
        f"DOB lookup unavailable for user {user_id} — Ayla REST DOB "
        "endpoint not implemented yet (#597). Yellow/red writes "
        "fail-closed per ADR-0011 §10.2."
    )


def _writer_principal() -> str:
    """Concrete identity for the writer (round-2 AS2 audit field)."""
    return f"memory_writer:{socket.gethostname()}:{os.getpid()}"


def _audit_write_rejected(
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    purpose: str,
) -> None:
    """Append a write_rejected_dob_lookup audit row — durable per ADR-0011 §11.3.

    Called from the fail-closed paths (DOB lookup failed OR minor_lock
    set). No MemoryEntry row was created, so `memory_entry_id` uses a
    placeholder uuid4() — the access_type already conveys «no entry
    exists for this row» semantics.

    Round-5 F2 fix: `transaction.atomic(durable=True)` commits the
    audit INSERT independently of the caller's outer atomic block. Per
    ADR-0011 §11.3 the rejection audit row is 152-ФЗ Chapter 3 forensic
    evidence — «platform refused to write sensitive data because age
    unverifiable». Losing the row to a caller-side rollback would
    break the regulatory invariant the writer was designed to uphold.

    `durable=True` raises `TransactionManagementError` if the caller is
    inside their own `transaction.atomic()` — this is intentional:
    callers MUST NOT wrap `write_entry()` in their atomic block, or
    they risk silent forensic loss. Fail-loud per Q2 fork 2026-05-25.
    """
    with transaction.atomic(durable=True):
        RedZoneAccessLog.objects.create(
            memory_entry_id=uuid.uuid4(),  # no real entry was created
            user_id=user_id,
            accessor_role=RedZoneAccessLog.ACCESSOR_SYSTEM_JOB,
            accessor_principal=_writer_principal(),
            access_type=RedZoneAccessLog.ACCESS_WRITE_REJECTED_DOB,
            request_id=request_id,
            purpose=purpose,
        )


def write_entry(
    *,
    user_id: uuid.UUID,
    personal_context: UserPersonalContext,
    sensitivity_zone: str,
    source: str,
    kind: str,
    content: dict[str, Any],
    request_id: uuid.UUID,
    purpose: str,
    consent_at: Optional[Any] = None,
    source_tenant_id: Optional[uuid.UUID] = None,
    last_inferred_at: Optional[Any] = None,
    ttl_days: Optional[int] = None,
) -> Optional[MemoryEntry]:
    """Create a new MemoryEntry with all spec §11 guards.

    Args:
        user_id: subject — must match `personal_context.user_id`.
        personal_context: parent UPC (FK target).
        sensitivity_zone: 'green' / 'yellow' / 'red'.
        source: 'explicit' / 'inferred' / 'signal'.
        kind: KIND_CHOICES value.
        content: JSON dict, will be Fernet-encrypted at rest.
        request_id: audit reference (UUID).
        purpose: human-readable purpose for the audit log.
        consent_at: REQUIRED for yellow/red (CHECK 2 enforces it).
        source_tenant_id: tenant the fact originated at. None for
            cross-tenant / platform-level facts.
        last_inferred_at: REQUIRED when source IN ('inferred','signal'),
            MUST be NULL when source='explicit' (CHECK 1 enforces it).
        ttl_days: per-zone retention cap. None = no auto-TTL (green).

    Returns:
        The created MemoryEntry on success, OR None when the write was
        silently dropped by the fail-closed minor-protection path.
    """
    # Yellow/red gate: minor protection.
    if sensitivity_zone in (
        MemoryEntry.SENSITIVITY_YELLOW,
        MemoryEntry.SENSITIVITY_RED,
    ):
        # Short-circuit: minor_lock pre-emptively rejects without DOB lookup.
        if personal_context.minor_lock:
            _audit_write_rejected(user_id, request_id, purpose)
            return None

        # DOB lookup. Phase 0 stub always raises → fail-closed.
        try:
            _check_minor_protection(user_id)
        except MinorProtectionLookupFailed:
            _audit_write_rejected(user_id, request_id, purpose)
            return None

    # Canonical §3.1 fields at write time (Migration Plan Step 3.5): stop
    # NEW schema drift after the Step-3 backfill. EXPLICIT persistent
    # writes are canonical user_stated facts — stamped here, in the single
    # sanctioned write path, so every explicit caller is covered. ONE
    # timestamp per write operation (no auto_now semantics): effective_from
    # == updated_at == the expiry base. inferred/signal rows are NOT
    # stamped — provenance=user_confirmed_inference may only come from the
    # proposal flow (Step 4+), never silently from the writer. consent_scope
    # / source_event_id / evidence_refs / derivation_method are never
    # fabricated here; purpose_tags stays [] (no category policy yet).
    canonical: dict[str, Any] = {}
    if source == MemoryEntry.SOURCE_EXPLICIT:
        write_ts = timezone.now()
        canonical = {
            "status": MemoryEntry.STATUS_ACTIVE,
            "provenance": MemoryEntry.PROVENANCE_USER_STATED,
            "effective_from": write_ts,
            "updated_at": write_ts,
            "expires_at": (write_ts + timedelta(days=ttl_days) if ttl_days is not None else None),
        }

    return MemoryEntry.objects.create(
        user_id=user_id,
        personal_context=personal_context,
        sensitivity_zone=sensitivity_zone,
        source=source,
        kind=kind,
        content=content,
        consent_at=consent_at,
        source_tenant_id=source_tenant_id,
        last_inferred_at=last_inferred_at,
        ttl_days=ttl_days,
        **canonical,
    )


def promote_zone(
    *,
    entry: MemoryEntry,
    new_zone: str,
    consent_token: Optional[str] = None,
) -> MemoryEntry:
    """Change `entry.sensitivity_zone`.

    Green→yellow or green→red requires `consent_token` per ADR-0011
    §11.2 — otherwise `ZonePromotionRequiresConsent`. On a valid
    promotion `consent_at` is set in the SAME UPDATE so CHECK 2 is
    satisfied (yellow/red rows require consent_at NOT NULL).

    Demotion (yellow→green, red→yellow, red→green) is always allowed
    without a token — moving toward less sensitivity is a privacy
    improvement.
    """
    promoting_up = entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN and new_zone in (
        MemoryEntry.SENSITIVITY_YELLOW,
        MemoryEntry.SENSITIVITY_RED,
    )

    if promoting_up:
        if not consent_token:
            raise ZonePromotionRequiresConsent(
                f"Cannot promote entry {entry.id} from "
                f"{entry.sensitivity_zone!r} to {new_zone!r} without "
                "consent_token (ADR-0011 §11.2)."
            )
        # Same UPDATE — satisfies CHECK 2 (yellow/red require
        # consent_at NOT NULL).
        entry.sensitivity_zone = new_zone
        entry.consent_at = timezone.now()
        entry.save(update_fields=["sensitivity_zone", "consent_at"])
    else:
        entry.sensitivity_zone = new_zone
        entry.save(update_fields=["sensitivity_zone"])

    return entry
