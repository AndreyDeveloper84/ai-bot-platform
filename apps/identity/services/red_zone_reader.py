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

  # DRF-2133 — the data subject's own screen «Что Ayla помнит»:
  RedZoneReader.list_live_for_subject(user_id=, accessor_role=, request_id=, purpose=,
                                      accessor_principal=)   # one audit row per entry
  RedZoneReader.soft_delete_for_subject(entry_id=, user_id=, accessor_role=, request_id=,
                                        purpose=, reason=, accessor_principal=)  # `delete` row
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.identity.models import MemoryEntry, RedZoneAccessLog
from apps.identity.services.exceptions import TenantScopeViolation
from apps.identity.services.red_zone_guc import (
    _reset_red_zone_guc,
    _set_red_zone_guc,
    red_zone_principal,
)

logger = logging.getLogger(__name__)


def _default_principal_for_role(role: str) -> str:
    """Best-effort concrete identity when caller didn't supply one.

    Per round-2 AS2: `accessor_principal` must be a concrete identity
    (Celery worker hostname + pid, cron job name, staff UUID,
    service-account name). For `ops_admin` callers we cannot infer
    the staff UUID — they MUST supply `accessor_principal` explicitly.
    """
    if role == RedZoneAccessLog.ACCESSOR_AYLA_LLM:
        return red_zone_principal(role, "worker")
    if role == RedZoneAccessLog.ACCESSOR_SYSTEM_JOB:
        return red_zone_principal(role, "system")
    return "unknown"


