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

Not every vendor has an embeddings API — Anthropic does not. When
``op="embedding"`` resolves to a vendor whose
``ProviderSpec.supports_embedding`` is False, the router **silently**
swaps to the first registered vendor that does and records
``source="embedding_fallback"`` in audit. Call sites never have to
special-case this; they always call ``get_provider(op="embedding")``.

### Quota fallback (one hop)

When the chosen provider raises :class:`LLMProviderQuotaExceeded` —
including its subclass :class:`LLMVendorCreditsExhausted`, "the vendor
says our balance is empty" — the resolved provider hops once to the
next configured vendor from :func:`fallback_candidates`. If that one
raises too, the exception propagates: better to surface "every vendor
is down" than chase an infinite loop.

**This used to be the caller's job and the callers never did it.**
Until DRF-1437 the router was purely "pick provider" and the fallback
was driven by the call site re-asking with
``get_provider(prefer_fallback_from=current)``. A tree sweep on
2026-08-31 found zero production call sites doing so — the parameter
was exercised only by tests. So the hop now lives in
:class:`QuotaFallbackProvider`, a wrapper applied inside
:meth:`get_provider`; every call site inherits it and none can forget
it. ``prefer_fallback_from`` survives for the explicit path and
suppresses the wrapper so a hand-driven retry cannot double-hop.

Fallback targets are filtered by :func:`provider_is_configured` — the
router will not hop onto a vendor whose API key is unset, because that
turns one dead provider into two and replaces a legible quota error
with an opaque 401.

### Adding a provider

Append a :class:`ProviderSpec` row to :data:`_PROVIDER_REGISTRY` and
ship the provider module. Nothing in the resolution tiers, the
fallback walker, or the embedding swap is hard-coded to two vendors.

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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.conf import settings

