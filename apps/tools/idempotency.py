"""Idempotency helper — single-winner contract (DRF-427 / B2).

Usage:

    from apps.tools.idempotency import with_idempotency, AlreadyClaimed

    try:
        with with_idempotency("webhook:max:event:abc123", ttl_seconds=86400):
            # Do the side-effecting work exactly once.
            send_outbound_reply(...)
    except AlreadyClaimed as e:
        # A previous call already won the key. e.row has the prior result.
        return e.row.payload.get("response")

Why DB-level unique constraint, not application locking:
  - Application locks (in-process or Redis-based) require everyone to
    coordinate. The DB unique constraint is enforced by Postgres /
    SQLite without any caller cooperation.
  - One ``IntegrityError`` per concurrent caller is cheap. The losing
    caller catches it and reads the existing row.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from typing import Iterator

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.tenancy.context import current_tenant
from apps.tools.models import IdempotencyKey


class AlreadyClaimed(Exception):
    """Raised when an idempotency key has already been claimed and not expired.

    Attribute ``row`` holds the prior :class:`IdempotencyKey` instance —
    callers can read its ``payload`` to re-return the cached result.
    """

    def __init__(self, row: IdempotencyKey):
        self.row = row
        super().__init__(f"Idempotency key {row.key!r} already claimed.")


@contextmanager
def with_idempotency(
    key: str, *, ttl_seconds: int = 86_400, payload: dict | None = None
) -> Iterator[IdempotencyKey]:
    """Acquire an idempotency key for the duration of the block.

    On entry: try to insert the key (atomic, unique-constraint-protected).
      - Success → yield the new row.
      - IntegrityError → check if existing row is still valid (not expired).
        - Valid → raise AlreadyClaimed(row).
        - Expired → upsert the row (delete + insert), yield the new one.

    On exit: nothing. The row stays in the DB until cleanup_old_idempotency_keys
    deletes it. Caller can update ``row.payload`` mid-block to cache the
    result for future retry hits.

    Args:
      key: Caller-supplied unique key (encode operation + payload).
      ttl_seconds: How long the claim stays valid. Default 1 day.
      payload: Optional initial payload to store on the row.
    """

    tenant = current_tenant()
    expires_at = timezone.now() + timedelta(seconds=ttl_seconds)

    try:
        with transaction.atomic():
            row = IdempotencyKey.objects.create(
                key=key,
                tenant=tenant,
                expires_at=expires_at,
                payload=payload or {},
            )
    except IntegrityError:
        # Someone else won the race or a prior call already claimed.
        existing = IdempotencyKey.objects.get(key=key)
        if existing.expires_at > timezone.now():
            raise AlreadyClaimed(existing) from None
        # Expired claim — reset it (rare path: post-TTL retry).
        with transaction.atomic():
            existing.delete()
            row = IdempotencyKey.objects.create(
                key=key,
                tenant=tenant,
                expires_at=expires_at,
                payload=payload or {},
            )

    yield row
