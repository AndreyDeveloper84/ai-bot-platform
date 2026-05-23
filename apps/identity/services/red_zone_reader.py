"""RedZoneReader — the sole sanctioned read path for red-zone MemoryEntry.

Per spec `docs/specs/memory-entry-schema.md` §10 + ADR-0011 §10.

# Defence-in-depth layers

1. **DB layer (veha 2)** — RLS policy `memory_entry_non_red_visible`
   hides red rows unless `current_setting('ayla.red_zone_access_context',
   true)` matches a canonical UUID regex. Direct ORM access (no GUC)
   returns zero rows on Postgres.
2. **Application layer (this module)** — the accessor sets the GUC
   via `SET LOCAL`, enforces ownership (caller-supplied user_id) and
   the cross-tenant carve-out (ADR-0011 §9.1), and appends the
   `RedZoneAccessLog` audit row AFTER the SELECT succeeds.
3. **CI layer (veha 4)** — AST lint `tools/lint/red_zone_guard.py`
   rejects production-code queries against `MemoryEntry` filtered by
   `sensitivity_zone='red'` outside this module.

# Atomicity invariant (round-2 AS1 — no orphan log)

Audit row is INSERTed AFTER the SELECT, both inside one
`transaction.atomic()` block. On any failure (DoesNotExist,
TenantScopeViolation, ownership mismatch) the entire transaction
rolls back — there's no audit row for a read that didn't happen,
AND there's no successful read with no audit row.

# Caller contract (spec §10 + round-2 AS1 + AS2)

  RedZoneReader.read(
      entry_id=uuid.UUID,
      user_id=uuid.UUID,                      # ownership check
      accessor_role=str,                      # RedZoneAccessLog.ACCESSOR_*
      request_id=uuid.UUID,                   # MUST match GUC regex
      purpose=str,                            # human-readable purpose
      accessor_principal=Optional[str],       # concrete identity (AS2)
      expected_source_tenant_id=Optional[uuid.UUID],  # None = cross-tenant
  )
"""

from __future__ import annotations

import os
import socket
import uuid
from typing import Optional

from django.db import connection, transaction

from apps.identity.models import MemoryEntry, RedZoneAccessLog
from apps.identity.services.exceptions import TenantScopeViolation


def _default_principal_for_role(role: str) -> str:
    """Best-effort concrete identity when caller didn't supply one.

    Per round-2 AS2: `accessor_principal` must be a concrete identity
    (Celery worker hostname + pid, cron job name, staff UUID,
    service-account name). For `ops_admin` callers we cannot infer
    the staff UUID — they MUST supply `accessor_principal` explicitly.
    """
    hostname = socket.gethostname()
    pid = os.getpid()
    if role == RedZoneAccessLog.ACCESSOR_AYLA_LLM:
        return f"worker:{hostname}:{pid}"
    if role == RedZoneAccessLog.ACCESSOR_SYSTEM_JOB:
        return f"system:{hostname}:{pid}"
    return "unknown"


class RedZoneReader:
    """The sole sanctioned read path for red-zone MemoryEntry rows."""

    @classmethod
    def read(
        cls,
        *,
        entry_id: uuid.UUID,
        user_id: uuid.UUID,
        accessor_role: str,
        request_id: uuid.UUID,
        purpose: str,
        accessor_principal: Optional[str] = None,
        expected_source_tenant_id: Optional[uuid.UUID] = None,
    ) -> MemoryEntry:
        """Read a red-zone entry with mandatory audit + scope checks.

        Args:
            entry_id: target MemoryEntry.id.
            user_id: caller's claim of who owns the entry — used for
                ownership check (spec §13 row 14.29).
            accessor_role: one of `RedZoneAccessLog.ACCESSOR_*`.
            request_id: UUID matching the GUC regex (lowercase canonical
                form with dashes). Validates the RLS GUC on Postgres.
            purpose: human-readable reason for access (audit field).
            accessor_principal: concrete identity (hostname + pid, staff
                UUID, etc.). For `ops_admin`, caller MUST supply it.
            expected_source_tenant_id: if not None, the entry's
                `source_tenant_id` must match — otherwise
                `TenantScopeViolation` (ADR-0011 §9.1).

        Returns:
            The MemoryEntry. Audit row in `RedZoneAccessLog` is
            committed atomically with the read.

        Raises:
            MemoryEntry.DoesNotExist: entry missing OR `entry.user_id`
                doesn't match `user_id` (ownership mismatch is
                indistinguishable from missing — by design, to avoid
                leaking «exists but not yours»).
            TenantScopeViolation: cross-tenant red read attempted.
        """
        if accessor_principal is None:
            accessor_principal = _default_principal_for_role(accessor_role)

        with transaction.atomic():
            # Step 1 — set the GUC so RLS allows the row through (Postgres).
            # SQLite has no RLS — SET ... is unknown syntax there, so the
            # call is gated on `connection.vendor`. On SQLite the read
            # works without RLS gating; the audit + ownership checks still
            # run (application-side defence works on both engines).
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('ayla.red_zone_access_context', %s, true)",
                        [str(request_id)],
                    )

            # Step 2 — ownership-scoped SELECT. If entry_id is missing OR
            # the entry belongs to a different user, DoesNotExist fires
            # and the atomic block rolls back — no audit row is committed
            # (the no-orphan-log invariant from round-2 AS1).
            entry = MemoryEntry.objects.get(id=entry_id, user_id=user_id)

            # Step 3 — cross-tenant carve-out check (ADR-0011 §9.1).
            # When the caller pins a tenant scope, the entry's source
            # tenant MUST match. None means «system-wide context, e.g.
            # ayla_llm building cross-tenant memory» — allowed.
            if expected_source_tenant_id is not None:
                if entry.source_tenant_id != expected_source_tenant_id:
                    raise TenantScopeViolation(
                        f"Red-zone entry {entry_id} belongs to tenant "
                        f"{entry.source_tenant_id!s} but caller expected "
                        f"{expected_source_tenant_id!s}."
                    )

            # Step 4 — audit row LAST, inside the same transaction, so
            # it's only committed when the access actually succeeded.
            RedZoneAccessLog.objects.create(
                memory_entry_id=entry_id,
                user_id=user_id,
                accessor_role=accessor_role,
                accessor_principal=accessor_principal,
                access_type=RedZoneAccessLog.ACCESS_READ,
                request_id=request_id,
                purpose=purpose,
            )

            return entry
