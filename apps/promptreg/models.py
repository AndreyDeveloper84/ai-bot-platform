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
