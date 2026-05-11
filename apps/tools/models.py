"""IdempotencyKey model (DRF-427 / B2).

Single-winner enforcement for retried webhook ingestion, repeated
external commands, and any other side-effecting operation that must
run exactly once for a given key.

Contract:
  - ``key`` is unique. The DB constraint is what enforces "exactly one
    winner" under concurrent insert — race resolution is delegated to
    Postgres / SQLite, not application code.
  - ``expires_at`` is the soft TTL. After expiry, the same key can be
    re-acquired (a retry from a far-future client doesn't replay a
    long-completed operation).
  - ``tenant`` is nullable — system-level idempotency (e.g., breaker
    rotation events) has no tenant scope.
"""

from __future__ import annotations

import uuid

from django.db import models


class IdempotencyKey(models.Model):
    """Records that "this key has been claimed by some caller".

    Insert order:
      1. caller tries ``IdempotencyKey.objects.create(key="...")``
      2. one wins, one raises IntegrityError (unique constraint)
      3. loser handles IntegrityError → returns the existing row's
         result OR re-raises depending on whether the operation was
         completed (call site decides)

    Use ``with_idempotency()`` in ``apps.tools.idempotency`` for the
    standard happy-path wrapper.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(
        max_length=200,
        unique=True,
        help_text="Caller-supplied key. Should encode the operation + payload "
        "(e.g. 'webhook:max:event_id:abc123').",
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="idempotency_keys",
        null=True,
        blank=True,
    )
    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Optional cached result of the operation. Caller stores "
        "what they want to re-return on a retry hit.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(
        help_text="After this timestamp the key can be re-acquired.",
    )

    # No TenantScopedManager — IdempotencyKey is consulted from contexts
    # that don't always have a tenant (worker boot, breaker reset).
    # Caller is responsible for scoping reads via .filter(tenant=...) when
    # the operation is tenant-aware.
    objects = models.Manager()

    class Meta:
        verbose_name = "Idempotency key"
        verbose_name_plural = "Idempotency keys"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["expires_at"]),
            models.Index(fields=["tenant", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"IdempotencyKey[{self.key}]"
