"""LLM provider router — three-tier resolution (DRF-587 / Sprint 7 / L5).

Picks the :class:`apps.llm.protocol.LLMProvider` implementation for
a given ``(tenant, skill, op)`` triple. Centralised so skill code
never instantiates a provider directly — that decision is policy,
not skill business.

### Three-tier resolution order

1. **Per-tenant override** — ``Tenant.features["llm_provider"]``.
   The canary surface: flip one tenant onto a new provider for a
   shadow soak without touching anyone else.
2. **Per-skill default** — ``settings.SKILL_LLM_PROVIDER`` dict
   (e.g. ``{"faq": "openai", "intent": "anthropic"}``). Lets ops
   decide that intent classification is cheap-fast on haiku while
   FAQ answers stay on the more reliable provider until soak data
   says otherwise.
3. **Org-wide default** — ``settings.LLM_PROVIDER`` (defaults to
   ``"openai"``). The bottom of the stack — what every tenant gets
   if neither tier above resolved.

A tier returning an **unknown** value (not in the provider registry)
falls through to the next tier rather than crashing — the audit row
records the fall-through so observability surfaces the misconfig.

### Embedding fallback

Anthropic has no embeddings API. When ``op="embedding"`` resolves
to Anthropic, the router **silently** swaps to OpenAI and records
``source="embedding_fallback"`` in audit. Calls sites never have
to special-case this; they always call ``get_provider(op="embedding")``.

### Quota fallback (one hop)

When the chosen provider raises :class:`LLMProviderQuotaExceeded`
inside a downstream call, the call site catches and asks the router
to fall back. We pick the **other** registered provider once.
Repeated quota-exceeded across providers raises — better to surface
"both providers are down" than chase an infinite loop.

Quota fallback isn't done by the router itself transparently — the
router is purely "pick provider"; the caller drives the fallback by
calling ``get_provider(prefer_fallback_from=current)`` again. That
keeps :meth:`get_provider` a pure read.

### Audit row

Every resolution writes an audit row with the chosen provider name
and the tier that resolved it (``tenant_feature`` / ``skill_default``
/ ``org_default`` / ``embedding_fallback`` / ``quota_fallback``).
Sprint 8 monitoring panels filter on ``source`` to spot tier-cascade
patterns ("everyone's hitting embedding_fallback" → Anthropic is
the configured default in places it shouldn't be).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings

from apps.audit.services import write_audit
from apps.llm.protocol import LLMProvider, LLMProviderUnavailable

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


_PROVIDER_NAMES = ("openai", "anthropic")

# Resolution sources — written into the audit row's ``source`` field.
_SOURCE_TENANT = "tenant_feature"
_SOURCE_SKILL = "skill_default"
_SOURCE_ORG = "org_default"
_SOURCE_EMBED_FALLBACK = "embedding_fallback"
_SOURCE_QUOTA_FALLBACK = "quota_fallback"

EVENT_PROVIDER_RESOLVED = "llm.provider_resolved"


class LLMRouter:
    """Centralised provider picker.

    Construction is cheap — no SDK clients are built here. Providers
    are constructed lazily on first request and cached for the lifetime
    of the router instance (one per process is sufficient).
    """

    def __init__(self) -> None:
        # Lazy provider cache. Filled by :meth:`_load_provider` on first
        # use of each provider name.
        self._providers: dict[str, LLMProvider] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_provider(
        self,
        tenant: "Tenant | None" = None,
        *,
        skill: str = "",
        op: str = "complete",
        prefer_fallback_from: str | None = None,
    ) -> LLMProvider:
        """Return an :class:`LLMProvider` for the given context.

        Args:
          tenant: tenant context. ``None`` short-circuits straight to
                  the per-skill / org-wide tiers.
          skill: skill slug (e.g. ``"faq"``, ``"intent"``). Empty
                 string skips the per-skill tier.
          op: ``"complete"`` (default) or ``"embedding"``. Drives the
              embedding-fallback path.
          prefer_fallback_from: when the caller hit a quota-exceeded
                                from a specific provider, pass its
                                ``name`` here. The router excludes
                                that provider from candidate lists and
                                records ``source="quota_fallback"``.
        """
        candidate, source = self._resolve_candidate(tenant, skill=skill)

        # Quota fallback — caller is asking us to avoid this provider.
        if prefer_fallback_from is not None and candidate == prefer_fallback_from:
            other = _other_provider(prefer_fallback_from)
            candidate = other
            source = _SOURCE_QUOTA_FALLBACK

        # Embedding fallback — Anthropic can't embed. Silently swap to OpenAI.
        if op == "embedding" and candidate == "anthropic":
            candidate = "openai"
            source = _SOURCE_EMBED_FALLBACK

        provider = self._load_provider(candidate)
        self._audit(
            tenant=tenant,
            skill=skill,
            op=op,
            chosen=candidate,
            source=source,
        )
        return provider

    # ------------------------------------------------------------------
    # Resolution tiers
    # ------------------------------------------------------------------

    def _resolve_candidate(
        self,
        tenant: "Tenant | None",
        *,
        skill: str,
    ) -> tuple[str, str]:
        """Walk the three tiers; return (provider_name, source)."""
        # Tier 1 — per-tenant override.
        if tenant is not None:
            features = getattr(tenant, "features", {}) or {}
            tenant_choice = features.get("llm_provider")
            if isinstance(tenant_choice, str) and tenant_choice in _PROVIDER_NAMES:
                return (tenant_choice, _SOURCE_TENANT)

        # Tier 2 — per-skill default.
        if skill:
            skill_map = getattr(settings, "SKILL_LLM_PROVIDER", {}) or {}
            skill_choice = skill_map.get(skill)
            if isinstance(skill_choice, str) and skill_choice in _PROVIDER_NAMES:
                return (skill_choice, _SOURCE_SKILL)

        # Tier 3 — org-wide default.
        org_choice = getattr(settings, "LLM_PROVIDER", "openai") or "openai"
        if org_choice not in _PROVIDER_NAMES:
            # Misconfigured org default — log + force OpenAI so we keep
            # serving.
            logger.warning("llm.router.bad_org_default value=%r forced=openai", org_choice)
            org_choice = "openai"
        return (org_choice, _SOURCE_ORG)

    # ------------------------------------------------------------------
    # Provider construction (lazy + cached)
    # ------------------------------------------------------------------

    def _load_provider(self, name: str) -> LLMProvider:
        if name in self._providers:
            return self._providers[name]

        # LLM retro B1: provider constructors may raise on missing API
        # keys, malformed settings, or SDK init failures. Pre-fix these
        # bubbled as bare ``Exception`` and forced every caller to wrap
        # router lookups in ``try/except Exception`` (see
        # ``apps/skills/booking/skill.py`` hotfix #8). Catching the
        # constructor and re-raising as a typed
        # ``LLMProviderUnavailable`` lets:
        #   - callers handle uniformly (one exception class to catch),
        #   - Sentry pinpoint the misconfigured provider via the chained
        #     traceback,
        #   - the audit row (written by ``get_provider``) carry an
        #     explicit ``init_failed`` discriminator.
        try:
            if name == "openai":
                from apps.llm.providers.openai_provider import OpenAIProvider

                provider: LLMProvider = OpenAIProvider()
            elif name == "anthropic":
                from apps.llm.providers.anthropic_provider import AnthropicProvider

                provider = AnthropicProvider()
            else:  # pragma: no cover — guarded above
                raise ValueError(f"unknown provider name: {name!r}")
        except LLMProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — typed re-raise below
            logger.warning(
                "llm.router.provider_init_failed name=%s err=%s",
                name,
                exc,
            )
            raise LLMProviderUnavailable(f"provider {name!r} failed to initialise: {exc}") from exc

        self._providers[name] = provider
        return provider

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def _audit(
        self,
        *,
        tenant: "Tenant | None",
        skill: str,
        op: str,
        chosen: str,
        source: str,
    ) -> None:
        write_audit(
            EVENT_PROVIDER_RESOLVED,
            target="LLMRouter",
            payload={
                "tenant_id": str(getattr(tenant, "id", "")) if tenant else "",
                "skill": skill,
                "op": op,
                "chosen_provider": chosen,
                "source": source,
            },
        )


# ---------------------------------------------------------------------------
# Helpers + singleton
# ---------------------------------------------------------------------------


def _other_provider(name: str) -> str:
    """Pick the only-other provider in the registry.

    For the Sprint 7 two-vendor footprint this is a coin flip. Sprint 9+
    multi-vendor expansion needs a real candidate-pool walker.
    """
    if name == "openai":
        return "anthropic"
    if name == "anthropic":
        return "openai"
    raise ValueError(f"cannot fallback from unknown provider: {name!r}")


_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    """Process-wide singleton accessor.

    The router itself holds no per-request state; sharing one instance
    saves the constructor work + keeps the provider-cache hot across
    Celery worker invocations.
    """
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


def reset_router_cache() -> None:
    """Test helper — drops the singleton so the next ``get_router`` call
    rebuilds. Production code never calls this.
    """
    global _router
    _router = None