from apps.audit.services import write_audit
from apps.llm.protocol import (
    CompletionResult,
    LLMProvider,
    LLMProviderQuotaExceeded,
    LLMProviderUnavailable,
)

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the router needs to know about one vendor.

    Adding a vendor is meant to be a row here plus a provider module —
    not surgery on the resolution logic. Fields:

      name: stable identifier. Appears in ``Tenant.features["llm_provider"]``,
        ``SKILL_LLM_PROVIDER`` values, ``LLM_PROVIDER``, audit rows, and
        cost attribution. Never rename one in place.
      import_path / class_name: resolved lazily on first use so a missing
        optional SDK costs nothing until someone actually selects it.
      key_setting_name: the NAME of the Django setting that must be
        non-empty for this provider to be usable. The router refuses to
        FALL BACK onto a provider with no key — hopping onto a vendor
        that will 401 turns one dead provider into two and hides the
        real cause.
      supports_embedding: Anthropic has no embeddings API. Drives the
        embedding-fallback target choice.
    """

    name: str
    import_path: str
    class_name: str
    key_setting_name: str
    supports_embedding: bool


# Registry order is also the DEFAULT fallback preference order: when a
# provider is exhausted we walk this tuple, skipping the exhausted one
# and any vendor without a configured key.
_PROVIDER_REGISTRY: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="openai",
        import_path="apps.llm.providers.openai_provider",
        class_name="OpenAIProvider",
        key_setting_name="OPENAI_API_KEY",
        supports_embedding=True,
    ),
    ProviderSpec(
        name="anthropic",
        import_path="apps.llm.providers.anthropic_provider",
        class_name="AnthropicProvider",
        key_setting_name="ANTHROPIC_API_KEY",
        supports_embedding=False,
    ),
)

_PROVIDER_SPECS: dict[str, ProviderSpec] = {spec.name: spec for spec in _PROVIDER_REGISTRY}
_PROVIDER_NAMES: tuple[str, ...] = tuple(_PROVIDER_SPECS)

# Resolution sources — written into the audit row's ``source`` field.
_SOURCE_TENANT = "tenant_feature"
_SOURCE_SKILL = "skill_default"
_SOURCE_ORG = "org_default"
_SOURCE_EMBED_FALLBACK = "embedding_fallback"
_SOURCE_QUOTA_FALLBACK = "quota_fallback"

EVENT_PROVIDER_RESOLVED = "llm.provider_resolved"
EVENT_QUOTA_FALLBACK_USED = "llm.quota_fallback_used"


def _retarget_model(kwargs: dict[str, Any], secondary: LLMProvider) -> dict[str, Any]:
    """Swap the caller's model id for one the FALLBACK vendor knows.

    Model ids are vendor-specific and there is no translation between
    them. Forwarding the primary's id verbatim is the difference between
    a working hop and a hop that trades one failure for another: sending
    ``gpt-4o-mini`` to ``api.anthropic.com`` returns
    ``404 not_found_error``, so the user would still get the static
    "не могу ответить" — with the second vendor's bill attached.

    Every call site passes a model tied to the vendor the router picked:
    ``apps/orchestrator/intent_router.py`` hard-codes ``"gpt-4o-mini"``,
    while the skills read ``provider.default_completion_model`` — which,
    through this wrapper, is the PRIMARY's default. So on a hop the id is
    always wrong for the target and must be replaced.

    We use the target's own ``default_completion_model`` rather than a
    cross-vendor equivalence table: a table would need an entry per
    model per vendor and would silently mis-route the moment someone
    passes an unlisted id, which is precisely the failure mode this
    whole ticket is about. A vendor's declared default is always valid
    for that vendor.

    Note the cost consequence, deliberately accepted: Anthropic's
    default completion model is the reply-tier one, so an intent
    classification that hops lands on a pricier model than the
    ``gpt-4o-mini`` it asked for. Correct-but-pricier beats
    cheap-and-404 on a path that only runs when the primary is down.
    """
    if "model" not in kwargs:
        return kwargs

    target_model = getattr(secondary, "default_completion_model", "") or ""
    if not target_model:
        # Nothing better to offer — leave the caller's value alone
        # rather than sending an empty model id.
        return kwargs

    if kwargs["model"] == target_model:
        return kwargs

    logger.info(
        "llm.router.quota_fallback_model_swap from=%s to=%s",
        kwargs["model"],
        target_model,
    )
    return {**kwargs, "model": target_model}


class QuotaFallbackProvider:
    """Wraps a primary provider and hops to the next one on exhaustion.

    ### Why this exists (DRF-1437)

    The router has advertised a one-hop quota fallback since Sprint 7,
    but it was documented as *caller-driven*: the call site was supposed
    to catch :class:`LLMProviderQuotaExceeded` and re-ask the router with
    ``prefer_fallback_from=``. A sweep of the tree on 2026-08-31 found
    **zero** production call sites doing that — the parameter was
    exercised only by ``apps/llm/tests/test_router.py`` and a replay
    stub. Every real call site (``apps/skills/{faq,booking}``,
    ``apps/orchestrator/{concierge,discovery,intent_router}``,
    ``apps/master_api/services/{assistant,ai_drafts}``) instead caught
    ``LLMError`` and degraded straight to a static Russian fallback.

    So the guarantee depended on discipline at seven-plus call sites and
    got it at none. Moving the hop into a wrapper makes it structural:
    every consumer of ``get_provider`` inherits it, including consumers
    written after this comment, and a new call site cannot forget.

    ### Scope of the hop

    * ``complete`` only. ``embedding`` deliberately does NOT hop —
      the only embedding-capable vendor in the registry is the one that
      just failed, and the ``op="embedding"`` resolution already routes
      there. A hop would be a hop to nowhere.
    * One hop per call, then the pool is exhausted and the original
      exception is re-raised. Callers keep their existing
      ``except LLMError`` degradation for the both-vendors-down case.
    * Only :class:`LLMProviderQuotaExceeded` (and therefore its subclass
      :class:`apps.llm.protocol.LLMVendorCreditsExhausted`) triggers it.
      A transport error or a 5xx does NOT — those are the retry layer's
      job, and hopping vendors on a transient blip would double the
      spend and halve the observability for no gain.
    """

    def __init__(
        self,
        *,
        primary: LLMProvider,
        primary_name: str,
        load: "Callable[[str], LLMProvider]",
        candidates: list[str],
        audit: "Callable[[str, str], Awaitable[None]]",
    ) -> None:
        self._primary = primary
        self._candidates = candidates
        self._load = load
        self._audit = audit
        # Audit, cost attribution and telemetry read ``.name`` and must
        # see the vendor actually chosen, not the wrapper.
        self.name = primary_name
        # Model defaults are read off the provider by several call sites
        # (``getattr(provider, "default_completion_model", None)``), so
        # the wrapper has to be transparent for them too.
        self.default_completion_model = getattr(primary, "default_completion_model", "")
        self.default_embedding_model = getattr(primary, "default_embedding_model", "")

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> CompletionResult:
        try:
            return await self._primary.complete(messages, **kwargs)
        except LLMProviderQuotaExceeded as exc:
            for candidate in self._candidates:
                logger.warning(
                    "llm.router.quota_fallback from=%s to=%s reason=%s",
                    self.name,
                    candidate,
                    type(exc).__name__,
                )
                await self._audit(candidate, type(exc).__name__)
                secondary = self._load(candidate)
                return await secondary.complete(messages, **_retarget_model(kwargs, secondary))
            # No configured alternative. Re-raise so the call site's
            # existing LLMError handling serves its static fallback —
            # and so the audit trail says "nowhere to go", not "we
            # never tried".
            logger.error(
                "llm.router.quota_fallback_exhausted from=%s reason=%s "
                "candidates=0 (check the other vendor's API key setting)",
                self.name,
                type(exc).__name__,
            )
            raise

    async def embedding(self, text: str, **kwargs: Any) -> list[float]:
        """Pass-through — see the class docstring on why embeddings never hop."""
        return await self._primary.embedding(text, **kwargs)


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

        # Embedding fallback — not every vendor has an embeddings API
        # (Anthropic does not). Silently swap to the first registered
        # vendor that does.
        if op == "embedding" and not _PROVIDER_SPECS[candidate].supports_embedding:
            embed_targets = fallback_candidates(candidate, require_embedding=True)
            if embed_targets:
                candidate = embed_targets[0]
                source = _SOURCE_EMBED_FALLBACK
            else:
                logger.warning("llm.router.no_embedding_capable_provider candidate=%s", candidate)

        provider = self._load_provider(candidate)
        self._audit(
            tenant=tenant,
            skill=skill,
            op=op,
            chosen=candidate,
            source=source,
        )

        # DRF-1437 — wrap in the one-hop quota fallback. Skipped when:
        #   * the caller already drove an explicit hop
        #     (``prefer_fallback_from``) — that is the legacy path and
        #     must stay a pure pick, or a caller retrying by hand would
        #     get two hops per call;
        #   * ``op != "complete"`` — embeddings have nowhere to hop to;
        #   * the operator disabled it via ``LLM_QUOTA_FALLBACK_ENABLED``;
        #   * no other vendor has a key configured — then the wrapper
        #     would add a frame and change nothing.
        if (
            prefer_fallback_from is None
            and op == "complete"
            and getattr(settings, "LLM_QUOTA_FALLBACK_ENABLED", True)
        ):
            candidates = fallback_candidates(candidate)
            if candidates:
                return self._wrap_with_fallback(
                    provider=provider,
                    primary_name=candidate,
                    candidates=candidates,
                    tenant=tenant,
                    skill=skill,
                    op=op,
                )

        return provider

    def _wrap_with_fallback(
        self,
        *,
        provider: LLMProvider,
        primary_name: str,
        candidates: list[str],
        tenant: "Tenant | None",
        skill: str,
        op: str,
    ) -> LLMProvider:
        """Build the :class:`QuotaFallbackProvider` around ``provider``.

        The wrapper is built per resolution (cheap — it holds references,
        not clients) rather than cached, because the candidate list
        depends on live settings and the audit closure carries this
        call's tenant/skill context.
        """

        async def _audit_hop(chosen: str, reason: str) -> None:
            # ``write_audit`` is a sync ORM write and the hop happens
            # inside ``await provider.complete(...)`` — calling it
            # directly raises Django's SynchronousOnlyOperation. Same
            # shape as ``apps.llm.retry.write_retry_attempt_audit``.
            #
            # Failure is swallowed: this row is telemetry, and losing it
            # must never convert a successful fallback (the user got an
            # answer from the other vendor) into an error.
            from asgiref.sync import sync_to_async

            payload = {
                "tenant_id": str(getattr(tenant, "id", "")) if tenant else "",
                "skill": skill,
                "op": op,
                "from_provider": primary_name,
                "chosen_provider": chosen,
                "source": _SOURCE_QUOTA_FALLBACK,
                "reason": reason,
            }
            try:
                await sync_to_async(_write_quota_fallback_audit, thread_sensitive=False)(payload)
            except Exception:  # noqa: BLE001 — telemetry must not break the hop
                logger.exception(
                    "llm.router.quota_fallback_audit_failed from=%s to=%s",
                    primary_name,
                    chosen,
                )

        return QuotaFallbackProvider(
            primary=provider,
            primary_name=primary_name,
            load=self._load_provider,
            candidates=candidates,
            audit=_audit_hop,
        )

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
        spec = _PROVIDER_SPECS.get(name)
        if spec is None:  # pragma: no cover — guarded by the resolution tiers
            raise LLMProviderUnavailable(f"unknown provider name: {name!r}")

        try:
            import importlib

            module = importlib.import_module(spec.import_path)
            raw_provider: Any = getattr(module, spec.class_name)()
        except LLMProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — typed re-raise below
            logger.warning(
                "llm.router.provider_init_failed name=%s err=%s",
                name,
                exc,
            )
            raise LLMProviderUnavailable(f"provider {name!r} failed to initialise: {exc}") from exc

        # PII tokenization wrap (Phase D / 152-ФЗ Tier-A). Single-point
        # enforcement at the LLM-call boundary — every provider (OpenAI,
        # Anthropic, future vendors) gets wrapped automatically. The
        # decorator is a no-op when no PII scope is active, so internal
        # background flows pay only a ContextVar.get() check.
        from apps.llm.pii_protected_provider import PIITokenizingProvider

        provider: LLMProvider = PIITokenizingProvider(raw_provider)
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


def _write_quota_fallback_audit(payload: dict[str, Any]) -> None:
    """Sync helper for the async audit hook in :meth:`LLMRouter._wrap_with_fallback`."""
    write_audit(
        EVENT_QUOTA_FALLBACK_USED,
        target="LLMRouter",
        payload=payload,
    )


def provider_is_configured(name: str) -> bool:
    """True when ``name``'s API-key setting is present and non-empty.

    Used to gate FALLBACK targets only — never the primary. A primary
    with no key still gets constructed so the failure surfaces as a
    loud ``LLMProviderUnavailable`` naming the misconfigured vendor,
    which is the diagnosis an operator needs. A fallback target with no
    key, by contrast, must be skipped silently-but-audibly: hopping
    onto a vendor that is guaranteed to 401 converts one dead provider
    into two and buries the original cause under an auth error.
    """
    spec = _PROVIDER_SPECS.get(name)
    if spec is None:
        return False
    return bool(getattr(settings, spec.key_setting_name, "") or "")


def fallback_candidates(exclude: str, *, require_embedding: bool = False) -> list[str]:
    """Ordered, configured providers to try after ``exclude`` gave up.

    Order comes from ``settings.LLM_FALLBACK_ORDER`` when set (a list of
    provider names — lets an operator prefer a vendor reachable without
    a tunnel), otherwise from :data:`_PROVIDER_REGISTRY` declaration
    order. Unknown names in the setting are dropped with a warning
    rather than raising: a typo in an env var must not take the bot
    down on the one path whose entire job is surviving an outage.

    Nothing here assumes a two-vendor world — with a third registry row
    the walk simply yields two candidates instead of one.
    """
    configured_order = getattr(settings, "LLM_FALLBACK_ORDER", None)
    if configured_order:
        order: list[str] = []
        for candidate in configured_order:
            if candidate in _PROVIDER_SPECS:
                order.append(candidate)
            else:
                logger.warning("llm.router.bad_fallback_order_entry value=%r ignored", candidate)
    else:
        order = [spec.name for spec in _PROVIDER_REGISTRY]

    return [
        name
        for name in order
        if name != exclude
        and provider_is_configured(name)
        and (not require_embedding or _PROVIDER_SPECS[name].supports_embedding)
    ]


def _other_provider(name: str) -> str:
    """Back-compat shim for the explicit ``prefer_fallback_from`` path.

    Returns the first registered provider that is not ``name``,
    IGNORING key configuration — the caller asked for a specific swap
    and gets it. New code should prefer :func:`fallback_candidates`,
    which additionally filters out vendors that cannot possibly serve.
    """
    for spec in _PROVIDER_REGISTRY:
        if spec.name != name:
            return spec.name
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