class RedZoneReader:
    """The sole sanctioned read (and subject-requested delete) path for red-zone rows."""

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
        accessor_principal = cls._resolve_principal(accessor_role, accessor_principal)

        # Round-5 F1 fix: the GUC binds to the outermost (sub)transaction,
        # not to the SAVEPOINT that Django opens when `atomic()` is nested.
        # Without an explicit RESET, the GUC survives past `read()` return
        # and the caller's NEXT ORM query inside their outer `atomic()`
        # would pass RLS for red rows of unrelated users. Cat 3 cross-
        # tenant leak vector identical-blast-radius to the round-4 Cat-4
        # policy-stacking fix. We chose variant B (explicit RESET in
        # finally, no `durable=True`) per tech-lead 2026-05-25 to avoid
        # breaking legitimate Celery / skills callers that wrap our
        # accessor in their own `transaction.atomic()`.
        try:
            with transaction.atomic():
                # Step 1 — set the GUC so RLS allows the row through (Postgres).
                # SQLite has no RLS — SET ... is unknown syntax there, so the
                # call is gated on `connection.vendor` inside the helper. On
                # SQLite the read works without RLS gating; the audit +
                # ownership checks still run (application-side defence works
                # on both engines).
                cls._set_guc(request_id)

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
        finally:
            # Round-5 F1: clear the GUC regardless of success/failure/exception
            # path so the next ORM query on this connection cannot inherit
            # red-zone visibility. RESET runs even when the atomic block
            # rolled back (set_config with is_local=true is supposed to clear
            # at transaction END, but caller's OUTER atomic keeps the txn
            # alive past our SAVEPOINT release — hence explicit RESET).
            # Round-5 F1-C (#703): a failed RESET must not MASK the original
            # exception from the try-block — see `_reset_guc`.
            cls._reset_guc()

    # ------------------------------------------------------------------
    # DRF-2133 — субъект данных: экран «Что Ayla помнит»
    # ------------------------------------------------------------------

    @classmethod
    def list_live_for_subject(
        cls,
        *,
        user_id: uuid.UUID,
        accessor_role: str,
        request_id: uuid.UUID,
        purpose: str,
        accessor_principal: Optional[str] = None,
    ) -> list[MemoryEntry]:
        """Live red-zone entries of ONE subject, audited row by row.

        The screen «Что Ayla помнит» needs the list, and :meth:`read` takes
        one id: an id list gathered outside this module would see zero rows
        on Postgres (RLS hides red without the GUC) and would be a second
        red read path besides. Same three layers as :meth:`read`: GUC set
        inside one ``atomic()``, ownership by construction (the filter IS
        ``user_id``), one ``RedZoneAccessLog`` row per returned entry in
        the same transaction — an empty result writes no log (no orphan
        log, round-2 AS1). ``RESET`` in ``finally`` (round-5 F1).

        Live = not tombstoned and no pending delete request, the same gate
        ``memory_reader`` applies to green. No cross-tenant carve-out here:
        the subject sees their own memory whatever tenant wrote it.
        """
        accessor_principal = cls._resolve_principal(accessor_role, accessor_principal)

        try:
            with transaction.atomic():
                cls._set_guc(request_id)
                entries = list(
                    MemoryEntry.objects.filter(
                        user_id=user_id,
                        sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
                        soft_deleted_at__isnull=True,
                        delete_requested_at__isnull=True,
                    ).order_by("created_at")
                )
                RedZoneAccessLog.objects.bulk_create(
                    [
                        RedZoneAccessLog(
                            memory_entry_id=entry.id,
                            user_id=user_id,
                            accessor_role=accessor_role,
                            accessor_principal=accessor_principal,
                            access_type=RedZoneAccessLog.ACCESS_READ,
                            request_id=request_id,
                            purpose=purpose,
                        )
                        for entry in entries
                    ]
                )
                return entries
        finally:
            cls._reset_guc()

    @classmethod
    def soft_delete_for_subject(
        cls,
        *,
        entry_id: uuid.UUID,
        user_id: uuid.UUID,
        accessor_role: str,
        request_id: uuid.UUID,
        purpose: str,
        reason: str,
        accessor_principal: Optional[str] = None,
    ) -> bool:
        """Tombstone ONE live red entry of the subject; ``True`` if a row moved.

        The green deleter (``memory_deleter.soft_delete_green_entries``) is
        green-only on purpose; a red tombstone needs the GUC (the UPDATE's
        WHERE is subject to the SELECT policy) and an access log of type
        ``delete``. Same UPDATE shape as the green path: tombstone +
        ``status='deleted'`` + ``updated_at`` in one statement (CHECK 4).
        Not the subject's, not red, or already gone → ``False``, no log.
        """
        accessor_principal = cls._resolve_principal(accessor_role, accessor_principal)

        try:
            with transaction.atomic():
                cls._set_guc(request_id)
                now = timezone.now()
                moved = MemoryEntry.objects.filter(
                    id=entry_id,
                    user_id=user_id,
                    sensitivity_zone=MemoryEntry.SENSITIVITY_RED,
                    soft_deleted_at__isnull=True,
                    delete_requested_at__isnull=True,
                ).update(
                    delete_requested_at=now,
                    soft_deleted_at=now,
                    deletion_reason=reason,
                    status=MemoryEntry.STATUS_DELETED,
                    updated_at=now,
                )
                if moved:
                    RedZoneAccessLog.objects.create(
                        memory_entry_id=entry_id,
                        user_id=user_id,
                        accessor_role=accessor_role,
                        accessor_principal=accessor_principal,
                        access_type=RedZoneAccessLog.ACCESS_DELETE,
                        request_id=request_id,
                        purpose=purpose,
                    )
                return bool(moved)
        finally:
            cls._reset_guc()

    @staticmethod
    def _resolve_principal(accessor_role: str, accessor_principal: Optional[str]) -> str:
        """Concrete «who» for the audit row, fail-loud where a default cannot be honest.

        ops_admin = privileged break-glass access. The fallback ``"unknown"``
        would be a 152-ФЗ Chapter 3 forensic blind spot — auditor query «who
        did this access» would yield nothing actionable. Per tech-lead
        direction 2026-05-23 (Q2 fork): ops_admin MUST supply explicit staff
        identity. DRF-2133 extends the same rule to ``data_subject``: the
        subject is a concrete shell (``bot_user:<pk>``), and a self-service
        read/delete logged as «unknown» is the same blind spot from the other
        side. Roles with a derivable default (worker / job) keep it.
        """
        if accessor_principal is None:
            accessor_principal = _default_principal_for_role(accessor_role)
        if accessor_principal == "unknown" and accessor_role in (
            RedZoneAccessLog.ACCESSOR_OPS_ADMIN,
            RedZoneAccessLog.ACCESSOR_DATA_SUBJECT,
        ):
            raise ValueError(
                f"{accessor_role} role requires explicit accessor_principal "
                "(staff User UUID for ops_admin, bot_user:<pk> for data_subject) "
                "per 152-ФЗ Chapter 3 audit traceability requirement."
            )
        return accessor_principal

    @staticmethod
    def _set_guc(request_id: uuid.UUID) -> None:
        """Шаг 1 любого красного доступа. DRF-2180 — общая реализация."""
        _set_red_zone_guc(request_id)

    @staticmethod
    def _reset_guc() -> None:
        """Снятие GUC на любом пути выхода. DRF-2180 — общая реализация.

        Вынесено в :mod:`apps.identity.services.red_zone_guc`, когда у зоны
        появился второй законный путь (массовое снятие по «забудь всё»):
        правило «только под GUC» живёт ровно в той мере, в какой у него одна
        реализация.
        """
        _reset_red_zone_guc()
