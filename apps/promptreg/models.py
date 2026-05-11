"""Prompt registry — versioned LLM prompts with live reload (DRF-477 / Sprint 4 / A1).

Per PHASE0_DESIGN §3.4 + §F0.5, the platform stores skill prompts in
the DB so operators can edit them through Django admin without a
deploy. Each edit creates a new ``PromptVersion`` row; an explicit
``publish_prompt`` call (Sprint 4 / A4) atomically flips one version
to active for a given ``(tenant, skill_name)``.

### Why versioned, not just "current"

* **Rollback in a click** — bad prompt change → admin Unpublish reverts
  to the prior version without touching code.
* **A/B traffic ramp** — ``traffic_percent`` allows a canary rollout
  by serving a fraction of the bucketed traffic to the new prompt
  (Sprint 4 / B-track experiments).
* **Audit trail** — every published version is preserved with
  ``created_by`` + ``published_at``, so compliance can answer "what
  prompt was live when the bot said X?" months later.

### Default manager

``TenantScopedManager`` — the E1 leakage scanner picks PromptVersion
up automatically. The ``all_tenants`` escape hatch is reserved for
the admin + cleanup tasks.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.tenancy.managers import TenantScopedManager


class PromptVersion(models.Model):
    """One immutable revision of a skill prompt.

    Rows are created on every operator edit; ``publish_prompt`` flips
    exactly one row per ``(tenant, skill_name)`` to ``is_active=True``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="prompt_versions",
        help_text="Owning tenant. PROTECT — accidental tenant delete must not "
        "vapourise the prompt library that bot behaviour depends on.",
    )
    skill_name = models.CharField(
        max_length=64,
        help_text="Skill this prompt belongs to (e.g. 'privacy_consent', "
        "'human_handoff', 'faq'). Matches the Skill.name ClassVar.",
    )
    body = models.TextField(
        help_text="The prompt text — Jinja2-style placeholders allowed; "
        "the orchestrator interpolates at call time.",
    )
    version = models.IntegerField(
        help_text="Monotonic per-skill version number. Operator-assigned at "
        "create time; uniqueness enforced by Meta.unique_together.",
    )
    traffic_percent = models.IntegerField(
        default=100,
        help_text="Canary rollout percentage [0-100]. Sprint 4+ experiment "
        "framework gates traffic to this version by bucketed hash. Default "
        "100 means: when active, all traffic goes here.",
    )
    is_active = models.BooleanField(
        default=False,
        help_text="True iff this version is the currently-served prompt for "
        "its (tenant, skill_name). Exactly ONE row per pair has this set; "
        "publish_prompt (A4) enforces the invariant atomically.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="prompt_versions_created",
        help_text="Operator who authored this revision. PROTECT so a "
        "user-account delete cannot vapourise the audit trail.",
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Stamped by publish_prompt at the moment of activation. "
        "NULL while the version sits in draft.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Prompt version"
        verbose_name_plural = "Prompt versions"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "skill_name", "version"],
                name="promptreg_promptversion_unique_tenant_skill_version",
            ),
        ]
        indexes = [
            # Hot read path: "which version is currently active for
            # (tenant, skill_name)?" — registry.get_prompt() hits this.
            models.Index(fields=["tenant", "skill_name", "is_active"]),
        ]

    def __str__(self) -> str:
        flag = "*" if self.is_active else " "
        return f"PromptVersion[{self.skill_name}/v{self.version}{flag}]"


class ThresholdConfig(models.Model):
    """Per-tenant tunable numeric thresholds (DRF-478 / Sprint 4 / A2).

    Examples: ``intent_confidence_min = 0.65``, ``food_scan_min = 0.7``.
    Operators tune these without code change; A4 + A6 cache makes them
    cheap to read on every turn.

    The ``applied_to`` field scopes a threshold to a specific skill (or
    "" for global / fallback). Resolution order at read time:
      1. ``applied_to == skill_name`` match
      2. fall back to ``applied_to == ""``
      3. KeyError
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="threshold_configs",
        help_text="Owning tenant.",
    )
    key = models.CharField(
        max_length=64,
        help_text="Threshold identifier — by convention snake_case noun.",
    )
    value = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text="Numeric value. Decimal not float — exactness matters when "
        "operators tune to 0.65 etc.",
    )
    applied_to = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text='Skill name the threshold applies to. Empty string ("") = global / fallback.',
    )
    version = models.IntegerField(
        default=1,
        help_text="Monotonic per (tenant, key, applied_to). Lets operators "
        "publish a new value without losing the old.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Currently-served threshold. Default True (different from "
        "PromptVersion which defaults False) — most threshold edits are "
        "in-place tuning rather than canary rollouts.",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Threshold config"
        verbose_name_plural = "Threshold configs"
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "key", "applied_to", "version"],
                name="promptreg_threshold_unique_tenant_key_applied_version",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "key", "applied_to", "is_active"]),
        ]

    def __str__(self) -> str:
        scope = self.applied_to or "*"
        return f"ThresholdConfig[{self.key}@{scope}={self.value}]"


class DisclaimerLibrary(models.Model):
    """Versioned legal / safety disclaimers (DRF-479 / Sprint 4 / A3).

    Picked up by skills when a response needs a regulatory caveat
    ("pricing may vary depending on assessment", "consult a doctor",
    etc.). Per-category × risk_level lookup; A6 ``get_disclaimer``
    returns the active text.

    ### Lifecycle

    - New revision = new row with version+=1
    - Active row per (tenant, category, risk_level) → exactly one
    - Operator "retires" a disclaimer by stamping `withdrawn_at` (kept
      in DB for forensic / replay)
    """

    class Category(models.TextChoices):
        # Open-ended on purpose — adding a new category is a DB row,
        # not a migration. Sprint 4 ships the most common five.
        MEDICAL = "medical", "Medical"
        PRICING = "pricing", "Pricing"
        AFTERCARE = "aftercare", "Aftercare"
        BOOKING = "booking", "Booking"
        GENERAL = "general", "General"

    class RiskLevel(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="disclaimers",
        help_text="Owning tenant.",
    )
    category = models.CharField(
        max_length=32,
        choices=Category.choices,
        help_text="Disclaimer category. Free-text-with-suggested-choices: "
        "Sprint 4 ships 5 categories, operators may extend without migration.",
    )
    risk_level = models.CharField(
        max_length=8,
        choices=RiskLevel.choices,
        help_text="Audience-of-this-disclaimer severity. Skills pick the "
        "matching level by topic risk.",
    )
    text = models.TextField(
        help_text="The disclaimer text. Markdown allowed; channel adapter strips on the way out.",
    )
    version = models.IntegerField(
        default=1,
        help_text="Monotonic per (tenant, category, risk_level).",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Currently-served disclaimer. Default True — disclaimers "
        "are written deliberately; the operator publishes immediately.",
    )
    withdrawn_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the operator retires this disclaimer entirely "
        "(distinct from is_active=False which is just 'not current').",
    )

    objects = TenantScopedManager()
    all_tenants = models.Manager()

    class Meta:
        verbose_name = "Disclaimer"
        verbose_name_plural = "Disclaimers"
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "category", "risk_level", "version"],
                name="promptreg_disclaimer_unique_tenant_cat_risk_version",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "category", "risk_level", "is_active"]),
        ]

    def __str__(self) -> str:
        preview = self.text[:40] + ("…" if len(self.text) > 40 else "")
        return f"Disclaimer[{self.category}/{self.risk_level}]({preview!r})"
