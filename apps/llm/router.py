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

Since DRF-2147 the same hop fires on **unavailability** — the primary's
retry budget spent on timeouts / connection failures / 5xx
(``RetriableLLMError``), a bare transport error, or an open breaker —
and every switch pages the operators (``alerting.page("warning", "llm
fallback", …)``, deduplicated 5 min) and stamps
``CompletionResult.fallback_from`` for the turn metric. A 400 / 422
still does not hop: see :func:`hop_kind`.

**This used to be the caller's job and the callers never did it.**
Until DRF-1437 the router was purely "pick provider" and the fallback
was driven by the call site re-asking with
``get_provider(prefer_fallback_from=current)``. A tree sweep on
2026-08-31 found zero production call sites doing so — the parameter
was exercised only by tests. So the hop now lives in
:class:`FallbackProvider` (``QuotaFallbackProvider`` until DRF-2147), a
wrapper applied inside :meth:`get_provider`; every call site inherits it
and none can forget it. ``prefer_fallback_from`` survives for the explicit path and
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
/ ``org_default`` / ``embedding_fallback`` / ``quota_fallback`` /
``unavailable_fallback``).
Sprint 8 monitoring panels filter on ``source`` to spot tier-cascade
patterns ("everyone's hitting embedding_fallback" → Anthropic is
the configured default in places it shouldn't be).
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from django.conf import settings

from apps.audit.services import write_audit
from apps.llm.model_tiers import resolve_model
from apps.llm.protocol import (
    CompletionResult,
    LLMProvider,
    LLMProviderQuotaExceeded,
    LLMProviderUnavailable,
    LLMTransportError,
)
from apps.llm.retry import RetriableLLMError

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
# DRF-2147 — a hop taken because the primary was UNAVAILABLE (timeout,
# connection failure, 5xx, open breaker), not because it ran out of
# quota. Kept apart from ``quota_fallback`` so the panels that filter on
# the latter keep meaning what they meant.
_SOURCE_UNAVAILABLE_FALLBACK = "unavailable_fallback"

EVENT_PROVIDER_RESOLVED = "llm.provider_resolved"
# Historical name, kept: every hop — quota OR unavailability — writes this
# event, and ``payload["kind"]`` / ``payload["source"]`` say which. Renaming
# the event would silently empty every panel filtering on it.
EVENT_QUOTA_FALLBACK_USED = "llm.quota_fallback_used"

# DRF-2147 — why the wrapper hopped. ``payload["kind"]`` on the audit row.
HOP_KIND_QUOTA = "quota"
HOP_KIND_UNAVAILABLE = "unavailable"

# DRF-2147 — operator page on every switch, collapsed by the alerting
# layer's dedup window (``ALERTS_DEDUP_TTL_SECONDS``, 5 min by default).
FALLBACK_PAGE_TITLE = "llm fallback"
FALLBACK_PAGE_DEDUP_PREFIX = "llm_fallback"


def hop_kind(exc: BaseException) -> str | None:
    """Classify a primary-provider failure: hop, and why — or stay put.

    Returns :data:`HOP_KIND_QUOTA`, :data:`HOP_KIND_UNAVAILABLE`, or
    ``None`` for «do not hop, re-raise». THE single place that decides
    which failures move traffic to another vendor (DRF-2147); the
    wrapper and the tests both read it here.

    * **quota** — :class:`LLMProviderQuotaExceeded` and its subclass
      :class:`~apps.llm.protocol.LLMVendorCreditsExhausted`: our daily
      cap or the vendor's drained balance. The Sprint 7 contract, kept.
    * **unavailable** — the vendor could not be reached or could not
      serve, and the layer whose job it was to wait for it has given up:

      - :class:`~apps.llm.retry.RetriableLLMError` — the provider's own
        retry budget spent on transient failures (``APITimeoutError``,
        ``APIConnectionError``, ``InternalServerError``, 5xx / 429 by
        status). This is the shape the 2026-09-15 incident took: 45
        minutes of a dead Anthropic proxy, every turn ending in the
        static stub while OpenAI, behind another proxy, was healthy.
        A 429 that survived the retries is included on purpose: after
        the wait the vendor still would not serve, and the client is
        waiting too.
      - :class:`~apps.llm.protocol.LLMTransportError` — the same three
        SDK classes surfaced without a retry loop around them.
      - ``BreakerOpenError`` (matched by class name — it lives in
        ``apps.orchestrator.llm.breaker``, which this module must not
        import) — the breaker refuses to even try.

    * **None** — everything else. In particular a plain
      :class:`~apps.llm.protocol.LLMError`, which is what the providers
      raise for 400 / 422 / 401 / 403 / 404: the request is wrong, or
      we are; the other vendor would say the same, at a second price.
      A direct :class:`~apps.llm.protocol.LLMQuotaError` (a vendor
      rate-limit that did NOT pass through the retry layer) stays put
      too — «slow down» is the retry layer's job, not a reason to move.
    """
    if isinstance(exc, LLMProviderQuotaExceeded):
        return HOP_KIND_QUOTA
    if isinstance(exc, (RetriableLLMError, LLMTransportError)):
        return HOP_KIND_UNAVAILABLE
    if any(klass.__name__ == "BreakerOpenError" for klass in type(exc).__mro__):
        return HOP_KIND_UNAVAILABLE
    return None


def _hop_reason(exc: BaseException) -> str:
    """Short, secret-free description for the log line, the audit row and the page."""
    if isinstance(exc, RetriableLLMError):
        return (
            f"{type(exc).__name__}(attempts={exc.attempts}, "
            f"last_error={type(exc.last_error).__name__})"
        )
    return type(exc).__name__


def _retarget_model(kwargs: dict[str, Any], secondary: LLMProvider) -> dict[str, Any]:
    """Swap the caller's model id for one the FALLBACK vendor knows.

    Model ids are vendor-specific and there is no translation between
    them. Forwarding the primary's id verbatim is the difference between
    a working hop and a hop that trades one failure for another: sending
    ``gpt-4o-mini`` to ``api.anthropic.com`` returns
    ``404 not_found_error``, so the user would still get the static
    "не могу ответить" — with the second vendor's bill attached.

    Every call site passes a model tied to the vendor the router picked:
    the skills read ``provider.default_completion_model`` — which, through
    this wrapper, is the PRIMARY's default — and until DRF-1443
    ``apps/orchestrator/intent_router.py`` hard-coded ``"gpt-4o-mini"``.
    (It now names the ``"fast"`` tier instead; a tier is vendor-neutral,
    so on a hop it needs resolving, not replacing.) Either way the value
    arriving here is not yet an id the TARGET understands.

    DRF-1443 moved the actual translation into
    :func:`apps.llm.model_tiers.resolve_model`, which the concrete
    providers now run on EVERY call rather than only on a hop — the hop
    was never the only place a foreign id could arrive, and with
    Anthropic promoted to primary it stopped being a place one arrived
    at all. This function is kept because it still earns its keep: it
    logs the swap against the routing decision that caused it, which a
    provider-level rewrite cannot attribute. Running the same resolver
    on both sides is safe — it is idempotent, since an id already
    belonging to the target vendor is returned unchanged.

    The cost consequence noted here before is also gone: the resolver
    preserves the TIER, so an intent classification that hops now lands
    on the target's fast model rather than its reply model.
    """
    if "model" not in kwargs:
        return kwargs

    smart = getattr(secondary, "default_completion_model", "") or ""
    if not smart:
        # Nothing better to offer — leave the caller's value alone
        # rather than sending an empty model id.
        return kwargs

    target_model = resolve_model(
        kwargs["model"],
        vendor=getattr(secondary, "name", "") or "",
        fast=getattr(secondary, "default_fast_model", "") or smart,
        smart=smart,
    )

    if kwargs["model"] == target_model:
        return kwargs

    logger.info(
        "llm.router.quota_fallback_model_swap from=%s to=%s",
        kwargs["model"],
        target_model,
    )
    return {**kwargs, "model": target_model}


class FallbackProvider:
    """Wraps a primary provider and hops to the next one when it cannot answer.

    ### Why this exists (DRF-1437, widened by DRF-2147)

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

    Until DRF-2147 this class was ``QuotaFallbackProvider`` and hopped
    ONLY on quota. On 2026-09-15 the Anthropic proxy was down for 45
    minutes; every turn ran primary → retry → ``RetriableLLMError`` →
    the static stub, while OpenAI behind a different proxy was healthy.
    The hop now also fires on unavailability — see :func:`hop_kind` for
    the exact set, and for what still does NOT hop (400 / 422).

    ### Scope of the hop

    * ``complete`` only. ``embedding`` deliberately does NOT hop —
      the only embedding-capable vendor in the registry is the one that
      just failed, and the ``op="embedding"`` resolution already routes
      there. A hop would be a hop to nowhere.
    * ONE hop per call — the first configured candidate, never a walk
      down the list. If it raises too, that exception propagates and
      callers keep their existing degradation: the concierge draws its
      outage line, the pipeline its «retry exhausted» stub + alert.
      With the two-vendor registry a hop and a walk are the same thing;
      widening to a walk when a third vendor lands multiplies latency
      and spend across N vendors on one turn, so it wants its own
      ticket.
    * Every switch is announced: an audit row (``source`` says quota or
      unavailability), an operator page (``warning`` / «llm fallback»,
      deduplicated by the alerting layer — 5 minutes by default), and
      ``CompletionResult.fallback_from`` on the answer so the turn metric
      can record which vendor answered and which one did not.

    ### What the wrapper does NOT do

    It does not tokenize PII and it does not translate tools. Both
    happen a layer down: ``load`` is :meth:`LLMRouter._load_provider`,
    which returns every vendor — the fallback included — already inside
    :class:`~apps.llm.pii_protected_provider.PIITokenizingProvider`;
    and the concrete providers each convert the canonical tool spec to
    their own shape on every call, hop or not. The tests pin both with a
    deliberately mis-wired ``load`` that hands out a bare vendor.
    """

    def __init__(
        self,
        *,
        primary: LLMProvider,
        primary_name: str,
        load: "Callable[[str], LLMProvider]",
        candidates: list[str],
        audit: "Callable[[str, str, str], Awaitable[None]]",
        page: "Callable[[str, str, str], Awaitable[None]] | None" = None,
    ) -> None:
        self._primary = primary
        self._candidates = candidates
        self._load = load
        self._audit = audit
        self._page = page
        # Audit, cost attribution and telemetry read ``.name`` and must
        # see the vendor actually chosen, not the wrapper.
        self.name = primary_name
        # Model defaults are read off the provider by several call sites
        # (``getattr(provider, "default_completion_model", None)``), so
        # the wrapper has to be transparent for them too.
        self.default_completion_model = getattr(primary, "default_completion_model", "")
        # DRF-1443 — same transparency rule for the fast tier.
        self.default_fast_model = getattr(primary, "default_fast_model", "")
        # Only when the primary really has one — see the same rule in
        # ``PIITokenizingProvider``: an invented empty attribute would
        # make a non-embedding vendor look like one with a blank default.
        if hasattr(primary, "default_embedding_model"):
            self.default_embedding_model = primary.default_embedding_model

    async def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> CompletionResult:
        try:
            return await self._primary.complete(messages, **kwargs)
        except Exception as exc:
            kind = hop_kind(exc)
            if kind is None:
                # A bad request, a foreign exception, a direct rate-limit:
                # not this wrapper's business. Same exception, same
                # handler at the call site as before.
                raise
            if not self._candidates:
                # No configured alternative. Re-raise so the call site's
                # existing LLMError handling serves its static fallback —
                # and so the log says "nowhere to go", not "we never
                # tried".
                logger.error(
                    "llm.router.fallback_exhausted from=%s kind=%s reason=%s "
                    "candidates=0 (check the other vendor's API key setting)",
                    self.name,
                    kind,
                    _hop_reason(exc),
                )
                raise

            # ONE hop, deliberately — the first candidate only, never a
            # walk down the list. This is the Sprint 7 contract and with
            # today's two-vendor registry the two are identical. When a
            # third vendor lands, widening this to a walk is a real
            # behaviour change (latency and spend multiply across N
            # vendors on a single turn) and wants its own ticket rather
            # than arriving as a side effect of the registry refactor.
            candidate = self._candidates[0]
            reason = _hop_reason(exc)
            logger.warning(
                "llm.router.fallback from=%s to=%s kind=%s reason=%s remaining_untried=%d",
                self.name,
                candidate,
                kind,
                reason,
                len(self._candidates) - 1,
            )
            await self._audit(candidate, reason, kind)
            if self._page is not None:
                await self._page(candidate, reason, kind)
            secondary = self._load(candidate)
            result = await secondary.complete(messages, **_retarget_model(kwargs, secondary))
            # Name the vendor that did NOT answer next to the one that did,
            # so the turn metric can tell a hop from a plain OpenAI turn.
            # Guarded: a duck-typed double that returns something other
            # than the DTO must not turn a successful hop into a crash.
            if isinstance(result, CompletionResult):
                return replace(result, fallback_from=self.name)
            return result

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

    def preload(self, name: str) -> LLMProvider:
        """Construct + cache one provider WITHOUT resolving or auditing.

        DRF-1445. The warm-up path (:mod:`apps.llm.warmup`) needs the
        very instance ``get_provider`` will later hand out, so that the
        SDK import and client construction it pays for are the ones the
        serving path skips. It must not go through ``get_provider``
        itself: that resolves tiers it has no tenant for and writes an
        audit row for a resolution nobody asked for.

        Raises :class:`LLMProviderUnavailable` exactly as the serving
        path would - the caller decides whether a vendor that cannot
        even be constructed is fatal (it is not, at boot).
        """
        return self._load_provider(name)

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

        # DRF-1437 — wrap in the one-hop fallback (quota, and since
        # DRF-2147 unavailability). Skipped when:
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
        """Build the :class:`FallbackProvider` around ``provider``.

        The wrapper is built per resolution (cheap — it holds references,
        not clients) rather than cached, because the candidate list
        depends on live settings and the audit / page closures carry this
        call's tenant/skill context.
        """

        async def _audit_hop(chosen: str, reason: str, kind: str) -> None:
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
                "source": (
                    _SOURCE_QUOTA_FALLBACK
                    if kind == HOP_KIND_QUOTA
                    else _SOURCE_UNAVAILABLE_FALLBACK
                ),
                "kind": kind,
                "reason": reason,
            }
            try:
                await sync_to_async(_write_quota_fallback_audit, thread_sensitive=False)(payload)
            except Exception:  # noqa: BLE001 — telemetry must not break the hop
                logger.exception(
                    "llm.router.fallback_audit_failed from=%s to=%s",
                    primary_name,
                    chosen,
                )

        async def _page_hop(chosen: str, reason: str, kind: str) -> None:
            # DRF-2147 — operators must see that the bot is living on the
            # second vendor. ``alerting.page`` is sync (cache + ORM +
            # HTTP), so it runs in a thread like the audit row; it never
            # raises by contract, and the belt-and-braces ``except`` is
            # for the contract being wrong — an alert must never cost the
            # client the answer that was just obtained.
            from asgiref.sync import sync_to_async

            try:
                await sync_to_async(_page_fallback, thread_sensitive=False)(
                    primary_name, chosen, reason, kind, skill
                )
            except Exception:  # noqa: BLE001 — alerting must not break the hop
                logger.exception(
                    "llm.router.fallback_page_failed from=%s to=%s",
                    primary_name,
                    chosen,
                )

        return FallbackProvider(
            primary=provider,
            primary_name=primary_name,
            load=self._load_provider,
            candidates=candidates,
            audit=_audit_hop,
            page=_page_hop,
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
        """Walk the three tiers; return (provider_name, source).

        Instance-level alias for :func:`resolve_provider_tier`. The walk
        reads only settings and its arguments — never ``self`` — so
        DRF-1631 moved the body to module level, where a NON-serving
        caller (the health probe) can run *the same* walk rather than
        re-implement it. Kept under this name because the serving path
        and its tests have called it so since Sprint 7.
        """
        return resolve_provider_tier(tenant, skill=skill)

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
            raw_provider: Any = build_provider(name)
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


def _page_fallback(
    from_provider: str, to_provider: str, reason: str, kind: str, skill: str
) -> None:
    """Sync helper for the async page hook in :meth:`LLMRouter._wrap_with_fallback`.

    One ``warning`` page per switch, keyed by the (from, to) pair so the
    alerting layer's dedup window (``ALERTS_DEDUP_TTL_SECONDS``, 5 min)
    turns a 45-minute outage into nine pages rather than nine hundred.
    The body names the vendors, the reason class and the skill — never a
    message, a key or a URL. Delivery (Telegram + Sentry today; the MAX
    receiver is DRF-2158's) is the alerting module's business.
    """
    from apps.observability import alerting

    why = "квота" if kind == HOP_KIND_QUOTA else "недоступность"
    body = (
        f"Переключение на запасного провайдера LLM: {from_provider} → {to_provider}.\n"
        f"Причина: {why} — {reason}.\n"
        f"Навык: {skill or '-'}. Бот отвечает через {to_provider}, пока "
        f"{from_provider} не восстановится; повтор страницы не чаще раза в окно дедупа."
    )
    sent = alerting.page(
        "warning",
        FALLBACK_PAGE_TITLE,
        body,
        dedup_key=f"{FALLBACK_PAGE_DEDUP_PREFIX}:{from_provider}:{to_provider}",
    )
    logger.info(
        "llm.router.fallback_paged from=%s to=%s kind=%s sent=%s",
        from_provider,
        to_provider,
        kind,
        sent,
    )


def resolve_provider_tier(
    tenant: "Tenant | None" = None,
    *,
    skill: str = "",
) -> tuple[str, str]:
    """Walk the three tiers; return ``(provider_name, source)``.

    THE single implementation of "which vendor answers here". Both the
    serving path (:meth:`LLMRouter.get_provider`, via
    :meth:`LLMRouter._resolve_candidate`) and the DRF-1631 health probe
    call this function — not a copy of it, not a rule that happens to
    agree with it.

    Tiers, highest first:

    1. ``Tenant.features["llm_provider"]`` — the canary surface;
    2. ``settings.SKILL_LLM_PROVIDER[skill]`` — per-skill override;
    3. ``settings.LLM_PROVIDER`` — org-wide default.

    A tier holding a name the registry does not know falls through to
    the next rather than raising: a typo in an env var must not take the
    bot down.

    Note what a caller passing neither ``tenant`` nor ``skill`` gets:
    tier 3, and only tier 3. That is the honest answer for a context
    that has no tenant and no skill — the health probe's context — and
    it is why :func:`configured_vendor_names` exists to name the vendors
    such a caller is therefore NOT speaking for.
    """
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


def build_provider(name: str, **provider_kwargs: Any) -> Any:
    """Construct a FRESH, unwrapped instance of vendor ``name``.

    THE single implementation of "name → concrete provider object".
    :meth:`LLMRouter._load_provider` calls it and then caches and wraps
    the result; the DRF-1631 health probe calls it and does neither, on
    purpose (see :mod:`apps.llm.health` — a pooled client would sail
    past the very failure the probe exists to catch).

    Sharing this function is what makes "the probe measures the vendor
    the router serves" a property of the code rather than of somebody
    remembering to update two places. Before DRF-1631 the probe named
    ``OpenAIProvider`` in an import; the pilot ran ``LLM_PROVIDER=
    anthropic``; the panel reported OpenAI's empty wallet as an outage
    for 2018 consecutive ticks while a genuine Anthropic failure would
    have gone unnoticed.

    Raises :class:`LLMProviderUnavailable` for a name outside the
    registry. Constructor failures (missing key, missing SDK) propagate
    as-is — ``_load_provider`` is what converts those into the typed
    error for the serving path, and the probe wants the raw exception so
    it can name the SDK class in the alert.
    """
    return provider_class(name)(**provider_kwargs)


def provider_class(name: str) -> Any:
    """Import and return the concrete provider CLASS registered as ``name``.

    The name→class step on its own, split out of :func:`build_provider`
    so a caller that must not *instantiate* — a test patching
    ``complete`` on whichever class the registry names, for instance —
    still goes through the registry instead of importing a vendor
    module by hand. Importing by hand is the whole of DRF-1631.

    Raises :class:`LLMProviderUnavailable` for an unregistered name.
    """
    spec = _PROVIDER_SPECS.get(name)
    if spec is None:
        raise LLMProviderUnavailable(f"unknown provider name: {name!r}")
    module = importlib.import_module(spec.import_path)
    return getattr(module, spec.class_name)


def configured_vendor_names() -> list[str]:
    """Every vendor this deployment's SETTINGS can route a live turn to.

    ``LLM_PROVIDER`` plus every value of ``SKILL_LLM_PROVIDER``, in that
    order, de-duplicated, restricted to names the registry knows.

    The per-TENANT tier is deliberately absent: reading it is a database
    query, and a tenant override is by definition the canary case.

    Used by the health probe to state, out loud, which vendors its one
    call does NOT speak for. A probe covers tier 3; a deployment that
    also sets ``SKILL_LLM_PROVIDER`` is serving some skills from a
    vendor nobody is watching, and that is a fact an operator has to be
    told rather than left to infer from a green lamp.

    (:func:`apps.llm.warmup.warmup_provider_names` computes a near-twin
    for a different question — what to *warm* — and honours the
    ``LLM_WARMUP_PROVIDERS`` pin plus an is-the-key-set filter, neither
    of which belongs in "what could be called". Kept separate on
    purpose.)
    """
    wanted: list[str] = [str(getattr(settings, "LLM_PROVIDER", "") or "")]
    skill_map = getattr(settings, "SKILL_LLM_PROVIDER", {}) or {}
    wanted += [value for value in skill_map.values() if isinstance(value, str)]

    out: list[str] = []
    for name in wanted:
        if name and name in _PROVIDER_SPECS and name not in out:
            out.append(name)
    return out


def registered_provider_names() -> tuple[str, ...]:
    """Every vendor name the registry knows, in declaration order.

    Public read of :data:`_PROVIDER_NAMES` for callers outside this
    module (DRF-1445 warm-up) that need to validate an operator-supplied
    vendor name without reaching into a private.
    """
    return _PROVIDER_NAMES


def provider_is_configured(name: str) -> bool:
    """True when ``name``'s API-key setting is present and non-empty.

    Used to gate FALLBACK targets only — never the primary. A primary
    with no key still gets constructed so the failure surfaces as a
    loud ``LLMProviderUnavailable`` naming the misconfigured vendor,
    which is the diagnosis an operator needs. A fallback target with no
    key, by contrast, must be skipped silently-but-audibly: hopping
    onto a vendor that is guaranteed to 401 converts one dead provider
    into two and buries the original cause under an auth error.

    ### The policy this gate arms

    DRF-1631 / owner decision В-14 (10.09.2026) named what this gate
    decides on the serving path: a key being *present* is the whole of
    the evidence that lets :class:`FallbackProvider` move live traffic
    off the configured vendor and onto another one. В-14 forbade that
    while no policy existed — a fall-back is permitted only under a
    policy designed, approved and tested.

        **Holding a secret is not permission to fall back.**

    The policy now exists: DRF-2147 (owner decision В3, 20.09.2026) —
    one hop along ``LLM_FALLBACK_ORDER`` on quota and on unavailability,
    never on a 400 / 422, every switch paged to the operators and
    stamped on the answer (``fallback_from``) for the turn metric. The
    key is still what makes a vendor a *candidate*; the policy is what
    says a candidate is wanted, and ``LLM_FALLBACK_ORDER`` is where the
    operator states the preference (the pilot: ``anthropic,openai``).

    ``OPENAI_API_KEY`` is on the pilot for a second, legitimate reason
    (embeddings — Anthropic has no embeddings API, so ``op="embedding"``
    routes to OpenAI by design; see the module docstring). One key
    serves two purposes and this function cannot tell them apart; the
    operator switch for completions alone is
    ``LLM_QUOTA_FALLBACK_ENABLED=0``.
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
