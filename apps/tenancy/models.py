"""Tenant model — multi-tenant foundation (DRF-417 / Sprint 1 / A1).

Ported from Ayla `origin/dev:tenants/models.py` (blob fc2078de). See
`docs/adr/ADR-0001-multi-tenant-ready.md` and `docs/adr/ADR-0003-tenant-context-via-contextvar.md`
for the strategic context. Sprint 1 ships the registry table only; scoping
managers (A4 / `apps.tenancy.managers`), middleware (A3 / `apps.tenancy.middleware`),
and the `create_tenant` management command (A2) land in follow-up sub-issues.

Why a dedicated app instead of inlining into another:
- Tenant is a cross-cutting domain concept that every other app depends on.
- Future fields (TenantSubscription, TenantBilling, TenantFeatureFlag) land
  here without polluting unrelated models.
- Independent migration history simplifies rollback if multi-tenant rollout
  has to pause mid-flight.
"""

from __future__ import annotations

import re
import uuid

from django.db import models

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,49}$")


class _ActiveTenantManager(models.Manager):
    """Default manager — hides ``is_active=False`` tenants from app code.

    Admin and billing surfaces that need to see deactivated rows use
    ``Tenant.all_objects`` instead. Matches the pattern used in the source
    Ayla codebase (see `users.User`, `ai.Conversation`).
    """

    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class Tenant(models.Model):
    """A logical isolation boundary for platform data (ADR-0001).

    The MVP shape is deliberately minimal — just enough to scope foreign
    keys later. Pricing, feature flags, branding, etc. land in future
    fields or sibling tables.

    Slug is the wire identifier (URL paths, ``X-Tenant`` header values).
    Name is human-readable for admin / billing UI. ``id`` is the canonical
    FK target across the platform.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(
        max_length=50,
        unique=True,
        help_text=(
            "Lowercase identifier used in URLs and the X-Tenant header. "
            "Letters, digits, hyphen, underscore. Must start with a letter "
            "or digit. Cannot be changed after creation."
        ),
    )
    name = models.CharField(
        max_length=200,
        help_text="Human-readable name shown in admin and billing.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "False hides the tenant from default queries. Soft-disable a "
            "tenant without dropping data — billing freezes, scoping "
            "middleware returns 403 (strict mode) or routes to None (audit "
            "mode)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = _ActiveTenantManager()
    all_objects = models.Manager()

    class Meta:
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active", "slug"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    def clean(self) -> None:
        """Validate slug shape at the model layer.

        Django's `SlugField` allows uppercase letters and lone hyphens
        (e.g. ``-foo-``). The platform's `X-Tenant` header and URL paths
        require a stricter shape so slugs round-trip cleanly.
        """
        from django.core.exceptions import ValidationError

        super().clean()
        if self.slug and not _SLUG_RE.match(self.slug):
            raise ValidationError(
                {
                    "slug": (
                        "Slug must be lowercase alphanumeric (with - or _), "
                        "2–50 chars, and start with a letter or digit."
                    ),
                }
            )
