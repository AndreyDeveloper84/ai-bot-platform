"""Consent registry (DRF-456 / Sprint 3 / A1).

First-class consent tracking for 152-ФЗ + GDPR compliance. Every
``has_consent(bot_user, type)`` check in the platform reads from this
table — there is no "consent is implied by event X" shortcut. Per
Compass principle from PHASE0_DESIGN §3.7: "hardcode consent checks
in code = legal liability when 152-ФЗ amends". First-class means a
new consent type is a migration (one row in the choices tuple), not
a code rewrite.

### Lifecycle

A ConsentRecord row is **append-only by convention**:
- ``grant()`` writes a new row with ``granted=True``, ``withdrawn_at=None``.
- ``withdraw()`` does NOT delete or update; it stamps ``withdrawn_at=now``
  on the latest active row, leaving the audit trail intact. A subsequent
  ``grant()`` writes a fresh row.
- ``has_consent()`` reads the latest row for ``(bot_user, consent_type)``
  with ``granted=True AND withdrawn_at IS NULL`` (and optionally a
  matching ``document_version``).

This means a user who grants → withdraws → grants again produces 3
rows, not 1. The whole history is queryable for a future DPO audit.

### 4 consent types (Phase 0)

- ``PERSONAL_DATA`` — 152-ФЗ baseline. Required for any user contact
  storage (BotUser.phone, Conversation history). Granted at first
  message via implicit consent flow (Sprint 4+).
- ``MARKETING`` — promotional broadcasts, newsletter, retargeting.
  Always opt-in.
- ``PHOTO_BIOMETRIC`` — image uploads (food photos for nutrition tracking,
  before/after for cosmetology). Special category under 152-ФЗ →
  explicit, document-versioned consent required.
- ``HEALTH`` — health-related questions / answers. Special category
  for medical-adjacent flows (massage contraindications, allergies).

### Document version

``document_version`` snapshots which privacy-policy text the user saw
when granting. If the operator publishes ``privacy-v2.0``, the
``has_consent(..., document_version="privacy-v2.0")`` check returns
False for users who only granted under v1.x — triggering a re-prompt
flow. This is the legal-foundation primitive.
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.tenancy.managers import TenantScopedManager


class ConsentRecord(models.Model):
    """One row per (bot_user, consent_type, source, captured_at).

    Append-only: ``withdraw()`` stamps ``withdrawn_at`` but never
    deletes the row. The full lifecycle is preserved for legal audit.
    """

    class ConsentType(models.TextChoices):
        # Sprint 3 ships 4 types per PHASE0_DESIGN §3.7. New types added
        # in later sprints follow the same ADR-0007-style alter_choices
        # migration recipe.
        PERSONAL_DATA = "personal_data", "Personal data (152-ФЗ)"
        MARKETING = "marketing", "Marketing"
        PHOTO_BIOMETRIC = "photo_biometric", "Photo / biometric"
        HEALTH = "health", "Health"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="consent_records",
        help_text="Denormalised for the E1 leakage scanner + per-tenant "
        "consent queries (Sprint 5+ replay). Mirrors bot_user.tenant.",
    )
    bot_user = models.ForeignKey(
        "identity.BotUser",
        on_delete=models.CASCADE,
        related_name="consents",
        help_text="The user this consent belongs to. CASCADE because "
        "ConsentRecord is forensic-supporting metadata for the user; "
        "when the user is fully deleted via `delete_bot_user_data` "
        "(Sprint 2.5 H1), their consent history goes with them. The "
        "audit trail of the deletion itself lives in AuditLog.",
    )
    consent_type = models.CharField(
        max_length=32,
        choices=ConsentType.choices,
        help_text="Which consent this row is about. See ConsentType for the 4 Phase-0 categories.",
    )
    granted = models.BooleanField(
        default=False,
        help_text="True when the user actively granted. False rows exist "
        "for explicit-decline audit (user said 'no' to MARKETING).",
    )
    source = models.CharField(
        max_length=64,
        help_text="Where the consent came from. Free-form but conventional: "
        '"bot_message:<msg-uuid>" (explicit /command), "admin:override" '
        '(operator), "registration_form" (wizard), "implicit_first_msg" '
        "(152-ФЗ baseline auto-grant).",
    )
    document_version = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Snapshot of which privacy-policy version the user saw. "
        'Empty for legacy / implicit grants. Example: "privacy-v1.2".',
    )
    captured_at = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set by withdraw(); leaves the row in DB as audit trail.",
    )

    # Default manager scopes to current_tenant(). Use ``all_tenants`` for
    # admin / cleanup / replay code that must see across tenants.
    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Consent record"
        verbose_name_plural = "Consent records"
        ordering = ["-captured_at"]
        indexes = [
            # Hot read path: has_consent(bot_user, type) — needs latest
            # row per (bot_user, type).
            models.Index(fields=["bot_user", "consent_type", "-captured_at"]),
            # Per-tenant analytics: how many active MARKETING consents?
            models.Index(fields=["tenant", "consent_type", "granted"]),
        ]

    def __str__(self) -> str:
        status = "granted" if self.granted else "denied"
        if self.withdrawn_at is not None:
            status = "withdrawn"
        return f"ConsentRecord[{self.consent_type}/{status}]"
