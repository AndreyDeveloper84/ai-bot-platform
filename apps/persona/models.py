"""BrandVoiceConfig skeleton (DRF-445 / Sprint 2 / E1).

Sprint-1 debt clear-up: PHASE0_DESIGN.md §3.1 originally listed
`brand_voice FK` on Tenant. Sprint 1 shipped Tenant without it (scope
narrowed). Sprint 2 lands the *shell* so the schema migration is clean
and Sprint 3+ AI orchestrator can wire prompt-composition through it.

### Shape decision: OneToOne *from* BrandVoiceConfig *to* Tenant

The plan task description wrote "Tenant.brand_voice OneToOneField →
BrandVoiceConfig". That's redundant if BrandVoiceConfig already has
`tenant = OneToOneField(Tenant)` — you'd have two columns describing
the same relation. We pick the direction with the related-name on
Tenant (so callers write `tenant.brand_voice`) but the FK column
lives on BrandVoiceConfig. One row per tenant, nullable from the
Tenant side via the reverse accessor (raises `BrandVoiceConfig.
DoesNotExist` when absent — callers handle with `hasattr` or a
try/except).

Sprint 4 (when persona work lands properly) will fill in:
- voice_examples — concrete tone-of-voice snippets for in-context learning
- disclaimers — per-tenant legal/safety wording attached to responses
- tone style guides

For Sprint 2 the model exists so:
1. The schema migration adding the other Tenant fields can include the
   related FK pattern.
2. The brand_voice reverse accessor exists for Sprint 3 prompt-composition
   code that imports `tenant.brand_voice` even if it's None today.
"""

from __future__ import annotations

import uuid

from django.db import models


class BrandVoiceConfig(models.Model):
    """Per-tenant brand-voice / persona configuration.

    Skeleton only in Sprint 2. Sprint 4 fills with real persona data
    (voice_examples + disclaimers + tone) when the AI orchestrator
    starts composing prompts.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.OneToOneField(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        related_name="brand_voice",
        help_text="The tenant this configuration belongs to. CASCADE — "
        "if a tenant is hard-deleted (rare; usually deactivated), its "
        "persona config goes too.",
    )
    tone = models.CharField(
        max_length=100,
        default="friendly",
        help_text="Placeholder for Sprint 2 — replaced by structured tone fields in Sprint 4.",
    )
    voice_examples = models.JSONField(
        default=list,
        blank=True,
        help_text="In-context learning samples for the LLM. Sprint 4 "
        "populates from the operator's tone-of-voice guide.",
    )
    disclaimers = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-tenant legal / safety wording attached to "
        "responses. Sprint 4 populates from operator config.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Brand voice configuration"
        verbose_name_plural = "Brand voice configurations"

    def __str__(self) -> str:
        return f"BrandVoice[{self.tenant.slug}]({self.tone})"
