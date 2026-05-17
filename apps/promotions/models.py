"""Promotion model (DRF-842 / Phase 1 / B6).

A :class:`Promotion` is a tenant-scoped promo code that grants a flat
percent discount on either ALL services on the tenant (empty
``applicable_service_ids``) or a curated subset. Admins manage rows
via Django admin (see :mod:`apps.promotions.admin`); the ``calc_price``
LLM tool reads via :func:`apps.promotions.services.validate_promo`.

### Why a separate app vs apps/catalog

The catalog is a read-only mirror of mysite, refreshed every 15 minutes
by an automated sync beat. Promotions are platform-owned data —
operators add / kill them on a different cadence (per-campaign, often
intra-day for flash sales). Bundling promos into ``apps.catalog`` would
mean either (a) the sync beat clobbers operator edits, or (b) the
sync logic grows a "platform-owned columns" exception list. Neither
scales. A dedicated app keeps the lifecycle clean.

### Multi-tenant by construction

``tenant`` FK + :class:`TenantScopedManager` default manager — same
pattern as :class:`apps.booking.models.BookingRequest`. Cross-tenant
code paths (admin sweep, billing reconciliation) use the
``all_tenants`` escape hatch.

### Case-insensitive code matching

Codes are stored UPPER-cased at write time (see
``Promotion.save`` + the admin form). Validation lookups also
upper-case the user-supplied code before the query. The
``unique_together=(tenant, code)`` constraint is therefore
case-insensitive by virtue of normalisation — without a per-row
``code_upper`` denormalised column or a functional index (Postgres-
specific, brittle on cross-DB migration).

### applicable_service_ids semantics

* Empty list → applies to every service on the tenant.
* Non-empty list → applies ONLY to listed services; other services
  return ``wrong_service`` from validation.

Stored as ``JSONField(default=list)`` rather than
``ArrayField(UUIDField)`` for cross-DB portability — the codebase
already uses JSON arrays for catalog ``goals`` (see
:class:`apps.catalog.models.CatalogService`). Postgres production and
SQLite tests both work without per-backend conditionals; the JSON
list semantics are identical to ArrayField for read-only lookup
(``service_id in ids``). UUID values stored as their string form.

### Indexing strategy

* ``unique_together=(tenant, code)`` — the case-insensitive
  uniqueness constraint AND the index that backs the
  hot-path validation query (``Promotion.objects.filter(
  tenant=t, code=normalised_code)``).
* Additional index on ``(tenant, code, is_active)`` so the validator's
  ``is_active`` predicate filters at index scan time instead of after
  the row fetch — useful when an admin disables a heavily-used code
  rather than deleting it.
"""

from __future__ import annotations

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.tenancy.managers import TenantScopedManager


class Promotion(models.Model):
    """A tenant-scoped promo code granting a flat percent discount."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="promotions",
        help_text="Owning tenant. PROTECT — promo codes are operator-"
        "configured commercial settings; deleting a tenant requires "
        "explicit data purge first (audit + finance retention).",
    )
    code = models.CharField(
        max_length=64,
        help_text="Promo code shown to the customer. Stored UPPER-cased "
        "at save time so matching is case-insensitive without a "
        "Postgres-specific functional index.",
    )
    discount_percent = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(99)],
        help_text="Percent off the service base price. 1..99 — a 0% "
        "promo is a UI bug; a 100% promo would be a free service, "
        "handled through ops/handoff not the bot.",
    )
    valid_from = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Earliest moment the code is honourable. Null = no lower bound.",
    )
    valid_to = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Latest moment the code is honourable. Null = open-"
        "ended (until admin disables it).",
    )
    applicable_service_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of CatalogService.id UUIDs (as strings) the "
        "promo applies to. Empty = applies to ALL services on the "
        "tenant. Stored as JSON for cross-DB portability (see "
        "module docstring on the ArrayField vs JSONField call).",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Admin kill-switch. False → validator returns "
        "``inactive`` regardless of validity window. Preserves the row "
        "for audit + analytics ('how many tried to use this expired "
        "code last month?') instead of hard-deleting.",
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Internal-only operator notes (campaign source, "
        "approval link, etc.). Never shown to the customer.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Promotion"
        verbose_name_plural = "Promotions"
        ordering = ["-created_at"]
        unique_together = (("tenant", "code"),)
        indexes = [
            # Validation hot path: (tenant, code) is the unique index
            # already; this composite adds is_active so the validator's
            # is_active predicate filters at index scan time.
            models.Index(fields=["tenant", "code", "is_active"]),
        ]

    def save(self, *args: object, **kwargs: object) -> None:
        """Normalise ``code`` to upper-case before writing.

        Storing the canonical form keeps the
        ``unique_together=(tenant, code)`` constraint
        case-insensitive without a per-row denormalised column or a
        Postgres-specific functional index. The admin form + the
        validation query both upper-case before they touch the DB.
        """
        if self.code:
            self.code = self.code.strip().upper()
        super().save(*args, **kwargs)  # type: ignore[arg-type]

    def __str__(self) -> str:
        return f"{self.code} (-{self.discount_percent}%)"
