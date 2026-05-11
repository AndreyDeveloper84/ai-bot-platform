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
        on_delete=models.PROTECT,
        related_name="brand_voice",
        help_text="The tenant this configuration belongs to. PROTECT for "
        "consistency with every other Tenant FK in the platform "
        "(audit/events/conversations/messages/bot_users/idempotency/"
        "webhooks). Tenant deletes are an operational event blocked by "
        "design — soft-deactivate via Tenant.is_active=False instead.",
    )
    tone = models.CharField(
        max_length=100,
        default="friendly",
        help_text="Sprint 2 placeholder — kept for backward compat. "
        "Sprint 4+ readers should use the structured `tone_vector` JSON.",
    )
    voice_examples = models.JSONField(
        default=list,
        blank=True,
        help_text="In-context learning samples for the LLM (populated Sprint 4 / C1).",
    )
    disclaimers = models.JSONField(
        default=dict,
        blank=True,
        help_text="Sprint 2 placeholder. Sprint 4 / A3 moved per-tenant "
        "disclaimers into `apps.promptreg.DisclaimerLibrary` — this "
        "field is dead weight kept for back-compat; cleanup in Sprint 5.",
    )
    # Sprint 4 / C1 (DRF-488) — structured voice config per PHASE0_DESIGN §3.5.
    persona = models.JSONField(
        default=dict,
        blank=True,
        help_text='Structured persona: {"name": "Alina", "role": "consultant", '
        '"age": 32, "traits": ["warm", "professional"]}. Sprint 4 ships the '
        "schema; Phase 1 wires it through `ayla-ai-core` voice utils.",
    )
    tone_vector = models.JSONField(
        default=dict,
        blank=True,
        help_text='Numeric tone axes 0..1: {"warmth": 0.8, "formality": 0.5, '
        '"energy": 0.6}. LLM prompt composition reads these to bias style.',
    )
    forbidden_phrases = models.JSONField(
        default=list,
        blank=True,
        help_text="List of regex patterns. C4 voice_check validates outbound "
        "text against these and emits `safety_triggered` on match.",
    )
    tone_modulations = models.JSONField(
        default=dict,
        blank=True,
        help_text="Conditional tone overrides by context. Example: "
        '{"risk_level=high": {"formality": 0.9, "warmth": 0.4}, '
        '"sentiment=frustrated": {"warmth": 0.95}}.',
    )
    version = models.IntegerField(
        default=1,
        help_text="Bumped on every operator save — loaders use it as the "
        "cache key tail so config changes invalidate without explicit "
        "pub/sub plumbing.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Brand voice configuration"
        verbose_name_plural = "Brand voice configurations"

    def __str__(self) -> str:
        return f"BrandVoice[{self.tenant.slug}]({self.tone})"
